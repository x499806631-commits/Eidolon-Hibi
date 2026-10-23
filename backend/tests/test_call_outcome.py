"""How a call turns out: whether it counts towards the user's limit, whether it is given
back, the one `ended` message the browser is told why with, and what its activity record
says (GLOSSARY.md「限额」「接通」「运行记录」).

The call is only its events here, reported as `_LiveCall` and `CallTurns` would, on an
`Admission` let through as `Calls` does; all of it is real but the deployer (see
`test_admission`), on a fresh database, allowing one call an hour. Neither Pipecat nor
aiortc is needed for any of it.
"""

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from eidolon.activity import CallEndReason, Result
from eidolon.call_outcome import CallLimits, CallOutcome, Ending
from eidolon.own_keys import BroughtKeys, OwnKeyFailed
from eidolon.usage import UsageKind, Use
from tests.conftest import users_in
from tests.test_admission import ALL_OWN, Deployment
from tests.test_turn_outcome import DONE


class Limiter(Deployment):
    """A user allowed one call an hour, the limiter counting them and their records, and the
    calls kept for stats (`kept`: when each connected, and how long it lasted)."""

    def __init__(self, path: Path, user: str) -> None:
        super().__init__(path, user)
        self.kept: list[tuple[datetime, float]] = []

    async def call(
        self, limits: CallLimits = CallLimits(), brought: BroughtKeys = BroughtKeys()
    ) -> CallOutcome:
        admission = await self.admissions().call(self.user, "mano", brought)
        async def keep(at: datetime, lasted: float) -> None:
            self.kept.append((at, lasted))

        return CallOutcome(admission, limits, self.clock, keep)

    async def call_records(self) -> list[tuple[Result, CallEndReason | None, bool]]:
        """Each call's record: how it turned out, why it ended, and whether it says how
        long it lasted, as it does once connected."""
        records, _ = await self.activity.since(self.clock() - timedelta(hours=1))
        return [
            (r.result, r.end_reason, r.duration_s is not None)
            for r in records
            if r.kind == "call"
        ]

    async def turn_records(self) -> list[Result]:
        """How each turn of the calls turned out, as its record says."""
        records, _ = await self.activity.since(self.clock() - timedelta(hours=1))
        return [r.result for r in records if r.kind == "turn"]


@pytest.fixture
async def limiter(tmp_path: Path) -> AsyncIterator[Limiter]:
    path = tmp_path / "eidolon.db"
    [user] = await users_in(path, "producer")
    limiter = Limiter(path, user)
    yield limiter
    await limiter.db.close()


async def test_a_call_on_the_users_own_keys_alone_is_told_it_does_not_count(
    limiter: Limiter,
):
    outcome = await limiter.call(brought=ALL_OWN)
    outcome.connected()
    outcome.turn_recorded()

    message = outcome.service_failed(None)
    await outcome.settle()

    assert message is not None and message["counts"] is False
    assert not await limiter.counted("call")
    assert await limiter.calls_taken() == 1  # kept, for the admin to see
    assert await limiter.call_records() == [("failed", "failed", True)]


async def test_a_service_failing_before_the_call_connects_says_nothing_and_it_is_given_back(
    limiter: Limiter,
):
    outcome = await limiter.call()

    assert outcome.service_failed(None) is None
    assert outcome.service_failed("tts") is None
    await outcome.settle()

    assert not await limiter.counted("call")
    assert await limiter.call_records() == [("not_connected", "left", False)]


async def test_the_deployers_service_failing_before_any_turn_is_given_back_as_not_connected(
    limiter: Limiter,
):
    outcome = await limiter.call()
    outcome.connected()

    message = outcome.service_failed(None)
    await outcome.settle()

    assert message == {
        "type": "ended",
        "reason": "not_connected",
        "message": "语音服务出了故障，通话没能接通",
        "counts": False,
    }
    assert not await limiter.counted("call")
    assert await limiter.call_records() == [("not_connected", "failed", True)]


async def test_an_own_key_failing_before_any_turn_says_which_and_the_call_is_given_back(
    limiter: Limiter,
):
    outcome = await limiter.call()
    outcome.connected()

    message = outcome.service_failed("stt")
    await outcome.settle()

    assert message == {
        "type": "ended",
        "reason": "own_key",
        "message": OwnKeyFailed("stt", "通话已结束").message,
        "counts": False,
    }
    assert not await limiter.counted("call")
    assert await limiter.call_records() == [("not_connected", "failed", True)]


