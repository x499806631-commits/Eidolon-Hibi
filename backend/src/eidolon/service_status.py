"""The service status (GLOSSARY.md「服务状态」): whether each of the deployer's services
works now, as users are shown it beside the announcements, under `/api/service-status`.

A service is judged by its calls on the deployer's keys over the latest minutes the live
configuration has (`ServiceStatusSettings.window_minutes`): by the share that failed, in any
way (`UNSTEADY_AT`, `DOWN_AT`). Users' own keys are left out: a key of theirs failing says
nothing of the deployment. Where a service had no such call, it is tried (`keep_probing`)
with the least of real requests, apart from any turn and recorded nowhere, so neither the
dashboard's usage nor its costs count it: the LLMs asked for a word, without reasoning
effort and cut short (`LiveConfig.trial_llm`), embedding and
reranking a few words, the judge model as the admin console tries it, speech synthesis
voicing one, speech recognition only checked for what costs nothing (it hears a stream, which
it has none of to hear). A probe that fails is made once again at once; failing twice, the
service is down. Image generation is not tried, every picture being paid for: without
calls it has nothing to go by, as any service has before its first probe.

Only the services the configuration shows are listed and tried, of those the deployment
has; none shown, users see nothing of it. What is shown is summed up at most once a
minute (`CACHED_FOR`), or again as the configuration changes, which also forgets what the
probes found: a key changed is tried again at the next tick.

A call's speech recognition and synthesis are recorded when the call ends, under when they
were made: a service is judged on them only then, and may be tried meanwhile.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from fastapi import FastAPI

from eidolon.activity import ActivityLog
from eidolon.call import Speech
from eidolon.clock import Clock
from eidolon.config import SERVICE_NAMES, ServiceName
from eidolon.embedding import Embedder
from eidolon.judge import try_judge_model
from eidolon.live_config import TTS, LiveConfig
from eidolon.llm import ChatLLM, ChatMessage
from eidolon.rerank import Reranker
from eidolon.sessions import UserId

log = logging.getLogger(__name__)

Status = Literal["ok", "unsteady", "down", "idle"]
"""Working; failing now and then; mostly failing; nothing to go by."""

UNSTEADY_AT = 0.05
"""The share of a service's calls failing from which it is unsteady."""
DOWN_AT = 0.5
"""The share from which it is down."""

TICK = timedelta(minutes=1)
"""How often the services without calls are looked for, to be tried."""
PROBE_TIMEOUT = 30.0
"""How long, in seconds, a probe may take before it fails."""
PROBED_FOR = TICK + timedelta(seconds=2 * PROBE_TIMEOUT)
"""How long past the window a probe still counts: the next one is made at the first tick
after the window, and may take two tries."""
CACHED_FOR = timedelta(minutes=1)
"""How long what users are shown is kept before it is summed up again."""

PROBE_MESSAGES = (ChatMessage("user", "在吗？回一个字就好。"),)
PROBE_TEXT = "在吗"

Probe = Callable[[], Awaitable[None]]
"""A probe of a service: returns if it works, raises whatever says why not."""


@dataclass(frozen=True)
class _Probed:
    at: datetime
    worked: bool


