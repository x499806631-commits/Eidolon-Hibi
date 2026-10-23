"""Users' own API keys: an LLM (which plays the characters, and writes down memories and
names conversations unless they bring another model of their own for that), speech
synthesis (Fish Audio), speech recognition (Soniox or Alibaba Cloud Model Studio,
阿里云百炼, whichever the user chose, whatever the deployer did) and image generation (a
NovelAI token or a relay's key, in either format; see `photos` for her photos on it).

A user who brings their own key for a service has their turns and calls use it there,
at their own expense. A call on their key that fails fails the turn, saying which of
their keys it was (`OwnKeyFailed`); it never falls back to the deployer's key. Their keys
are kept in their browser, which brings them with each text turn and call (ADR-0017);
none is kept here, and a turn or call holds the ones it brings only until it ends and what
its turns do after them in the background (writing down memories, naming the conversation)
is done.

Whose keys a turn or call runs on is answered here, once as it starts (`for_turn`,
`for_call`), from the user's own keys it brings (`BroughtKeys`): theirs where it brought
them, the deployer's otherwise, with whether it counts towards the user's limit. The
deployer's are those of the live configuration as the turn or call starts (see
`live_config`), held until it releases them as it ends. Nothing downstream tells the
user's keys from the deployer's. Whether a turn or call counts is answered by one rule,
from the kind of use, the services it brings keys for and, for a call, the exempting keys
the live configuration has then (GLOSSARY.md「免限额密钥」), touching no key (`exempt`): as
it starts, before a call is placed (`call_exempt`), and for the settings page.

Before the browser keeps an own key, it is tried once, keeping nothing (`check_llm`,
`check_memory_llm`, `check_tts`, `check_stt`, `check_image`, the last generating a picture
at the user's expense). Whether a key fails its try or a turn, what
the service said is shown to the user with the key masked (`masked_cause`), and logged no other
way; a call's speech service on the user's key has it masked in its errors before Pipecat
logs them (`call._masking`). An LLM is tried as the deployer's chat model is at startup: it
must stream and take tools. In a public deployment it must also be on the public internet,
so no one can point the backend at the machines next to it, and read their answers back in
the errors they are shown. That is checked on every connection, not only when the key is
tried: a name that resolved to a public address then can resolve to a private one later.
The proxy, which would connect for us unchecked, is only for a known provider's host
(GLOSSARY.md「已知服务商」, ADR-0022), as the proxy settings have users' LLMs go. An image
service likewise, every request to it over HTTPS, a picture downloaded from where it says
too; its only known provider is NovelAI's own address.
"""

import asyncio
import ipaddress
import logging
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Protocol
from urllib.parse import urlsplit

import httpcore
import httpx

from eidolon.activity import ActivityLog, ServiceName, llm_origin, metered_llm
from eidolon.voices import DEFAULT_GPT_SOVITS_URL
from eidolon.config import (
    DEFAULT_FISH_AUDIO_MODEL,
    DEFAULT_NOVELAI_BASE_URL,
    SERVICES,
    ChatModelSettings,
    FishAudioModel,
    ImageSettings,
    NOT_AN_ADDRESS,
    LLMProtocol,
    LLMSettings,
    Service,
    VoiceEngine,
    SttKey,
    SttProvider,
)
from eidolon.ambience import AmbiencePicking
from eidolon.emotion import EmotionJudging
from eidolon.image_generation import (
    GENERATION_TIMEOUT,
    ImageFailed,
    ImageGenerator,
    try_image_service,
)
from eidolon.llm import ChatLLM, ChatMessage, LLMError, Streamed, Tool, TurnLLMs
from eidolon.llm import listing
from eidolon.llm.providers import check_chat_models, open_chat_models, open_llm
from eidolon.proxy import POOL_LIMITS, OwnAddressed, ProxyRoutes
from eidolon.usage import UsageKind

log = logging.getLogger(__name__)

SERVICE_LABELS: dict[Service, str] = {
    "llm": "LLM",
    "tts": "语音合成（Fish Audio）",
    "stt": "语音识别",
}
STT_PROVIDER_NAMES: dict[SttProvider, str] = {"soniox": "Soniox", "bailian": "阿里云百炼"}