async def test_a_service_failing_after_a_turn_ends_a_call_that_was_connected(limiter: Limiter):
    outcome = await limiter.call()
    outcome.connected()
    outcome.turn_recorded()

    message = outcome.service_failed(None)
    await outcome.settle()

    assert message == {
        "type": "ended",
        "reason": "dropped",
        "message": "语音服务出了故障，通话中断了",
        "counts": True,
    }
    assert await limiter.counted("call")
    assert await limiter.call_records() == [("failed", "failed", True)]


async def test_an_own_key_failing_after_a_turn_ends_a_call_that_was_connected(limiter: Limiter):
    outcome = await limiter.call()
    outcome.connected()
    outcome.turn_recorded()

    message = outcome.service_failed("tts")
    await outcome.settle()

    assert message is not None
    assert (message["reason"], message["counts"]) == ("own_key", True)
    assert message["message"] == OwnKeyFailed("tts", "通话已结束").message
    assert await limiter.counted("call")
    assert await limiter.call_records() == [("failed", "failed", True)]


async def test_a_call_reaches_its_turn_limit_with_its_last_allowed_turn_and_is_kept(
    limiter: Limiter,
):
    outcome = await limiter.call(CallLimits(turns=2))
    outcome.connected()
    outcome.turn_recorded()
    assert outcome.limit_reached() is None
    outcome.turn_recorded()

    reached = outcome.limit_reached()
    await outcome.settle()

    assert reached == Ending(
        "这次通话已经说了 2 轮了",
        {
            "type": "ended",
            "reason": "turn_limit",
            "message": "这次通话已经说了 2 轮了，先挂断啦。想接着聊可以重新拨打。",
            "counts": True,
        },
    )
    assert await limiter.counted("call")
    assert await limiter.call_records() == [("success", "turn_limit", True)]


@pytest.mark.parametrize(
    ("time_limit", "minutes"), [(timedelta(minutes=10), "10"), (timedelta(seconds=90), "1.5")]
)
async def test_a_call_whose_time_is_up_is_kept_even_before_any_turn(
    limiter: Limiter, time_limit: timedelta, minutes: str
):
    outcome = await limiter.call(CallLimits(duration=time_limit))
    outcome.connected()
    assert outcome.time_limit == time_limit
    assert outcome.limit_reached() is None

    outcome.time_up()
    reached = outcome.limit_reached()
    await outcome.settle()

    assert reached == Ending(
        f"这次通话已经满 {minutes} 分钟了",
        {
            "type": "ended",
            "reason": "duration_limit",
            "message": f"这次通话已经满 {minutes} 分钟了，先挂断啦。想接着聊可以重新拨打。",
            "counts": True,
        },
    )
    assert await limiter.counted("call")
    assert await limiter.call_records() == [("success", "duration_limit", True)]


async def test_a_call_whose_time_is_up_as_its_turns_run_out_is_told_its_time_is_up(
    limiter: Limiter,
):
    outcome = await limiter.call(limits=CallLimits(duration=timedelta(minutes=10), turns=1))
    outcome.connected()
    outcome.turn_recorded()
    outcome.time_up()

    reached = outcome.limit_reached()

    assert reached is not None and reached.ended is not None
    assert reached.why == "这次通话已经满 10 分钟了"
    assert reached.ended["reason"] == "duration_limit"


async def test_a_call_without_limits_never_reaches_them(limiter: Limiter):
    outcome = await limiter.call()
    outcome.connected()
    for _ in range(100):
        outcome.turn_recorded()
    outcome.time_up()

    assert outcome.time_limit is None
    assert outcome.limit_reached() is None


async def test_a_call_hung_up_once_connected_counts_even_before_any_turn(limiter: Limiter):
    outcome = await limiter.call()
    outcome.connected()

    await outcome.settle()

    assert await limiter.counted("call")
    assert await limiter.call_records() == [("success", "left", True)]


