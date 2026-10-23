"""Settings, read from environment variables."""

import base64
import hmac
import json
import logging
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Required, TypedDict, get_args
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from eidolon.outfit_tags import VisionSettings

log = logging.getLogger(__name__)


class IceServer(TypedDict, total=False):
    """One entry of WebRTC's `iceServers`; TURN servers also need a username and credential."""

    urls: Required[str | list[str]]
    username: str
    credential: str


FishAudioModel = Literal["s2.1-pro-free", "s2.1-pro", "drama-3", "s2-pro", "s1"]
"""The Fish Audio models speech is synthesized with, the deployer's and users' own: each
named as its `model` header has it, which Fish Audio does not check, voicing any other
name with its paid S2.1 Pro. S1 is there for deployments still set to it, until Fish
Audio retires it."""
FISH_AUDIO_MODELS: tuple[FishAudioModel, ...] = get_args(FishAudioModel)

DEFAULT_FISH_AUDIO_MODEL: FishAudioModel = "s2.1-pro-free"
"""Fish Audio's free-tier model: it draws on no API credit, unlike the paid ones."""
FREE_FISH_AUDIO_MODELS: frozenset[FishAudioModel] = frozenset({DEFAULT_FISH_AUDIO_MODEL})
VoiceEngine = Literal["fish", "gpt_sovits"]
"""What speaks as the characters: Fish Audio, or the deployer's own GPT-SoVITS server
(GLOSSARY.md「本地语音」, `voices`)."""
VOICE_ENGINES: tuple[VoiceEngine, ...] = get_args(VoiceEngine)
SAME_AS_CALLS = "same"
"""The read-aloud model (GLOSSARY.md「朗读」) that is whatever calls are voiced with."""
ReadModel = FishAudioModel | Literal["same"]
READ_MODELS: tuple[str, ...] = (SAME_AS_CALLS, *FISH_AUDIO_MODELS)
ReadEngine = VoiceEngine | Literal["same"]
READ_ENGINES: tuple[str, ...] = (SAME_AS_CALLS, *VOICE_ENGINES)
CHARACTER_BILLED_FISH_AUDIO_MODELS: frozenset[FishAudioModel] = frozenset({"drama-3"})
"""Billed for the characters of text spoken; the others for its UTF-8 bytes."""

SttProvider = Literal["soniox", "bailian"]
"""The speech recognition services: Soniox, or Alibaba Cloud Model Studio (阿里云百炼)."""

STT_PROVIDERS: tuple[SttProvider, ...] = get_args(SttProvider)

Service = Literal["llm", "tts", "stt", "image"]
"""A service users may bring their own key for (GLOSSARY.md「自带密钥」)."""
SERVICES: tuple[Service, ...] = get_args(Service)
EXEMPTING_SERVICES: tuple[Service, ...] = ("llm", "tts", "stt")
"""The services that may be exempting keys (GLOSSARY.md「免限额密钥」): never image
generation, whose photos count by whose they are on alone."""

ProxiedService = Literal[
    "chat_llm", "memory_llm", "judge", "embedding", "rerank", "tts", "stt", "image",
    "web_search",
    "own_llm", "own_tts", "own_stt", "own_image",
]
"""What may reach its provider through the proxy (GLOSSARY.md「网络代理」): the deployer's
services, the chat model, in text turns and calls alike; the memory model; the judge
model, whichever; the embedding service and the reranker; Fish Audio; the speech
recognition chosen; image generation, wherever it is. Then users' own keys, apart from the
deployer's, their providers being their own: their LLMs, background models too, though only
to a known provider's host (GLOSSARY.md「已知服务商」, ADR-0022); Fish Audio; the speech
recognition they chose; image generation, though only to NovelAI's own address."""
PROXIED_SERVICES: tuple[ProxiedService, ...] = get_args(ProxiedService)
OWN_KEYS_PROXIED: dict[Service, ProxiedService] = {
    "llm": "own_llm", "tts": "own_tts", "stt": "own_stt", "image": "own_image",
}
"""What users' own key for each service goes through the proxy as."""

PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")

ServiceName = Literal[
    "chat_llm", "memory_llm", "tts", "stt", "judge", "embedding", "rerank", "image",
    "web_search",
]
"""The outside services: the LLM playing the characters and the one writing down memories,
speech synthesis and recognition, the judge model, embedding, reranking and image
generation."""
SERVICE_NAMES: tuple[ServiceName, ...] = get_args(ServiceName)

STATUS_WINDOWS = (10, 30, 60)
"""How many of the latest minutes the service status may go by (GLOSSARY.md「服务状态」)."""
DEFAULT_STATUS_WINDOW = 30


@dataclass(frozen=True)
class ServiceStatusSettings:
    """Which services users are shown the status of (GLOSSARY.md「服务状态」), of those the
    deployment has, and how many of the latest minutes it goes by: every one, over half an
    hour, unless `.env` says otherwise; the admin console may override it."""

    services: frozenset[ServiceName] = frozenset(SERVICE_NAMES)
    window_minutes: int = DEFAULT_STATUS_WINDOW


DEFAULT_REACH_OUTS_PER_DAY = 3
"""How many reach-outs each user may have a day, of all characters together, unless the admin
console says otherwise."""


@dataclass(frozen=True)
class ReachOutSettings:
    """Whether characters reach out to users (GLOSSARY.md「搭话」), off unless `.env` turns it
    on, and how many reach-outs each user may have a day; the admin console may override
    both."""

    enabled: bool
    per_day: int


@dataclass(frozen=True)
class ProxySettings:
    """The proxy what is in `services` reaches its providers through, if it is
    `enabled`; everything else goes direct (GLOSSARY.md「网络代理」). `url` carries its
    password, if any."""

    url: str | None = None
    enabled: bool = False
    services: frozenset[ProxiedService] = frozenset()

    def proxy_for(self, service: ProxiedService) -> str | None:
        """Where `service` connects through; None for direct."""
        return self.url if self.enabled and self.url and service in self.services else None


def checked_proxy_url(url: str) -> str:
    """`url` as a proxy's address: one of `PROXY_SCHEMES`, a host and a port, perhaps a
    user and password, nothing else. Raises ValueError saying what is wrong."""
    url = url.strip()
    parts = urlsplit(url)
    example = "比如 http://127.0.0.1:7890 或 socks5://127.0.0.1:7891"
    if parts.scheme not in PROXY_SCHEMES:
        raise ValueError(f"代理地址要以 {'、'.join(s + '://' for s in PROXY_SCHEMES)} 开头，{example}")
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"代理地址的端口不对，{example}") from None
    if not parts.hostname or port is None:
        raise ValueError(f"代理地址要写明主机和端口，{example}")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(f"代理地址只写协议、主机和端口（可以带用户名和密码），不带路径，{example}")
    return urlunsplit(parts._replace(path=""))


def split_proxy_password(url: str) -> tuple[str, str | None]:
    """`url` without its password, and the password; None if it has none."""
    parts = urlsplit(url)
    userinfo, at, host = parts.netloc.rpartition("@")
    user, colon, password = userinfo.partition(":")
    if not at or not colon:
        return url, None
    return urlunsplit(parts._replace(netloc=f"{user}@{host}")), password or None


def with_proxy_password(url: str, password: str | None) -> str:
    """`url`, without a password, with `password`: only if it names a user to go with."""
    parts = urlsplit(url)
    user, at, host = parts.netloc.rpartition("@")
    if not password or not at:
        return url
    return urlunsplit(parts._replace(netloc=f"{user}:{password}@{host}"))