BACKGROUND_LLM_LABEL = "后台模型："
"""Before why the user's own background model failed its try, telling it from their LLM."""

MAX_CAUSE_LENGTH = 300
"""How much of the service's own error message the user is shown."""

HTTPS_ONLY = "接口地址需要以 https:// 开头"

NO_PHOTOS = "这个部署发不了照片"
"""Why a user's own image generation is refused untried: no turn here has a photo."""

NOVELAI_HOST = urlsplit(DEFAULT_NOVELAI_BASE_URL).hostname
"""NovelAI's own image generation's host: the one known provider of image generation."""


class OwnKeyFailed(Exception):
    """A call on the user's own key failed; `message` says which key, for the user: for
    speech recognition, the service it is for (`stt_provider`)."""

    def __init__(
        self, service: Service, cause: str, stt_provider: SttProvider | None = None
    ) -> None:
        self.service: Service = service
        label = SERVICE_LABELS[service]
        if service == "stt" and stt_provider is not None:
            label += f"（{STT_PROVIDER_NAMES[stt_provider]}）"
        self.message = f"你的{label}密钥调用失败：{cause[:MAX_CAUSE_LENGTH]}"
        super().__init__(self.message)


class OwnKeyRejected(Exception):
    """Trying the key before keeping it failed."""


class UnknownService(ValueError):
    """Services were named that there are none of."""

    def __init__(self, unknown: Iterable[str]) -> None:
        super().__init__(f"没有这项服务：{'、'.join(sorted(unknown))}")


def named_services(own: str) -> frozenset[Service]:
    """The services `own` names (as `llm,tts,stt`, or nothing). Raises UnknownService for
    one there is not."""
    named = {s for s in own.split(",") if s}
    if unknown := named - set(SERVICES):
        raise UnknownService(unknown)
    return frozenset(s for s in SERVICES if s in named)


class CallsUnavailable(Exception):
    """Voice calls are not configured: there is no key for speech recognition or
    synthesis, neither the user's own nor the deployer's."""

    def __init__(self) -> None:
        super().__init__("语音通话尚未配置，请联系管理员")


class LLMService(Protocol):
    """The LLM services users bring their own keys to."""

    def open(self, settings: LLMSettings) -> ChatLLM:
        """The model `settings` describe, cheap to open for each turn and never closed."""
        ...

    async def check(self, settings: ChatModelSettings) -> None:
        """Raise LLMError saying why, unless the model can play the characters in text
        turns and calls, each at its own effort."""
        ...

    async def list_models(self, protocol: LLMProtocol, base_url: str, api_key: str) -> list[str]:
        """The models the service at `base_url` offers `api_key`, as it lists them; raises
        LLMError saying why not."""
        ...

    async def aclose(self) -> None: ...


class ProviderLLMService:
    """Whichever service the user names, over one HTTP client shared by all of them:
    through the proxy, as `routes` have users' LLMs go, to a host `known_provider` knows
    (GLOSSARY.md「已知服务商」); direct to every other one, and without `routes`."""

    def __init__(
        self,
        *,
        public_only: bool,
        routes: ProxyRoutes | None = None,
        known_provider: Callable[[str], bool] = lambda host: False,
    ) -> None:
        """With `public_only`, a service it connects to direct must be on the public
        internet over HTTPS."""
        self._public_only = public_only
        self._routes = routes
        self._known_provider = known_provider
        self._client = _own_client(
            "own_llm", httpx.Timeout(60, connect=10), public_only=public_only, routes=routes,
            known_provider=known_provider,
        )

    def open(self, settings: LLMSettings) -> ChatLLM:
        return open_llm(settings, client=self._client)

    async def check(self, settings: ChatModelSettings) -> None:
        if self._public_only and not _proxied(
            self._routes, "own_llm", settings.text.base_url, self._known_provider
        ):
            # Not where the proxy resolves it: a name may not resolve here as it does there.
            await _check_public(settings.text.base_url)
        await check_chat_models(open_chat_models(settings, self._client), settings)

    async def list_models(self, protocol: LLMProtocol, base_url: str, api_key: str) -> list[str]:
        """As the service lists them, asked as a turn would be: over the same client,
        checked to be on the public internet the same way (`listing.list_models`)."""
        if self._public_only and not _proxied(
            self._routes, "own_llm", base_url, self._known_provider
        ):
            await _check_public(base_url)
        return await listing.list_models(self._client, protocol, base_url, api_key)

    async def aclose(self) -> None:
        await self._client.aclose()


