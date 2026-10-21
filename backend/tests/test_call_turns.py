"""Turns during a voice call, at the Pipecat stage that wraps the conversation engine.

Speech recognition, TTS and the transport are left out: the stage is fed what Soniox
and the browser's data channel would deliver, and what it sends on is inspected where
Fish Audio (text frames) and the browser (server messages) would receive it.

What is a follow-on and what interrupts the character is `TurnTaking`'s, tested in
`test_turn_taking.py`; here are the frames and the engine it is wired to.
"""

import asyncio
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial

import httpx
import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import (
    RTVIClientMessageFrame,
    RTVIServerMessageFrame,
)
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.workers.runner import WorkerRunner

from eidolon.ambience import AmbiencePicking
from eidolon.call import (
    SONIOX_FOLLOW_ON_TIMEOUT,
    Call,
    CallTurns,
    SegmentCompleteFrame,
    SegmentTextFrame,
)
from eidolon.call_outcome import CallLimits, CallOutcome
from eidolon.config import EmotionSettings, Settings
from eidolon.engine import MAX_INPUT_LENGTH
from eidolon.own_keys import BroughtKeys
from eidolon.store import Store
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    HANG,
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeServiceClients,
    Judging,
    build_faked,
    level,
)
from tests.test_affinity_prompt import FEELINGS, about_now
from tests.test_affinity_turns import value
from tests.test_text_turn import start_conversation

NOW = datetime(2026, 9, 25, 12, 30, tzinfo=UTC)
MANO_REPLY = ["<ja>ほわっ、", "おはようございます！</ja><zh>哇，早上好！</zh><ja>今日も</ja><zh>今天也</zh>"]