class ServiceStatus:
    """The service status, summed up from `activity` and the probes, over the services
    `config` shows and has. Services without a probe go by their calls alone."""

    def __init__(
        self,
        activity: ActivityLog,
        config: LiveConfig,
        probes: Mapping[ServiceName, Probe],
        clock: Clock,
    ) -> None:
        self._activity = activity
        self._config = config
        self._probes = probes
        self._clock = clock
        self._probed: dict[ServiceName, _Probed] = {}
        self._cached: tuple[datetime, dict] | None = None
        self._generation = 0
        """Counts what made what is cached stale, so that a status summed up meanwhile is
        not cached."""
        config.on_change(self._changed)

    def shown(self) -> list[ServiceName]:
        """The services the configuration shows that the deployment has, in the order of
        `SERVICE_NAMES`."""
        shown = self._config.service_status.services
        return [s for s in SERVICE_NAMES if s in shown and self._config.has_service(s)]

    async def now(self) -> dict:
        """What users are shown: the window, and each service shown with its status."""
        now = self._clock()
        if self._cached is not None and now - self._cached[0] < CACHED_FOR:
            return self._cached[1]
        generation = self._generation
        minutes = self._config.service_status.window_minutes
        since = now - timedelta(minutes=minutes)
        calls = await self._activity.deployer_calls_since(since)
        status = {
            "window_minutes": minutes,
            "services": [
                {"service": s, "status": self._status(s, calls.get(s), since)}
                for s in self.shown()
            ],
        }
        if generation == self._generation:
            self._cached = (now, status)
        return status

    def _status(self, service: ServiceName, calls: tuple[int, int] | None, since: datetime) -> Status:
        if calls is not None:
            made, failed = calls
            rate = failed / made
            return "down" if rate >= DOWN_AT else "unsteady" if rate >= UNSTEADY_AT else "ok"
        probed = self._probed.get(service)
        if probed is None or probed.at <= since - PROBED_FOR:
            return "idle"
        return "ok" if probed.worked else "down"

    async def keep_probing(self) -> None:
        """Try the services shown that had no call on the deployer's keys over the window,
        and were not tried over it, at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.probe_idle()
            except Exception:
                log.exception("the service status could not try the services without calls")

    async def probe_idle(self) -> None:
        """One tick of `keep_probing`."""
        now = self._clock()
        since = now - timedelta(minutes=self._config.service_status.window_minutes)
        calls = await self._activity.deployer_calls_since(since)
        idle: list[ServiceName] = [
            s for s in self.shown()
            if s in self._probes and s not in calls
            and ((p := self._probed.get(s)) is None or p.at <= since)
        ]
        worked = await asyncio.gather(*(self._try(self._probes[s]) for s in idle))
        for service, works in zip(idle, worked):
            if not works:
                log.warning("the service status found %s down: its probe failed twice", service)
            self._probed[service] = _Probed(self._clock(), works)
        if idle:
            self._forget()

    async def _try(self, probe: Probe) -> bool:
        for _ in range(2):
            try:
                async with asyncio.timeout(PROBE_TIMEOUT):
                    await probe()
                return True
            except Exception as e:
                log.info("a probe of the service status failed: %r", e)
        return False

    def _forget(self) -> None:
        self._cached = None
        self._generation += 1

    async def _changed(self) -> None:
        """The configuration changed, maybe a service's key: what was found of it by trying
        it no longer holds, and it is tried again at the next tick."""
        self._probed.clear()
        self._forget()


def probes(
    config: LiveConfig,
    speech: Speech,
    voice_id: str,
    embedder: Embedder | None,
    reranker: Reranker | None,
) -> dict[ServiceName, Probe]:
    """The probe of each service that has one, on the deployer's clients in effect at each:
    embedding and reranking on the `embedder` and `reranker` as made, before they are
    metered; speech synthesis voicing in `voice_id`, any character's."""

    async def chat_llm() -> None:
        async with config.trial_llm("chat_llm") as llm:
            await _first_word(llm)

    async def memory_llm() -> None:
        async with config.trial_llm("memory_llm") as llm:
            await _first_word(llm)

    async def judge() -> None:
        lease = config.lease_judge()
        if lease is None:
            raise RuntimeError("没有判断模型")
        try:
            await try_judge_model(lease.client)
        finally:
            lease.release()

    async def tts() -> None:
        values = config.effective(TTS)
        await speech.voice_a_word(values["api_key"], values["model"], voice_id)

    async def stt() -> None:
        key = config.stt_key
        if key is None:
            raise RuntimeError("没有语音识别的 key")
        await speech.check_stt(key.provider, key.api_key, own=False)

    found: dict[ServiceName, Probe] = {
        "chat_llm": chat_llm, "memory_llm": memory_llm, "judge": judge, "tts": tts, "stt": stt,
    }
    if embedder is not None:
        found["embedding"] = lambda: _embed(embedder)
    if reranker is not None:
        found["rerank"] = lambda: _rerank(reranker)
    return found


async def _first_word(llm: ChatLLM) -> None:
    """Ask `llm` for a word, and stop at the first part of its reply: a model that thinks
    first ends its reply when it has written all it may, its thinking or not."""
    stream = llm.stream(PROBE_MESSAGES)
    try:
        async for _ in stream:
            break
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            await aclose()


async def _embed(embedder: Embedder) -> None:
    await embedder.embed_query(PROBE_TEXT)


async def _rerank(reranker: Reranker) -> None:
    documents: Sequence[str] = ("在的",)
    await reranker.rerank(PROBE_TEXT, documents, 1)


def add_service_status_routes(app: FastAPI, status: ServiceStatus) -> None:
    @app.get("/api/service-status")
    async def service_status(_: UserId) -> dict:
        """Whether each service shown works now (see the module)."""
        return await status.now()