def _proxied(
    routes: ProxyRoutes | None,
    service: OwnAddressed,
    base_url: str,
    known_provider: Callable[[str], bool],
) -> bool:
    """Whether a request of users' own `service` to `base_url` goes through the proxy now,
    as `routes` have it go to a host `known_provider` knows."""
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL:  # said so by the check, as it is not proxied
        return False
    return routes is not None and (
        routes.own_proxy(service, url.scheme, url.host, known_provider) is not None
    )


def novelai_itself(host: str) -> bool:
    """Whether `host` is NovelAI's own image generation's (GLOSSARY.md「已知服务商」)."""
    return host == NOVELAI_HOST


class OwnImageService:
    """Users' own image services, whichever they name, over one HTTP client shared by all
    of them, as `ProviderLLMService` reaches their LLMs: through the proxy, as `routes`
    have users' image generation go, to NovelAI's own address alone, over HTTPS; direct to
    every other one, and without `routes`. Pictures come in the deployment's art style
    then (`style`), as the deployer's do."""

    def __init__(
        self,
        *,
        public_only: bool,
        routes: ProxyRoutes | None = None,
        style: Callable[[], str] = lambda: "",
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: "Resolver | None" = None,
    ) -> None:
        """With `public_only`, every request goes over HTTPS, and a service it connects
        to direct must be on the public internet, as `resolver` finds it. A `transport`
        given is used as it is instead, for every request: for tests."""
        self._public_only = public_only and transport is None
        self._routes = routes
        self._style = style
        self._resolver = resolver or resolve
        if transport is not None:
            self._client = httpx.AsyncClient(
                transport=transport, timeout=GENERATION_TIMEOUT, trust_env=False
            )
        else:
            self._client = _own_client(
                "own_image", GENERATION_TIMEOUT, public_only=public_only, routes=routes,
                known_provider=novelai_itself, resolver=self._resolver, https_only=public_only,
            )

    def generator(self, own: ImageSettings) -> ImageGenerator:
        """Generating on the user's own image service `own` names, one picture at a time,
        in the deployment's art style now."""
        return ImageGenerator(replace(own, concurrency=1, style=self._style()), self._client)

    async def check(self, own: ImageSettings) -> None:
        """Generate one picture on `own`, as a change to an image service is tried
        (`image_generation.try_image_service`); raises ImageFailed saying why there is
        none. In a public deployment its address is checked first, unless the proxy is to
        look it up: a name may not resolve here as it does there."""
        if self._public_only and not _proxied(
            self._routes, "own_image", own.base_url, novelai_itself
        ):
            try:
                await _require_public(own.base_url, self._resolver)
            except NotPublic as e:
                raise ImageFailed("service_error", str(e)) from None
        await try_image_service(self.generator(own))

    async def aclose(self) -> None:
        await self._client.aclose()


def _own_client(
    service: OwnAddressed,
    timeout: httpx.Timeout,
    *,
    public_only: bool,
    routes: ProxyRoutes | None,
    known_provider: Callable[[str], bool],
    resolver: "Resolver | None" = None,
    https_only: bool = False,
) -> httpx.AsyncClient:
    """A client for users' own `service`: through the proxy, as `routes` have it go, to a
    host `known_provider` knows; direct to every other one, which with `public_only` must
    be on the public internet, as `resolver` finds it. With `https_only`, every request
    goes over HTTPS."""
    direct = httpx.AsyncHTTPTransport(limits=POOL_LIMITS)
    if public_only:
        pool = direct._pool  # httpx takes no network backend of its own
        assert isinstance(pool, httpcore.AsyncConnectionPool)
        pool._network_backend = PublicOnlyBackend(pool._network_backend, resolver or resolve)
    transport: httpx.AsyncBaseTransport = (
        routes.own_transport(service, known_provider, direct) if routes is not None else direct
    )
    if https_only:
        transport = _HttpsOnly(transport)
    # Not from the environment: a proxy would connect for us, unchecked.
    return httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False)