class CallUnderTest:
    """One call into a fresh conversation with Mano, and the API to read its record."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient, conversation_id: str):
        self._settings = settings
        self._client = client
        self._store = Store(settings.database_path)
        conversation = self._store.get_conversation(conversation_id)
        assert conversation is not None
        self.conversation = conversation

    async def run(
        self,
        llm: FakeLLM,
        frames: Sequence[Frame],
        *,
        muted: bool = False,
        limits: CallLimits = CallLimits(),
        memory: FakeBackgroundLLM | None = None,
        retrieval_judge: FakeJudge | None = None,
        embedder: FakeEmbedder | None = None,
        connected: bool = True,
        judge: FakeJudge | None = None,
        emotion: EmotionSettings | None = None,
        follow_on_timeout: float = SONIOX_FOLLOW_ON_TIMEOUT,
        affinity: Sequence[Judging] | None = None,
        cooled: tuple[float, datetime] | None = None,
        asmr: bool = False,
        ambience: AmbiencePicking | None = None,
    ) -> tuple[list[str], list[dict]]:
        """Run the frames through; returns the text sent to TTS and the messages sent to the browser."""
        down = await self.run_frames(
            llm, frames, muted=muted, limits=limits, memory=memory,
            retrieval_judge=retrieval_judge, embedder=embedder, connected=connected,
            judge=judge, emotion=emotion, follow_on_timeout=follow_on_timeout,
            affinity=affinity, cooled=cooled, asmr=asmr, ambience=ambience,
        )
        spoken = [f.text for f in down if isinstance(f, SegmentTextFrame)]
        messages = [f.data for f in down if isinstance(f, RTVIServerMessageFrame)]
        return spoken, messages

    async def run_frames(
        self,
        llm: FakeLLM,
        frames: Sequence[Frame],
        *,
        muted: bool = False,
        limits: CallLimits = CallLimits(),
        memory: FakeBackgroundLLM | None = None,
        retrieval_judge: FakeJudge | None = None,
        embedder: FakeEmbedder | None = None,
        connected: bool = True,
        judge: FakeJudge | None = None,
        emotion: EmotionSettings | None = None,
        follow_on_timeout: float = SONIOX_FOLLOW_ON_TIMEOUT,
        affinity: Sequence[Judging] | None = None,
        cooled: tuple[float, datetime] | None = None,
        asmr: bool = False,
        ambience: AmbiencePicking | None = None,
    ) -> Sequence[Frame]:
        """Run the frames through a call that, unless told otherwise, connects as it starts,
        remembering with the background model `memory`, if given, and retrieving with
        `retrieval_judge` and `embedder` (new ones unless given), with `judge` as the deployer's Jev judging
        each segment's emotion, if given, as `emotion` says, and its speech recognition
        taking `follow_on_timeout` at most to transcribe a follow-on. Given `affinity`, affinity is on, the judge model judging
        each turn's as it says (`FakeJudgeModel.judgings`). Given `cooled`, she falls into a
        cooldown elsewhere that many seconds into the call, ending when it says. `asmr`
        makes it an ASMR call; `ambience` picks its ambience when asked."""
        remembering = {} if memory is None else {
            "background_llm": memory, "retrieval_judge": retrieval_judge or FakeJudge(),
            "embedder": embedder or FakeEmbedder(),
            "reranker": FakeReranker(),
        }
        settings = self._settings if emotion is None else replace(self._settings, emotion=emotion)
        clients = FakeServiceClients()
        if affinity is not None:
            settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)
            clients.judge.judgings = list(affinity)
        backend = build_faked(
            settings, llm, clients=clients, clock=lambda: NOW, emotion_judge=judge,
            **remembering,
        )
        try:
            mano = backend.characters["mano"]
            admission = backend.admissions.call(self.conversation.user_id, "mano", BroughtKeys())
            call = Call(self.conversation, mano, admission.llms, muted, "standard", asmr=asmr)
            outcome = CallOutcome(
                admission, limits, backend.clock,
                partial(backend.store.keep_call, self.conversation.id),
            )
            turns = CallTurns(
                backend.engine, backend.store, call, outcome, follow_on_timeout,
                admission.keys.emotions, ambience,
            )
            if connected:
                turns.connected()
            if cooled is not None:
                asyncio.get_running_loop().call_later(cooled[0], turns.cooled, cooled[1])
            down, _ = await run_test(turns, frames_to_send=frames)
            outcome.settle()
        finally:
            await backend.aclose()
        return down

    async def fail_while_cancelling_is_held_up(
        self, llm: FakeLLM, said: str, cancel_timeout: float
    ) -> None:
        """Take a turn, the user having said `said`, then have a voice service fail and
        the call end, as `_LiveCall` ends it, while its reply is in progress; something
        upstream of `CallTurns` holds the cancelling up past `cancel_timeout` seconds, so
        the call is settled before the turn is over."""
        backend = build_faked(self._settings, llm, clock=lambda: NOW)
        try:
            mano = backend.characters["mano"]
            admission = backend.admissions.call(self.conversation.user_id, "mano", BroughtKeys())
            call = Call(self.conversation, mano, admission.llms, False, "standard")
            outcome = CallOutcome(
                admission, CallLimits(), backend.clock,
                partial(backend.store.keep_call, self.conversation.id),
            )
            turns = CallTurns(
                backend.engine, backend.store, call, outcome, SONIOX_FOLLOW_ON_TIMEOUT
            )
            worker = PipelineWorker(
                Pipeline([HoldsUpCancelling(), turns]),
                cancel_timeout_secs=cancel_timeout,
                cancel_on_idle_timeout=False,
            )
            started = asyncio.Event()

            @worker.event_handler("on_pipeline_started")
            async def on_started(worker: PipelineWorker, frame: Frame) -> None:
                started.set()

            running = asyncio.create_task(WorkerRunner(handle_sigint=False).run(worker))
            await asyncio.wait_for(started.wait(), timeout=1)
            turns.connected()
            await worker.queue_frame(spoken(said))
            await asyncio.sleep(0.3)  # the reply is in progress

            assert outcome.service_failed(None) is not None
            await worker.cancel()
            await running
            outcome.settle()
        finally:
            await backend.aclose()

    def turn_records(self) -> list[tuple[str, float | None]]:
        """Each turn's activity record: how it turned out, and how long the character
        took to start speaking, in milliseconds (「到开口」)."""
        with closing(sqlite3.connect(self._settings.database_path)) as db:
            return db.execute(
                "SELECT result, time_to_speak_ms FROM activity_records WHERE kind = 'turn'"
                " ORDER BY id"
            ).fetchall()

    async def record(self) -> list[tuple]:
        res = await self._client.get(f"/api/conversations/{self.conversation.id}/messages")
        return [(m["role"], m["text"], m["ja"], m["zh"]) for m in res.json()]

    async def said(self) -> list[str | None]:
        """What the user said in each recorded turn, None for the character's replies."""
        return [row[1] for row in await self.record()]

    def close(self) -> None:
        self._store.close()


