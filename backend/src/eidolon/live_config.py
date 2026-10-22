"""The live configuration: the settings the server started with (`.env`), overridden by
what admins change in the admin console (ADR-0013). It is the admin console's one way to
read and change the configuration.

The configuration is in items, each a few fields saved and restored together: the chat
and memory models, speech synthesis and recognition, image generation (`_Image`), the
judge model and its thresholds,
the reranker's thresholds, and the limits (per hour, per call, and the turns each prompt
carries, with the exempting keys), whether affinity and gifts are on, whether characters
reach out to users and how often at most (GLOSSARY.md「搭话」), and which of the
deployer's services and of users' own keys go through which proxy (GLOSSARY.md「网络代理」), and
which services users are shown the status of, over how long (GLOSSARY.md「服务状态」). Overrides are
kept in the database, one row per field, a key encrypted with a key derived from
`EIDOLON_SECRET_KEY` (without it, keys cannot be changed here, only seen). A change to a
service is tried before it is kept, as the chat model is when the server starts; it then holds for every turn and call that starts after
it, while those under way keep what they started with (see `lease` and
`service_clients.Swappable`). Restoring an item deletes its overrides.

The chat model has a reasoning effort for calls besides its own, for text turns: one
model, made into two over one HTTP client, replaced together. The memory model, unless
`.env` sets it apart (`BACKGROUND_LLM_*`) or the admin console does, is the chat model in
effect, where it is and which (`CONNECTION_FIELDS`), but on its own client: how hard it
thinks, how long it writes and what else its requests carry stay its own, and it is
tried with them as the chat model it follows is. The thresholds hold only for the model
they were set for, the judge model's for its family (`judge.judge_family`): they are
cleared when the judge model changes to another, and either is not in effect once `.env`
has the server start on another model.

As the server starts (`start`), every override of a service is tried again. One that
fails falls back to `.env`, kept but marked as failed, so a revoked key cannot keep the
server, or the admin console it is fixed in, from starting. Each item is reported in one
of three states: as `.env` has it, overridden, or overridden but not in effect, on
`.env`'s. What cannot be changed here at all is only shown (`READ_ONLY`).
"""

import asyncio
import base64
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from eidolon.activity import (
    ActivityLog,
    MeteredAffinityJudge,
    MeteredPromiseJudge,
    MeteredSearchJudge,
    MeteredAmbienceJudge,
    MeteredEmotionJudge,
    MeteredJudge,
    judge_origin,
    llm_origin,
    metered_llm,
)
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.config import (
    CLOUDFLARE_JUDGE_MODELS,
    DEFAULT_REACH_OUTS_PER_DAY,
    DEFAULT_BAILIAN_JUDGE_REGION,
    ChatModelSettings,
    DEFAULT_JEV_BASE_URL,
    FISH_AUDIO_MODELS,
    BailianJudgeSettings,
    CloudflareJudgeSettings,
    ImageSettings,
    JevSettings,
    JudgeSettings,
    EXEMPTING_SERVICES,
    LLMSettings,
    OWN_KEYS_PROXIED,
    PROXIED_SERVICES,
    SERVICE_NAMES,
    STATUS_WINDOWS,
    ProxiedService,
    ProxySettings,
    ReachOutSettings,
    Service,
    ServiceName,
    ServiceStatusSettings,
    Settings,
    SttKey,
    UsageLimits,
    checked_extra_body,
    checked_proxy_url,
    effort_problem,
    split_proxy_password,
    with_proxy_password,
)
from eidolon.database import connect
from eidolon.affinity import Judged
from eidolon.affinity_judge import ModelAffinityJudge
from eidolon.ambience import AMBIENCE_TIMEOUT, AmbiencePicking, ModelAmbienceJudge
from eidolon.emotion import EmotionJudging, ModelEmotionJudge
from eidolon.image_generation import (
    GENERATION_TIMEOUT,
    ImageFailed,
    ImageGenerator,
    try_image_service,
)
from eidolon.judge import (
    CLOUDFLARE_API_URL,
    JudgeError,
    JudgeModel,
    JudgeThresholds,
    judge_thresholds,
    same_judge_family,
    try_judge_model,
    warn_if_uncalibrated,
)
from eidolon.llm import ChatLLM, ChatModels, LLMError
from eidolon.llm.providers import check_chat_llm, check_chat_models
from eidolon.own_keys import DeployerKeys, DeployerLease, KeyCheck, OwnKeyRejected
from eidolon.proxy import ProxyRoutes, Unreachable, reach as reach_provider
from eidolon.rerank import DEFAULT_THRESHOLDS, RerankThresholds, thresholds_for
from eidolon.promise_judge import ModelPromiseJudge
from eidolon.search_judge import ModelSearchJudge
from eidolon.retrieval import ModelRetrievalJudge, Need
from eidolon.service_clients import Lease, ServiceClients, Swappable
from eidolon.store import Message

log = logging.getLogger(__name__)

Status = Literal["env", "overridden", "failed"]
"""As `.env` has it; overridden in the admin console; or overridden, but not in effect:
the override failed its try as the server started, or was set for another model."""

CHAT_LLM = "chat_llm"
MEMORY_LLM = "memory_llm"
TTS = "tts"
STT = "stt"
IMAGE = "image"
JUDGE = "judge"
JUDGE_THRESHOLDS = "judge_thresholds"
RERANK = "rerank"
LIMITS = "limits"
AFFINITY = "affinity"
REACH_OUTS = "reach_outs"
PROXY = "proxy"
SERVICE_STATUS = "service_status"

LLM_FIELDS = (
    "protocol", "base_url", "api_key", "model", "max_tokens", "reasoning_effort", "extra_body",
)

CALL_EFFORT = "call_reasoning_effort"
"""The chat model's alone, beside its fields: its reasoning effort in calls."""

CONNECTION_FIELDS = ("protocol", "base_url", "api_key", "model")
"""Where a model is and which: what the memory model has of the chat model it follows."""

FOLLOWING = "记忆模型跟随对话模型，用它自己的思考强度和额外请求参数不行："
"""Before why the memory model following the chat model would not work on it."""

FOR_MODEL = "for_model"
"""Kept beside thresholds: the model they were set for."""

TRIAL_MAX_TOKENS = 16
"""The most an LLM tried cheaply may write (`trial_llm`): the least the Responses API takes."""

MAX_REASON_LENGTH = 300
"""How much of the service's own error message the admin is shown."""

READ_ONLY: dict[str, str] = {
    "admins": "改错了会把自己锁在门外，被攻破的后台也能借它把别人加成管理者，只能在服务器上改。",
    "secret_key": "后台保存的 key 用它加密，封禁名单也靠它，换了就都解不开、认不出，只能在服务器上改。",
    "oauth": "登录靠它，改错了谁都进不了后台去修。",
    "domain": "改了以后登录回调、Cookie 和 HTTPS 证书都要跟着变，需要重新部署。",
    "webrtc": "通话的连接方式由服务器和防火墙一起决定，需要在服务器上改。",
    "backup": "备份去哪、用什么加密，关系到数据能不能找回，只能在服务器上改。",
    "paths": "数据库和角色配置的位置，进程启动时就定了。",
    "embedding": "换了向量化的服务或模型，所有向量都要重新计算（eidolon reembed），不能在运行中切换。",
}
"""What the admin console only shows, and why it cannot change it."""

_SCHEMA = """
-- One row per setting the admin console overrides, named `<item>.<field>`. `value` is
-- JSON, or for a key its Fernet token, of which `last4` is what is ever shown again.
CREATE TABLE IF NOT EXISTS config_overrides (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    last4 TEXT,
    updated_at TEXT NOT NULL
);
"""


class ConfigRejected(Exception):
    """Trying the change failed, saying why; nothing was saved."""


class ConfigInvalid(Exception):
    """The change is not one the item takes, saying why; nothing was saved."""


class KeysReadOnly(Exception):
    """The deployment has no `EIDOLON_SECRET_KEY` to encrypt keys with."""

    def __init__(self) -> None:
        super().__init__("这个部署没有设置 EIDOLON_SECRET_KEY，key 只能在 .env 里修改")


