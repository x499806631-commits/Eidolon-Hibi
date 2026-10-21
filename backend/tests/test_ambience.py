"""The ambience picked for a call (环境音「自动」): what the judge model is told and asked,
how its answer becomes a kind to play, and when, during a call, it is picked and the
browser told (`ambience` server messages)."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pipecat.frames.frames import Frame
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.tests.utils import SleepFrame

from eidolon.ambience import (
    PICK_EVERY,
    AmbienceKind,
    AmbienceMoment,
    AmbiencePicking,
    ModelAmbienceJudge,
    decide,
    moment_state,
)
from eidolon.characters import Character
from eidolon.judge import JudgeError
from eidolon.store import Message
from tests.fakes import FakeLLM
from tests.test_retrieval_services import MANO
from tests.test_call_turns import (
    NOW,
    CallUnderTest,
    mano_call,  # noqa: F401  # the fixture
    one_by_one,
    spoken,
)

MADRID = ZoneInfo("Europe/Madrid")


def mano() -> Character:
    return MANO


def message(role: str, text: str) -> Message:
    if role == "user":
        return Message(1, "user", text, None, None, "2026-10-21T00:00:00Z")
    return Message(2, "character", None, "<ja>…</ja>", text, "2026-10-21T00:00:00Z")


def test_the_judge_is_told_the_time_the_season_the_call_and_the_latest_messages():
    history = [message("user", f"第{i}句") for i in range(8)] + [message("character", "外面在下雨呢")]
    moment = AmbienceMoment(mano(), datetime(2026, 10, 21, 1, 54, tzinfo=MADRID), True, history)

    state = moment_state(moment)

    assert state["现在"] == "10月21日 星期三 凌晨 01:54"
    assert state["季节"] == "秋天"
    assert "ASMR" in str(state["通话"])
    assert state["上文"] == [f"制作人：第{i}句" for i in range(3, 8)] + [
        f"{mano().name}：外面在下雨呢"
    ]


def test_with_nothing_said_yet_the_judge_is_told_so():
    moment = AmbienceMoment(mano(), datetime(2026, 7, 1, 21, 0, tzinfo=MADRID), False, [])

    state = moment_state(moment)

    assert state["上文"] == "（还没有聊什么）"
    assert state["季节"] == "夏天" and state["通话"] == "普通的通话"


class AskedJudgeModel:
    def __init__(self, answers: Any) -> None:
        self.answers = answers
        self.asked: list[tuple[dict, dict]] = []

    async def ask(self, state: dict[str, Any], questions: dict[str, dict]) -> dict[str, Any]:
        self.asked.append((state, questions))
        return self.answers

    async def aclose(self) -> None:
        pass


async def test_the_model_is_asked_one_choice_and_read_by_option_name():
    model = AskedJudgeModel(
        {"ambience": {"probabilities": {"暖炉": 0.6, "雨声": 0.3, "安静": 0.1, "别的": 0.5}}}
    )
    moment = AmbienceMoment(mano(), datetime(2026, 12, 24, 22, 0, tzinfo=MADRID), False, [])

    read = await ModelAmbienceJudge(model).judge_ambience(moment)

    assert read == {"fireplace": 0.6, "rain": 0.3, "none": 0.1}
    [(_, questions)] = model.asked
    assert questions["ambience"]["type"] == "choice"
    assert len(questions["ambience"]["criteria"]) == 8


@pytest.mark.parametrize("answers", [{}, {"ambience": {"probabilities": {"别的": 1}}}])
async def test_an_answer_that_cannot_be_read_is_a_judge_error(answers: dict):
    moment = AmbienceMoment(mano(), datetime(2026, 12, 24, 22, 0, tzinfo=MADRID), False, [])

    with pytest.raises(JudgeError):
        await ModelAmbienceJudge(AskedJudgeModel(answers)).judge_ambience(moment)


def test_with_nothing_playing_the_likeliest_plays():
    assert decide({"rain": 0.3, "cafe": 0.35, "none": 0.35 - 0.01}, None) == "cafe"


def test_what_plays_gives_way_only_to_a_kind_clearly_likelier():
    assert decide({"rain": 0.3, "fireplace": 0.45}, "rain") is None
    assert decide({"rain": 0.3, "fireplace": 0.55}, "rain") == "fireplace"
    assert decide({"rain": 0.6, "fireplace": 0.3}, "rain") is None
    assert decide({"fireplace": 0.3}, "waves") == "fireplace"


class ScriptedAmbience:
    """Answers each question with the next of `answers`: probabilities, None to fail, or
    "hang" to never answer."""

    def __init__(self, *answers: Mapping[AmbienceKind, float] | str | None) -> None:
        self.answers = list(answers)
        self.moments: list[AmbienceMoment] = []

    async def judge_ambience(self, moment: AmbienceMoment) -> Mapping[AmbienceKind, float]:
        self.moments.append(moment)
        quiet: Mapping[AmbienceKind, float] = {"none": 1.0}
        answer = self.answers.pop(0) if self.answers else quiet
        if answer is None:
            raise JudgeError("挂了")
        if answer == "hang":
            await asyncio.Event().wait()
        assert not isinstance(answer, str)
        return answer


@pytest.mark.parametrize("answer", [None, "hang"], ids=["failed", "too slow"])
async def test_a_pick_that_fails_or_takes_too_long_changes_nothing(answer: str | None):
    picking = AmbiencePicking(ScriptedAmbience(answer), timeout=0.05)
    moment = AmbienceMoment(mano(), datetime(2026, 12, 24, 22, 0, tzinfo=MADRID), False, [])

    assert await picking.pick(moment, "rain") is None


def auto(on: bool) -> RTVIClientMessageFrame:
    return RTVIClientMessageFrame(msg_id="a1", type="ambience-auto", data={"on": on})


def kinds(messages: list[dict]) -> list[str]:
    return [m["kind"] for m in messages if m["type"] == "ambience"]


SETTLE = SleepFrame(0.2)


def replies(n: int) -> FakeLLM:
    return FakeLLM(*[[f"<ja>{i}</ja><zh>{i}</zh>"] for i in range(n)])


def turns(n: int) -> list[Frame]:
    frames: list[Frame] = []
    for i in range(n):
        frames += [spoken(f"第{i}句"), SleepFrame(0.3)]
    return frames


async def test_nothing_is_picked_unless_the_browser_asks(mano_call: CallUnderTest):
    judge = ScriptedAmbience({"rain": 1.0})

    _, messages = await mano_call.run(
        replies(1), [*turns(1), SETTLE], ambience=AmbiencePicking(judge, 1, lambda: NOW)
    )

    assert kinds(messages) == [] and judge.moments == []


async def test_asked_it_is_picked_at_once_with_the_call_and_the_producers_time(
    mano_call: CallUnderTest,
):
    judge = ScriptedAmbience({"rain": 0.7, "cafe": 0.3})

    _, messages = await mano_call.run(
        replies(0), [auto(True), SETTLE], asmr=True,
        ambience=AmbiencePicking(judge, 1, lambda: NOW),
    )

    assert kinds(messages) == ["rain"]
    [moment] = judge.moments
    assert moment.asmr and moment.character.id == "mano"
    assert moment.local == NOW and moment.local.tzinfo is not UTC  # in the producer's zone


async def test_it_is_picked_again_every_few_turns_and_changes_only_when_clearly_better(
    mano_call: CallUnderTest,
):
    judge = ScriptedAmbience(
        {"rain": 0.6, "cafe": 0.4},
        {"rain": 0.45, "cafe": 0.55},  # not clearly better: rain stays
        {"rain": 0.2, "cafe": 0.8},
    )

    _, messages = await mano_call.run(
        replies(2 * PICK_EVERY),
        one_by_one(auto(True), SETTLE, *turns(2 * PICK_EVERY), SETTLE),
        ambience=AmbiencePicking(judge, 1, lambda: NOW),
    )

    assert kinds(messages) == ["rain", "cafe"]
    assert len(judge.moments) == 3
    assert [m.text for m in judge.moments[1].history if m.role == "user"][-1] == (
        f"第{PICK_EVERY - 1}句"
    )


async def test_turned_off_it_is_picked_no_more(mano_call: CallUnderTest):
    judge = ScriptedAmbience({"rain": 1.0}, {"cafe": 1.0})

    _, messages = await mano_call.run(
        replies(PICK_EVERY),
        one_by_one(auto(True), SETTLE, auto(False), *turns(PICK_EVERY), SETTLE),
        ambience=AmbiencePicking(judge, 1, lambda: NOW),
    )

    assert kinds(messages) == ["rain"] and len(judge.moments) == 1


async def test_turned_on_again_it_starts_afresh(mano_call: CallUnderTest):
    judge = ScriptedAmbience({"rain": 1.0}, {"rain": 1.0})

    _, messages = await mano_call.run(
        replies(0),
        one_by_one(auto(True), SETTLE, auto(False), auto(True), SETTLE),
        ambience=AmbiencePicking(judge, 1, lambda: NOW),
    )

    assert kinds(messages) == ["rain", "rain"]  # the browser stopped it, so it is told again