class _HttpsOnly(httpx.AsyncBaseTransport):
    """Sends on requests over HTTPS alone."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https":
            raise httpx.UnsupportedProtocol(HTTPS_ONLY, request=request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass(frozen=True)
class BroughtKeys:
    """The user's own keys a turn or call brings, by service; None for one it does not
    bring, which it runs on the deployer's key for."""

    llm: ChatModelSettings | None = None
    """Text turns and memories on its text settings, calls' turns on its call ones."""
    tts: str | None = None
    """For Fish Audio, which speaks on it with `tts_model`."""
    stt: SttKey | None = None
    """For the speech recognition the user chose, with the region of a DashScope key."""
    memory_llm: LLMSettings | None = None
    """Memories on it instead of on `llm`, which it is only ever brought with: not a
    service of its own, whether a use counts is only ever asked of `llm`."""
    tts_model: FishAudioModel = DEFAULT_FISH_AUDIO_MODEL
    """The model the user chose to be voiced with on their own key for Fish Audio, at
    their own expense; not the deployer's."""
    image: ImageSettings | None = None
    """For the image service her photos are generated on, at the user's own expense and
    counting towards nothing; in the deployment's art style, one at a time."""

    def __post_init__(self) -> None:
        assert self.memory_llm is None or self.llm is not None

    @property
    def services(self) -> frozenset[Service]:
        """The services it brings a key for."""
        return frozenset(s for s in SERVICES if getattr(self, s) is not None)


class KeyCheck(Protocol):
    """Trying a speech service's key before keeping it, a user's `own` or the
    deployer's, the way the proxy settings have it go (see `call.Speech`)."""

    async def check_stt(self, provider: SttProvider, api_key: str, *, own: bool) -> SttKey:
        """The key as calls are to use it, with the region a DashScope key works in. Raises
        OwnKeyRejected saying why, unless `api_key` works for `provider`."""
        ...

    async def check_tts(self, api_key: str, model: FishAudioModel, *, own: bool) -> None:
        """Raise OwnKeyRejected saying why, unless `api_key` works for Fish Audio with
        `model`."""
        ...

    async def check_gpt_sovits(self, base_url: str) -> None:
        """Raise OwnKeyRejected saying why, unless a GPT-SoVITS server answers at `base_url`."""
        ...


def _no_hold() -> Callable[[], None]:
    return _nothing


@dataclass(frozen=True)
class DeployerKeys:
    """What the deployer pays for wherever a user brought nothing of their own; None for
    a speech service the deployer has no key for."""

    chat_llm: ChatLLM
    memory_llm: ChatLLM
    stt: SttKey | None = None
    """For whichever speech recognition the deployer chose (`STT_PROVIDER`)."""
    tts: str | None = None
    tts_model: FishAudioModel = DEFAULT_FISH_AUDIO_MODEL
    """What Fish Audio speaks with on the deployer's key."""
    tts_engine: VoiceEngine = "fish"
    """What speaks as her in calls: Fish Audio on `tts`, or the deployer's own GPT-SoVITS
    server at `gpt_sovits_url` (GLOSSARY.md「本地语音」), which needs no key."""
    gpt_sovits_url: str = DEFAULT_GPT_SOVITS_URL
    emotions: EmotionJudging | None = None
    """Judging her emotion during a call, on the deployer's judge model, whoever's keys
    the call is on; None if it is not judged here (see `call.Calls`)."""
    hold_background: Callable[[], Callable[[], None]] = field(default=_no_hold, compare=False)
    """See `TurnLLMs.hold_background`."""
    call_llm: ChatLLM | None = None
    """The chat model as calls' turns use it, at its reasoning effort in calls; None for
    `chat_llm`."""
    ambience: AmbiencePicking | None = None
    """Picking the ambience during calls (环境音「自动」), on the deployer's judge model,
    whoever's keys the call is on; None without one."""

    def lease(self) -> "DeployerLease":
        """These keys, which never change, for a turn or call."""
        return DeployerLease(self, _nothing)