class Unavailable(Exception):
    """The item is not configured in `.env`, so there is nothing to change here."""


Values = dict[str, Any]


@dataclass(frozen=True)
class _Row:
    value: str
    last4: str | None


class _Unreadable(Exception):
    """An override that can no longer be read back."""


class _Item:
    """One item of the configuration: its fields, `.env`'s values for them, how a change
    is tried and how it is put into effect. `stored` fields are kept beside the others
    but never changed by the admin directly."""

    name: str
    fields: tuple[str, ...]
    secrets: frozenset[str] = frozenset()
    stored: tuple[str, ...] = ()
    unavailable_reason: str = ""

    def __init__(self, config: "LiveConfig") -> None:
        self.c = config

    def available(self) -> bool:
        return True

    def env(self) -> Values:
        raise NotImplementedError

    def normalized(self, change: Values) -> Values:
        """The change as the item's fields have it, before anything is checked of it."""
        return change

    def bound_model(self) -> str | None:
        """The model the item's values hold for, if they hold for one only."""
        return None

    def candidate(self, current: Values, change: Values) -> Values:
        """The values the change makes of those in effect; a key left out stays."""
        return current | change

    async def tried(self, values: Values) -> tuple[Values, Any]:
        """Try `values`, raising ConfigRejected saying why they do not work; the values
        as kept (with what the try found out) and whatever `apply` is to put in effect."""
        return values, None

    async def apply(self, values: Values, prepared: Any = None) -> None:
        """Put `values` in effect for whatever starts from now on."""

    async def retry(self, values: Values) -> Values:
        """Try the override in effect again as the server starts; raises ConfigRejected."""
        return values

    def shown(self, values: Values) -> Values:
        """`values` as the admin console shows them: of a key, its last 4 characters."""
        shown: Values = {}
        for field in self.fields + self.stored:
            if field == FOR_MODEL or field not in values:
                continue
            if field in self.secrets:
                key = values[field]
                shown[f"{field}_last4"] = key[-4:] if key else None
            else:
                shown[field] = values[field]
        return shown


class _LLMItem(_Item):
    """A model: its client, made from its settings, tried as `check` has it."""

    fields = LLM_FIELDS
    secrets = frozenset({"api_key"})
    efforts: tuple[str, ...] = ("reasoning_effort",)

    def swappable(self) -> Swappable[Any, Any]:
        raise NotImplementedError

    def settings_of(self, values: Values) -> Any:
        raise NotImplementedError

    def make(self, settings: Any) -> Any:
        raise NotImplementedError

    async def check(self, client: Any, settings: Any) -> None:
        """Raise LLMError saying why, unless `client`, made from `settings`, works."""
        raise NotImplementedError

    def candidate(self, current: Values, change: Values) -> Values:
        values = current | change
        for field in self.efforts:
            effort = values[field]
            if effort is not None and (problem := effort_problem(effort, values["protocol"])):
                raise ConfigInvalid(problem)
        if values["extra_body"] is not None:
            try:
                checked_extra_body(values["extra_body"])
            except ValueError as e:
                raise ConfigInvalid(str(e)) from None
        return values

    async def tried(self, values: Values) -> tuple[Values, Any]:
        settings = self.settings_of(values)
        client = self.make(settings)
        try:
            await self.check(client, settings)
        except LLMError as e:
            await client.aclose()
            raise ConfigRejected(str(e)[:MAX_REASON_LENGTH]) from None
        return values, client

    async def apply(self, values: Values, prepared: Any = None) -> None:
        settings = self.settings_of(values)
        await self.swappable().replace(prepared or self.make(settings), settings)

    async def retry(self, values: Values) -> Values:
        try:
            await self.check(self.swappable().current, self.settings_of(values))
        except LLMError as e:
            raise ConfigRejected(str(e)[:MAX_REASON_LENGTH]) from None
        return values


class _ChatLLM(_LLMItem):
    """The chat model, at one effort in text turns and another in calls."""

    name = CHAT_LLM
    fields = (*LLM_FIELDS, CALL_EFFORT)
    efforts = ("reasoning_effort", CALL_EFFORT)

    def env(self) -> Values:
        return _llm_values(self.c.settings.chat_llm) | {
            CALL_EFFORT: self.c.settings.call_reasoning_effort
        }

    def swappable(self) -> Swappable[ChatModels, ChatModelSettings]:
        return self.c._chat

    def settings_of(self, values: Values) -> ChatModelSettings:
        return _chat_model(values)

    def make(self, settings: ChatModelSettings) -> ChatModels:
        return self.c._clients.chat_models(settings)

    async def check(self, client: ChatModels, settings: ChatModelSettings) -> None:
        await check_chat_models(client, settings)

    def candidate(self, current: Values, change: Values) -> Values:
        values = super().candidate(current, change)
        memory = self.c._following(values)
        if memory is not None and memory.reasoning_effort is not None:
            if problem := effort_problem(memory.reasoning_effort, memory.protocol):
                raise ConfigInvalid(f"{FOLLOWING}{problem}")
        return values

    async def tried(self, values: Values) -> tuple[Values, Any]:
        """The chat model tried, and the memory model following it, if it is to change,
        at its own effort and extra body unless the chat model was tried with them."""
        values, chat = await super().tried(values)
        memory = self.c._following(values)
        if memory is None or memory == self.c._memory.settings:
            return values, (chat, None)
        settings = self.settings_of(values)
        if any(
            memory.reasoning_effort == s.reasoning_effort and memory.extra_body == s.extra_body
            for s in (settings.text, settings.call)
        ):
            return values, (chat, None)
        client = self.c._clients.memory_llm(memory)
        try:
            await check_chat_llm(client, memory.reasoning_effort)
        except LLMError as e:
            await client.aclose()
            await chat.aclose()
            raise ConfigRejected(f"{FOLLOWING}{e}"[:MAX_REASON_LENGTH]) from None
        return values, (chat, client)

    async def apply(self, values: Values, prepared: Any = None) -> None:
        chat, memory = prepared or (None, None)
        await super().apply(values, chat)
        await self.c._follow_chat(memory)


class _MemoryLLM(_LLMItem):
    name = MEMORY_LLM

    def env(self) -> Values:
        return _llm_values(self.c.settings.background_llm)

    def swappable(self) -> Swappable[ChatLLM, LLMSettings]:
        return self.c._memory

    def settings_of(self, values: Values) -> LLMSettings:
        return _llm(values)

    def make(self, settings: LLMSettings) -> ChatLLM:
        return self.c._clients.memory_llm(settings)

    async def check(self, client: ChatLLM, settings: LLMSettings) -> None:
        await check_chat_llm(client, settings.reasoning_effort)


class _TTS(_Item):
    name = TTS
    fields = ("api_key", "model")
    secrets = frozenset({"api_key"})

    def env(self) -> Values:
        s = self.c.settings
        return {"api_key": s.fish_audio_api_key, "model": s.fish_audio_model}

    async def tried(self, values: Values) -> tuple[Values, Any]:
        if not values["api_key"]:
            raise ConfigRejected("还没有语音合成的 key，请填写")
        if values["model"] not in FISH_AUDIO_MODELS:  # kept before there was a list
            raise ConfigRejected(f"语音合成的模型只能是 {'、'.join(FISH_AUDIO_MODELS)}，而不是 {values['model']}")
        await self.c._check_speech(
            lambda check: check.check_tts(values["api_key"], values["model"], own=False)
        )
        return values, None

    async def retry(self, values: Values) -> Values:
        return (await self.tried(values))[0]


STT_KEY_FIELDS = {"soniox": "soniox_api_key", "bailian": "dashscope_api_key"}


