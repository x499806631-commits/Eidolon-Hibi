"""Making the deployer's service clients from their configuration, and replacing one while
the backend runs (ADR-0013).

`ServiceClients` is the one place the deployer's clients are made from settings: at
startup by `assembly.build`, and whenever the live configuration changes (see
`live_config`). Tests pass in a fake, every deployer's client then made by it all the
same.

`Swappable` holds the client a service runs on now, together with the settings it was
made from: what it is, as its calls are recorded and its answers judged. Each turn and
each call takes a `Lease` on the two as it starts and runs on that client to the end,
whatever replaces it meanwhile; a client replaced is closed once the last lease on it is
released.
"""

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Generic, Literal, Protocol, TypeVar

from eidolon.config import (
    ChatModelSettings, JudgeSettings, LLMProtocol, LLMSettings, ProxySettings,
)
from eidolon.judge import JUDGE_TIMEOUT, JudgeModel, open_judge
from eidolon.llm import ChatLLM, ChatModels
from eidolon.llm import listing
from eidolon.llm.providers import LLM_TIMEOUT, open_chat_models, open_llm
from eidolon.proxy import ProxyRoutes


class ServiceClients(Protocol):
    """Makes the deployer's clients for the services, each from its settings; each is
    closed by whoever made it through this. Speech synthesis and recognition have none:
    each call makes its own from the keys (see `call.Speech`)."""

    def chat_models(self, settings: ChatModelSettings) -> ChatModels:
        """The model `settings` describe, to play the characters in text turns and calls."""
        ...

    def memory_llm(self, settings: LLMSettings) -> ChatLLM:
        """The model `settings` describe, to write down memories."""
        ...

    def judge_model(self, settings: JudgeSettings) -> JudgeModel:
        """The judge model `settings` choose."""
        ...

    async def list_models(
        self, service: Literal["chat_llm", "memory_llm"], protocol: LLMProtocol,
        base_url: str, api_key: str,
    ) -> list[str]:
        """The models the service at `base_url` offers `api_key`, asked as `service`'s
        requests go; raises LLMError saying why not."""
        ...


class ProviderClients:
    """The clients of the real services, each going through the proxy or direct as
    `routes` have it at each request (GLOSSARY.md「网络代理」)."""

    def __init__(self, routes: ProxyRoutes | None = None) -> None:
        self._routes = routes or ProxyRoutes(ProxySettings())

    def chat_models(self, settings: ChatModelSettings) -> ChatModels:
        return open_chat_models(settings, self._routes.client("chat_llm", LLM_TIMEOUT))

    def memory_llm(self, settings: LLMSettings) -> ChatLLM:
        return open_llm(settings, self._routes.client("memory_llm", LLM_TIMEOUT))

    def judge_model(self, settings: JudgeSettings) -> JudgeModel:
        return open_judge(settings, self._routes.client("judge", JUDGE_TIMEOUT))

    async def list_models(
        self, service: Literal["chat_llm", "memory_llm"], protocol: LLMProtocol,
        base_url: str, api_key: str,
    ) -> list[str]:
        async with self._routes.client(service, listing.LIST_TIMEOUT) as client:
            return await listing.list_models(client, protocol, base_url, api_key)


class Closable(Protocol):
    def aclose(self) -> Awaitable[None]: ...


C = TypeVar("C", bound=Closable)
S = TypeVar("S")


class Lease(Generic[C, S]):
    """A turn's or a call's hold on the client it runs on, and the settings it was made
    from; released once, when it ends."""

    def __init__(
        self,
        client: C,
        settings: S,
        release: Callable[[], None],
        again: "Callable[[], Lease[C, S]]",
    ) -> None:
        self.client = client
        self.settings = settings
        self._release: Callable[[], None] | None = release
        self._again = again

    def again(self) -> "Lease[C, S]":
        """Another hold on the same client, released on its own: for what goes on with
        it after the turn, as writing down memories does."""
        return self._again()

    def release(self) -> None:
        """Let go of the client; again does nothing."""
        if self._release is not None:
            release, self._release = self._release, None
            release()


class _Held(Generic[C, S]):
    def __init__(self, client: C, settings: S) -> None:
        self.client = client
        self.settings = settings
        self.leases = 0


class Swappable(Generic[C, S]):
    """The client a service runs on now and the settings it was made from, replaced
    together as its configuration changes.

    Leases are taken from any thread (the API's synchronous routes run in a pool), so
    the counts are kept under a lock; a client retired by its last lease is closed on the
    event loop it was replaced on."""

    def __init__(self, client: C, settings: S) -> None:
        self._lock = threading.Lock()
        self._current = _Held(client, settings)
        self._retired: set[_Held[C, S]] = set()
        self._closing: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def current(self) -> C:
        """The client in effect now, to try it: what is asked of it is not recorded."""
        return self._current.client

    @property
    def settings(self) -> S:
        """What the client in effect now was made from."""
        return self._current.settings

    def lease(self) -> Lease[C, S]:
        with self._lock:
            return self._lease(self._current)

    def _lease(self, held: _Held[C, S]) -> Lease[C, S]:
        """A hold on `held`, under the lock."""
        held.leases += 1

        def again() -> Lease[C, S]:
            with self._lock:
                return self._lease(held)

        return Lease(held.client, held.settings, lambda: self._release(held), again)

    async def replace(self, client: C, settings: S) -> None:
        """Run whatever starts from now on on `client`, made from `settings`; the client
        it replaces is closed now if nothing is on it, else once the last turn or call on
        it has ended."""
        self._loop = asyncio.get_running_loop()
        with self._lock:
            old, self._current = self._current, _Held(client, settings)
            idle = old.leases == 0
            if not idle:
                self._retired.add(old)
        if idle:
            await old.client.aclose()

    async def aclose(self) -> None:
        """Close every client still held, as the backend shuts down, whatever is on it."""
        with self._lock:
            held = [self._current, *self._retired]
            self._retired.clear()
        for h in held:
            await h.client.aclose()
        await asyncio.gather(*self._closing, return_exceptions=True)

    def _release(self, held: _Held[C, S]) -> None:
        with self._lock:
            held.leases -= 1
            done = held in self._retired and held.leases == 0
            if done:
                self._retired.discard(held)
        if done:
            self._close_later(held.client)

    def _close_later(self, client: C) -> None:
        """Close `client` on the loop it was replaced on, from whichever thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def close() -> None:
            task = loop.create_task(_close(client))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            close()
        else:
            loop.call_soon_threadsafe(close)


async def _close(client: Closable) -> None:
    await client.aclose()