class HoldsUpCancelling(FrameProcessor):
    """Never passes on a `CancelFrame`, as a processor stuck cancelling would not."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, CancelFrame):
            await asyncio.Event().wait()
        await self.push_frame(frame, direction)


@pytest.fixture
async def mano_call(make_client: ClientFactory, settings: Settings):
    client = make_client(FakeLLM())
    under_test = CallUnderTest(settings, client, await start_conversation(client, "mano"))
    yield under_test
    under_test.close()


def one_by_one(*frames: Frame) -> list[Frame]:
    """Space frames out as a person would. Client messages are system frames, which
    otherwise overtake transcriptions still queued in the pipeline."""
    spaced: list[Frame] = []
    for frame in frames:
        spaced += [frame, SleepFrame(0.05)]
    return spaced


def spoken(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text, "user", "2026-09-25T00:00:00Z", finalized=True)


def typed(text: str) -> RTVIClientMessageFrame:
    return RTVIClientMessageFrame(msg_id="m1", type="user-text", data={"text": text})


def mute(muted: bool) -> RTVIClientMessageFrame:
    return RTVIClientMessageFrame(msg_id="m2", type="mute", data={"muted": muted})


async def test_each_reply_is_one_tts_response(mano_call: CallUnderTest):
    down = await mano_call.run_frames(FakeLLM(MANO_REPLY), [spoken("早上好")])

    voiced = [type(f) for f in down if not isinstance(f, RTVIServerMessageFrame)]
    assert voiced[0] is LLMFullResponseStartFrame and voiced[-1] is LLMFullResponseEndFrame
    assert LLMFullResponseStartFrame not in voiced[1:-1]


def to_tts(down: Sequence[Frame]) -> list[tuple]:
    """What went to TTS: the replies, and each segment's Japanese under its reply and
    segment, till it was complete."""
    sent: list[tuple] = []
    for frame in down:
        if isinstance(frame, SegmentTextFrame):
            sent.append((frame.text, frame.reply, frame.segment))
        elif isinstance(frame, SegmentCompleteFrame):
            sent.append(("complete", frame.reply, frame.segment))
        elif isinstance(frame, LLMFullResponseStartFrame):
            sent.append(("reply",))
        elif isinstance(frame, TextFrame) and not isinstance(frame, TranscriptionFrame):
            sent.append((frame.text,))  # Japanese sent under no segment
    return sent


async def test_each_segment_of_each_reply_goes_to_tts_under_its_numbers(
    mano_call: CallUnderTest,
):
    llm = FakeLLM(MANO_REPLY, ["<ja>うん。</ja><zh>嗯。</zh>"])

    down = await mano_call.run_frames(llm, [spoken("早上好"), SleepFrame(0.5), spoken("在吗")])

    assert to_tts(down) == [
        ("reply",),
        ("ほわっ、", 1, 0), ("おはようございます！", 1, 0), ("complete", 1, 0),
        ("今日も", 1, 1), ("complete", 1, 1),
        ("reply",),
        ("うん。", 2, 0), ("complete", 2, 0),
    ]


async def test_muted_call_sends_subtitles_but_nothing_to_voice(mano_call: CallUnderTest):
    llm = FakeLLM(["<ja>一</ja><zh>一</zh>"], ["<ja>二</ja><zh>二</zh>"], ["<ja>三</ja><zh>三</zh>"])

    spoken_text, messages = await mano_call.run(
        llm,
        one_by_one(spoken("第一句"), mute(False), spoken("第二句"), mute(True), typed("第三句")),
        muted=True,
    )

    assert spoken_text == ["二"]
    assert [m["message"]["ja"] for m in messages if m["type"] == "done"] == ["一", "二", "三"]


async def test_blank_input_is_ignored(mano_call: CallUnderTest):
    spoken_text, messages = await mano_call.run(FakeLLM(), [spoken("  "), typed("")])

    assert spoken_text == [] and messages == []
    assert await mano_call.record() == []


INTERRUPTED_REPLY = ["<ja>ほわっ</ja><zh>哇</zh>", "<ja>今日", HANG]
"""A reply with one complete segment, then half of the next, then nothing, ever."""
INTERRUPTIBLE = [SleepFrame(0.1), BotStartedSpeakingFrame(), SleepFrame(0.3)]
"""The character starts speaking, and goes on until the second segment is started."""
INTERRUPTING = [*INTERRUPTIBLE, InterruptionFrame()]
"""The user interrupts her then."""


async def test_an_interruption_stops_the_reply_which_is_recorded_with_its_complete_segments(
    mano_call: CallUnderTest,
):
    memory = FakeBackgroundLLM()

    spoken_text, messages = await mano_call.run(
        FakeLLM(INTERRUPTED_REPLY, delay=0.3), [spoken("早上好"), *INTERRUPTING],
        memory=memory, limits=CallLimits(turns=1),
    )
    messages = without_emotions(messages)

    assert spoken_text == ["ほわっ", "今日"]
    assert kinds(messages) == ["user", "done", "ended"]  # it counts as a turn
    assert (messages[-2]["message"]["ja"], messages[-2]["message"]["zh"]) == ("ほわっ", "哇")
    assert await mano_call.record() == [
        ("user", "早上好", None, None), ("character", None, "ほわっ", "哇"),
    ]
    assert len(memory.prompts) == 1


async def test_what_interrupts_the_character_is_taken_up_at_once(mano_call: CallUnderTest):
    llm = FakeLLM(INTERRUPTED_REPLY, MANO_REPLY, delay=0.3)

    _, messages = await mano_call.run(
        llm, [spoken("早上好"), *INTERRUPTING, SleepFrame(0.1), spoken("在吗")]
    )

    assert said(messages) == ["早上好", "在吗"]
    assert await mano_call.said() == ["早上好", None, "在吗", None]


async def test_what_is_typed_once_the_reply_can_be_interrupted_interrupts_the_character(
    mano_call: CallUnderTest,
):
    llm = FakeLLM(INTERRUPTED_REPLY, MANO_REPLY, delay=0.3)

    down = await mano_call.run_frames(llm, [spoken("早上好"), *INTERRUPTIBLE, typed("在吗")])

    messages = [f.data for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert said(messages) == ["早上好", "在吗"]
    assert InterruptionFrame in frame_types(down)
    assert await mano_call.record() == [
        ("user", "早上好", None, None), ("character", None, "ほわっ", "哇"),
        ("user", "在吗", None, None),
        ("character", None, "ほわっ、おはようございます！今日も", "哇，早上好！今天也"),
    ]


SLOW_REPLY = ["<ja>ほわっ", "</ja><zh>哇</zh>", "<ja>今日も</ja><zh>今天也</zh>"]
"""A reply slow enough to be followed on: its first segment completes 0.3 s in."""


def said(messages: list[dict]) -> list[str]:
    return [m["text"] for m in messages if m["type"] == "user"]


def without_emotions(messages: list[dict]) -> list[dict]:
    """The messages but her emotions, judged in calls that remember, the judge model
    retrieval asks being at hand (see `test_call_emotions.py`)."""
    return [m for m in messages if m["type"] != "emotion"]


async def test_what_is_said_before_the_character_speaks_joins_the_turn(mano_call: CallUnderTest):
    # Slower than the others: her first reply must have begun, taking the first of the
    # script, before the user goes on, and a slow runner may take a while to begin it.
    llm = FakeLLM(SLOW_REPLY, MANO_REPLY, delay=0.6)
    memory = FakeBackgroundLLM()

    _, messages = await mano_call.run(
        llm,
        [spoken("早上好"), SleepFrame(0.3), InterruptionFrame(), SleepFrame(0.1), spoken("在吗")],
        memory=memory,
    )
    messages = without_emotions(messages)

    assert said(messages) == ["早上好", "早上好\n在吗"]
    assert kinds(messages) == ["user", "user", "done"]
    assert llm.prompts[-1][-1].content == "早上好\n在吗"
    assert await mano_call.record() == [
        ("user", "早上好\n在吗", None, None),
        ("character", None, "ほわっ、おはようございます！今日も", "哇，早上好！今天也"),
    ]
    assert len(memory.prompts) == 1  # the reply dropped was not written down


async def test_a_follow_on_drops_what_the_turn_was_looking_up_and_looks_up_the_whole_of_it(
    mano_call: CallUnderTest,
):
    llm, judge, embedder = FakeLLM(MANO_REPLY), FakeJudge(), FakeEmbedder()
    judge.hold("早上好")  # still judging, and looking up, when the user goes on
    embedder.hanging = {"早上好"}

    _, messages = await mano_call.run(
        llm,
        [spoken("早上好"), SleepFrame(0.1), InterruptionFrame(), SleepFrame(0.1), spoken("在吗")],
        memory=FakeBackgroundLLM(), retrieval_judge=judge, embedder=embedder,
    )

    assert said(without_emotions(messages)) == ["早上好", "早上好\n在吗"]
    assert [text for _, text in judge.asked] == ["早上好", "早上好\n在吗"]
    assert embedder.abandoned == ["早上好"]
    assert embedder.queries == ["早上好", "早上好\n在吗"]
    assert len(llm.prompts) == 1
    assert await mano_call.said() == ["早上好\n在吗", None]


async def test_a_turn_with_a_follow_on_changes_affinity_as_the_whole_of_it_is_judged(
    mano_call: CallUnderTest, settings: Settings
):
    llm = FakeLLM(SLOW_REPLY, MANO_REPLY, delay=0.3)

    _, messages = await mano_call.run(
        llm,
        [spoken("早上好"), SleepFrame(0.1), InterruptionFrame(), SleepFrame(0.1), spoken("在吗")],
        affinity=[level(0), level(4)],
    )
    done = without_emotions(messages)[-1]

    # The reply dropped, what it was judged at went with it: she knew it as it was judged.
    assert value(settings.database_path) == 52
    assert FEELINGS[0] in about_now(llm, 0)
    assert FEELINGS[4] in about_now(llm, 1)
    assert FEELINGS[0] not in about_now(llm, 1)
    assert done["message"]["affinity"] == {
        "up": True, "tier_changed": False, "tier": "trust", "cooldown": False, "thawed": False
    }
    assert done["affinity"] == {"tier": "trust", "cooldown_until": None}


async def test_what_is_typed_before_the_character_speaks_joins_the_turn_and_stops_its_voice(
    mano_call: CallUnderTest,
):
    """The turn is replied to afresh, going to TTS under a number of its own."""
    llm = FakeLLM(SLOW_REPLY, ["<ja>うん。</ja><zh>嗯。</zh>"], delay=0.3)

    down = await mano_call.run_frames(llm, [spoken("早上好"), SleepFrame(0.1), typed("在吗")])

    messages = [f.data for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert said(messages) == ["早上好", "早上好\n在吗"]
    assert await mano_call.said() == ["早上好\n在吗", None]
    voiced = [type(f) for f in down if not isinstance(f, RTVIServerMessageFrame)]
    assert voiced[:4] == [
        LLMFullResponseStartFrame, SegmentTextFrame, LLMFullResponseEndFrame, InterruptionFrame
    ]
    assert to_tts(down) == [
        ("reply",), ("ほわっ", 1, 0), ("reply",), ("うん。", 2, 0), ("complete", 2, 0)
    ]


async def test_a_follow_on_never_transcribed_has_the_turn_replied_to_again_in_time(
    mano_call: CallUnderTest,
):
    llm = FakeLLM(SLOW_REPLY, MANO_REPLY, delay=0.3)

    _, messages = await mano_call.run(
        llm, [spoken("早上好"), SleepFrame(0.1), InterruptionFrame(), SleepFrame(0.1)],
        follow_on_timeout=0.3,
    )

    assert said(messages) == ["早上好", "早上好"]
    assert await mano_call.said() == ["早上好", None]


def frame_types(frames: Sequence[Frame]) -> list[type[Frame]]:
    return [type(f) for f in frames]


def kinds(messages: list[dict]) -> list[str]:
    return [m["type"] for m in messages if m["type"] != "delta"]


async def test_call_hangs_up_after_its_last_allowed_turn(mano_call: CallUnderTest):
    llm = FakeLLM(MANO_REPLY, MANO_REPLY)

    spoken_text, messages = await mano_call.run(
        llm,
        one_by_one(spoken("早上好"), typed("在吗"), spoken("还在吗")),
        limits=CallLimits(turns=2),
    )

    assert kinds(messages) == ["user", "done", "user", "done", "ended"]
    assert messages[-1]["reason"] == "turn_limit"
    assert "".join(spoken_text).count("ほわっ") == 2  # the last reply is still voiced
    assert len(await mano_call.record()) == 4


async def test_call_hangs_up_when_time_is_up_once_the_reply_in_progress_is_done(
    mano_call: CallUnderTest,
):
    llm = FakeLLM(MANO_REPLY, delay=0.2)

    _, messages = await mano_call.run(
        llm,
        [spoken("早上好"), SleepFrame(0.6), spoken("还在吗")],
        limits=CallLimits(duration=timedelta(seconds=0.1)),
    )

    assert kinds(messages) == ["user", "done", "ended"]
    assert messages[-1]["reason"] == "duration_limit"
    assert len(await mano_call.record()) == 2


async def test_a_calls_time_is_counted_from_when_it_connects(mano_call: CallUnderTest):
    """The call screen counts from 接通 (GLOSSARY.md), so the time limit does too."""
    _, messages = await mano_call.run(
        FakeLLM(MANO_REPLY),
        [SleepFrame(0.3), spoken("早上好")],
        limits=CallLimits(duration=timedelta(seconds=0.1)),
        connected=False,
    )

    assert kinds(messages) == ["user", "done"]  # never connected, so time never ran out


async def test_turns_that_fail_or_are_rejected_do_not_count_and_the_call_goes_on(
    mano_call: CallUnderTest,
):
    llm = FakeLLM(["  "], MANO_REPLY)  # the first reply is empty
    too_long = "啊" * (MAX_INPUT_LENGTH + 1)

    spoken_text, messages = await mano_call.run(
        llm,
        one_by_one(spoken(too_long), typed(too_long), spoken("在吗"), spoken("在吗？")),
        limits=CallLimits(turns=1),
    )

    assert kinds(messages) == ["user", "error"] * 3 + ["user", "done", "ended"]
    assert messages[-1]["reason"] == "turn_limit"
    assert len(llm.prompts) == 2  # what was too long never reached the model
    assert "".join(spoken_text).count("ほわっ") == 1
    assert await mano_call.said() == ["在吗？", None]