class _STT(_Item):
    name = STT
    fields = ("provider", "soniox_api_key", "dashscope_api_key")
    secrets = frozenset({"soniox_api_key", "dashscope_api_key"})
    stored = ("region",)

    def env(self) -> Values:
        s = self.c.settings
        return {
            "provider": s.stt_provider,
            "soniox_api_key": s.soniox_api_key,
            "dashscope_api_key": s.dashscope_api_key,
            "region": s.dashscope_region,
        }

    async def tried(self, values: Values) -> tuple[Values, Any]:
        provider = values["provider"]
        api_key = values[STT_KEY_FIELDS[provider]]
        if not api_key:
            raise ConfigRejected("还没有这家语音识别的 key，请填写")
        key = await self.c._check_speech(
            lambda check: check.check_stt(provider, api_key, own=False)
        )
        assert isinstance(key, SttKey)
        region = key.region if provider == "bailian" else values["region"]
        return values | {"region": region}, None

    async def retry(self, values: Values) -> Values:
        return (await self.tried(values))[0]


class _Image(_Item):
    """The deployer's image generation (GLOSSARY.md「生图」): where, in which format, with
    which key and model, how many at a time, in which art style; off without a key. A
    change is tried by generating one picture, billed for nothing and recorded nowhere,
    unless it changes nothing; it is not tried again as the server starts, each try being
    a picture paid for."""

    name = IMAGE
    fields = ("api_format", "base_url", "api_key", "model", "concurrency", "style")
    secrets = frozenset({"api_key"})

    def env(self) -> Values:
        image = self.c.settings.image
        values = asdict(image or ImageSettings(""))
        return values | {"api_key": image.api_key if image else None}

    def normalized(self, change: Values) -> Values:
        """An address without its trailing slash; an art style left out or empty is none."""
        change = change | {"style": (change.get("style") or "").strip()}
        if "base_url" in change:
            change["base_url"] = change["base_url"].rstrip("/")
        return change

    async def tried(self, values: Values) -> tuple[Values, Any]:
        if values == self.c.effective(self.name):
            return values, None
        if not values["api_key"]:
            raise ConfigRejected("还没有生图的 key，请填写")
        try:
            await try_image_service(ImageGenerator(_image(values), self.c._images))
        except ImageFailed as e:
            raise ConfigRejected(e.detail[:MAX_REASON_LENGTH]) from None
        return values, None


class _Judge(_Item):
    name = JUDGE
    fields = ("provider", "api_key", "model", "account_id", "workspace_id", "region", "emotion_timeout")
    secrets = frozenset({"api_key"})
    _per_service = ("api_key", "account_id", "workspace_id", "region")
    """What each service has of its own, and another cannot use."""
    unavailable_reason = (
        ".env 里没有配置判断模型，检索、记忆和通话中的情绪判断在启动时就关掉了；"
        "要启用，请在 .env 里配置后重启"
    )

    def available(self) -> bool:
        return self.c.settings.judge is not None

    def env(self) -> Values:
        judge = self.c.settings.judge
        values: Values = {field: None for field in self._per_service} | {
            "emotion_timeout": self.c.settings.emotion.timeout
        }
        if isinstance(judge, CloudflareJudgeSettings):
            return values | {"provider": "cloudflare", "api_key": judge.api_token,
                             "model": judge.model, "account_id": judge.account_id}
        if isinstance(judge, BailianJudgeSettings):
            return values | {"provider": "bailian", "api_key": judge.api_key, "model": judge.model,
                             "workspace_id": judge.workspace_id, "region": judge.region}
        assert judge is not None
        return values | {"provider": "jev", "api_key": judge.api_key, "model": judge.model}

    def candidate(self, current: Values, change: Values) -> Values:
        env = self.env()
        values = current | change
        provider = values["provider"]
        if provider != current["provider"]:
            # Another service's key, account and workspace cannot be the ones in effect.
            for field in self._per_service:
                if field not in change:
                    values[field] = env[field] if env["provider"] == provider else None
            if "api_key" not in change and values["api_key"] is None:
                raise ConfigInvalid("换了判断模型的服务，需要填写它的 key")
        if provider != "cloudflare":
            values["account_id"] = None
        if provider != "bailian":
            values["workspace_id"] = values["region"] = None
        if provider == "cloudflare":
            if not values["account_id"]:
                raise ConfigInvalid("Cloudflare 需要填写账号 ID（account_id）")
            if values["model"] not in CLOUDFLARE_JUDGE_MODELS:
                raise ConfigInvalid(
                    f"Cloudflare 的判断模型只能是 {'、'.join(CLOUDFLARE_JUDGE_MODELS)}"
                )
        if provider == "bailian":
            if not values["workspace_id"]:
                raise ConfigInvalid("阿里云百炼需要填写业务空间 ID（workspace_id）")
            values["region"] = values["region"] or DEFAULT_BAILIAN_JUDGE_REGION
        return values

    async def tried(self, values: Values) -> tuple[Values, Any]:
        model = self.c._clients.judge_model(self.c._judge_settings(values))
        try:
            await try_judge_model(model)
        except JudgeError as e:
            await model.aclose()
            raise ConfigRejected(str(e)[:MAX_REASON_LENGTH]) from None
        return values, model

    async def apply(self, values: Values, prepared: Any = None) -> None:
        if self.c._judge is not None:
            settings = self.c._judge_settings(values)
            await self.c._judge.replace(
                prepared or self.c._clients.judge_model(settings), settings
            )
        elif prepared is not None:
            await prepared.aclose()

    async def retry(self, values: Values) -> Values:
        if self.c._judge is not None:
            try:
                await try_judge_model(self.c._judge.current)
            except JudgeError as e:
                raise ConfigRejected(str(e)[:MAX_REASON_LENGTH]) from None
        return values


class _JudgeThresholds(_Item):
    name = JUDGE_THRESHOLDS
    fields = ("recall", "emotion", "out_of_line", "manipulation", "photo")
    stored = (FOR_MODEL,)
    unavailable_reason = _Judge.unavailable_reason

    def available(self) -> bool:
        return self.c.settings.judge is not None

    def env(self) -> Values:
        thresholds = judge_thresholds(self.c.judge_settings, self.c.settings.emotion)
        return {field: getattr(thresholds, field) for field in self.fields}

    def bound_model(self) -> str | None:
        return self.c.effective(JUDGE)["model"]


class _Rerank(_Item):
    name = RERANK
    fields = ("references", "memories")
    stored = (FOR_MODEL,)
    unavailable_reason = ".env 里没有配置重排（SILICONFLOW_API_KEY），检索和记忆在启动时就关掉了"

    def available(self) -> bool:
        return self.c.settings.rerank is not None

    def env(self) -> Values:
        rerank = self.c.settings.rerank
        assert rerank is not None
        return asdict(thresholds_for(rerank))

    def bound_model(self) -> str | None:
        rerank = self.c.settings.rerank
        return rerank.model if rerank else None


LIMIT_LABELS = {
    "turns_per_hour": "每小时回合数",
    "calls_per_hour": "每小时通话次数",
    "photos_per_hour": "每小时照片数",
    "call_minutes": "单次通话的时长上限（分钟）",
    "turns_per_call": "单次通话的回合上限",
    "history_turns": "带上的历史回合数",
}
"""The limits, as the admin console names them; each a whole number, 0 for no limit."""

EXEMPTING_FIELDS: dict[Service, str] = {s: f"exempting_{s}" for s in EXEMPTING_SERVICES}
"""Beside the limits: whether each service is one of the exempting keys (GLOSSARY.md
「免限额密钥」); true or false, never all false."""

EXEMPTING_LABELS: dict[str, str] = {
    EXEMPTING_FIELDS["llm"]: "免限额密钥的 LLM 开关",
    EXEMPTING_FIELDS["tts"]: "免限额密钥的语音合成开关",
    EXEMPTING_FIELDS["stt"]: "免限额密钥的语音识别开关",
}
"""The exempting keys' switches, as the admin console names them."""

NO_EXEMPTING_KEYS = "免限额密钥至少要选一项；想取消限额，请把每小时的回合数和通话次数设成不限"