BailianRegion = Literal["beijing", "singapore"]
"""Where a DashScope key works: each region's keys are refused in the other."""

DEFAULT_BAILIAN_STT_MODEL = "qwen-audio-3.1-asr-flash-streaming"
BAILIAN_STT_MODELS = (DEFAULT_BAILIAN_STT_MODEL,)
"""The models Alibaba Cloud Model Studio may recognize speech with: those that take turns
as the call needs (ticket bailian-stt 01). The 3.0 model and fun-asr-realtime speak the
same protocol, but without `vad_model` a cough comes out as an empty sentence, or as 「咳咳。」."""


@dataclass(frozen=True)
class SttKey:
    """A key for speech recognition, and the service it is for; a DashScope key's region,
    if known (else it is found on each call, Beijing first)."""

    provider: SttProvider
    api_key: str
    region: BailianRegion | None = None


DEFAULT_SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_RERANK_MODEL = "Qwen/Qwen3-Reranker-0.6B"
"""On the calibration set it ranks story and character facts better than 4B does."""

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_EMBEDDING_DIMENSIONS = 1536
EMBEDDING_DIMENSIONS_RANGE = (128, 3072)

DEFAULT_JEV_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_JEV_MODEL = "typesafe/jev-1.13"
"""Pinned: a new Jev changes which turns retrieve, so moving to one is a choice, checked
in a real conversation."""

JudgeProvider = Literal["jev", "cloudflare", "bailian"]
"""The services the judge model may be: Jev through a System One endpoint, Cloudflare's
Clef on Workers AI, or Alibaba Cloud Model Studio's decision model."""

JUDGE_PROVIDERS: tuple[JudgeProvider, ...] = get_args(JudgeProvider)

CloudflareJudgeModel = Literal["clef", "clef-flash"]
CLOUDFLARE_JUDGE_MODELS: tuple[CloudflareJudgeModel, ...] = get_args(CloudflareJudgeModel)
DEFAULT_CLOUDFLARE_JUDGE_MODEL: CloudflareJudgeModel = "clef-flash"
"""For now: the judge eval (ticket cloudflare-judge 03) decides."""

DEFAULT_BAILIAN_JUDGE_MODEL = "decision-model-preview"
DEFAULT_BAILIAN_JUDGE_REGION: BailianRegion = "beijing"
BAILIAN_REGION_CODES: dict[BailianRegion, str] = {"beijing": "cn-beijing", "singapore": "ap-southeast-1"}
"""Alibaba Cloud's names for the regions the decision model is served in."""

DEFAULT_NOVELAI_BASE_URL = "https://image.novelai.net"
DEFAULT_NOVELAI_MODEL = "nai-diffusion-5-curated"

ImageFormat = Literal["novelai", "openai"]
"""How an image service is spoken to (`image_generation`): in NovelAI's own format, as
NovelAI and the relays passing requests on as they are take it, or in OpenAI's image
format, as other relays do. Chosen by whoever configures it, never found out."""
IMAGE_FORMATS: tuple[ImageFormat, ...] = get_args(ImageFormat)
MAX_IMAGE_CONCURRENCY = 10
"""The most photos the deployer's image key may be set to generate at a time."""

DEFAULT_ICE_SERVERS: list[IceServer] = [{"urls": ["stun:stun.l.google.com:19302"]}]

DEFAULT_TURNS_PER_HOUR = 60
DEFAULT_CALLS_PER_HOUR = 5
"""The reference project's limit on voice connections."""
DEFAULT_CALL_MINUTES = 10
DEFAULT_TURNS_PER_CALL = 40
"""About what ten minutes of talking takes; a cap on typing turns in quickly during a call."""
DEFAULT_PHOTOS_PER_HOUR = 5
DEFAULT_CONCURRENT_CALLS = 10
"""What one process on the deployment's two cores carries, every call's audio on the one
event loop."""
DEFAULT_HISTORY_TURNS = 20
"""How many of a conversation's latest turns the chat model reads; memories carry the rest."""


LLMProtocol = Literal["chat-completions", "responses", "anthropic"]
"""OpenAI-compatible Chat Completions, OpenAI Responses, or Anthropic Messages."""

LLM_PROTOCOLS: tuple[LLMProtocol, ...] = get_args(LLMProtocol)

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
"""How hard the model thinks before it replies (GLOSSARY.md「思考强度」), `none` for not at
all; sent as each protocol writes it, whatever the model makes of it (ADR-0019)."""

REASONING_EFFORTS: tuple[ReasoningEffort, ...] = get_args(ReasoningEffort)

EFFORT_NAMES: dict[ReasoningEffort, str] = {
    "none": "关闭",
    "minimal": "极低",
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "极高",
    "max": "最高",
}

NOT_ON_ANTHROPIC: frozenset[ReasoningEffort] = frozenset({"minimal"})
"""The efforts the Anthropic protocol has no way to write."""

EXTRA_BODY_REFUSED = (
    "model", "stream", "stream_options", "messages", "input", "instructions", "system",
    "tools", "tool_choice",
)
"""What an extra request body may not set: the adapters write these to make the request."""


NOT_AN_ADDRESS = "接口地址不是一个网址，应该像 https://api.example.com/v1 这样"


def checked_base_url(value: str) -> str:
    """`value`, an LLM's address, without surrounding spaces; raises ValueError
    (`NOT_AN_ADDRESS`) unless it is a web address: not a link pasted with its brackets
    (`[https://…](…)`), nor a host alone."""
    url = value.strip()
    try:
        parts = urlsplit(url)
        named = parts.scheme in ("http", "https") and parts.hostname is not None
        parts.port  # noqa: B018 - raises ValueError for one that is not a number
    except ValueError:
        named = False
    if not named:
        raise ValueError(NOT_AN_ADDRESS)
    return url


def checked_extra_body(value: object) -> dict[str, Any]:
    """`value` as an extra request body, merged into each request last; raises ValueError
    saying why it is not one: it must be a JSON object, and not set what makes the
    request (`EXTRA_BODY_REFUSED`). What it sets of thinking is up to the deployer."""
    if not isinstance(value, dict):
        raise ValueError("额外请求参数需要是一个 JSON 对象，例如 {\"enable_thinking\": false}")
    if refused := [key for key in EXTRA_BODY_REFUSED if key in value]:
        keys = "、".join(f"「{key}」" for key in refused)
        raise ValueError(f"额外请求参数不能包含 {keys}：这些由服务器生成，改了会弄坏请求")
    return value


def effort_problem(effort: object, protocol: str) -> str | None:
    """Why `effort` is not one a model on `protocol` may be set to, if it is not."""
    if effort not in REASONING_EFFORTS:
        return f"思考强度只能是 {'、'.join(REASONING_EFFORTS)} 之一，而不是 {effort}"
    if protocol == "anthropic" and effort in NOT_ON_ANTHROPIC:
        return f"Anthropic 接口没有「{EFFORT_NAMES[effort]}」（{effort}）这一档"
    return None