@dataclass(frozen=True)
class DeployerLease:
    """The deployer's keys a turn or call runs on, held until it calls `release`."""

    keys: DeployerKeys
    release: Callable[[], None]


class Deployer(Protocol):
    """Where the deployer's keys come from as each turn or call starts."""

    def lease(self) -> DeployerLease: ...


def _nothing() -> None:
    pass


@dataclass(frozen=True)
class TurnKeys:
    """Whose keys a text turn runs on, and whether it counts towards the user's limit."""

    llms: TurnLLMs
    exempt: bool
    release: Callable[[], None] = field(default=_nothing, compare=False)
    """Called once the turn has ended, to let go of the deployer's keys it ran on."""
    own_llm: bool = False
    """Whether `llms` are the user's own LLM."""


@dataclass(frozen=True)
class CallKeys:
    """Whose keys a call runs on: its turns' models, the keys it hears (`stt`) and speaks
    (`tts`) with and which of those are the user's own, and whether it counts towards the
    user's limit."""

    llms: TurnLLMs
    stt: SttKey
    tts: str
    own: frozenset[Service]
    exempt: bool
    release: Callable[[], None] = field(default=_nothing, compare=False)
    """Called once the call has ended, to let go of the deployer's keys it ran on."""
    tts_model: FishAudioModel = DEFAULT_FISH_AUDIO_MODEL
    """What Fish Audio speaks with on `tts`: the user's choice on their own key."""
    tts_engine: VoiceEngine = "fish"
    """Fish Audio on `tts`, or the deployer's GPT-SoVITS server at `gpt_sovits_url` (never
    for one on their own Fish Audio key)."""
    gpt_sovits_url: str = DEFAULT_GPT_SOVITS_URL
    emotions: EmotionJudging | None = None
    """The deployer's, as the call connected (see `DeployerKeys.emotions`)."""
    own_llm: bool = False
    """Whether `llms` are the user's own LLM."""
    ambience: AmbiencePicking | None = None
    """The deployer's, as the call connected (see `DeployerKeys.ambience`)."""