class _Limits(_Item):
    """The limits per hour, a call's own limits and the turns each prompt carries, as
    `.env` takes them (0 for none), and the exempting keys; not tried."""

    name = LIMITS
    fields = (*LIMIT_LABELS, *EXEMPTING_LABELS)

    def env(self) -> Values:
        limits = self.c.settings.usage_limits
        exempting = self.c.settings.exempting_keys
        return {
            "turns_per_hour": limits.turns_per_hour or 0,
            "calls_per_hour": limits.calls_per_hour or 0,
            "photos_per_hour": limits.photos_per_hour or 0,
            "call_minutes": limits.call_minutes or 0,
            "turns_per_call": limits.turns_per_call or 0,
            "history_turns": self.c.settings.history_turns or 0,
        } | {field: s in exempting for s, field in EXEMPTING_FIELDS.items()}

    def candidate(self, current: Values, change: Values) -> Values:
        for field, value in change.items():
            if field in EXEMPTING_LABELS:
                if not isinstance(value, bool):
                    raise ConfigInvalid(
                        f"{EXEMPTING_LABELS[field]}需要是 true 或 false，而不是 {value!r}"
                    )
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigInvalid(
                    f"{LIMIT_LABELS[field]}需要是非负整数（0 表示不限），而不是 {value!r}"
                )
        values = current | change
        if not _any_exempting(values):
            raise ConfigInvalid(NO_EXEMPTING_KEYS)
        return values

    async def retry(self, values: Values) -> Values:
        # `.env` may since have turned off the exempting keys the override left on.
        if not _any_exempting(values):
            raise ConfigRejected(NO_EXEMPTING_KEYS)
        return values


def _any_exempting(values: Values) -> bool:
    return any(values[field] for field in EXEMPTING_FIELDS.values())


class _Affinity(_Item):
    """Whether affinity and gifts are on (GLOSSARY.md「好感度」), as `.env` has it (on by
    default); not tried. Only with the judge model, which `.env` alone sets up."""

    name = AFFINITY
    fields = ("enabled",)
    unavailable_reason = (
        ".env 里没有配置判断模型，好感度要靠它判断，在启动时就关掉了；"
        "要启用，请在 .env 里配置判断模型后重启"
    )

    def available(self) -> bool:
        return self.c.settings.judge is not None

    def env(self) -> Values:
        return {"enabled": self.c.settings.affinity_and_gifts}

    def candidate(self, current: Values, change: Values) -> Values:
        if not isinstance(change.get("enabled"), bool):
            raise ConfigInvalid(f"好感度和礼物需要是 true 或 false，而不是 {change.get('enabled')!r}")
        return current | change


class _ReachOuts(_Item):
    """Whether characters reach out to users (GLOSSARY.md「搭话」), as `.env` has it (off by
    default), and how many reach-outs each user may have a day, a whole number from 1 up; not
    tried."""

    name = REACH_OUTS
    fields = ("enabled", "per_day")

    def env(self) -> Values:
        return {"enabled": self.c.settings.reach_outs, "per_day": DEFAULT_REACH_OUTS_PER_DAY}

    def candidate(self, current: Values, change: Values) -> Values:
        if "enabled" in change and not isinstance(change["enabled"], bool):
            raise ConfigInvalid(f"搭话需要是 true 或 false，而不是 {change['enabled']!r}")
        per_day = change.get("per_day", current["per_day"])
        if isinstance(per_day, bool) or not isinstance(per_day, int) or per_day < 1:
            raise ConfigInvalid(f"每位用户每天最多几次搭话需要是正整数，而不是 {per_day!r}")
        return current | change


PROXY_LABELS: dict[ProxiedService, str] = {
    "chat_llm": "扮演角色的 LLM",
    "memory_llm": "后台模型",
    "judge": "判断模型",
    "embedding": "向量化",
    "rerank": "重排",
    "tts": "语音合成",
    "stt": "语音识别",
    "image": "生图",
    "web_search": "联网搜索",
    "own_llm": "自带的 LLM",
    "own_tts": "自带的语音合成",
    "own_stt": "自带的语音识别",
    "own_image": "自带的生图",
}
"""What may go through the proxy, as the admin console names it."""

PROVIDER_URLS = {
    "tts": "https://api.fish.audio",
    "soniox": "https://stt-rt.soniox.com",
    "beijing": "https://dashscope.aliyuncs.com",
    "singapore": "https://dashscope-intl.aliyuncs.com",
}
"""Where the speech services are reached, tried over HTTP before a proxy change is kept:
Fish Audio, Soniox, and Alibaba Cloud Model Studio in each region."""

Reach = Callable[[str, str | None], Awaitable[None]]
"""Raises Unreachable saying why, unless a URL's host answers through a proxy, or direct
for None (`proxy.reach`)."""


class _Proxy(_Item):
    """Which of the deployer's services and of users' own keys go through which proxy
    (GLOSSARY.md「网络代理」): the switch, the address without its password, the password,
    and a switch for each. A change is tried by reaching each of the deployer's services
    configured that it routes another way, users' own keys having none here to try; it
    holds from the next request on, of turns and calls under way too."""

    name = PROXY
    fields = ("enabled", "url", "password", *PROXIED_SERVICES)
    secrets = frozenset({"password"})

    def env(self) -> Values:
        proxy = self.c.settings.proxy
        url, password = split_proxy_password(proxy.url) if proxy.url else (None, None)
        return {"enabled": proxy.enabled, "url": url, "password": password} | {
            s: s in proxy.services for s in PROXIED_SERVICES
        }

    def normalized(self, change: Values) -> Values:
        """A password typed into the address is kept apart, as a key is."""
        if not change.get("url"):
            return change | {"url": None} if "url" in change else change
        try:
            url = checked_proxy_url(change["url"])
        except ValueError as e:
            raise ConfigInvalid(str(e)) from None
        url, password = split_proxy_password(url)
        change = change | {"url": url}
        return change | {"password": password} if password else change

    def candidate(self, current: Values, change: Values) -> Values:
        values = current | change
        if values["enabled"] and not values["url"]:
            raise ConfigInvalid("打开代理需要填写代理地址")
        return values

    async def tried(self, values: Values) -> tuple[Values, Any]:
        await self.c._reach_changed(self.c.proxy_settings, _proxy_settings(values))
        return values, None

    async def apply(self, values: Values, prepared: Any = None) -> None:
        self.c._routes.replace(_proxy_settings(values))

    async def retry(self, values: Values) -> Values:
        await self.c._reach_changed(self.c.settings.proxy, _proxy_settings(values))
        return values

    def shown(self, values: Values) -> Values:
        """A password is not shown with an address naming no user: it goes unused."""
        shown = super().shown(values)
        if "url" in values and not _names_user(values["url"]):
            shown["password_last4"] = None
        return shown


class _ServiceStatus(_Item):
    """Which services users are shown the status of (GLOSSARY.md「服务状态」), a switch for
    each, and how many of the latest minutes it goes by; not tried. A service the deployment
    does not have is not shown, switched on or not."""

    name = SERVICE_STATUS
    fields = ("window_minutes", *SERVICE_NAMES)

    def env(self) -> Values:
        status = self.c.settings.service_status
        return {"window_minutes": status.window_minutes} | {
            s: s in status.services for s in SERVICE_NAMES
        }

    def candidate(self, current: Values, change: Values) -> Values:
        for field, value in change.items():
            if field == "window_minutes":
                if isinstance(value, bool) or value not in STATUS_WINDOWS:
                    raise ConfigInvalid(
                        f"统计时段只能是 {'、'.join(map(str, STATUS_WINDOWS))} 分钟，而不是 {value!r}"
                    )
            elif not isinstance(value, bool):
                raise ConfigInvalid(f"显示与否需要是 true 或 false，而不是 {value!r}")
        return current | change


def _proxy_settings(values: Values) -> ProxySettings:
    url = values["url"]
    return ProxySettings(
        with_proxy_password(url, values["password"]) if url else None,
        values["enabled"],
        frozenset(s for s in PROXIED_SERVICES if values[s]),
    )


def _names_user(url: str | None) -> bool:
    return url is not None and "@" in urlsplit(url).netloc