@dataclass(frozen=True)
class LLMSettings:
    """One model slot: the protocol its service speaks, where it is, and which model."""

    base_url: str
    api_key: str
    model: str
    protocol: LLMProtocol = "chat-completions"
    max_tokens: int | None = None
    """A cap on each reply's length; the Anthropic protocol needs one and has a default."""
    reasoning_effort: ReasoningEffort | None = None
    """None to send none, leaving it to the model."""
    extra_body: dict[str, Any] | None = None
    """Merged into every request last (see `checked_extra_body`)."""

    @classmethod
    def chat_from_env(cls) -> "LLMSettings":
        return cls._from_env("CHAT")

    @classmethod
    def background_from_env(cls) -> "LLMSettings":
        """The background model; each setting left unset is the chat model's."""
        return cls._from_env("BACKGROUND", fallback="CHAT")

    @classmethod
    def _from_env(cls, slot: str, fallback: str | None = None) -> "LLMSettings":
        def value(name: str) -> str | None:
            names = [f"{slot}_LLM_{name}"] + ([f"{fallback}_LLM_{name}"] if fallback else [])
            return next((v for n in names if (v := os.environ.get(n))), None)

        def required(name: str) -> str:
            if found := value(name):
                return found
            alternative = f"（或 {fallback}_LLM_{name}）" if fallback else ""
            raise RuntimeError(f"环境变量 {slot}_LLM_{name}{alternative}未设置")

        protocol = value("PROTOCOL") or "chat-completions"
        if protocol not in LLM_PROTOCOLS:
            raise RuntimeError(
                f"环境变量 {slot}_LLM_PROTOCOL 只能是 {'、'.join(LLM_PROTOCOLS)}，而不是 {protocol}"
            )
        # Each slot's own: a cap that suits the chat could cut a drafted sheet short.
        max_tokens = os.environ.get(f"{slot}_LLM_MAX_TOKENS") or None
        if max_tokens is not None and not (
            max_tokens.isascii() and max_tokens.isdigit() and int(max_tokens) > 0
        ):
            raise RuntimeError(f"环境变量 {slot}_LLM_MAX_TOKENS 需要是正整数，而不是 {max_tokens}")
        # So are the effort and the extra body: the chat's may not suit drafting a sheet.
        effort = effort_from_env(f"{slot}_LLM_REASONING_EFFORT", protocol)
        extra_body = None
        if text := os.environ.get(f"{slot}_LLM_EXTRA_BODY", "").strip():
            try:
                extra_body = checked_extra_body(json.loads(text))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"环境变量 {slot}_LLM_EXTRA_BODY 不是合法的 JSON：{e}") from None
            except ValueError as e:
                raise RuntimeError(f"环境变量 {slot}_LLM_EXTRA_BODY 有误：{e}") from None
        return cls(
            base_url=required("BASE_URL"),
            api_key=required("API_KEY"),
            model=required("MODEL"),
            protocol=protocol,  # pyright: ignore[reportArgumentType]  # checked above
            max_tokens=int(max_tokens) if max_tokens else None,
            reasoning_effort=effort,
            extra_body=extra_body,
        )


def effort_from_env(name: str, protocol: str) -> ReasoningEffort | None:
    """The reasoning effort `.env` sets in `name` for a model on `protocol`; None if it sets
    none. Raises RuntimeError if it is not one such a model may be set to."""
    effort = os.environ.get(name, "").strip() or None
    if effort is not None and (problem := effort_problem(effort, protocol)):
        raise RuntimeError(f"环境变量 {name} 有误：{problem}")
    return effort  # pyright: ignore[reportReturnType]  # checked above


@dataclass(frozen=True)
class ChatModelSettings:
    """The chat model: text turns on `text`, calls' turns on the same model at an effort of
    their own, `call_effort`; None to send none in calls, whatever `text`'s is."""

    text: LLMSettings
    call_effort: ReasoningEffort | None = None

    @property
    def call(self) -> LLMSettings:
        """The model as calls' turns use it."""
        return replace(self.text, reasoning_effort=self.call_effort)


