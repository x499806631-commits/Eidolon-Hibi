"""How a call turns out: whether it counts towards the user's limit, when it has reached
its limits, what the browser is told when the backend ends it, and what its activity
record says of it (GLOSSARY.md「限额」「接通」「运行记录」).

`CallOutcome` is all the rule is: the call is let through (`Admission`) when it is
offered; the call's pipeline reports what happens to it (set up or not, connected, a turn
recorded, a cooldown begun elsewhere, its time up, a service failing, who or what ended
it, over). Each event hands back the `ended` message to send the browser, if it is the one
that ends the call; between turns the pipeline asks whether she hangs up, then whether the
call has reached its limits, and is handed back why it hangs up and that message. Once the
call is over it is settled as that message said: the admission keeps the use or gives it
back, and writes the record, by how it turned out; any turn still open is settled first,
as cut short by how the call ended. A call that connected is kept, too, for stats
(ADR-0021).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from typing import Literal

from eidolon.activity import CallEndReason, Recording, Result
from eidolon.admission import Admission
from eidolon.affinity import EndsFor
from eidolon.clock import Clock
from eidolon.config import SttProvider
from eidolon.engine import CallTurnRecorded, InCall
from eidolon.own_keys import CallKeys, OwnKeyFailed, Service
from eidolon.turn_outcome import TurnOutcome

log = logging.getLogger(__name__)

EndReason = Literal[
    "not_connected", "dropped", "duration_limit", "turn_limit", "own_key", "she_hung_up",
    "cooldown",
]
"""Why the backend ended a call, as the `ended` message tells the browser: it never really
connected (a voice service failed before any turn), a voice service failed later, the call
reached its time or its turn limit, one of the user's own keys failed, or she hung up, for
what the user said in the call (`affinity.ends`) or her affinity fallen into a
cooldown."""

_ENDED_BY: dict[EndReason, CallEndReason] = {
    "not_connected": "failed",
    "dropped": "failed",
    "own_key": "failed",
    "duration_limit": "duration_limit",
    "turn_limit": "turn_limit",
    "she_hung_up": "character_hung_up",
    "cooldown": "character_hung_up",
}
"""Why the call's activity record says it ended, when the browser was told why."""


KeepCall = Callable[[datetime, float], Awaitable[object]]
"""Keeps a call that connected, for stats, and what it comes to: when it connected, and how
long it lasted once it had, in seconds."""


@dataclass(frozen=True)
class CallLimits:
    """How long one call may last and how many turns it may take; None is no limit. Once
    either is reached, the reply in progress is finished and the call hangs up."""

    duration: timedelta | None = None
    turns: int | None = None


@dataclass(frozen=True)
class Ending:
    """The call hangs up between turns, she hanging up or its limits reached: `why`, and
    the `ended` message telling the browser so, None when it has been told why the call
    ended already. Whether `by_her`."""

    why: str
    ended: dict | None
    by_her: bool = False


def ended(reason: EndReason, message: str, counts: bool) -> dict:
    """The `ended` message: why the call is over, and whether it counted towards the
    user's limit (GLOSSARY.md「限额」「接通」)."""
    return {"type": "ended", "reason": reason, "message": message, "counts": counts}


