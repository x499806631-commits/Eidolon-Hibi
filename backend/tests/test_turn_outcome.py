"""How one turn turns out (GLOSSARY.md「回合」「开口」), text turn or turn of a call alike:
from the events it is told and whether a failure cut it short, the one result it is
settled with, and when the character began to speak, as its activity record says.

The turn is only its events here, told as the text turn and `CallTurns` tell them; its
record is a real one, settled as the text turn's `Admission` or the call's turn record
would be told.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from eidolon.activity import ActivityLog, Recording, Result
from eidolon.data_access import Database
from eidolon.engine import TurnDone, TurnEvent, TurnFailed, TurnRejected, UserSaid
from eidolon.reply_format import SegmentDelta
from eidolon.store import Message
from eidolon.turn_outcome import TurnOutcome
from tests.fakes import FakeClock

SAID = UserSaid("在吗")
DELTA = SegmentDelta(0, "zh", "好的")
DONE = TurnDone(Message(1, "character", None, "はいっ", "好的", "2026-10-08T00:00:00+00:00"))
FAILED = TurnFailed("角色暂时无法回复，请稍后再试。")
REJECTED = TurnRejected("随便怎么说都行")


class Turn:
    """One turn's `TurnOutcome`, and each result it was settled with."""

    def __init__(self, activity: ActivityLog) -> None:
        self.recording: Recording = activity.begin(
            "turn", "producer", "mano", own_llm=False, exempt=False
        )
        self.settled: list[Result] = []

        async def settle(result: Result) -> None:
            self.settled.append(result)

        self.outcome = TurnOutcome(self.recording, settle)


@pytest.fixture
async def activity(tmp_path: Path) -> AsyncIterator[ActivityLog]:
    db = Database(tmp_path / "eidolon.db")
    try:
        yield ActivityLog(db, FakeClock())
    finally:
        await db.close()


@pytest.mark.parametrize(
    ("events", "cut_short_by_failure", "result"),
    [
        ([SAID, DELTA, DONE], False, "success"),
        ([SAID, DELTA, DONE], True, "success"),
        ([SAID, REJECTED], False, "rejected"),
        ([SAID, DELTA, FAILED], False, "failed"),
        ([SAID, DELTA], False, "left"),
        ([SAID, DELTA], True, "failed"),
        # Replied to afresh after a follow-on: the last reply's result is the turn's.
        ([SAID, FAILED, SAID, DELTA, DONE], False, "success"),
        ([SAID, DONE, SAID, DELTA], False, "success"),
    ],
)
async def test_a_turn_turns_out_as_its_last_result_or_as_what_cut_it_short(
    activity: ActivityLog,
    events: list[TurnEvent],
    cut_short_by_failure: bool,
    result: Result,
):
    turn = Turn(activity)

    for event in events:
        turn.outcome.see(event)
    await turn.outcome.over(cut_short_by_failure=cut_short_by_failure)

    assert turn.settled == [result]


async def test_a_turn_is_settled_only_the_first_time_it_is_over(activity: ActivityLog):
    turn = Turn(activity)

    turn.outcome.see(SAID)
    await turn.outcome.over(cut_short_by_failure=False)
    turn.outcome.see(DONE)
    await turn.outcome.over(cut_short_by_failure=True)

    assert turn.settled == ["left"]


async def test_the_character_began_to_speak_the_first_time_she_was_said_to(activity: ActivityLog):
    turn = Turn(activity)

    turn.outcome.see(SAID)
    turn.outcome.see(DELTA)  # the reply appearing is not her speaking, unless said to be
    before = turn.recording.time_to_speak_ms
    turn.outcome.began_speaking()
    first = turn.recording.time_to_speak_ms
    turn.outcome.began_speaking()

    assert before is None
    assert first is not None and first >= 0
    assert turn.recording.time_to_speak_ms == first