@dataclass(frozen=True)
class Settings:
    database_path: Path
    characters_path: Path
    chat_llm: LLMSettings
    background_llm: LLMSettings = None  # pyright: ignore[reportAssignmentType]  # see below
    """Left out, the background model is the chat model."""
    call_reasoning_effort: ReasoningEffort | None = None
    """The chat model's in calls (`CHAT_LLM_CALL_REASONING_EFFORT`): see `chat_model`."""
    ice_servers: list[IceServer] = field(default_factory=lambda: list(DEFAULT_ICE_SERVERS))
    turn: "TurnSettings | None" = None
    """A TURN server the browser gets short-lived credentials for, besides `ice_servers`."""
    stt_provider: SttProvider = "soniox"
    """Which service hears users who brought no key of their own for it (ADR-0010)."""
    soniox_api_key: str | None = None
    dashscope_api_key: str | None = None
    """Alibaba Cloud Model Studio's (阿里云百炼), for speech recognition."""
    dashscope_region: BailianRegion | None = None
    """Where `dashscope_api_key` works: found by trying it as the server starts."""
    bailian_stt_model: str = DEFAULT_BAILIAN_STT_MODEL
    """What Alibaba Cloud Model Studio recognizes speech with, whoever's key it is on."""
    fish_audio_api_key: str | None = None
    fish_audio_model: FishAudioModel = DEFAULT_FISH_AUDIO_MODEL
    fish_audio_read_model: ReadModel = SAME_AS_CALLS
    """What her messages are read aloud with (GLOSSARY.md「朗读」); `SAME_AS_CALLS` for the
    model calls are voiced with, whatever it is changed to."""
    voice_engine: VoiceEngine = "fish"
    """What speaks as her in calls (GLOSSARY.md「本地语音」)."""
    read_engine: ReadEngine = SAME_AS_CALLS
    """What reads her messages aloud; `SAME_AS_CALLS` for what speaks in calls."""
    gpt_sovits_url: str | None = None
    """The deployer's own GPT-SoVITS server (`api_v2.py`), if any."""
    local_voices_path: Path = Path("../local-voices/voices.toml")
    """The characters' voices on it (`voices.load_local_voices`)."""
    embedding: "EmbeddingSettings | None" = None
    rerank: "RerankSettings | None" = None
    image: "ImageSettings | None" = None
    """The deployer's image generation (GLOSSARY.md「生图」), for photos; None without it."""
    judge: "JudgeSettings | None" = None
    """The judge model (`JUDGE_PROVIDER`). Retrieval needs the embedding service, the
    reranker and the judge model; without any of them, turns go unretrieved. Without the
    judge model, neither is any emotion judged during calls."""
    emotion: "EmotionSettings" = field(default_factory=lambda: EmotionSettings())
    usage_limits: "UsageLimits" = field(default_factory=lambda: UsageLimits())
    history_turns: int | None = DEFAULT_HISTORY_TURNS
    """How many of the conversation's latest turns each prompt carries; None is all of them."""
    weather: "WeatherSettings | None" = None
    """Where the producer is, for the weather said in every turn (GLOSSARY.md「天气」); None
    unless `.env` gives both `EIDOLON_WEATHER_LAT` and `EIDOLON_WEATHER_LON`."""
    exempting_keys: frozenset[Service] = frozenset(EXEMPTING_SERVICES)
    """The services a call must bring the user's own keys for, all of them, to count
    towards no limit (GLOSSARY.md「免限额密钥」), at least one; all three unless `.env`
    turns some off, and the admin console may override it. Text turns go by the LLM alone."""
    affinity_and_gifts: bool = True
    """Whether affinity and gifts are on (GLOSSARY.md「好感度」), as they are only with the
    judge model; on unless `.env` turns them off, and the admin console may override it."""
    letters: bool = False
    """Whether characters write users letters (GLOSSARY.md「来信」), on the deployer's chat
    model; off unless `.env` turns it on (`EIDOLON_LETTERS`)."""
    schedules: bool = False
    """Whether characters have a schedule written for each day (GLOSSARY.md「行程」), on the
    deployer's chat model; off unless `.env` turns it on (`EIDOLON_SCHEDULES`)."""
    keepsakes: bool = False
    """Whether characters give users keepsakes (GLOSSARY.md「纪念品」), chosen on the deployer's
    chat model and drawn on its image generation, if any; off unless `.env` turns it on
    (`EIDOLON_KEEPSAKES`)."""
    diary: bool = False
    """Whether characters keep a diary of the days that mattered (GLOSSARY.md「日记」), on the
    deployer's chat model; off unless `.env` turns it on (`EIDOLON_DIARY`)."""
    reach_outs: bool = False
    """Whether characters reach out to users (GLOSSARY.md「搭话」), on the deployer's chat
    model; off unless `.env` turns it on, and the admin console may override it."""
    proxy: ProxySettings = field(default_factory=ProxySettings)
    """Which of the deployer's services go through which proxy; none unless `.env` sets
    one, and the admin console may override it."""
    search_key: str | None = None
    """The key the web search is asked with (`EIDOLON_SEARCH_KEY`), or None for the chat
    model's: search is a service of its own, so that changing the chat model does not end it."""
    search_url: str = "https://api.deepseek.com/v1/tools/search"
    """Where the web search is asked (`EIDOLON_SEARCH_URL`)."""
    service_status: ServiceStatusSettings = field(default_factory=ServiceStatusSettings)
    domain: str | None = None
    """The public deployment's domain (behind Caddy); None in development."""
    dev_login: bool = False
    """Lets anyone sign in as any user by name, for development and tests only."""
    github: "OAuthSettings | None" = None
    """Signing in with GitHub; not offered unless configured."""
    google: "OAuthSettings | None" = None
    """Signing in with Google; not offered unless configured."""
    secret_key: str | None = None
    """What the keys the admin console keeps are encrypted with, and the ban list hashed
    with; without it, no key is kept there and the ban list is a plain hash. Users' own
    keys are kept by no one but their browsers (ADR-0017)."""
    gemini_billing_enabled: bool = False
    """The deployer says Gemini's billing is on, so its paid tier, which Google does not
    train on, embeds users' memories (ADR-0005, revised)."""
    admins: frozenset[tuple[str, str]] = frozenset()
    """The identities, as (provider, subject), whose users are admins and may enter the
    admin console (ADR-0013); without any, there is no admin console."""
    contact: str | None = None
    """How to reach the deployer, told to banned users when they try to sign in; None
    tells them no one."""
    vision: "VisionSettings | None" = None
    """A model that reads images, for drawing her in an outfit from her outfit list
    (`outfit_tags`); None and she is told the outfit's name alone."""
    stand_assets_url: str | None = None
    """Where browsers download the stands from, without a trailing slash: the public URL
    of the object storage they are synced to (ADR-0003, revised 2026-09-30). None shows
    every character's call background without her stand."""

    def __post_init__(self) -> None:
        if self.background_llm is None:
            object.__setattr__(self, "background_llm", self.chat_llm)
        if self.dev_login and self.domain:
            raise RuntimeError(
                "EIDOLON_DEV_LOGIN=1 让任何人都能不经登录冒充任何用户，只能在开发环境打开；"
                "配置了 EIDOLON_DOMAIN 的公开部署不会启动，请去掉 EIDOLON_DEV_LOGIN"
            )

    @property
    def chat_model(self) -> ChatModelSettings:
        """The chat model, as text turns and calls each use it."""
        return ChatModelSettings(self.chat_llm, self.call_reasoning_effort)

    @property
    def stt_key(self) -> SttKey | None:
        """The deployer's key for the speech recognition chosen; None without one."""
        if self.stt_provider == "bailian":
            if self.dashscope_api_key is None:
                return None
            return SttKey("bailian", self.dashscope_api_key, self.dashscope_region)
        return None if self.soniox_api_key is None else SttKey("soniox", self.soniox_api_key)

    @property
    def public_origin(self) -> str:
        """Where browsers reach the site: the domain over HTTPS, or the frontend's
        development server."""
        return f"https://{self.domain}" if self.domain else "http://localhost:3000"

    @classmethod
    def from_env(cls) -> "Settings":
        if "EIDOLON_OWN_LLM_REQUIRED" in os.environ:  # users once had to bring their own LLM
            log.warning(
                "EIDOLON_OWN_LLM_REQUIRED no longer does anything: users need not bring their"
                " own LLM, and only the limits hold them; delete it from .env"
            )
        chat_llm = LLMSettings.chat_from_env()
        return cls(
            database_path=database_path_from_env(),
            characters_path=characters_path_from_env(),
            chat_llm=chat_llm,
            background_llm=LLMSettings.background_from_env(),
            call_reasoning_effort=effort_from_env(
                "CHAT_LLM_CALL_REASONING_EFFORT", chat_llm.protocol
            ),
            ice_servers=_ice_servers(os.environ.get("EIDOLON_ICE_SERVERS")),
            turn=TurnSettings.from_env(),
            stt_provider=_stt_provider(),
            soniox_api_key=os.environ.get("SONIOX_API_KEY") or None,
            dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY") or None,
            bailian_stt_model=_bailian_stt_model(),
            fish_audio_api_key=os.environ.get("FISH_AUDIO_API_KEY") or None,
            fish_audio_model=fish_audio_model_from_env(),
            fish_audio_read_model=read_model_from_env(),
            voice_engine=_choice("VOICE_ENGINE", VOICE_ENGINES, "fish"),  # pyright: ignore[reportArgumentType]
            read_engine=_choice("READ_VOICE_ENGINE", READ_ENGINES, SAME_AS_CALLS),  # pyright: ignore[reportArgumentType]
            gpt_sovits_url=(os.environ.get("GPT_SOVITS_URL") or "").strip() or None,
            local_voices_path=Path(
                os.environ.get("EIDOLON_LOCAL_VOICES") or "../local-voices/voices.toml"
            ),
            embedding=EmbeddingSettings.from_env(),
            rerank=RerankSettings.from_env(),
            image=ImageSettings.from_env(),
            judge=judge_from_env(),
            emotion=EmotionSettings.from_env(),
            usage_limits=UsageLimits.from_env(),
            history_turns=history_turns_from_env(),
            weather=WeatherSettings.from_env(),
            exempting_keys=exempting_keys_from_env(),
            affinity_and_gifts=_switch("EIDOLON_AFFINITY_AND_GIFTS", default=True),
            reach_outs=_switch("EIDOLON_REACH_OUTS"),
            letters=_switch("EIDOLON_LETTERS"),
            diary=_switch("EIDOLON_DIARY"),
            schedules=_switch("EIDOLON_SCHEDULES"),
            keepsakes=_switch("EIDOLON_KEEPSAKES"),
            proxy=proxy_from_env(),
            service_status=service_status_from_env(),
            search_key=os.environ.get("EIDOLON_SEARCH_KEY", "").strip() or None,
            search_url=(
                os.environ.get("EIDOLON_SEARCH_URL", "").strip()
                or "https://api.deepseek.com/v1/tools/search"
            ),
            domain=os.environ.get("EIDOLON_DOMAIN", "").strip() or None,
            dev_login=os.environ.get("EIDOLON_DEV_LOGIN", "").strip() == "1",
            github=OAuthSettings.from_env("GITHUB"),
            google=OAuthSettings.from_env("GOOGLE"),
            secret_key=os.environ.get("EIDOLON_SECRET_KEY") or None,
            gemini_billing_enabled=os.environ.get("GEMINI_BILLING_ENABLED", "").strip() == "1",
            stand_assets_url=stand_assets_url_from_env(),
            vision=vision_from_env(),
            admins=admins_from_env(),
            contact=os.environ.get("EIDOLON_CONTACT", "").strip() or None,
        )