class CallOutcome:
    """Whether one call counts towards the user's limit, from being offered to being over,
    and when it has reached its limits.

    The call is let through when it is offered. It never connected unless it connects,
    and a call that connects but ends on a failing voice service before any turn is
    recorded never really connected either. A call that connected and is hung up, by the
    user or not, or that reaches its limits, is a success, or failed when a voice service
    or an own key ended it; whether that counts is the `admission`'s to say. The browser
    is told why the backend ended a call only once, by the first event to end it, and the
    call is settled as it was told: nothing that happens after that changes whether it
    counts.

    The call's `limits` are kept here, and so is the count of its turns, the only one:
    the call has reached them once its time is up (`time_up`, timed by the pipeline from
    `time_limit`) or it has recorded as many turns as it may take, and what the browser is
    told then, every word of it, is said here too. So is how often the user has been out of
    line in the call, and how much each of its turns took off her affinity, which its turns
    are told (`in_call`), and whether she hangs up after the turn recorded last, as it said,
    or for a cooldown begun elsewhere (`cooled`), whichever came first: before its limits,
    which the same turn may reach.
    The call she hangs up on was connected, and is a success.

    It is settled with how it turned out, how long it lasted once connected, and why it
    ended: whatever ended it first, as the browser was told or as reported (`ended_by`),
    the user having left the page if nothing else did. Its turns are kept until each is
    settled: one still open as the call is settled is settled then, cut short by a failure
    if one ended the call (`ended_by_failure`), as it would be once over. Unless it never
    connected, it is kept then, too, for as long as its record says it lasted, whoever
    hung up and whatever ended it.
    """

    def __init__(
        self, admission: Admission[CallKeys], limits: CallLimits, clock: Clock, keep: KeepCall
    ) -> None:
        """The call let through by `admission`, which may go on as long as `limits` say;
        kept by `keep` once settled, if it connected, as of the time `clock` tells as it
        did."""
        self._admission = admission
        self.recording: Recording = admission.recording
        """The call's activity record, which its turns and service calls go under."""
        self._limits = limits
        self._clock = clock
        self._keep = keep
        self._connected_at: float | None = None  # `perf_counter`, once connected
        self._connected_by_clock: datetime | None = None  # the time `clock` told, once connected
        self._recorded = 0
        self._out_of_line = 0  # turns recorded out of line
        self._hurts: list[float] = []  # what each turn recorded took off her affinity
        self._hanging_up: CallTurnRecorded | None = None  # the turn she hangs up after
        self._time_up = False
        self._told = False  # the browser has been told why the call ended
        self._failed_to_connect = False  # a service failed before any turn (「接通」)
        self._failed = False  # a service, or an own key, ended it once connected
        self._ended_by: CallEndReason | None = None
        self._open_turns: list[TurnOutcome] = []  # begun, not yet settled
        self._settled = False

    @property
    def _connected(self) -> bool:
        return self._connected_at is not None

    async def set_up_failed(self) -> None:
        """The call could not be set up. It never connected; the browser learns of it from
        the answer to its offer, so there is nothing to tell it here."""
        self.ended_by("failed")
        await self._settle()

    def connected(self) -> dict | None:
        """The call has connected (GLOSSARY.md「接通」)."""
        if self._connected_at is None:
            self._connected_at = perf_counter()
            self._connected_by_clock = self._clock()
        return None

    def ended_by(self, reason: CallEndReason) -> None:
        """What ended the call, unless something did already: the user hanging up, a ban,
        the server shutting down, the call failing to connect in time."""
        if self._ended_by is None:
            self._ended_by = reason

    def turn_begun(self, own_llm: bool) -> TurnOutcome:
        """A turn taken in the call, on the user's own LLM or not, settled into its
        activity record, which goes under the call's; kept until it is settled."""
        recording = self.recording.turn(own_llm)

        async def settle(result: Result) -> None:
            self._open_turns.remove(turn)
            await recording.finish(result)

        turn = TurnOutcome(recording, settle)
        self._open_turns.append(turn)
        return turn

    @property
    def ended_by_failure(self) -> bool:
        """Whether a failure ended the call, a service's or an own key's: a turn it cut
        short failed (GLOSSARY.md「回合」), rather than being left by the user."""
        return self._ended_by == "failed"

    def turn_recorded(self, recorded: CallTurnRecorded = CallTurnRecorded()) -> dict | None:
        """A turn of the call was recorded, coming to what `recorded` says."""
        self._recorded += 1
        self._out_of_line += recorded.out_of_line
        self._hurts.append(recorded.hurt)
        if recorded.hangs_up is not None:
            self._hang_up_after(recorded)
        return None

    def cooled(self, until: datetime) -> bool:
        """She has fallen into a cooldown with the user, ending `until`, whatever the turns
        of the call come to: whether she hangs up for it, as for one, after the turn in
        progress, if any. Not if the call is ending already, for whatever reason."""
        if self._ended_by is not None:
            return False
        self._hang_up_after(CallTurnRecorded(hangs_up="cooldown", cooldown_until=until))
        return True

    def conversation_ended(self, why: EndsFor) -> bool:
        """She has ended the conversation the call is in, typing elsewhere, for `why`:
        whether she hangs up for it, as for what the user said, after the turn in progress,
        if any. Not if the call is ending already, for whatever reason, nor if she is hanging
        up already."""
        if self._ended_by is not None or self._hanging_up is not None:
            return False
        self._hang_up_after(CallTurnRecorded(hangs_up=why))
        return True

    def _hang_up_after(self, recorded: CallTurnRecorded) -> None:
        """She hangs up as `recorded` says, unless she is to for something else already."""
        if self._hanging_up is None:
            self._hanging_up = recorded

    @property
    def in_call(self) -> InCall:
        """What the call's next turn is told of it."""
        return InCall(self._out_of_line, tuple(self._hurts))

    def time_up(self) -> dict | None:
        """The call's time is up, `time_limit` after it connected."""
        self._time_up = True
        return None

    @property
    def time_limit(self) -> timedelta | None:
        """How long the call may last once it has connected; None is no limit."""
        return self._limits.duration

    def service_failed(
        self, own_service: Service | None, stt_provider: SttProvider | None = None
    ) -> dict | None:
        """A voice service failed, ending the call; `own_service` is the one failing on
        the user's own key, if it is theirs, and `stt_provider` the speech recognition the
        call is heard by. Before any turn is recorded the call never really connected and
        is given back. A failing own key is theirs to fix, so they are told which. Before
        the call connects there is no one to tell: the browser learns the call is over as
        its connection closes."""
        if not self._connected or self._told:
            return None
        self._failed_to_connect = self._recorded == 0
        if own_service is not None:
            message = OwnKeyFailed(own_service, "通话已结束", stt_provider).message
            return self._tell("own_key", message)
        if self._failed_to_connect:
            return self._tell("not_connected", "语音服务出了故障，通话没能接通")
        return self._tell("dropped", "语音服务出了故障，通话中断了")

    def she_hangs_up(self) -> Ending | None:
        """Whether she hangs up after the turn recorded last: if so, why, and the `ended`
        message telling the browser so, unless it has been told why the call ended already.
        Should the cooldown she hung up for not have begun after all, another turn recorded
        meanwhile, she hung up as for what the user said: the user can call her again."""
        recorded = self._hanging_up
        if recorded is None:
            return None
        why = "她挂断了电话"
        if self._told:
            return Ending(why, None, by_her=True)
        until = recorded.cooldown_until
        if recorded.hangs_up == "cooldown" and until is not None:
            message = self._tell("cooldown", f"{why}。")
            message["cooldown_until"] = until.isoformat()
        else:
            message = self._tell("she_hung_up", f"{why}。")
        return Ending(why, message, by_her=True)

    def limit_reached(self) -> Ending | None:
        """Whether the call has reached its limits, its time before its turns: if so, why
        it hangs up and, unless the browser has been told why the call ended already, the
        `ended` message telling it so. The call was connected, so it is kept."""
        duration, turns = self._limits.duration, self._limits.turns
        reason: EndReason
        if self._time_up and duration is not None:
            reason, why = "duration_limit", f"这次通话已经满 {duration / timedelta(minutes=1):g} 分钟了"
        elif turns is not None and self._recorded >= turns:
            reason, why = "turn_limit", f"这次通话已经说了 {self._recorded} 轮了"
        else:
            return None
        if self._told:
            return Ending(why, None)
        return Ending(why, self._tell(reason, f"{why}，先挂断啦。想接着聊可以重新拨打。"))

    async def settle(self) -> None:
        """The call is over, however it ended: settle it, once, any turn still open first."""
        for turn in list(self._open_turns):
            await turn.over(cut_short_by_failure=self.ended_by_failure)
        await self._settle()

    def _tell(self, reason: EndReason, message: str) -> dict:
        """The `ended` message telling the browser why the call ended and, from what has
        happened to it so far, whether it counts; nothing after this changes that."""
        self._told = True
        self._failed = reason in ("dropped", "own_key")
        self.ended_by(_ENDED_BY[reason])
        return ended(reason, message, self._admission.counts(self._result()))

    def _result(self) -> Result:
        if not self._connected or self._failed_to_connect:
            return "not_connected"
        return "failed" if self._failed else "success"

    async def _settle(self) -> None:
        if self._settled:
            return
        self._settled = True
        duration = None
        if self._connected_at is not None:
            duration = perf_counter() - self._connected_at
        result = self._result()
        try:
            await self._admission.settle(result, duration, self._ended_by or "left")
        finally:
            connected = self._connected_by_clock
            if result != "not_connected" and connected is not None and duration is not None:
                try:
                    await self._keep(connected, duration)
                except Exception:  # its record is settled; stats and achievements miss the call
                    log.exception("could not keep a call for stats")