class LiveConfig:
    """The configuration the backend runs on now, and the deployer's clients made from it."""

    def __init__(
        self,
        settings: Settings,
        clients: ServiceClients,
        clock: Clock,
        *,
        activity: ActivityLog,
        speech: KeyCheck | None = None,
        routes: ProxyRoutes | None = None,
        reach: Reach = reach_provider,
        image_client: httpx.AsyncClient | None = None,
    ) -> None:
        """The chat, memory and judge models are made by `clients`, each question to the
        judge model recorded in `activity`; `speech` tries the keys for speech synthesis
        and recognition. Each service goes through the proxy or direct as `routes` have
        it, kept to the proxy settings in effect; a change to them is tried by `reach`.
        Image generation is on `image_client`, by default the routes' for it."""
        self.settings = settings
        self._clients = clients
        self._routes = routes or ProxyRoutes(settings.proxy)
        self._images = image_client or self._routes.client("image", GENERATION_TIMEOUT)
        self._reach = reach
        self._clock = clock
        self._speech = speech
        self._activity = activity
        self._cipher = _cipher(settings.secret_key) if settings.secret_key else None
        self._db = connect(settings.database_path)
        self._db_lock = threading.Lock()
        self._changing = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        with self._db_lock, self._db:
            self._db.executescript(_SCHEMA)
            # Whether users had to bring their own LLM, which no longer is a setting.
            self._db.execute("DELETE FROM config_overrides WHERE name = 'limits.own_llm_required'")
            self._rows = {
                name: _Row(value, last4)
                for name, value, last4 in self._db.execute(
                    "SELECT name, value, last4 FROM config_overrides"
                )
            }
        self._items: dict[str, _Item] = {
            item.name: item
            for item in (
                # The proxy first: as the server starts, the services are tried through it.
                _Proxy(self), _ChatLLM(self), _MemoryLLM(self), _TTS(self), _STT(self),
                _Image(self), _Judge(self), _JudgeThresholds(self), _Rerank(self),
                _Limits(self), _Affinity(self), _ReachOuts(self), _ServiceStatus(self),
            )
        }
        self._failures: dict[str, str] = {}
        """Why an item's override, failing as the server started or unreadable, is not
        in effect."""
        self._overrides: dict[str, Values] = {}
        for item in self._items.values():
            if item.available() and self._overridden(item.name):
                try:
                    self._overrides[item.name] = self._read(item)
                except _Unreadable as e:
                    self._failures[item.name] = str(e)

        self._routes.replace(self.proxy_settings)
        chat = self.chat_model
        self._chat = Swappable(clients.chat_models(chat), chat)
        memory = self.memory_llm
        self._memory = Swappable(clients.memory_llm(memory), memory)
        self._judge: Swappable[JudgeModel, JudgeSettings] | None = None
        if (judge := self.judge_settings) is not None:
            self._judge = Swappable(clients.judge_model(judge), judge)
            warn_if_uncalibrated(judge.model)

    @property
    def keys_editable(self) -> bool:
        return self._cipher is not None

    @property
    def chat_model(self) -> ChatModelSettings:
        """The deployer's chat model in effect now."""
        return _chat_model(self.effective(CHAT_LLM))

    @property
    def memory_llm(self) -> LLMSettings:
        """The deployer's memory model in effect now."""
        return _llm(self.effective(MEMORY_LLM))

    @property
    def judge_settings(self) -> JudgeSettings | None:
        """The judge model in effect now; None without one."""
        if self.settings.judge is None:
            return None
        return self._judge_settings(self.effective(JUDGE))

    @property
    def judge_thresholds(self) -> JudgeThresholds:
        """What the judge model in effect now judges by."""
        return self.judge_thresholds_of(self.judge_settings)

    def judge_thresholds_of(self, judge: JudgeSettings | None) -> JudgeThresholds:
        """What the judge model `judge` judges by: its own thresholds, overridden by
        those set in the admin console if they hold for it."""
        thresholds = judge_thresholds(judge, self.settings.emotion)
        item = self._items[JUDGE_THRESHOLDS]
        if judge is None or not self._in_effect(item, judge.model):
            return thresholds
        override = self._overrides[JUDGE_THRESHOLDS]
        return replace(thresholds, **{k: v for k, v in override.items() if k in item.fields})

    @property
    def rerank_thresholds(self) -> RerankThresholds:
        """The reranker's thresholds in effect now."""
        if self.settings.rerank is None:
            return DEFAULT_THRESHOLDS
        return RerankThresholds(**{
            k: v for k, v in self.effective(RERANK).items() if k != FOR_MODEL
        })

    @property
    def usage_limits(self) -> UsageLimits:
        """The limits per hour and each call's own, in effect now; None for no limit."""
        values = self.effective(LIMITS)
        return UsageLimits(
            values["turns_per_hour"] or None,
            values["calls_per_hour"] or None,
            values["call_minutes"] or None,
            values["turns_per_call"] or None,
            values["photos_per_hour"] or None,
        )

    @property
    def exempting_keys(self) -> frozenset[Service]:
        """The exempting keys now (GLOSSARY.md「免限额密钥」), at least one."""
        values = self.effective(LIMITS)
        return frozenset(s for s, field in EXEMPTING_FIELDS.items() if values[field])

    @property
    def affinity_on(self) -> bool:
        """Whether affinity is on now: the deployment has the judge model, and the switch
        is on."""
        item = self._items[AFFINITY]
        return item.available() and self.effective(AFFINITY)["enabled"]

    @property
    def reach_outs(self) -> ReachOutSettings:
        """Whether characters reach out to users now, and how many times a day each user at
        most."""
        values = self.effective(REACH_OUTS)
        return ReachOutSettings(values["enabled"], values["per_day"])

    @property
    def proxy_settings(self) -> ProxySettings:
        """Which of the deployer's services go through which proxy now."""
        return _proxy_settings(self.effective(PROXY))

    @property
    def service_status(self) -> ServiceStatusSettings:
        """Which services users are shown the status of now, and over how long."""
        values = self.effective(SERVICE_STATUS)
        return ServiceStatusSettings(
            frozenset(s for s in SERVICE_NAMES if values[s]), values["window_minutes"]
        )

    def has_service(self, service: ServiceName) -> bool:
        """Whether the deployment has `service` on the deployer's keys now: the LLMs always;
        the others with their keys in effect, embedding and reranking as `.env` has them."""
        match service:
            case "chat_llm" | "memory_llm":
                return True
            case "tts":
                return bool(self.effective(TTS)["api_key"])
            case "stt":
                return self.stt_key is not None
            case "judge":
                return self._judge is not None
            case "embedding":
                return self.settings.embedding is not None
            case "rerank":
                return self.settings.rerank is not None
            case "image":
                return self.image_settings is not None
            case "web_search":
                # 搜索用能单独配的钥匙，不配就用聊天模型那把
                return self.settings.search_key is not None or bool(
                    self.settings.chat_llm.api_key
                )

    @asynccontextmanager
    async def trial_llm(self, service: Literal["chat_llm", "memory_llm"]) -> AsyncIterator[ChatLLM]:
        """The chat model in effect now, as text turns use it, or the memory model, on a
        client of its own that asks for no reasoning effort and at most `TRIAL_MAX_TOKENS`:
        to try it cheaply, its thinking cut short. What is asked of it is not recorded; it
        is closed on leaving."""
        def cheap(settings: LLMSettings) -> LLMSettings:
            return replace(settings, max_tokens=TRIAL_MAX_TOKENS, reasoning_effort=None)

        if service == "chat_llm":
            models = self._clients.chat_models(ChatModelSettings(cheap(self.chat_model.text)))
            try:
                yield models.text
            finally:
                await models.aclose()
            return
        memory = self._clients.memory_llm(cheap(self.memory_llm))
        try:
            yield memory
        finally:
            await memory.aclose()

    def lease_judge(self) -> Lease[JudgeModel, JudgeSettings] | None:
        """A hold on the judge model in effect now, to try it: what is asked of it is not
        recorded. None without one."""
        return None if self._judge is None else self._judge.lease()

    @property
    def image_settings(self) -> ImageSettings | None:
        """The deployer's image generation in effect now; None without its key."""
        values = self.effective(IMAGE)
        return _image(values) if values["api_key"] else None

    @property
    def image_style(self) -> str:
        """The deployment's art style in effect now, users' own image generation's too,
        though the deployer has no key; empty for none."""
        return self.effective(IMAGE)["style"]

    def image_generator(self) -> ImageGenerator | None:
        """The deployer's image generation in effect now, to generate on; None without it."""
        settings = self.image_settings
        return None if settings is None else ImageGenerator(settings, self._images)

    @property
    def history_turns(self) -> int | None:
        """How many of the latest turns each prompt carries now; None for all of them."""
        return self.effective(LIMITS)["history_turns"] or None

    def effective(self, name: str) -> Values:
        """The item's values in effect now: `.env`'s, with its overrides if they are."""
        item = self._items[name]
        if name == MEMORY_LLM:
            return self._memory_values()
        values = item.env()
        if self._in_effect(item):
            values |= self._overrides.get(name, {})
        return values

    def lease(self) -> DeployerLease:
        """The deployer's keys for a turn or call starting now, held until it releases
        them. Each model's requests are recorded as service calls to the model it is, and
        the judge model judges by its own thresholds, whatever replaces it meanwhile."""
        chat = self._chat.lease()
        memory = self._memory.lease()
        held: list[Lease[Any, Any]] = [chat, memory]
        emotions = None
        ambience = None
        if self._judge is not None:
            judge = self._judge.lease()
            held.append(judge)
            emotions = EmotionJudging(
                MeteredEmotionJudge(
                    ModelEmotionJudge(judge.client), self._activity, judge_origin(judge.settings)
                ),
                self.judge_thresholds_of(judge.settings).emotion,
                self.effective(JUDGE)["emotion_timeout"],
            )
            ambience = AmbiencePicking(
                MeteredAmbienceJudge(
                    ModelAmbienceJudge(judge.client), self._activity, judge_origin(judge.settings)
                ),
                AMBIENCE_TIMEOUT,
                self._clock,
            )
        tts = self.effective(TTS)
        keys = DeployerKeys(
            metered_llm(
                chat.client.text, self._activity, llm_origin("chat_llm", chat.settings.text)
            ),
            metered_llm(
                memory.client, self._activity, llm_origin("memory_llm", memory.settings)
            ),
            self.stt_key, tts["api_key"], tts["model"],
            emotions, hold_background=lambda: memory.again().release,
            call_llm=metered_llm(
                chat.client.call, self._activity, llm_origin("chat_llm", chat.settings.call)
            ),
            ambience=ambience,
        )
        return DeployerLease(keys, lambda: _release(held))

    def retrieval_judge(self) -> "LiveRetrievalJudge | None":
        """The judge model as retrieval asks it, the one in effect at each question, each
        recorded as a service call; None without one."""
        if self._judge is None:
            return None
        return LiveRetrievalJudge(self, self._judge, self._activity)

    def affinity_judge(self) -> "LiveAffinityJudge | None":
        """The judge model as a turn asks it about affinity or a photo apart, where retrieval
        is off: the one in effect at each question, each recorded as a service call; None
        without one."""
        if self._judge is None:
            return None
        return LiveAffinityJudge(self, self._judge, self._activity)

    def promise_judge(self) -> "LivePromiseJudge | None":
        """The judge model as a turn asks it whether what the producer said kept a promise she
        asked about: the one in effect at each question, recorded as a service call; None
        without one."""
        if self._judge is None:
            return None
        return LivePromiseJudge(self, self._judge, self._activity)

    def search_judge(self) -> "LiveSearchJudge | None":
        """The judge model as a turn asks it whether the producer asked her to look something
        up: the one in effect at that question, recorded as a service call; None without
        one."""
        if self._judge is None:
            return None
        return LiveSearchJudge(self, self._judge, self._activity)

    async def start(self) -> None:
        """Try every override as the server starts: one that fails falls back to `.env`,
        with a warning. Logs those in effect."""
        for item in self._items.values():
            if item.name in self._failures:  # a key that can no longer be decrypted
                log.warning(
                    "the admin console's override of %s cannot be read, so .env's is used"
                    " until an admin fixes or restores it: %s",
                    item.name, self._failures[item.name],
                )
                continue
            if item.name not in self._overrides:
                continue
            if not self._bound_holds(item):
                log.warning(
                    "the admin console's override of %s was set for model %s, not %s, so"
                    " .env's is used until an admin sets it again",
                    item.name, self._overrides[item.name].get(FOR_MODEL), item.bound_model(),
                )
                continue
            values = self.effective(item.name)
            try:
                kept = await item.retry(values)
            except ConfigRejected as e:
                self._failures[item.name] = str(e)
                await item.apply(self.effective(item.name))
                if item.name == JUDGE:
                    self._judge_changed(values, self.effective(item.name), clear=False)
                log.warning(
                    "the admin console's override of %s failed, so .env's is used until an"
                    " admin fixes or restores it: %s",
                    item.name, e,
                )
                continue
            if kept != values:  # what the try found out, as a key's region
                self._overrides[item.name] |= {k: kept[k] for k in item.stored}
        in_effect = [
            name for name in sorted(self._rows)
            if self._in_effect(self._items[name.split(".")[0]])
            and not name.endswith(f".{FOR_MODEL}")
        ]
        if in_effect:
            log.info(
                "config overrides in effect, set in the admin console over .env: %s",
                ", ".join(in_effect),
            )

    def describe(self) -> dict[str, Any]:
        """What the admin console shows: for each item, `.env`'s value, the one in effect,
        the override if any and its status. Of a key, only its last 4 characters."""
        described: dict[str, Any] = {"keys_editable": self.keys_editable}
        for name, item in self._items.items():
            if not item.available():
                described[name] = {"unavailable": item.unavailable_reason}
                continue
            override = self._overrides.get(name)
            failure = self._failures.get(name)
            if override is not None and failure is None and not self._bound_holds(item):
                failure = (
                    f"这项覆盖是为 {override.get(FOR_MODEL)} 设的，现在用的是"
                    f" {item.bound_model()}，所以没有生效"
                )
            status: Status = "failed" if failure else "env" if override is None else "overridden"
            entry = {
                "status": status,
                "env": item.shown(item.env()),
                "effective": item.shown(self.effective(name)),
                "override": item.shown(override) if override else None,
                "failure": failure,
            }
            if name == MEMORY_LLM:
                entry["follows_chat"] = self._memory_follows()
            if name == SERVICE_STATUS:
                entry["unconfigured"] = [s for s in SERVICE_NAMES if not self.has_service(s)]
            if name == PROXY:
                targets = self._proxy_targets()
                own = OWN_KEYS_PROXIED.values()
                entry["unconfigured"] = [
                    s for s in PROXIED_SERVICES if s not in targets and s not in own
                ]
            if item.bound_model() is not None:
                entry["model"] = item.bound_model()
            described[name] = entry
        described["read_only"] = self._read_only()
        return described

    async def save(self, name: str, change: Values) -> None:
        """Try the item `change` makes and, if it works, keep it and run every turn and
        call from now on on it. Raises KeysReadOnly for a key without
        `EIDOLON_SECRET_KEY`, ConfigInvalid or ConfigRejected saying why it was not kept,
        Unavailable for an item `.env` does not configure."""
        item = self._items[name]
        if not item.available():
            raise Unavailable(item.unavailable_reason)
        change = item.normalized(change)
        if self._cipher is None and any(f in item.secrets for f in change):
            raise KeysReadOnly()
        async with self._changing:
            current = self.effective(name)
            values = item.candidate(current, change)
            _check_ranges(item, values)
            values, prepared = await item.tried(values)
            if item.bound_model() is not None:
                values[FOR_MODEL] = item.bound_model()
            self._write(item, values, keep={f for f in item.secrets if f not in change})
            self._failures.pop(name, None)
            await item.apply(values, prepared)
            self._changed(name, current, values)

    async def restore(self, name: str) -> None:
        """Delete the item's overrides, so that `.env`'s holds from now on."""
        item = self._items[name]
        if not item.available():
            raise Unavailable(item.unavailable_reason)
        async with self._changing:
            before = self.effective(name)
            self._delete(name)
            after = self.effective(name)
            if after != before:
                await item.apply(after)
            self._changed(name, before, after)

    def on_change(self, listener: Callable[[], None]) -> None:
        """Have `listener` called after each item an admin saves or restores, whether or not
        it changed what is in effect."""
        self._listeners.append(listener)

    def _changed(self, name: str, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Item `name` went from `before` to `after`."""
        if name == JUDGE:
            self._judge_changed(before, after, clear=True)
        for listener in self._listeners:
            listener()

    def _judge_changed(self, before: Values, after: Values, *, clear: bool) -> None:
        """The judge model went from `before`'s to `after`'s: a model not calibrated is
        warned of, and with `clear`, thresholds that no longer hold for it go."""
        if after["model"] == before["model"]:
            return
        warn_if_uncalibrated(after["model"])
        if clear and not same_judge_family(before["model"], after["model"]):
            self._delete(JUDGE_THRESHOLDS)

    async def aclose(self) -> None:
        await self._chat.aclose()
        await self._memory.aclose()
        if self._judge is not None:
            await self._judge.aclose()
        with self._db_lock:
            self._db.close()

    def _judge_settings(self, values: Values) -> JudgeSettings:
        if values["provider"] == "cloudflare":
            return CloudflareJudgeSettings(values["api_key"], values["account_id"], values["model"])
        if values["provider"] == "bailian":
            return BailianJudgeSettings(
                values["api_key"], values["workspace_id"], values["region"], values["model"]
            )
        env = self.settings.judge
        base_url = env.base_url if isinstance(env, JevSettings) else DEFAULT_JEV_BASE_URL
        return JevSettings(values["api_key"], values["model"], base_url)

    def _memory_follows(self) -> bool:
        """Whether the memory model is the chat model in effect: `.env` has it nowhere
        else, and the admin console does not set it apart."""
        env = self.settings
        follows = all(
            getattr(env.background_llm, f) == getattr(env.chat_llm, f) for f in CONNECTION_FIELDS
        )
        return follows and not self._in_effect(self._items[MEMORY_LLM])

    def _memory_values(self) -> Values:
        item = self._items[MEMORY_LLM]
        values = item.env()
        if self._memory_follows():
            return _followed(values, self.effective(CHAT_LLM))
        if self._in_effect(item):
            values |= self._overrides[MEMORY_LLM]
        return values

    def _following(self, chat: Values) -> LLMSettings | None:
        """The memory model on the chat model `chat` describes, if it follows the chat
        model; else None."""
        if not self._memory_follows():
            return None
        return _llm(_followed(self._items[MEMORY_LLM].env(), chat))

    async def _follow_chat(self, prepared: ChatLLM | None = None) -> None:
        """Have the memory model, if it follows the chat model, on the one now in effect,
        unless it is already: on `prepared`, if it was made for it."""
        memory = self.memory_llm
        if memory != self._memory.settings:
            await self._memory.replace(prepared or self._clients.memory_llm(memory), memory)
        elif prepared is not None:
            await prepared.aclose()

    @property
    def stt_key(self) -> SttKey | None:
        """The deployer's key for speech recognition in effect now; None without one."""
        values = self.effective(STT)
        api_key = values[STT_KEY_FIELDS[values["provider"]]]
        if not api_key:
            return None
        region = values["region"] if values["provider"] == "bailian" else None
        return SttKey(values["provider"], api_key, region)

    async def _check_speech(self, check: Callable[[KeyCheck], Any]) -> Any:
        if self._speech is None:
            raise ConfigRejected("这个部署没有语音服务")
        try:
            return await check(self._speech)
        except OwnKeyRejected as e:
            raise ConfigRejected(str(e)[:MAX_REASON_LENGTH]) from None

    def _proxy_targets(self) -> dict[ProxiedService, str]:
        """Where each of the deployer's services that is configured reaches its provider;
        users' own keys, whatever their providers, are not among them."""
        targets: dict[ProxiedService, str] = {
            "chat_llm": self.chat_model.text.base_url,
            "memory_llm": self.memory_llm.base_url,
        }
        judge = self.judge_settings
        if isinstance(judge, CloudflareJudgeSettings):
            targets["judge"] = CLOUDFLARE_API_URL
        elif judge is not None:
            targets["judge"] = judge.base_url
        if self.settings.embedding is not None:
            targets["embedding"] = self.settings.embedding.base_url
        if self.settings.rerank is not None:
            targets["rerank"] = self.settings.rerank.base_url
        if self.effective(TTS)["api_key"]:
            targets["tts"] = PROVIDER_URLS["tts"]
        if (key := self.stt_key) is not None:
            where = (key.region or "beijing") if key.provider == "bailian" else "soniox"
            targets["stt"] = PROVIDER_URLS[where]
        if (image := self.image_settings) is not None:
            targets["image"] = image.base_url
        if self.has_service("web_search"):
            targets["web_search"] = self.settings.search_url
        return targets

    async def _reach_changed(self, before: ProxySettings, after: ProxySettings) -> None:
        """Reach every service configured that `after` routes otherwise than `before`, the
        way `after` has it, all at once; raises ConfigRejected saying which could not be
        reached, and why."""
        targets: dict[ProxiedService, str] = {
            s: url for s, url in self._proxy_targets().items()
            if after.proxy_for(s) != before.proxy_for(s)
        }
        results = await asyncio.gather(
            *(self._reach(url, after.proxy_for(s)) for s, url in targets.items()),
            return_exceptions=True,
        )
        failed: list[str] = []
        for (service, url), result in zip(targets.items(), results, strict=True):
            if isinstance(result, Unreachable):
                way = "经代理" if after.proxy_for(service) else "直连"
                host = urlsplit(url).hostname
                failed.append(f"{PROXY_LABELS[service]}（{way}）连 {host} 不通：{result}")
            elif isinstance(result, BaseException):
                raise result
        if failed:
            raise ConfigRejected("；".join(failed)[:MAX_REASON_LENGTH])

    def _in_effect(self, item: _Item, model: str | None = None) -> bool:
        return (
            item.name in self._overrides
            and item.name not in self._failures
            and self._bound_holds(item, model)
        )

    def _bound_holds(self, item: _Item, model: str | None = None) -> bool:
        """Whether the item's overrides were set for `model`, by default the model in
        effect, if they go with one."""
        bound = item.bound_model() if model is None else model
        if bound is None:
            return True
        set_for = self._overrides.get(item.name, {}).get(FOR_MODEL)
        if item.name == JUDGE_THRESHOLDS:
            return set_for is not None and same_judge_family(set_for, bound)
        return set_for == bound

    def _overridden(self, name: str) -> bool:
        return any(row.startswith(f"{name}.") for row in self._rows)

    def _read(self, item: _Item) -> Values:
        """The item's overrides; raises _Unreadable if a key can no longer be decrypted."""
        values: Values = {}
        for field in item.fields + item.stored:
            row = self._rows.get(f"{item.name}.{field}")
            if row is None:
                continue
            if field in item.secrets:
                if self._cipher is None:
                    raise _Unreadable("没有设置 EIDOLON_SECRET_KEY，后台保存的 key 无法解密")
                try:
                    values[field] = self._cipher.decrypt(row.value.encode()).decode()
                except InvalidToken:
                    raise _Unreadable(
                        "后台保存的 key 无法解密（EIDOLON_SECRET_KEY 改过），请重新填写"
                    ) from None
            else:
                values[field] = json.loads(row.value)
        return values

    def _write(self, item: _Item, values: Values, *, keep: set[str]) -> None:
        """Keep as the item's overrides every field of `values` that differs from
        `.env`'s; a key kept as it was (in `keep`) is not encrypted again."""
        env = item.env()
        now = self._clock().isoformat()
        rows: dict[str, _Row | None] = {}
        for field in item.fields + item.stored:
            name, value = f"{item.name}.{field}", values.get(field)
            if field == FOR_MODEL:
                continue
            if value == env.get(field):
                rows[name] = None
            elif field in item.secrets:
                if field in keep and name in self._rows:
                    continue
                if self._cipher is None:
                    raise KeysReadOnly()
                assert isinstance(value, str)
                rows[name] = _Row(self._cipher.encrypt(value.encode()).decode(), value[-4:])
            else:
                rows[name] = _Row(json.dumps(value), None)
        if FOR_MODEL in item.stored:
            kept = any(r is not None for r in rows.values())
            rows[f"{item.name}.{FOR_MODEL}"] = (
                _Row(json.dumps(values[FOR_MODEL]), None) if kept else None
            )
        with self._db_lock, self._db:
            for name, row in rows.items():
                if row is None:
                    self._db.execute("DELETE FROM config_overrides WHERE name = ?", (name,))
                else:
                    self._db.execute(
                        "INSERT INTO config_overrides (name, value, last4, updated_at)"
                        " VALUES (?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET"
                        " value = excluded.value, last4 = excluded.last4,"
                        " updated_at = excluded.updated_at",
                        (name, row.value, row.last4, now),
                    )
        for name, row in rows.items():
            if row is None:
                self._rows.pop(name, None)
            else:
                self._rows[name] = row
        if self._overridden(item.name):
            self._overrides[item.name] = self._read(item)
        else:
            self._overrides.pop(item.name, None)

    def _delete(self, name: str) -> None:
        with self._db_lock, self._db:
            self._db.execute("DELETE FROM config_overrides WHERE name LIKE ?", (f"{name}.%",))
        self._rows = {n: r for n, r in self._rows.items() if not n.startswith(f"{name}.")}
        self._overrides.pop(name, None)
        self._failures.pop(name, None)

    def _read_only(self) -> list[dict[str, Any]]:
        """What is only shown, with why: never a key, only whether it is set."""
        s = self.settings
        turn = s.turn.urls if s.turn else []
        ice = [u for server in s.ice_servers for u in _urls(server["urls"])]
        embedding = (
            f"{s.embedding.provider} · {s.embedding.model}" if s.embedding else None
        )
        values: dict[str, Any] = {
            "admins": sorted(f"{p}:{subject}" for p, subject in s.admins),
            "secret_key": s.secret_key is not None,
            "oauth": [name for name, o in (("github", s.github), ("google", s.google)) if o],
            "domain": s.domain,
            "webrtc": {"ice_servers": ice, "turn": turn},
            "backup": _backup_shown(),
            "paths": {"database": str(s.database_path), "characters": str(s.characters_path)},
            "embedding": embedding,
        }
        return [{"name": k, "value": values[k], "why": why} for k, why in READ_ONLY.items()]


class LiveRetrievalJudge:
    """The judge model as retrieval asks it: at each question, the one in effect then,
    held while it answers, by its own thresholds, recorded as the model it is."""

    def __init__(
        self,
        config: LiveConfig,
        judge: Swappable[JudgeModel, JudgeSettings],
        activity: ActivityLog,
    ) -> None:
        self._config = config
        self._judge = judge
        self._activity = activity

    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = False,
        photo: bool = False,
    ) -> tuple[Need, Judged | None, bool]:
        lease = self._judge.lease()
        try:
            judge = MeteredJudge(
                ModelRetrievalJudge(lease.client, self._config.judge_thresholds_of(lease.settings)),
                self._activity,
                judge_origin(lease.settings),
            )
            return await judge.judge(character, history, text, affinity, photo)
        finally:
            lease.release()


