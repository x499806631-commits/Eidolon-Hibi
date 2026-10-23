"""Letting a turn or call begin, and settling it once it is over (GLOSSARY.md「限额」
「运行记录」): whose keys it runs on, its activity record, its use of the limit and the
deployer's keys it holds, all in one place.

`Admissions` lets each begin, in order: it refuses one with a character who is in a
cooldown (`CoolingDown`, GLOSSARY.md「冷却」) before anything else is asked, recording
nothing; it takes the keys (`OwnKeys`), from those the turn or call brings, whose
models record each request as a service call to the model they are, opens the activity
record, and only then takes a use of the limit, so that nothing failing before it is
counted. One refused over the limit is recorded rejected; whatever else fails on the way
after the record is opened is recorded so (`_FAILED`); and what was taken is let go
before it is raised. Before a call is placed, it is only asked whether the call would
count.

The `Admission` it hands out settles it, once: given back unless its result counts
(`_COUNTED`), the record written, the deployer's keys let go.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from eidolon.activity import ActivityLog, CallEndReason, Recording, Result
from eidolon.llm import TurnLLMs
from eidolon.own_keys import BroughtKeys, CallKeys, OwnKeys, TurnKeys
from eidolon.usage import OverLimit, UsageLimiter, Use

log = logging.getLogger(__name__)

Admitted = Literal["turn", "call"]
"""What is let begin: a turn or a call, which the user takes; never a reach-out."""

_COUNTED: dict[Admitted, frozenset[Result]] = {
    "turn": frozenset({"success", "left"}),
    "call": frozenset({"success", "failed"}),
}
"""The results that count towards the user's limit, by kind; any other is given back. A
turn counts once it was taken, done or left; a call once it connected."""

_FAILED: dict[Admitted, Result] = {"turn": "failed", "call": "not_connected"}
"""How a turn or call is recorded that failed before it began: a call never connected."""

K = TypeVar("K", TurnKeys, CallKeys)


class CoolingDown(Exception):
    """The character is in a cooldown with the user (GLOSSARY.md「冷却」) until
    `cooldown_until`: she does not want to talk to them, by text or call, until then."""

    def __init__(self, cooldown_until: datetime) -> None:
        super().__init__("她现在不想说话，晚些时候再来吧")
        self.cooldown_until = cooldown_until


class Admission(Generic[K]):
    """One turn or call let begin, held until it is settled."""

    def __init__(self, kind: Admitted, keys: K, recording: Recording, use: Use) -> None:
        self._counted = _COUNTED[kind]
        self.keys = keys
        """Whose keys it runs on."""
        self.recording = recording
        """Its activity record, which the service calls made in it are recorded under."""
        self._use = use
        self._settled = False

    @property
    def llms(self) -> TurnLLMs:
        """The models it runs on, each request recorded as a service call."""
        return self.keys.llms

    @property
    def own_llm(self) -> bool:
        """Whether those are the user's own LLM."""
        return self.keys.own_llm

    @property
    def exempt(self) -> bool:
        """Whether it is exempt on the user's own keys, and counts towards nothing."""
        return self.keys.exempt

    def counts(self, result: Result) -> bool:
        """Whether it counts towards the user's limit, turning out as `result`."""
        return not self.exempt and result in self._counted

    async def settle(
        self,
        result: Result,
        duration: float | None = None,
        end_reason: CallEndReason | None = None,
    ) -> None:
        """It is over as `result` (a call's with how long it lasted and why it ended):
        its use given back unless that counts, its record written, the deployer's keys
        let go, each whatever became of the one before. Only the first time."""
        if self._settled:
            return
        self._settled = True
        try:
            if result not in self._counted:
                await self._use.give_back()  # an exempt use is kept for the admin to see
        finally:
            try:
                await self.recording.finish(result, duration, end_reason)
            finally:
                self.keys.release()


