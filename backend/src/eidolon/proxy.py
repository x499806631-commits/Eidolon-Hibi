"""Which way each of the deployer's services reaches its provider: through the proxy or
direct (GLOSSARY.md「网络代理」), decided at each request, so that a change made in the
admin console holds from the next request on, without its client being made again.

`ProxyRoutes` holds the settings in effect. Over HTTP, each service's client is on a
transport of its own that, at each request, hands it on to a direct one or to one through
the proxy in effect, each kept for as long as the routes are; over a websocket, the
connection asks for the proxy as it is opened (`ProxiedWebsocket`). Neither goes by the
environment's `HTTPS_PROXY` and the like: whether a service goes through a proxy is the
settings' alone. Users' own keys are routed apart from the deployer's; their LLMs and image
generation go through the proxy only to a known provider's host (`own_transport`, ADR-0022).
"""

from collections.abc import Callable
from typing import Any, Literal

import httpcore
import httpx
from httpcore._async.http_proxy import AsyncTunnelHTTPConnection
from httpcore._synchronization import AsyncShieldCancellation
from socksio import ProtocolError as SocksProtocolError
from websockets.asyncio.client import ClientConnection

from eidolon.config import ProxiedService, ProxySettings, split_proxy_password

OwnAddressed = Literal["own_llm", "own_image"]
"""Users' own services whose addresses are theirs to name."""

REACH_TIMEOUT = httpx.Timeout(10)
"""How long trying whether a provider can be reached may take."""

MAX_CONNECTIONS = 300
"""How many connections each way a service has to its provider (direct, or through a
proxy) may have open at once. Every user's turns and calls share them: httpx's own
100 are for one program's requests, and with replies streamed for up to a minute, a busy
hour would wait on them."""

POOL_LIMITS = httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=60)
"""`MAX_CONNECTIONS`, of which as many as 60 are kept open between requests (httpx's own
20)."""


def open_client(proxy: str | None, timeout: httpx.Timeout) -> httpx.AsyncClient:
    """A client of its own, through `proxy`, or direct for None."""
    return httpx.AsyncClient(transport=_transport(proxy), timeout=timeout, trust_env=False)


class ProxyRoutes:
    """The proxy settings in effect, and each service's way to its provider by them."""

    def __init__(self, settings: ProxySettings) -> None:
        self._settings = settings
        self._transports: dict[ProxiedService, _RoutedTransport] = {}
        self._owns: list[_RoutedTransport] = []

    @property
    def settings(self) -> ProxySettings:
        return self._settings

    def replace(self, settings: ProxySettings) -> None:
        """Route every request from now on by `settings`; those under way go on as they
        started."""
        self._settings = settings

    def proxy_for(self, service: ProxiedService) -> str | None:
        """Where `service` connects through now; None for direct."""
        return self._settings.proxy_for(service)

    def client(self, service: ProxiedService, timeout: httpx.Timeout) -> httpx.AsyncClient:
        """A client for `service`, each request of which goes the way the settings in
        effect then have it go. Closing it leaves the connections, which are the
        routes', open."""
        return httpx.AsyncClient(
            transport=self.transport(service), timeout=timeout, trust_env=False
        )

    def transport(self, service: ProxiedService) -> httpx.AsyncBaseTransport:
        if service not in self._transports:
            self._transports[service] = _RoutedTransport(lambda _: self.proxy_for(service))
        return self._transports[service]

    def own_transport(
        self,
        service: OwnAddressed,
        known_provider: Callable[[str], bool],
        direct: httpx.AsyncHTTPTransport,
    ) -> httpx.AsyncBaseTransport:
        """For users' own `service`, whose addresses are theirs to name: a request over
        HTTPS to a host `known_provider` knows (GLOSSARY.md「已知服务商」) goes through the
        proxy if the settings in effect have `service` go through it; every other one, to
        whatever host, goes on `direct`, which checks where it connects as it must. The
        proxy would connect for us unchecked (ADR-0022)."""
        def route(request: httpx.Request) -> str | None:
            return self.own_proxy(service, request.url.scheme, request.url.host, known_provider)

        transport = _RoutedTransport(route, direct)
        self._owns.append(transport)
        return transport

    def own_proxy(
        self,
        service: OwnAddressed,
        scheme: str,
        host: str,
        known_provider: Callable[[str], bool],
    ) -> str | None:
        """Where a user's own `service` at `host` over `scheme` connects through now: the
        proxy for HTTPS to a host `known_provider` knows, `service` going through it; None,
        direct, for every other."""
        known = scheme == "https" and known_provider(host)
        return self.proxy_for(service) if known else None

    async def aclose(self) -> None:
        for transport in [*self._transports.values(), *self._owns]:
            await transport.close_all()