class LivePromiseJudge:
    """The judge model as a turn asks it whether a promise was kept: the one in effect then,
    held while it answers, recorded as the model it is."""

    def __init__(
        self,
        config: LiveConfig,
        judge: Swappable[JudgeModel, JudgeSettings],
        activity: ActivityLog,
    ) -> None:
        self._config = config
        self._judge = judge
        self._activity = activity

    async def kept(
        self, character: Character, history: Sequence[Message], text: str, promised: str
    ) -> bool:
        lease = self._judge.lease()
        try:
            judge = MeteredPromiseJudge(
                ModelPromiseJudge(lease.client),
                self._activity,
                judge_origin(lease.settings),
            )
            return await judge.kept(character, history, text, promised)
        finally:
            lease.release()


class LiveSearchJudge:
    """The judge model as a turn asks it whether he asked her to look something up: the one in
    effect then, held while it answers, recorded as the model it is."""

    def __init__(
        self,
        config: LiveConfig,
        judge: Swappable[JudgeModel, JudgeSettings],
        activity: ActivityLog,
    ) -> None:
        self._config = config
        self._judge = judge
        self._activity = activity

    async def asked(
        self, character: Character, history: Sequence[Message], text: str
    ) -> bool:
        lease = self._judge.lease()
        try:
            judge = MeteredSearchJudge(
                ModelSearchJudge(lease.client),
                self._activity,
                judge_origin(lease.settings),
            )
            return await judge.asked(character, history, text)
        finally:
            lease.release()