class Admissions:
    """Lets the users' turns and calls begin, on the limits and keys of the moment."""

    def __init__(
        self,
        usage: UsageLimiter,
        activity: ActivityLog,
        own_keys: OwnKeys,
        cooldown_until: Callable[[str, str], Awaitable[datetime | None]],
    ) -> None:
        """`cooldown_until` tells when the cooldown a character (by id) is in with a user
        ends, None if she is in none or affinity is off."""
        self._usage = usage
        self._activity = activity
        self._own_keys = own_keys
        self._cooldown_until = cooldown_until
        self._settling: set[asyncio.Future[Any]] = set()
        """What was let in as no one waited for it any more, till it is settled."""

    async def refuse_while_cooling(self, user_id: str, character_id: str) -> None:
        """Raise CoolingDown if the character is in a cooldown with the user: as each turn
        or call begins, before anything else is asked, and before a call is placed."""
        until = await self._cooldown_until(user_id, character_id)
        if until is not None:
            raise CoolingDown(until)

    async def turn(
        self, user_id: str, character_id: str, brought: BroughtKeys
    ) -> Admission[TurnKeys]:
        """Let the user's next text turn with `character_id`, bringing `brought`, begin.
        Raises CoolingDown, recording nothing, while she is in a cooldown with them;
        OverLimit when they have no turns left this hour; then, and whatever else fails before it
        begins, it is recorded and takes no use of the limit."""
        await self.refuse_while_cooling(user_id, character_id)
        return await self._admit("turn", user_id, character_id, self._own_keys.for_turn(brought))

    async def call(
        self, user_id: str, character_id: str, brought: BroughtKeys
    ) -> Admission[CallKeys]:
        """Let the user's next call with `character_id`, bringing `brought`, begin. Raises
        OverLimit when they have no calls left this hour; then, and whatever else fails
        before it begins, it is recorded and takes no use of the limit. Raises
        CallsUnavailable, recording nothing, when the deployment has no keys for calls and
        neither does the call bring them; CoolingDown as a turn does."""
        await self.refuse_while_cooling(user_id, character_id)
        return await self._admit("call", user_id, character_id, self._own_keys.for_call(brought))

    async def admitted(
        self,
        admitting: Awaitable[Admission[K]],
        never_began: Callable[[Admission[K]], Awaitable[None]],
    ) -> Admission[K]:
        """What `admitting` (`turn`, `call`) lets begin. Should the wait be cancelled,
        admitting goes on all the same, as what it writes would be written all the same
        (ADR-0028): what it lets begin then never does, and is settled by `never_began`."""
        task = asyncio.ensure_future(admitting)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self._settling.add(task)

            def settle(done: "asyncio.Future[Admission[K]]") -> None:
                self._settling.discard(done)
                if done.cancelled() or done.exception() is not None:
                    return  # nothing let in: refused or failed, as recorded
                settling = asyncio.ensure_future(never_began(done.result()))
                self._settling.add(settling)
                settling.add_done_callback(self._settled)

            task.add_done_callback(settle)
            raise

    async def aclose(self) -> None:
        """Wait for what was let in as no one waited for it any more to be settled."""
        while self._settling:
            await asyncio.gather(*self._settling, return_exceptions=True)

    def _settled(self, settling: "asyncio.Future[Any]") -> None:
        self._settling.discard(settling)
        if not settling.cancelled() and (error := settling.exception()) is not None:
            log.error("settling what never began failed", exc_info=error)

    async def _admit(
        self, kind: Admitted, user_id: str, character_id: str, keys: K
    ) -> Admission[K]:
        try:
            recording = self._activity.begin(
                kind, user_id, character_id, own_llm=keys.own_llm, exempt=keys.exempt
            )
            try:
                use = await self._usage.take(user_id, kind, exempt=keys.exempt)
            except OverLimit:
                await recording.finish("rejected")
                raise
            except BaseException:
                await recording.finish(_FAILED[kind])
                raise
        except BaseException:
            keys.release()
            raise
        return Admission(kind, keys, recording, use)