class OwnKeys:
    """Whose keys each turn and call runs on, and trying a user's own key before their
    browser keeps it."""

    def __init__(
        self,
        llm_service: LLMService,
        deployer: Deployer,
        activity: ActivityLog,
        speech_check: KeyCheck | None = None,
        *,
        exempting: Callable[[], frozenset[Service]],
        image_service: OwnImageService | None = None,
    ) -> None:
        """Each request to a user's own LLM is recorded in `activity` as a service call
        on their own key; the deployer's models are recorded as `deployer` leases them.
        `exempting` tells the exempting keys now (GLOSSARY.md「免限额密钥」). Users' own
        image services are tried on `image_service`; without it, none is."""
        self._llm_service = llm_service
        self._deployer = deployer
        self._activity = activity
        self._speech_check = speech_check
        self._exempting = exempting
        self._image_service = image_service

    def for_turn(self, brought: BroughtKeys) -> TurnKeys:
        """Whose keys a text turn bringing `brought` runs on: the user's own LLM, if it
        brings one, both plays the character and writes down their memories, unless it
        brings their own model for memories too."""
        exempt = self.exempt("turn", brought.services)
        if brought.llm is not None:
            return TurnKeys(self._llms(brought, None), exempt, own_llm=True)
        lease = self._deployer.lease()
        return TurnKeys(self._llms(brought, lease.keys), exempt, lease.release)

    def for_call(self, brought: BroughtKeys) -> CallKeys:
        """Whose keys a call bringing `brought` runs on: each of speech recognition and
        synthesis on the user's own key if it brings one, else the deployer's; raises
        CallsUnavailable if there is neither for either. A user's own key for speech
        recognition is for the service they chose, whichever the deployer did; for speech
        synthesis, it speaks with the model they chose."""
        lease = self._deployer.lease()
        try:
            deployer = lease.keys
            _require_speech(brought.services, deployer)
            stt = brought.stt if brought.stt is not None else deployer.stt
            tts = brought.tts if brought.tts is not None else deployer.tts
            tts_model = brought.tts_model if brought.tts is not None else deployer.tts_model
            engine: VoiceEngine = "fish" if brought.tts is not None else deployer.tts_engine
            assert stt and (tts or engine != "fish")
            own = brought.services & {"stt", "tts"}
            llms = self._llms(brought, deployer, call=True)
        except BaseException:
            lease.release()
            raise
        return CallKeys(
            llms, stt, tts or "", own, self.exempt("call", brought.services), lease.release,
            tts_model, engine, deployer.gpt_sovits_url, deployer.emotions,
            own_llm=brought.llm is not None, ambience=deployer.ambience,
        )

    def exempt(self, kind: UsageKind, services: Iterable[Service]) -> bool:
        """Whether a use of `kind` bringing the user's own keys for `services` counts
        towards nothing: a text turn on their own LLM; a call on their own keys for every
        one of the exempting keys now (GLOSSARY.md「免限额密钥」). Only the rule: no key is
        touched, and neither the user's own limits nor whether calls are available are
        asked."""
        own = frozenset(services)
        return "llm" in own if kind == "turn" else self._exempting() <= own

    def call_exempt(self, services: Iterable[Service]) -> bool:
        """Whether a call bringing the user's own keys for `services` would count towards
        nothing, touching no key. Raises CallsUnavailable as `for_call` would."""
        services = frozenset(services)
        lease = self._deployer.lease()
        try:
            _require_speech(services, lease.keys)
        finally:
            lease.release()
        return self.exempt("call", services)

    async def check_llm(self, own: ChatModelSettings) -> None:
        """Try the user's LLM at each of its efforts, keeping nothing. Raises
        OwnKeyRejected saying why it does not work."""
        with _rejecting(own.text.api_key):
            await self._llm_service.check(own)

    async def list_llm_models(self, protocol: LLMProtocol, base_url: str, api_key: str) -> list[str]:
        """The models the user's key is offered at `base_url`, as the service lists them,
        keeping nothing. Raises OwnKeyRejected saying why not, the key masked."""
        with _rejecting(api_key):
            return await self._llm_service.list_models(protocol, base_url, api_key)

    async def check_memory_llm(self, own: LLMSettings) -> None:
        """Try the user's model for memories at its effort, as their LLM is tried, keeping
        nothing. Raises OwnKeyRejected saying why it does not work, and that it was this
        one."""
        with _rejecting(own.api_key, BACKGROUND_LLM_LABEL):
            await self._llm_service.check(ChatModelSettings(own, own.reasoning_effort))

    async def check_tts(self, api_key: str, model: FishAudioModel) -> None:
        """Try the user's key for speech synthesis with `model`, keeping nothing. Raises
        OwnKeyRejected saying why it does not work."""
        with _rejecting(api_key):
            await self._require_speech_check().check_tts(api_key, model, own=True)

    async def check_stt(self, provider: SttProvider, api_key: str) -> SttKey:
        """Try the user's key for speech recognition by `provider`, keeping nothing: the key
        as calls are to bring it, a DashScope key with the region it works in. Raises
        OwnKeyRejected saying why it does not work."""
        with _rejecting(api_key):
            return await self._require_speech_check().check_stt(provider, api_key, own=True)

    async def check_image(self, own: ImageSettings) -> None:
        """Try the user's own image service by generating one picture on it, at their
        expense and in the deployment's art style, keeping, counting and recording nothing.
        Raises OwnKeyRejected saying why it does not work."""
        if self._image_service is None:
            raise OwnKeyRejected(NO_PHOTOS)
        try:
            await self._image_service.check(own)
        except ImageFailed as e:
            raise OwnKeyRejected(masked_cause(e.detail, own.api_key)) from None

    def _llms(
        self, brought: BroughtKeys, deployer: DeployerKeys | None, *, call: bool = False
    ) -> TurnLLMs:
        """The models a turn bringing `brought` runs on, a call's with `call`: the user's
        own LLM writes down memories at its text effort, in calls too, unless they brought
        their own model for memories."""
        own_llm = brought.llm
        if own_llm is None:
            assert deployer is not None
            chat = deployer.call_llm if call and deployer.call_llm else deployer.chat_llm
            return TurnLLMs(chat, deployer.memory_llm, deployer.hold_background)
        chat_settings = own_llm.call if call else own_llm.text
        memory_settings = brought.memory_llm or own_llm.text
        return TurnLLMs(
            self._own_llm("chat_llm", chat_settings), self._own_llm("memory_llm", memory_settings)
        )

    def _own_llm(self, service: ServiceName, settings: LLMSettings) -> ChatLLM:
        """The user's own LLM `settings` describe, its every request recorded."""
        own = _OwnLLM(self._llm_service, settings)
        return metered_llm(own, self._activity, llm_origin(service, settings, own_key=True))

    def _require_speech_check(self) -> KeyCheck:
        if self._speech_check is None:
            raise OwnKeyRejected("这个部署没有语音服务")
        return self._speech_check