class LiveAffinityJudge:
    """The judge model as a turn asks it about affinity or a photo apart, where retrieval is
    off: at each question, the one in effect then, held while it answers, by its own
    thresholds, recorded as the model it is."""

    def __init__(
        self,
        config: LiveConfig,
        judge: Swappable[JudgeModel, JudgeSettings],
        activity: ActivityLog,
    ) -> None:
        self._config = config
        self._judge = judge
        self._activity = activity

    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = True,
        photo: bool = False,
    ) -> tuple[Judged | None, bool]:
        lease = self._judge.lease()
        try:
            judge = MeteredAffinityJudge(
                ModelAffinityJudge(lease.client, self._config.judge_thresholds_of(lease.settings)),
                self._activity,
                judge_origin(lease.settings),
            )
            return await judge.judge(character, history, text, affinity, photo)
        finally:
            lease.release()


def _check_ranges(item: _Item, values: Values) -> None:
    """Thresholds are probabilities and scores, from 0 to 1."""
    if isinstance(item, _JudgeThresholds | _Rerank):
        for field in item.fields:
            if not 0 <= values[field] <= 1:
                raise ConfigInvalid("阈值需要在 0 到 1 之间")


def _release(held: list[Lease[Any, Any]]) -> None:
    for lease in held:
        lease.release()