async def test_the_browser_is_told_why_only_once(limiter: Limiter):
    """Told once, the call still hangs up at its limits, saying why, but tells nothing more."""
    outcome = await limiter.call(limits=CallLimits(duration=timedelta(minutes=10), turns=1))
    outcome.connected()
    outcome.turn_recorded()

    first = outcome.limit_reached()

    assert first is not None and first.ended is not None
    assert outcome.service_failed(None) is None
    assert outcome.service_failed("stt") is None
    outcome.time_up()
    assert outcome.limit_reached() == Ending("这次通话已经满 10 分钟了", None)


async def test_what_the_browser_was_told_is_how_the_call_is_settled(limiter: Limiter):
    """A turn recorded after the call was told it never connected does not make it count."""
    outcome = await limiter.call()
    outcome.connected()
    told = outcome.service_failed(None)
    outcome.turn_recorded()

    await outcome.settle()

    assert told is not None and told["counts"] is False
    assert not await limiter.counted("call")
    assert await limiter.call_records() == [("not_connected", "failed", True)]


async def test_a_call_that_could_not_be_set_up_is_given_back_at_most_once_saying_nothing(
    limiter: Limiter, monkeypatch: pytest.MonkeyPatch
):
    """`Use.give_back` would shrug off a second call itself; the outcome never makes one."""
    given_back: list[Use] = []
    take = limiter.usage.take

    async def take_counting(user_id: str, kind: UsageKind, exempt: bool = False) -> Use:
        use = await take(user_id, kind, exempt)
        give_back = use.give_back

        async def counted() -> None:
            given_back.append(use)
            await give_back()

        monkeypatch.setattr(use, "give_back", counted)
        return use

    monkeypatch.setattr(limiter.usage, "take", take_counting)
    outcome = await limiter.call()

    await outcome.set_up_failed()  # after hanging up the call, as `Calls.start` does
    await outcome.settle()
    await outcome.settle()

    assert len(given_back) == 1
    assert not await limiter.counted("call")
    assert await limiter.call_records() == [("not_connected", "failed", False)]


CallEnding = Callable[[CallOutcome], object]


def hung_up(outcome: CallOutcome) -> None:
    outcome.ended_by("hung_up")


def pipeline_failed(outcome: CallOutcome) -> None:
    outcome.ended_by("failed")


def left(outcome: CallOutcome) -> None:
    """Nothing ended it: the user left the conversation page."""


async def test_a_call_that_connected_is_kept_once_as_of_then_for_as_long_as_its_record_says(
    limiter: Limiter,
):
    """One that never connected, or never really did, is not."""
    never = await limiter.call()
    await never.settle()
    not_really = await limiter.call()
    not_really.connected()
    not_really.service_failed(None)
    await not_really.settle()
    connected_at = limiter.clock()
    outcome = await limiter.call()
    outcome.connected()
    limiter.clock.advance(minutes=1)
    outcome.turn_recorded()

    await outcome.settle()
    await outcome.settle()

    records, _ = await limiter.activity.since(connected_at - timedelta(hours=1))
    [lasted] = [r.duration_s for r in records if r.kind == "call" and r.result == "success"]
    assert limiter.kept == [(connected_at, lasted)]


@pytest.mark.parametrize(
    ("ending", "result"),
    [
        pytest.param(lambda o: o.service_failed(None), "failed", id="a service failing"),
        pytest.param(pipeline_failed, "failed", id="the call's pipeline failing"),
        pytest.param(hung_up, "left", id="hung up"),
        pytest.param(left, "left", id="left"),
    ],
)
async def test_a_turn_still_open_as_the_call_is_settled_is_settled_by_how_the_call_ended(
    limiter: Limiter, ending: CallEnding, result: Result
):
    """A failure that ends the call cuts the turn short (GLOSSARY.md「回合」); else the user
    left it. A turn settled already is not changed, and the open one settling later, once
    the call has been, changes nothing either."""
    outcome = await limiter.call()
    outcome.connected()
    done = outcome.turn_begun(own_llm=False)
    done.see(DONE)
    await done.over(cut_short_by_failure=False)
    outcome.turn_recorded()
    open_turn = outcome.turn_begun(own_llm=False)

    ending(outcome)
    await outcome.settle()
    await open_turn.over(cut_short_by_failure=result != "failed")

    assert await limiter.turn_records() == ["success", result]