def _stt_provider() -> SttProvider:
    provider = os.environ.get("STT_PROVIDER") or "soniox"
    if provider not in STT_PROVIDERS:
        raise RuntimeError(
            f"环境变量 STT_PROVIDER 只能是 {'、'.join(STT_PROVIDERS)}，而不是 {provider}"
        )
    return provider  # pyright: ignore[reportReturnType]  # checked above


def fish_audio_model_from_env() -> FishAudioModel:
    model = (os.environ.get("FISH_AUDIO_MODEL") or "").strip() or DEFAULT_FISH_AUDIO_MODEL
    if model not in FISH_AUDIO_MODELS:
        raise RuntimeError(
            f"环境变量 FISH_AUDIO_MODEL 只能是 {'、'.join(FISH_AUDIO_MODELS)}，而不是 {model}"
        )
    return model  # pyright: ignore[reportReturnType]  # checked above


def _choice(name: str, choices: tuple[str, ...], default: str) -> str:
    value = (os.environ.get(name) or "").strip() or default
    if value not in choices:
        raise RuntimeError(f"环境变量 {name} 只能是 {'、'.join(choices)}，而不是 {value}")
    return value


def read_model_from_env() -> ReadModel:
    model = (os.environ.get("FISH_AUDIO_READ_MODEL") or "").strip() or SAME_AS_CALLS
    if model not in READ_MODELS:
        raise RuntimeError(
            f"环境变量 FISH_AUDIO_READ_MODEL 只能是 {'、'.join(READ_MODELS)}，而不是 {model}"
        )
    return model  # pyright: ignore[reportReturnType]  # checked above


def _bailian_stt_model() -> str:
    model = os.environ.get("BAILIAN_STT_MODEL") or DEFAULT_BAILIAN_STT_MODEL
    if model not in BAILIAN_STT_MODELS:
        raise RuntimeError(
            f"环境变量 BAILIAN_STT_MODEL 目前只能是 {'、'.join(BAILIAN_STT_MODELS)}，而不是 {model}"
            "（其他模型听到咳嗽会出空稿，接不住补充和打断）"
        )
    return model


def database_path_from_env() -> Path:
    return Path(os.environ.get("EIDOLON_DATABASE_PATH", "data/eidolon.db"))


def characters_path_from_env() -> Path:
    return Path(os.environ.get("EIDOLON_CHARACTERS_PATH", "../characters/characters.toml"))


def story_path_from_env() -> Path:
    """SCTranslationData's story directory, which `eidolon story sync` imports from."""
    return Path(os.environ.get("EIDOLON_STORY_PATH", "../corpus/SCTranslationData/data/story"))


def mirror_cache_path_from_env() -> Path:
    """Where `eidolon story sync` keeps the scripts it downloads from the mirror."""
    return Path(os.environ.get("EIDOLON_MIRROR_CACHE_PATH", "../corpus/mirror"))


def event_names_path_from_env() -> Path:
    """The admin's table of event names, which `eidolon story sync` names events from."""
    return Path(os.environ.get("EIDOLON_EVENT_NAMES_PATH", "../deploy/event-names.toml"))


def vision_from_env() -> "VisionSettings | None":
    """`VISION_LLM_BASE_URL`, `VISION_LLM_API_KEY` and `VISION_LLM_MODEL`, all three, or
    none: a model that reads images, over OpenAI's chat completions."""
    from eidolon.outfit_tags import VisionSettings

    values = [
        (os.environ.get(f"VISION_LLM_{n}") or "").strip() for n in ("BASE_URL", "API_KEY", "MODEL")
    ]
    if not all(values):
        return None
    return VisionSettings(*values)


def stand_assets_url_from_env() -> str | None:
    """`EIDOLON_STAND_ASSETS_URL`, the stands' public address, without a trailing slash."""
    value = (os.environ.get("EIDOLON_STAND_ASSETS_URL") or "").strip().rstrip("/")
    if not value:
        return None
    if not value.startswith(("https://", "http://")):
        raise RuntimeError(
            f"环境变量 EIDOLON_STAND_ASSETS_URL 应为立绘素材的公开访问地址（http:// 或 https:// 开头），"
            f"而不是 {value}"
        )
    return value


def stand_build_path_from_env() -> Path:
    """Where `eidolon stand fetch` keeps the stands it fetched (`source/`), `eidolon stand
    outfits` writes the outfit lists and the thumbnails are rendered, laid out as the
    bucket is, before they are uploaded."""
    return Path(os.environ.get("EIDOLON_STAND_BUILD_PATH", "../corpus/stand"))


ADMIN_PROVIDERS = ("github", "google")
"""The identity providers whose identities may make admins (`identity.PROVIDER_NAMES`)."""


def admins_from_env() -> frozenset[tuple[str, str]]:
    """`EIDOLON_ADMINS`: comma-separated `provider:subject`, as `eidolon user list` prints
    each sign-in method; by subject, not email, as two identities sharing an email are not
    the same user."""
    admins: set[tuple[str, str]] = set()
    for item in _comma_list(os.environ.get("EIDOLON_ADMINS", "")):
        provider, _, subject = item.partition(":")
        if provider not in ADMIN_PROVIDERS:
            raise RuntimeError(
                f"环境变量 EIDOLON_ADMINS 里的 {item} 不对：每一项是「提供方:身份标识」，"
                f"提供方只能是 {'、'.join(ADMIN_PROVIDERS)}（身份标识见 eidolon user list）"
            )
        if not subject.strip():
            raise RuntimeError(
                f"环境变量 EIDOLON_ADMINS 里的 {item} 缺少身份标识（见 eidolon user list）"
            )
        admins.add((provider, subject.strip()))
    return frozenset(admins)


def history_turns_from_env() -> int | None:
    return _limit("EIDOLON_HISTORY_TURNS", DEFAULT_HISTORY_TURNS)


def exempting_keys_from_env() -> frozenset[Service]:
    """`EIDOLON_EXEMPTING_LLM`, `_TTS` and `_STT`, each on unless 0; never all off."""
    keys: frozenset[Service] = frozenset(
        s for s in EXEMPTING_SERVICES if _switch(f"EIDOLON_EXEMPTING_{s.upper()}", default=True)
    )
    if not keys:
        raise RuntimeError(
            "环境变量 EIDOLON_EXEMPTING_LLM、EIDOLON_EXEMPTING_TTS、EIDOLON_EXEMPTING_STT 不能都是 0："
            "免限额密钥至少要选一项；想取消限额，请把每小时的回合数和通话次数设成 0（不限）"
        )
    return keys