def _urls(urls: str | list[str]) -> list[str]:
    return [urls] if isinstance(urls, str) else list(urls)


def _backup_shown() -> str | None:
    """Which bucket backups go to, if they are configured (`backup`); never its keys."""
    return (os.environ.get("EIDOLON_BACKUP_S3_BUCKET") or "").strip() or None


def _llm_values(settings: LLMSettings) -> Values:
    return {
        "protocol": settings.protocol,
        "base_url": settings.base_url,
        "api_key": settings.api_key,
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "reasoning_effort": settings.reasoning_effort,
        "extra_body": settings.extra_body,
    }


def _llm(values: Values) -> LLMSettings:
    return LLMSettings(
        values["base_url"], values["api_key"], values["model"], values["protocol"],
        values["max_tokens"], values["reasoning_effort"], values["extra_body"],
    )


def _image(values: Values) -> ImageSettings:
    return ImageSettings(**{field: values[field] for field in _Image.fields})


def _chat_model(values: Values) -> ChatModelSettings:
    return ChatModelSettings(_llm(values), values[CALL_EFFORT])


def _followed(memory: Values, chat: Values) -> Values:
    """The memory model's values `memory`, following the chat model's, `chat`."""
    return memory | {field: chat[field] for field in CONNECTION_FIELDS}


def _cipher(secret_key: str) -> Fernet:
    """The cipher for the deployer's keys kept as overrides, derived from the deployment's
    secret key apart from the one for the ban list."""
    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"eidolon config overrides"
    ).derive(secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))