class _RoutedTransport(httpx.AsyncBaseTransport):
    """Hands each request on to `direct` (by default httpx's own), or to one through the
    proxy `route` gives for it at the time; one of each kind is kept, for every address,
    until `close_all`."""

    def __init__(
        self,
        route: Callable[[httpx.Request], str | None],
        direct: httpx.AsyncHTTPTransport | None = None,
    ) -> None:
        self._route = route
        self._transports: dict[str | None, httpx.AsyncHTTPTransport] = (
            {None: direct} if direct is not None else {}
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        proxy = self._route(request)
        if proxy not in self._transports:
            self._transports[proxy] = _transport(proxy)
        return await self._transports[proxy].handle_async_request(request)

    async def aclose(self) -> None:
        """Left open as a client on it closes: other clients of the service go on with it."""

    async def close_all(self) -> None:
        for transport in self._transports.values():
            await transport.aclose()
        self._transports.clear()


def _transport(proxy: str | None) -> httpx.AsyncHTTPTransport:
    return _Transport(proxy=proxy)


class _Transport(httpx.AsyncHTTPTransport):
    """httpx's own, on `POOL_LIMITS`, with a SOCKS proxy's reply it cannot read raised as
    the proxy's error, as httpx raises any other's, not as socksio's; and an HTTP proxy's
    tunnels that fail, or are given up on, before TLS through them is set up closed
    (`_Tunnel`)."""

    def __init__(self, proxy: str | None) -> None:
        super().__init__(proxy=proxy, limits=POOL_LIMITS)
        if type(self._pool) is httpcore.AsyncHTTPProxy:
            self._pool.__class__ = _HTTPProxy

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await super().handle_async_request(request)
        except SocksProtocolError as e:
            raise httpx.ProxyError(f"SOCKS 代理的回复无法解析：{e}", request=request) from e


class _HTTPProxy(httpcore.AsyncHTTPProxy):
    """httpcore's, its tunnels `_Tunnel`s."""

    def create_connection(self, origin: httpcore.Origin) -> httpcore.AsyncConnectionInterface:
        connection = super().create_connection(origin)
        if type(connection) is AsyncTunnelHTTPConnection:
            connection.__class__ = _Tunnel
        return connection


class _Tunnel(AsyncTunnelHTTPConnection):
    """httpcore's tunnel through an HTTP proxy, closed when it fails, timing out or given
    up on, after the proxy answered CONNECT but before TLS through it is set up. httpcore
    (1.0.9) leaves it open then, its slot in the pool taken for good: once a client's every
    slot is, each of its requests fails waiting for one (PoolTimeout)."""

    async def handle_async_request(self, request: httpcore.Request) -> httpcore.Response:
        try:
            return await super().handle_async_request(request)
        except BaseException:
            if not self._connected:
                with AsyncShieldCancellation():
                    await self._connection.aclose()
            raise


class Unreachable(Exception):
    """The provider could not be reached the way tried, saying why."""


async def reach(url: str, proxy: str | None) -> None:
    """Raise Unreachable saying why, unless `url`'s host answers over HTTP, any answer at
    all but the proxy's asking for credentials, through `proxy` (direct for None). Its
    password, if any, is masked in what is said."""
    try:
        async with open_client(proxy, REACH_TIMEOUT) as client:
            res = await client.get(url)
    except httpx.ProxyError as e:
        raise Unreachable(masked(f"代理拒绝了连接：{e}", proxy)) from None
    except httpx.TimeoutException:
        raise Unreachable(f"{REACH_TIMEOUT.connect:g} 秒内没有回应") from None
    except httpx.HTTPError as e:
        raise Unreachable(masked(repr(e), proxy)) from None
    if res.status_code == httpx.codes.PROXY_AUTHENTICATION_REQUIRED:
        raise Unreachable("代理要求认证（407），请检查用户名和密码")


def masked(text: str, proxy: str | None) -> str:
    """`text` with the password of `proxy`, if any, masked."""
    password = split_proxy_password(proxy)[1] if proxy else None
    return text.replace(password, "***") if password else text


class ProxiedWebsocket:
    """For a Pipecat websocket service: connects through the proxy `proxy` gives as each
    connection is opened; direct for None, the default."""

    _proxy: Callable[[], str | None] = staticmethod(lambda: None)

    async def _websocket_connect(self, uri: str, **kwargs: Any) -> ClientConnection:
        kwargs.setdefault("proxy", self._proxy())
        return await super()._websocket_connect(uri, **kwargs)  # pyright: ignore[reportAttributeAccessIssue]