def proxy_from_env() -> ProxySettings:
    """`EIDOLON_PROXY_URL`, on if set, and `EIDOLON_PROXY_SERVICES`: comma-separated
    `PROXIED_SERVICES`, or `all`."""
    services: set[ProxiedService] = set()
    for name in _comma_list(os.environ.get("EIDOLON_PROXY_SERVICES", "")):
        if name == "all":
            services.update(PROXIED_SERVICES)
        elif name in PROXIED_SERVICES:
            services.add(name)  # pyright: ignore[reportArgumentType]  # checked above
        else:
            raise RuntimeError(
                f"环境变量 EIDOLON_PROXY_SERVICES 里的 {name} 不对：每一项只能是"
                f" {'、'.join(PROXIED_SERVICES)}，或者写 all 表示全部"
            )
    url = os.environ.get("EIDOLON_PROXY_URL", "").strip()
    if not url:
        return ProxySettings(None, False, frozenset(services))
    try:
        url = checked_proxy_url(url)
    except ValueError as e:
        raise RuntimeError(f"环境变量 EIDOLON_PROXY_URL 不对：{e}") from None
    return ProxySettings(url, True, frozenset(services))


def service_status_from_env() -> ServiceStatusSettings:
    """`EIDOLON_STATUS_SERVICES`: comma-separated `SERVICE_NAMES`, `all` (unset is all) or
    `none`; `EIDOLON_STATUS_WINDOW_MINUTES`: one of `STATUS_WINDOWS`."""
    names = _comma_list(os.environ.get("EIDOLON_STATUS_SERVICES", "")) or ["all"]
    services: set[ServiceName] = set()
    for name in names:
        if name == "all":
            services.update(SERVICE_NAMES)
        elif name in SERVICE_NAMES:
            services.add(name)  # pyright: ignore[reportArgumentType]  # checked above
        elif name != "none":
            raise RuntimeError(
                f"环境变量 EIDOLON_STATUS_SERVICES 里的 {name} 不对：每一项只能是"
                f" {'、'.join(SERVICE_NAMES)}，或者写 all 表示全部、none 表示都不显示"
            )
    window = (os.environ.get("EIDOLON_STATUS_WINDOW_MINUTES") or "").strip()
    if window and window not in {str(w) for w in STATUS_WINDOWS}:
        raise RuntimeError(
            "环境变量 EIDOLON_STATUS_WINDOW_MINUTES 只能是"
            f" {'、'.join(map(str, STATUS_WINDOWS))}，而不是 {window}"
        )
    return ServiceStatusSettings(
        frozenset(services), int(window) if window else DEFAULT_STATUS_WINDOW
    )


@dataclass(frozen=True)
class OAuthSettings:
    """An OAuth app registered with Google or GitHub, which users sign in through."""

    client_id: str
    client_secret: str

    @classmethod
    def from_env(cls, provider: str) -> "OAuthSettings | None":
        """From `{provider}_CLIENT_ID` and `{provider}_CLIENT_SECRET`; None if neither is set."""
        if not os.environ.get(f"{provider}_CLIENT_ID") and not os.environ.get(
            f"{provider}_CLIENT_SECRET"
        ):
            return None
        return cls(_required(f"{provider}_CLIENT_ID"), _required(f"{provider}_CLIENT_SECRET"))


EmbeddingProvider = Literal["siliconflow", "gemini"]
EMBEDDING_PROVIDERS: tuple[EmbeddingProvider, ...] = get_args(EmbeddingProvider)


@dataclass(frozen=True)
class EmbeddingSettings:
    """The service that builds and searches the vector index, chosen at deploy time
    (ADR-0005): SiliconFlow's Qwen3-Embedding or Google's Gemini Embedding."""

    provider: EmbeddingProvider
    api_key: str
    model: str
    base_url: str
    dimensions: int | None = None
    """How long Gemini's vectors are; SiliconFlow's are as long as the model makes them."""

    @classmethod
    def from_env(cls) -> "EmbeddingSettings | None":
        """None if the chosen service's key is not set; a bad choice raises all the same."""
        provider = _embedding_provider()
        if not os.environ.get(_EMBEDDING_KEYS[provider]):
            return None
        return cls.required_from_env()

    @classmethod
    def required_from_env(cls) -> "EmbeddingSettings":
        """For the commands that cannot do without embedding: a missing key raises."""
        provider = _embedding_provider()
        api_key = _required(_EMBEDDING_KEYS[provider])
        model = os.environ.get("EMBEDDING_MODEL") or None
        if provider == "siliconflow":
            return cls(
                provider,
                api_key,
                model or DEFAULT_EMBEDDING_MODEL,
                os.environ.get("SILICONFLOW_BASE_URL") or DEFAULT_SILICONFLOW_BASE_URL,
            )
        return cls(
            provider,
            api_key,
            model or DEFAULT_GEMINI_EMBEDDING_MODEL,
            os.environ.get("GEMINI_BASE_URL") or DEFAULT_GEMINI_BASE_URL,
            _embedding_dimensions(),
        )