class _OwnLLM:
    """The user's LLM, whose every failure is theirs to fix."""

    def __init__(self, service: LLMService, settings: LLMSettings) -> None:
        self._llm = service.open(settings)
        self._api_key = settings.api_key

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        try:
            async for item in self._llm.stream(messages, tools, forbid_calls=forbid_calls):
                yield item
        except Exception as e:
            # Not from `e`, whose message may repeat the key, so no traceback logged shows
            # it; what kind of failure it was is still found in its context.
            raise OwnKeyFailed("llm", masked_cause(str(e) or type(e).__name__, self._api_key)) from None

    async def aclose(self) -> None:
        pass  # the service's client is shared; see ProviderLLMService


def masked_cause(message: str, api_key: str) -> str:
    """What a service said about the user's `api_key`, as they are shown it: the key masked
    wherever it says it, then cut short, so that no part of the key is left."""
    return message.replace(api_key, "***")[:MAX_CAUSE_LENGTH]


@contextmanager
def _rejecting(api_key: str, label: str = "") -> Iterator[None]:
    """Raise OwnKeyRejected, saying why with `api_key` masked after `label`, if trying it
    fails."""
    try:
        yield
    except (LLMError, OwnKeyRejected) as e:
        raise OwnKeyRejected(label + masked_cause(str(e), api_key)) from None


def _require_speech(own: frozenset[Service], deployer: DeployerKeys) -> None:
    """Raise CallsUnavailable unless a call bringing the user's own keys for `own` has a
    key for each of speech recognition and synthesis, theirs or the deployer's."""
    speaks = deployer.tts or deployer.tts_engine != "fish"
    if not ("stt" in own or deployer.stt) or not ("tts" in own or speaks):
        raise CallsUnavailable()


async def _check_public(base_url: str) -> None:
    """Raise LLMError unless `base_url` is HTTPS to a host on the public internet."""
    try:
        await _require_public(base_url)
    except NotPublic as e:
        raise LLMError(str(e)) from None


async def _require_public(base_url: str, resolver: "Resolver | None" = None) -> None:
    """Raise NotPublic unless `base_url` is HTTPS to a host on the public internet, as
    `resolver` finds it."""
    try:  # a link pasted with its brackets (`[https://…](…)`) is not one
        parts = urlsplit(base_url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise NotPublic(NOT_AN_ADDRESS) from None
    if parts.scheme != "https" or not host:
        raise NotPublic(HTTPS_ONLY)
    await public_address(host, port or 443, resolver or resolve)


class NotPublic(OSError):
    """The host is not found, or not only on the public internet."""


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to."""
    found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(address[0]).split("%")[0] for *_, address in found]


async def public_address(host: str, port: int, resolver: Resolver = resolve) -> str:
    """The address to connect to `host` at; raises NotPublic unless every address it
    resolves to is on the public internet."""
    try:
        addresses = await resolver(host, port)
    except OSError:
        raise NotPublic(f"找不到 {host}") from None
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses):
        raise NotPublic(f"{host} 不是公网地址")
    return addresses[0]


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Connects only to hosts on the public internet, at the very address it checked, so
    the name cannot resolve elsewhere in between. TLS still checks the certificate
    against the host's name."""

    def __init__(
        self, inner: httpcore.AsyncNetworkBackend, resolver: Resolver = resolve
    ) -> None:
        self._inner = inner
        self._resolver = resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            address = await public_address(host, port, self._resolver)
        except NotPublic as e:
            raise httpcore.ConnectError(str(e)) from None
        return await self._inner.connect_tcp(
            address, port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("不能连接本机的套接字")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
