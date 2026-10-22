"""The proxy (GLOSSARY.md「网络代理」): each of the deployer's services the settings tick
reaches its provider through it, the rest direct, and users' own keys likewise, ticked
apart; their LLMs only to a known provider's host (GLOSSARY.md「已知服务商」). The
environment's `HTTP_PROXY` and the like play no part. A change holds from the next request
on. `.env` sets it; the admin console overrides it, trying each of the deployer's services
it routes another way before keeping it. A proxy and a provider are faked on localhost, as
are DashScope and the price catalogs."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

import httpx
import pytest

from eidolon.call import ProxiedSoniox, SpeechServices
from eidolon.config import (
    DEFAULT_BAILIAN_STT_MODEL,
    ChatModelSettings,
    ImageSettings,
    LLMSettings,
    PROXIED_SERVICES,
    ProxiedService,
    ProxySettings,
    Settings,
    SttKey,
    checked_proxy_url,
    proxy_from_env,
    split_proxy_password,
    with_proxy_password,
)
from eidolon.llm import ChatMessage, LLMError
from eidolon.image_generation import ImageFailed
from eidolon.own_keys import OwnImageService, ProviderLLMService
from eidolon.photo_prompt import check_prompt
from eidolon.price_catalog import Catalog
from eidolon.proxy import ProxyRoutes, Unreachable, masked, reach
from tests.conftest import ClientFactory
from tests.fakes import (
    FakeCatalog,
    FakeClock,
    FakeDashScope,
    FakeIdentityProvider,
    FakeLLM,
    FakeServiceClients,
)
from tests.test_live_config import CONFIG, REPLY, admin_client, start

PROXY = "/api/admin/config/proxy"
ALL_OFF = {s: False for s in PROXIED_SERVICES}


@dataclass
class FakeProxy:
    """An HTTP proxy on localhost: tunnels CONNECT, forwards requests in absolute form,
    recording where each went (`went`: "host:port"); asks for credentials (407) unless
    given `password`, if set. Without `upstream`, it answers 502 to each, connecting
    nowhere, as for a real provider's host."""

    password: str | None = None
    upstream: bool = True
    went: list[str] = field(default_factory=list)
    url: str = ""

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"

    async def aclose(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        line, *headers = head.decode().split("\r\n")
        method, target, version = line.split(" ")
        if self.password is not None and not any(
            h.lower().startswith("proxy-authorization:") for h in headers
        ):
            writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if method == "CONNECT":
            where = target
        else:
            parts = urlsplit(target)
            where = parts.netloc
            path = parts.path + (f"?{parts.query}" if parts.query else "")
            head = "\r\n".join([f"{method} {path or '/'} {version}", *headers]).encode()
        self.went.append(where)
        host, port = where.rsplit(":", 1)
        try:
            if not self.upstream:
                raise OSError("not connecting upstream")
            up_reader, up_writer = await asyncio.open_connection(host, int(port))
        except OSError:  # a provider not found here, as no real one is
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        else:
            up_writer.write(head)
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


@dataclass
class FakeProvider:
    """A provider on localhost answering every request 200, counting them."""

    hits: int = 0
    url: str = ""

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def aclose(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        self.hits += 1
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()


@pytest.fixture
async def proxy() -> AsyncIterator[FakeProxy]:
    fake = FakeProxy()
    await fake.start()
    yield fake
    await fake.aclose()


@pytest.fixture
async def provider() -> AsyncIterator[FakeProvider]:
    fake = FakeProvider()
    await fake.start()
    yield fake
    await fake.aclose()


def through(proxy: FakeProxy, *services: ProxiedService) -> ProxySettings:
    return ProxySettings(proxy.url, True, frozenset(services))


def where(provider: FakeProvider) -> str:
    return urlsplit(provider.url).netloc


# Each service goes the way the settings have it, request by request.


async def test_a_service_ticked_goes_through_the_proxy_and_the_others_direct(
    proxy: FakeProxy, provider: FakeProvider
):
    routes = ProxyRoutes(through(proxy, "chat_llm"))
    async with routes.client("chat_llm", httpx.Timeout(5)) as chat:
        await chat.get(provider.url)
    async with routes.client("memory_llm", httpx.Timeout(5)) as memory:
        await memory.get(provider.url)
    await routes.aclose()

    assert proxy.went == [where(provider)]
    assert provider.hits == 2


async def test_with_the_proxy_switched_off_every_service_goes_direct(
    proxy: FakeProxy, provider: FakeProvider
):
    routes = ProxyRoutes(replace(through(proxy, *PROXIED_SERVICES), enabled=False))
    async with routes.client("judge", httpx.Timeout(5)) as client:
        await client.get(provider.url)
    await routes.aclose()

    assert proxy.went == []
    assert provider.hits == 1


async def test_a_change_holds_from_the_next_request_on_the_same_client(
    proxy: FakeProxy, provider: FakeProvider
):
    routes = ProxyRoutes(ProxySettings())
    client = routes.client("rerank", httpx.Timeout(5))
    await client.get(provider.url)
    routes.replace(through(proxy, "rerank"))
    await client.get(provider.url)
    routes.replace(ProxySettings())
    await client.get(provider.url)
    await client.aclose()
    await routes.aclose()

    assert proxy.went == [where(provider)]
    assert provider.hits == 3


async def test_the_environments_proxy_plays_no_part(
    proxy: FakeProxy, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
):
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, proxy.url)
    async with httpx.AsyncClient() as anyone:  # as any other request, it would go through it
        await anyone.get(provider.url)
    routes = ProxyRoutes(ProxySettings())
    async with routes.client("embedding", httpx.Timeout(5)) as client:
        await client.get(provider.url)
    await routes.aclose()

    assert proxy.went == [where(provider)]
    assert provider.hits == 2


async def test_a_socks5_proxy_is_spoken_to_in_socks5(provider: FakeProvider):
    greetings: list[bytes] = []

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        greetings.append(await reader.read(1))  # not a SOCKS server: hangs up after that
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    routes = ProxyRoutes(ProxySettings(f"socks5://127.0.0.1:{port}", True, frozenset({"tts"})))
    async with routes.client("tts", httpx.Timeout(5)) as client:
        with pytest.raises(httpx.ProxyError, match="SOCKS"):
            await client.get(provider.url)
    await routes.aclose()
    server.close()
    await server.wait_closed()

    assert greetings == [b"\x05"]  # SOCKS version 5
    assert provider.hits == 0


@dataclass
class StallingProxy:
    """An HTTP proxy that tunnels CONNECT, then says nothing more: TLS through it never
    starts. It answers a request in absolute form itself, 200. `handshakes` counts the TLS
    handshakes begun through it."""

    url: str = ""
    handshakes: int = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def aclose(self) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        if not head.startswith(b"CONNECT "):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        try:
            if await reader.read(65536):  # the client's hello
                self.handshakes += 1
            while await reader.read(65536):
                pass
        except ConnectionError:
            pass
        writer.close()


@pytest.fixture
async def stalling() -> AsyncIterator[StallingProxy]:
    fake = StallingProxy()
    await fake.start()
    yield fake
    await fake.aclose()


POOL_LIMIT = 100
"""How many connections an httpx client keeps at most."""

GIVEN_UP = httpx.Timeout(5, connect=0.2, pool=1)


async def test_tunnels_whose_tls_timed_out_leave_the_pool_room(stalling: StallingProxy):
    routes = ProxyRoutes(ProxySettings(stalling.url, True, frozenset({"judge"})))
    async with routes.client("judge", GIVEN_UP) as client:
        failed = await asyncio.gather(
            *(client.post("https://provider.example/systemone") for _ in range(POOL_LIMIT)),
            return_exceptions=True,
        )
        assert all(isinstance(e, httpx.ConnectTimeout) for e in failed)
        res = await client.get("http://provider.example/")
    await routes.aclose()

    assert res.text == "ok"


async def test_tunnels_given_up_on_as_tls_starts_leave_the_pool_room(stalling: StallingProxy):
    routes = ProxyRoutes(ProxySettings(stalling.url, True, frozenset({"judge"})))
    async with routes.client("judge", httpx.Timeout(5, pool=1)) as client:
        requests = [
            asyncio.ensure_future(client.post("https://provider.example/systemone"))
            for _ in range(POOL_LIMIT)
        ]
        while stalling.handshakes < POOL_LIMIT:
            await asyncio.sleep(0.01)
        for request in requests:
            request.cancel()
        await asyncio.gather(*requests, return_exceptions=True)
        res = await client.get("http://provider.example/")
    await routes.aclose()

    assert res.text == "ok"


# Speech: over websockets, the deployer's keys through the proxy, users' own direct.


async def test_the_deployers_dashscope_key_is_tried_through_the_proxy_and_a_users_own_direct(
    proxy: FakeProxy, dashscope: FakeDashScope
):
    dashscope.keys["deployers-key"] = "beijing"
    dashscope.keys["users-key"] = "beijing"
    speech = SpeechServices(
        DEFAULT_BAILIAN_STT_MODEL, routes=ProxyRoutes(through(proxy, "stt"))
    )

    await speech.check_stt("bailian", "deployers-key", own=False)
    through_proxy = list(proxy.went)
    await speech.check_stt("bailian", "users-key", own=True)

    beijing = urlsplit(dashscope.urls["beijing"]).netloc
    assert through_proxy == [beijing]
    assert proxy.went == [beijing]
    assert len(dashscope.tasks) == 2


async def test_each_speech_service_asks_for_the_proxy_as_it_connects(proxy: FakeProxy):
    routes = ProxyRoutes(ProxySettings())
    speech = SpeechServices(routes=routes)
    deployers_tts = speech.tts("fish-key", "s2.1-pro-free", "mano-voice", 16000, own=False)
    own_tts = speech.tts("my-fish-key", "s2.1-pro-free", "mano-voice", 16000, own=True)
    soniox = speech.stt(SttKey("soniox", "soniox-key"), own=False).stt
    bailian = speech.stt(SttKey("bailian", "dashscope-key", "beijing"), own=False).stt

    before = [s._proxy() for s in (deployers_tts, soniox, bailian)]  # pyright: ignore[reportAttributeAccessIssue]
    routes.replace(through(proxy, "tts", "stt"))
    after = [s._proxy() for s in (deployers_tts, soniox, bailian, own_tts)]  # pyright: ignore[reportAttributeAccessIssue]

    assert isinstance(soniox, ProxiedSoniox)
    assert before == [None, None, None]
    assert after == [proxy.url, proxy.url, proxy.url, None]


async def test_users_own_speech_keys_go_through_the_proxy_ticked_apart_from_the_deployers(
    proxy: FakeProxy, dashscope: FakeDashScope
):
    dashscope.keys["users-key"] = "beijing"
    routes = ProxyRoutes(through(proxy, "own_tts", "own_stt"))
    speech = SpeechServices(DEFAULT_BAILIAN_STT_MODEL, routes=routes)
    own = [
        speech.tts("my-fish-key", "s2.1-pro-free", "mano-voice", 16000, own=True),
        speech.stt(SttKey("soniox", "my-soniox-key"), own=True).stt,
    ]
    deployers = [
        speech.tts("fish-key", "s2.1-pro-free", "mano-voice", 16000, own=False),
        speech.stt(SttKey("soniox", "soniox-key"), own=False).stt,
    ]

    await speech.check_stt("bailian", "users-key", own=True)

    assert [s._proxy() for s in own] == [proxy.url, proxy.url]  # pyright: ignore[reportAttributeAccessIssue]
    assert [s._proxy() for s in deployers] == [None, None]  # pyright: ignore[reportAttributeAccessIssue]
    assert proxy.went == [urlsplit(dashscope.urls["beijing"]).netloc]


# Users' own LLMs: through the proxy only to a known provider's host; every other request
# direct, and checked to be on the public internet, as in a public deployment.

KNOWN = "https://llm.known.example/v1"


@pytest.fixture
def catalog(settings: Settings) -> Catalog:
    """The price catalogs as fetched: a provider at `KNOWN`, and those that are not on the
    internet by name: one run on this machine, as models.dev lists LM Studio, and others
    at an address or a name for this machine."""
    fake = FakeCatalog({
        "known": {"id": "known", "api": KNOWN, "models": {}},
        "lmstudio": {"id": "lmstudio", "api": "http://lmstudio.example/v1", "models": {}},
        "private": {"id": "private", "api": "https://10.0.0.8/v1", "models": {}},
        "loopback": {"id": "loopback", "api": "https://llm.localhost/v1", "models": {}},
    })
    catalog = Catalog(settings.database_path, FakeClock(), fake.transport())
    catalog.refresh()
    return catalog


async def own_llm_fails(service: ProviderLLMService, base_url: str) -> str:
    """Why a turn on the user's own LLM at `base_url` failed: no provider answers here."""
    llm = service.open(LLMSettings(base_url, "sk-mine", "m"))
    with pytest.raises(LLMError) as failed:
        async for _ in llm.stream([ChatMessage("user", "hi")]):
            pass
    return str(failed.value)


async def test_a_users_own_llm_goes_through_the_proxy_only_to_a_known_providers_host(
    proxy: FakeProxy, catalog: Catalog
):
    routes = ProxyRoutes(through(proxy, "own_llm"))
    service = ProviderLLMService(
        public_only=True, routes=routes, known_provider=catalog.known_provider
    )
    try:
        await own_llm_fails(service, KNOWN)
        over_http = await own_llm_fails(service, "http://llm.known.example/v1")
        routes.replace(through(proxy, "chat_llm"))
        not_ticked = await own_llm_fails(service, KNOWN)
    finally:
        await service.aclose()
        await routes.aclose()
        catalog.close()

    assert proxy.went == ["llm.known.example:443"]
    for why in [over_http, not_ticked]:
        assert "公网地址" in why or "找不到" in why


async def test_a_known_providers_llm_is_tried_through_the_proxy_not_looked_up_here(
    proxy: FakeProxy, catalog: Catalog
):
    routes = ProxyRoutes(through(proxy, "own_llm"))
    service = ProviderLLMService(
        public_only=True, routes=routes, known_provider=catalog.known_provider
    )
    try:
        with pytest.raises(LLMError) as through_proxy:
            await service.check(ChatModelSettings(LLMSettings(KNOWN, "sk-mine", "m")))
        routes.replace(ProxySettings())
        with pytest.raises(LLMError, match="公网地址|找不到"):
            await service.check(ChatModelSettings(LLMSettings(KNOWN, "sk-mine", "m")))
    finally:
        await service.aclose()
        await routes.aclose()
        catalog.close()

    assert "公网地址" not in str(through_proxy.value) and "找不到" not in str(through_proxy.value)
    assert proxy.went and set(proxy.went) == {"llm.known.example:443"}


# Users' own image generation: through the proxy only to NovelAI's own address over HTTPS;
# a relay's, whatever it is, direct, and checked to be on the public internet.

NOVELAI = "https://image.novelai.net"


async def everywhere_private(host: str, port: int) -> list[str]:
    """Resolves every name to a private address, so nothing is reached direct."""
    return ["10.0.0.8"]


async def own_image_fails(service: OwnImageService, base_url: str) -> str:
    """Why a photo on the user's own image service at `base_url` was not generated."""
    generator = service.generator(ImageSettings("pst-mine", base_url))
    with pytest.raises(ImageFailed) as failed:
        await generator.generate(check_prompt(""), asyncio.get_running_loop().time() + 10)
    return failed.value.detail


async def test_a_users_own_image_generation_goes_through_the_proxy_only_to_novelais_own_address(
    proxy: FakeProxy,
):
    proxy.upstream = False
    routes = ProxyRoutes(through(proxy, "own_image"))
    service = OwnImageService(public_only=True, routes=routes, resolver=everywhere_private)
    try:
        through_proxy = await own_image_fails(service, NOVELAI)
        relay = await own_image_fails(service, "https://relay.image.example")
        over_http = await own_image_fails(service, "http://image.novelai.net")
        with pytest.raises(ImageFailed) as tried:
            await service.check(ImageSettings("pst-mine", NOVELAI))
        routes.replace(through(proxy, "image"))
        not_ticked = await own_image_fails(service, NOVELAI)
    finally:
        await service.aclose()
        await routes.aclose()

    assert proxy.went == ["image.novelai.net:443"] * 2  # a turn's and a try's, not looked up here
    for why in [through_proxy, tried.value.detail]:
        assert "公网地址" not in why
    assert "公网地址" in relay and "公网地址" in not_ticked
    assert "https" in over_http


def test_known_providers_are_the_catalogs_hosts_and_those_written_down_by_name_only(
    catalog: Catalog,
):
    known = [
        host for host in (
            "llm.known.example", "LLM.Known.Example", "generativelanguage.googleapis.com",
            "openrouter.ai", "lmstudio.example", "10.0.0.8", "llm.localhost",
            "llm.unknown.example",
        )
        if catalog.known_provider(host)
    ]
    catalog.close()

    assert known == [
        "llm.known.example", "LLM.Known.Example", "generativelanguage.googleapis.com",
        "openrouter.ai",
    ]


# Reaching a provider, as a change is tried before it is kept.


async def test_a_provider_is_reached_through_the_proxy(proxy: FakeProxy, provider: FakeProvider):
    await reach(provider.url, proxy.url)

    assert proxy.went == [where(provider)]


async def test_a_proxy_asking_for_credentials_is_said_so(provider: FakeProvider):
    proxy = FakeProxy(password="secret")
    await proxy.start()
    try:
        with pytest.raises(Unreachable, match="407"):
            await reach(provider.url, proxy.url)
    finally:
        await proxy.aclose()


async def test_a_proxy_out_of_reach_is_said_so_its_password_masked(provider: FakeProvider):
    with pytest.raises(Unreachable) as caught:
        await reach(provider.url, "http://me:hunter2@127.0.0.1:1")

    assert "hunter2" not in str(caught.value)
    assert masked("me:hunter2@x", "http://me:hunter2@x:1") == "me:***@x"


# `.env`, and the address as it is written.


def test_env_sets_the_proxy_and_the_services_through_it(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EIDOLON_PROXY_URL", " socks5h://127.0.0.1:7891/ ")
    monkeypatch.setenv(
        "EIDOLON_PROXY_SERVICES", "chat_llm, tts, image, web_search, own_llm, own_image"
    )
    assert proxy_from_env() == ProxySettings(
        "socks5h://127.0.0.1:7891", True,
        frozenset({"chat_llm", "tts", "image", "web_search", "own_llm", "own_image"}),
    )
    monkeypatch.setenv("EIDOLON_PROXY_SERVICES", "all")
    assert proxy_from_env().services == frozenset(PROXIED_SERVICES)
    monkeypatch.delenv("EIDOLON_PROXY_URL")
    assert proxy_from_env() == ProxySettings(None, False, frozenset(PROXIED_SERVICES))


@pytest.mark.parametrize(
    ("name", "value", "says"),
    [
        ("EIDOLON_PROXY_SERVICES", "chat_llm,llm", "llm 不对"),
        ("EIDOLON_PROXY_URL", "127.0.0.1:7890", "开头"),
        ("EIDOLON_PROXY_URL", "ftp://127.0.0.1:7890", "开头"),
        ("EIDOLON_PROXY_URL", "http://127.0.0.1", "主机和端口"),
        ("EIDOLON_PROXY_URL", "http://127.0.0.1:7890/path", "不带路径"),
    ],
)
def test_env_refuses_what_is_not_a_proxy(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, says: str
):
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=says):
        proxy_from_env()


def test_a_password_is_kept_apart_from_the_address_and_put_back():
    url = checked_proxy_url("http://me:p%40ss@proxy.example:8080")

    assert split_proxy_password(url) == ("http://me@proxy.example:8080", "p%40ss")
    assert with_proxy_password("http://me@proxy.example:8080", "p%40ss") == url
    assert split_proxy_password("http://proxy.example:8080") == ("http://proxy.example:8080", None)
    assert with_proxy_password("http://proxy.example:8080", "unused") == "http://proxy.example:8080"


# The admin console: tried, kept, shown, restored.


@dataclass
class FakeReach:
    """Records each provider reached, and how (`reached`: (url, proxy)); one whose host is
    in `down` cannot be."""

    reached: list[tuple[str, str | None]] = field(default_factory=list)
    down: set[str] = field(default_factory=set)

    async def __call__(self, url: str, proxy: str | None) -> None:
        self.reached.append((url, proxy))
        if urlsplit(url).hostname in self.down:
            raise Unreachable("ConnectError('refused')")


@pytest.fixture
def admins(settings: Settings) -> Settings:
    return replace(settings, admins=frozenset({("github", "1001")}))


async def proxy_admin(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider, reach: FakeReach
) -> httpx.AsyncClient:
    clients = FakeServiceClients(FakeLLM(REPLY))

    def factory(*args, **kwargs) -> httpx.AsyncClient:
        return make_client(*args, reach=reach, **kwargs)

    return await admin_client(factory, settings, github, clients)


def routes_of(client: httpx.AsyncClient) -> ProxySettings:
    return client._transport.app.state.backend.config.proxy_settings  # pyright: ignore[reportAttributeAccessIssue]


async def test_a_saved_proxy_is_tried_on_the_services_it_reroutes_then_holds_until_restored(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    tried = FakeReach()
    client = await proxy_admin(make_client, admins, github, tried)
    before = (await client.get(CONFIG)).json()["proxy"]

    change = {"enabled": True, "url": "http://me:hunter2@127.0.0.1:7890"} | ALL_OFF | {
        "chat_llm": True, "tts": True, "judge": True, "own_llm": True, "own_tts": True,
    }
    saved = await client.put(PROXY, json=change)
    assert saved.status_code == 200, saved.text
    in_effect = routes_of(client)
    again = await client.put(PROXY, json=change | {"url": "http://me@127.0.0.1:7890"})
    assert again.status_code == 200, again.text
    restored = await client.delete(PROXY)
    assert restored.status_code == 200, restored.text

    assert before["status"] == "env" and before["effective"]["enabled"] is False
    assert before["unconfigured"] == ["judge", "embedding", "rerank", "image"]
    # The judge model is not configured, users' own keys are not here: neither is tried.
    # The password, kept apart, is not shown.
    proxy = "http://me:hunter2@127.0.0.1:7890"
    assert tried.reached == [("http://llm.invalid/v1", proxy), ("https://api.fish.audio", proxy)]
    shown = saved.json()["proxy"]
    assert shown["status"] == "overridden"
    assert shown["effective"]["url"] == "http://me@127.0.0.1:7890"
    assert shown["effective"]["password_last4"] == "ter2"
    assert "hunter2" not in saved.text
    assert in_effect == ProxySettings(
        proxy, True, frozenset({"chat_llm", "tts", "judge", "own_llm", "own_tts"})
    )
    assert again.json()["proxy"]["effective"]["password_last4"] == "ter2"  # left out: kept
    assert routes_of(client) == ProxySettings(None, False, frozenset())


async def test_every_switch_of_a_saved_proxy_is_kept(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    client = await proxy_admin(make_client, admins, github, FakeReach())
    all_on = {s: True for s in PROXIED_SERVICES}

    saved = await client.put(PROXY, json={"enabled": True, "url": "http://127.0.0.1:7890"} | all_on)

    assert saved.status_code == 200, saved.text
    assert routes_of(client).services == frozenset(PROXIED_SERVICES)
    assert {s: saved.json()["proxy"]["effective"][s] for s in PROXIED_SERVICES} == all_on


async def test_the_deployers_image_generation_goes_through_the_proxy_ticked_for_it(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider,
    proxy: FakeProxy, provider: FakeProvider,
):
    settings = replace(admins, proxy=through(proxy, "image"))
    client = await admin_client(make_client, settings, github, FakeServiceClients(FakeLLM(REPLY)))

    tried = await client.put("/api/admin/config/image", json={
        "api_format": "novelai", "base_url": provider.url, "model": "nai-diffusion-5-curated",
        "concurrency": 1, "api_key": "pst-key-1234",
    })

    assert tried.status_code == 422  # the provider here answers with no picture
    assert proxy.went == [where(provider)]
    assert provider.hits == 1


async def test_a_proxy_ticked_for_image_generation_is_tried_on_its_address(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    tried = FakeReach()
    settings = replace(admins, image=ImageSettings("pst-key", "https://relay.image.test"))
    client = await proxy_admin(make_client, settings, github, tried)

    saved = await client.put(
        PROXY, json={"enabled": True, "url": "http://127.0.0.1:7890"} | ALL_OFF | {"image": True}
    )

    assert saved.status_code == 200, saved.text
    assert tried.reached == [("https://relay.image.test", "http://127.0.0.1:7890")]
    assert "image" not in saved.json()["proxy"]["unconfigured"]


async def test_a_proxy_some_service_cannot_reach_through_is_not_kept(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    tried = FakeReach(down={"api.fish.audio"})
    client = await proxy_admin(make_client, admins, github, tried)

    refused = await client.put(
        PROXY, json={"enabled": True, "url": "http://127.0.0.1:7890"} | ALL_OFF | {"tts": True}
    )

    assert refused.status_code == 422
    assert "语音合成（经代理）连 api.fish.audio 不通" in refused.json()["detail"]
    assert routes_of(client) == ProxySettings(None, False, frozenset())
    assert (await client.get(CONFIG)).json()["proxy"]["status"] == "env"


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"enabled": True, "url": None}, "需要填写代理地址"),
        ({"enabled": False, "url": "127.0.0.1:7890"}, "开头"),
    ],
)
async def test_a_proxy_switched_on_without_an_address_or_with_a_wrong_one_is_refused(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider,
    change: dict, says: str,
):
    client = await proxy_admin(make_client, admins, github, FakeReach())

    refused = await client.put(PROXY, json=change | ALL_OFF)

    assert refused.status_code == 422
    assert says in refused.json()["detail"]


async def test_without_a_secret_key_an_address_with_a_password_is_refused_one_without_kept(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    tried = FakeReach()
    client = await proxy_admin(make_client, replace(admins, secret_key=None), github, tried)

    refused = await client.put(
        PROXY, json={"enabled": True, "url": "http://me:pw@127.0.0.1:7890"} | ALL_OFF
    )
    kept = await client.put(PROXY, json={"enabled": True, "url": "http://127.0.0.1:7890"} | ALL_OFF)

    assert refused.status_code == 409
    assert kept.status_code == 200, kept.text


async def test_a_proxy_that_cannot_be_reached_as_the_server_starts_falls_back_to_env(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    tried = FakeReach()
    client = await proxy_admin(make_client, admins, github, tried)
    change = {"enabled": True, "url": "http://127.0.0.1:7890"} | ALL_OFF | {"chat_llm": True}
    assert (await client.put(PROXY, json=change)).status_code == 200
    tried.down.add("llm.invalid")
    restarted = await proxy_admin(make_client, admins, github, tried)

    await start(restarted)

    shown = (await restarted.get(CONFIG)).json()["proxy"]
    assert shown["status"] == "failed"
    assert "扮演角色的 LLM（经代理）连 llm.invalid 不通" in shown["failure"]
    assert routes_of(restarted) == ProxySettings(None, False, frozenset())