_EMBEDDING_KEYS: dict[EmbeddingProvider, str] = {
    "siliconflow": "SILICONFLOW_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def _embedding_provider() -> EmbeddingProvider:
    provider = os.environ.get("EMBEDDING_PROVIDER") or "siliconflow"
    if provider not in EMBEDDING_PROVIDERS:
        raise RuntimeError(
            f"环境变量 EMBEDDING_PROVIDER 只能是 {'、'.join(EMBEDDING_PROVIDERS)}，而不是 {provider}"
        )
    return provider  # pyright: ignore[reportReturnType]  # checked above


def _embedding_dimensions() -> int:
    value = (os.environ.get("EMBEDDING_DIMENSIONS") or "").strip()
    if not value:
        return DEFAULT_EMBEDDING_DIMENSIONS
    low, high = EMBEDDING_DIMENSIONS_RANGE
    if not (value.isascii() and value.isdigit() and low <= int(value) <= high):
        raise RuntimeError(f"环境变量 EMBEDDING_DIMENSIONS 需要是 {low}–{high} 之间的整数，而不是 {value}")
    return int(value)


@dataclass(frozen=True)
class RerankSettings:
    """SiliconFlow's Qwen3-Reranker, which reranks what the vector search finds, whichever
    service embeds. The thresholds, unless set here, are the model's (see `rerank`)."""

    api_key: str
    model: str = DEFAULT_RERANK_MODEL
    base_url: str = DEFAULT_SILICONFLOW_BASE_URL
    min_relevance: float | None = None
    """Overrides the model's threshold for story passages and character facts."""
    memory_min_relevance: float | None = None
    """Overrides the model's threshold for memories."""

    @classmethod
    def from_env(cls) -> "RerankSettings | None":
        """None if SiliconFlow's key is not set."""
        if not os.environ.get("SILICONFLOW_API_KEY"):
            return None
        return cls(
            api_key=_required("SILICONFLOW_API_KEY"),
            model=os.environ.get("RERANK_MODEL") or DEFAULT_RERANK_MODEL,
            base_url=os.environ.get("SILICONFLOW_BASE_URL") or DEFAULT_SILICONFLOW_BASE_URL,
            min_relevance=_relevance("RERANK_MIN_RELEVANCE"),
            memory_min_relevance=_relevance("RERANK_MEMORY_MIN_RELEVANCE"),
        )


def _relevance(name: str) -> float | None:
    value = (os.environ.get(name) or "").strip()
    if not value:
        return None
    try:
        relevance = float(value)
    except ValueError:
        relevance = -1.0
    if not 0 <= relevance <= 1:
        raise RuntimeError(f"环境变量 {name} 需要是 0 到 1 之间的数，而不是 {value}")
    return relevance


@dataclass(frozen=True)
class WeatherSettings:
    """Where the producer is (GLOSSARY.md「天气」), in degrees, north and east positive: the
    weather said in every turn is the one there."""

    latitude: float
    longitude: float

    @classmethod
    def from_env(cls) -> "WeatherSettings | None":
        """None unless both `EIDOLON_WEATHER_LAT` and `EIDOLON_WEATHER_LON` are set: without
        a place there is no weather, and the rest of the deployment stands as it was."""
        latitude = _degrees("EIDOLON_WEATHER_LAT")
        longitude = _degrees("EIDOLON_WEATHER_LON")
        if latitude is None and longitude is None:
            return None
        if latitude is None or longitude is None:
            raise RuntimeError(
                "EIDOLON_WEATHER_LAT 和 EIDOLON_WEATHER_LON 要一起填：只填一个就没有天气"
            )
        if not -90 <= latitude <= 90:
            raise RuntimeError(f"EIDOLON_WEATHER_LAT 需要是 -90 到 90 之间的纬度，而不是 {latitude}")
        if not -180 <= longitude <= 180:
            raise RuntimeError(
                f"EIDOLON_WEATHER_LON 需要是 -180 到 180 之间的经度，而不是 {longitude}"
            )
        return cls(latitude, longitude)


def _degrees(name: str) -> float | None:
    value = (os.environ.get(name) or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 需要是一个数（度数），而不是 {value}") from None


@dataclass(frozen=True)
class JevSettings:
    """Jev decides whether a turn needs retrieval, through TypeSafe's System One endpoint:
    OpenRouter's by default, or Vercel AI Gateway's (`https://ai-gateway.vercel.sh/typesafe/v1`)
    or TypeSafe's own (`https://api.typesafe.ai/v1`), which take the same requests."""

    api_key: str
    model: str = DEFAULT_JEV_MODEL
    base_url: str = DEFAULT_JEV_BASE_URL

    @classmethod
    def from_env(cls) -> "JevSettings":
        return cls(
            api_key=_required("JEV_API_KEY"),
            model=os.environ.get("JEV_MODEL") or DEFAULT_JEV_MODEL,
            base_url=os.environ.get("JEV_BASE_URL") or DEFAULT_JEV_BASE_URL,
        )


@dataclass(frozen=True)
class ImageSettings:
    """An image service (GLOSSARY.md「生图」): in NovelAI's own format, `base_url` up to the
    host (`/ai/generate-image`, answering with a ZIP), at NovelAI or a relay passing
    requests on as they are; or in OpenAI's image format, `base_url` up to `/v1`
    (`/images/generations`). The deployer's generates `concurrency` photos at a time, in the
    deployment's art style, `style`: tags written into every photo's prompt as they are,
    weights, artists and all; none if empty."""

    api_key: str
    base_url: str = DEFAULT_NOVELAI_BASE_URL
    model: str = DEFAULT_NOVELAI_MODEL
    api_format: ImageFormat = "novelai"
    concurrency: int = 1
    style: str = ""

    @classmethod
    def from_env(cls) -> "ImageSettings | None":
        """`NOVELAI_API_KEY`, without which there is none, `NOVELAI_API_FORMAT`,
        `NOVELAI_BASE_URL`, `NOVELAI_MODEL`, `NOVELAI_CONCURRENCY` and `NOVELAI_STYLE`."""
        api_key = (os.environ.get("NOVELAI_API_KEY") or "").strip()
        if not api_key:
            return None
        api_format = (os.environ.get("NOVELAI_API_FORMAT") or "").strip() or "novelai"
        if api_format not in IMAGE_FORMATS:
            raise RuntimeError(
                f"环境变量 NOVELAI_API_FORMAT 只能是 {'、'.join(IMAGE_FORMATS)}，而不是 {api_format}"
            )
        concurrency = (os.environ.get("NOVELAI_CONCURRENCY") or "").strip() or "1"
        if not (
            concurrency.isascii()
            and concurrency.isdigit()
            and 1 <= int(concurrency) <= MAX_IMAGE_CONCURRENCY
        ):
            raise RuntimeError(
                f"环境变量 NOVELAI_CONCURRENCY 需要是 1 到 {MAX_IMAGE_CONCURRENCY} 之间的整数，"
                f"而不是 {concurrency}"
            )
        return cls(
            api_key=api_key,
            base_url=(os.environ.get("NOVELAI_BASE_URL") or "").strip().rstrip("/")
            or DEFAULT_NOVELAI_BASE_URL,
            model=(os.environ.get("NOVELAI_MODEL") or "").strip() or DEFAULT_NOVELAI_MODEL,
            api_format=api_format,  # pyright: ignore[reportArgumentType]  # checked above
            concurrency=int(concurrency),
            style=(os.environ.get("NOVELAI_STYLE") or "").strip(),
        )


@dataclass(frozen=True)
class EmotionSettings:
    """How the judge model's judging of each segment's emotion during calls is taken (see
    `emotion`): the likeliest answer's probability below which she is calm and does not
    nod, and how long, in seconds, a segment may take to be judged before it goes without."""

    min_probability: float | None = None
    """None is the judge model's own (`judge.JUDGE_THRESHOLDS`); set, it is every model's."""
    timeout: float = 1.5

    @classmethod
    def from_env(cls) -> "EmotionSettings":
        for old, new in _RENAMED_EMOTION_SETTINGS.items():
            if os.environ.get(old):
                raise RuntimeError(
                    f"环境变量 {old} 已改名为 {new}（判断模型不再只有 Jev），请改用新名字"
                )
        timeout = (os.environ.get("JUDGE_EMOTION_TIMEOUT") or "").strip()
        return cls(
            min_probability=_relevance("JUDGE_EMOTION_MIN_PROBABILITY"),
            timeout=_seconds("JUDGE_EMOTION_TIMEOUT", timeout) if timeout else cls.timeout,
        )


_RENAMED_EMOTION_SETTINGS = {
    "JEV_EMOTION_MIN_PROBABILITY": "JUDGE_EMOTION_MIN_PROBABILITY",
    "JEV_EMOTION_TIMEOUT": "JUDGE_EMOTION_TIMEOUT",
}


def _seconds(name: str, value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        seconds = 0.0
    if not seconds > 0:
        raise RuntimeError(f"环境变量 {name} 需要是大于 0 的秒数，而不是 {value}")
    return seconds


@dataclass(frozen=True)
class CloudflareJudgeSettings:
    """Cloudflare's Clef or Clef-flash on Workers AI, called directly with an API token
    that may use Workers AI on the account."""

    api_token: str
    account_id: str
    model: CloudflareJudgeModel = DEFAULT_CLOUDFLARE_JUDGE_MODEL

    @classmethod
    def from_env(cls) -> "CloudflareJudgeSettings":
        model = os.environ.get("CLOUDFLARE_JUDGE_MODEL") or DEFAULT_CLOUDFLARE_JUDGE_MODEL
        if model not in CLOUDFLARE_JUDGE_MODELS:
            raise RuntimeError(
                f"环境变量 CLOUDFLARE_JUDGE_MODEL 只能是 {'、'.join(CLOUDFLARE_JUDGE_MODELS)}，"
                f"而不是 {model}"
            )
        return cls(
            api_token=_required("CLOUDFLARE_API_TOKEN"),
            account_id=_required("CLOUDFLARE_ACCOUNT_ID"),
            model=model,  # pyright: ignore[reportArgumentType]  # checked above
        )


@dataclass(frozen=True)
class BailianJudgeSettings:
    """Alibaba Cloud Model Studio's decision model, served over System One as Jev is, but
    only at a workspace's own endpoint, with a key of that workspace."""

    api_key: str
    workspace_id: str
    region: BailianRegion = DEFAULT_BAILIAN_JUDGE_REGION
    model: str = DEFAULT_BAILIAN_JUDGE_MODEL

    @property
    def host(self) -> str:
        """The region's host, the provider whichever workspace is in it."""
        return f"{BAILIAN_REGION_CODES[self.region]}.maas.aliyuncs.com"

    @property
    def base_url(self) -> str:
        return f"https://{self.workspace_id}.{self.host}/compatible-mode/v1"

    @classmethod
    def from_env(cls) -> "BailianJudgeSettings":
        region = os.environ.get("BAILIAN_JUDGE_REGION") or DEFAULT_BAILIAN_JUDGE_REGION
        if region not in BAILIAN_REGION_CODES:
            raise RuntimeError(
                f"环境变量 BAILIAN_JUDGE_REGION 只能是 {'、'.join(BAILIAN_REGION_CODES)}，而不是 {region}"
            )
        return cls(
            api_key=_required("BAILIAN_JUDGE_API_KEY"),
            workspace_id=_required("BAILIAN_JUDGE_WORKSPACE_ID"),
            region=region,  # pyright: ignore[reportArgumentType]  # checked above
            model=os.environ.get("BAILIAN_JUDGE_MODEL") or DEFAULT_BAILIAN_JUDGE_MODEL,
        )


JudgeSettings = JevSettings | CloudflareJudgeSettings | BailianJudgeSettings
"""The judge model chosen, and how to reach it."""


def judge_from_env(provider: str | None = None) -> JudgeSettings | None:
    """The judge model `provider`, or else `JUDGE_PROVIDER`, chooses: Cloudflare's and
    Model Studio's need their settings, and the `JEV_*` ones are then ignored; Jev, the
    default, is off without its key."""
    provider = provider or os.environ.get("JUDGE_PROVIDER") or "jev"
    if provider not in JUDGE_PROVIDERS:
        raise RuntimeError(
            f"环境变量 JUDGE_PROVIDER 只能是 {'、'.join(JUDGE_PROVIDERS)}，而不是 {provider}"
        )
    if provider == "cloudflare":
        return CloudflareJudgeSettings.from_env()
    if provider == "bailian":
        return BailianJudgeSettings.from_env()
    return _jev_from_env()


def _jev_from_env() -> JevSettings | None:
    """Jev, given its key; without it there is no retrieval, but a key still set under its
    old name stops the start rather than turn retrieval and memory off without a word."""
    if os.environ.get("JEV_API_KEY"):
        return JevSettings.from_env()
    if os.environ.get("AI_GATEWAY_API_KEY"):
        raise RuntimeError(
            "环境变量 AI_GATEWAY_API_KEY 已改名为 JEV_API_KEY（Jev 默认经由 OpenRouter 调用，"
            "填 OpenRouter 的 key；仍用 Vercel AI Gateway 的话，另设"
            " JEV_BASE_URL=https://ai-gateway.vercel.sh/typesafe/v1，并把 JEV_MODEL 设成 Vercel 的模型名），"
            "AI_GATEWAY_BASE_URL 也改名为 JEV_BASE_URL"
        )
    return None


@dataclass(frozen=True)
class TurnSettings:
    """A TURN server that shares a secret with the backend (coturn's `use-auth-secret`): the
    backend mints each browser credentials that expire, so the secret never leaves it."""

    urls: list[str]
    secret: str

    @classmethod
    def from_env(cls) -> "TurnSettings | None":
        urls = _comma_list(os.environ.get("EIDOLON_TURN_URLS", ""))
        if not urls:
            return None
        return cls(urls, _required("EIDOLON_TURN_SECRET"))

    def credentials(self, user_id: str, now: datetime) -> IceServer:
        """The username carries when the credentials expire; the password is the username
        signed with the secret."""
        expires = int((now + TURN_CREDENTIAL_LIFETIME).timestamp())
        username = f"{expires}:{user_id}"
        signature = hmac.new(self.secret.encode(), username.encode(), "sha1").digest()
        return {
            "urls": list(self.urls),
            "username": username,
            "credential": base64.b64encode(signature).decode(),
        }


TURN_CREDENTIAL_LIFETIME = timedelta(hours=24)
"""How long minted TURN credentials last. The TURN server checks them again each time a
call refreshes its relay, so they must outlast the longest call, and calls may have no
time limit (`EIDOLON_CALL_MINUTES=0`)."""


@dataclass(frozen=True)
class UsageLimits:
    """How many text turns and voice calls each user may start in any hour, and how many
    photos she may send them (GLOSSARY.md「照片」), counted across all their devices and
    sign-in methods, how long and how many turns one call may last, and how many calls
    there may be at once, all users' together, exempt ones too; None is no limit."""

    turns_per_hour: int | None = DEFAULT_TURNS_PER_HOUR
    calls_per_hour: int | None = DEFAULT_CALLS_PER_HOUR
    call_minutes: int | None = DEFAULT_CALL_MINUTES
    turns_per_call: int | None = DEFAULT_TURNS_PER_CALL
    photos_per_hour: int | None = DEFAULT_PHOTOS_PER_HOUR
    concurrent_calls: int | None = DEFAULT_CONCURRENT_CALLS

    @classmethod
    def from_env(cls) -> "UsageLimits":
        return cls(
            turns_per_hour=_limit("EIDOLON_TURNS_PER_HOUR", DEFAULT_TURNS_PER_HOUR),
            calls_per_hour=_limit("EIDOLON_CALLS_PER_HOUR", DEFAULT_CALLS_PER_HOUR),
            call_minutes=_limit("EIDOLON_CALL_MINUTES", DEFAULT_CALL_MINUTES),
            turns_per_call=_limit("EIDOLON_TURNS_PER_CALL", DEFAULT_TURNS_PER_CALL),
            photos_per_hour=_limit("EIDOLON_PHOTOS_PER_HOUR", DEFAULT_PHOTOS_PER_HOUR),
            concurrent_calls=_limit("EIDOLON_CONCURRENT_CALLS", DEFAULT_CONCURRENT_CALLS),
        )


def _limit(name: str, default: int) -> int | None:
    """A limit from the environment: unset is the default, 0 is no limit."""
    value = (os.environ.get(name) or "").strip()
    if not value:
        return default
    if not (value.isascii() and value.isdigit()):
        raise RuntimeError(f"环境变量 {name} 需要是非负整数（0 表示不限制），而不是 {value}")
    return int(value) or None


def _switch(name: str, *, default: bool = False) -> bool:
    """A setting turned on with 1 and off with 0; unset is `default`."""
    value = (os.environ.get(name) or "").strip()
    if value not in ("", "0", "1"):
        raise RuntimeError(f"环境变量 {name} 需要是 0 或 1（1 表示打开），而不是 {value}")
    return default if value == "" else value == "1"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"环境变量 {name} 未设置")
    return value


def _ice_servers(value: str | None) -> list[IceServer]:
    """Either a JSON list of ICE servers, or comma-separated STUN/TURN URLs."""
    if not value or not value.strip():
        return list(DEFAULT_ICE_SERVERS)
    if value.lstrip().startswith("["):
        try:
            servers = json.loads(value)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"环境变量 EIDOLON_ICE_SERVERS 不是有效的 JSON：{e}") from None
        if not isinstance(servers, list) or not all(
            isinstance(s, dict) and "urls" in s for s in servers
        ):
            raise RuntimeError("环境变量 EIDOLON_ICE_SERVERS 的每一项都需要有 urls")
        return servers
    return [{"urls": _comma_list(value)}]


def _comma_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
