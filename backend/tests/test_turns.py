"""The turn protocol, driven through the conversation engine's interface.

Every turn opens with `user` and closes with `done` or `error`, whether it arrives as a
text turn or during a call; the SSE and call tests only cover how each carries it.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import pytest

from eidolon.config import Settings
from eidolon.engine import MAX_INPUT_LENGTH
from eidolon.llm import ToolCall
from eidolon.own_keys import BroughtKeys
from eidolon.reply_format import SegmentComplete, SpeechDelta
from eidolon.store import Conversation, Store
from tests.conftest import store_in
from tests.fakes import (
    HANG,
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    build_faked,
)

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
REPLY = ["<ja>ほわっ、</ja><zh>哇，</zh>"]


class Turns:
    """A conversation with Mano and the engine that takes its turns."""

    def __init__(self, settings: Settings, store: Store, conversation: Conversation) -> None:
        self._settings = settings
        self._store = store
        self.conversation = conversation

    async def take(
        self,
        llm: FakeLLM,
        text: str,
        *,
        in_house: bool = False,
        stop_after: int | None = None,
        **services: Any,
    ) -> list[dict]:
        """Take one turn; returns its events as they go out on the wire, and with
        `in_house` also those that stay on the server. With `stop_after`, the turn is told
        to stop a moment after that segment is complete, by when it is waiting on the
        model or a tool. Any other keyword is a script for the deployer's models or an
        outside service to use (see `fakes.build_faked`)."""
        backend = build_faked(self._settings, llm, clock=lambda: NOW, **services)
        stop = asyncio.Event()
        events = []
        try:
            llms = backend.own_keys.for_turn(BroughtKeys()).llms
            async for event in backend.engine.turn(self.conversation, text, llms, stop=stop):
                events.append(event)
                if isinstance(event, SegmentComplete) and event.segment == stop_after:
                    asyncio.get_running_loop().call_later(0.05, stop.set)
        finally:
            await backend.aclose()
        return [
            asdict(e) for e in events if in_house or not isinstance(e, SegmentComplete | SpeechDelta)
        ]

    async def record(self) -> list[tuple]:
        return [
            (m.role, m.text, m.ja) for m in await self._store.list_messages(self.conversation.id)
        ]


@asynccontextmanager
async def turns_in(settings: Settings) -> AsyncIterator[Turns]:
    """Turns in a new conversation of a new user with Mano."""
    async with store_in(settings.database_path) as store:
        user = await store.create_user("producer")
        yield Turns(settings, store, await store.create_conversation(user.id, "mano"))


@pytest.fixture
async def turns(settings: Settings) -> AsyncIterator[Turns]:
    async with turns_in(settings) as turns:
        yield turns


def types(events: list[dict]) -> list[str]:
    return [e["type"] for e in events]


async def test_input_longer_than_the_limit_is_rejected_without_reaching_the_model(turns: Turns):
    llm = FakeLLM(REPLY)

    events = await turns.take(llm, "啊" * (MAX_INPUT_LENGTH + 1))

    assert types(events) == ["user", "error"]
    assert events[-1]["message"] == f"这句话太长了，最多 {MAX_INPUT_LENGTH} 字。"
    assert llm.prompts == []
    assert await turns.record() == []


async def test_blank_input_is_rejected_without_reaching_the_model(turns: Turns):
    llm = FakeLLM(REPLY)

    events = await turns.take(llm, " \n\t")

    assert events == [{"type": "user", "text": ""}, {"type": "error", "message": "没有收到要说的话。"}]
    assert llm.prompts == []
    assert await turns.record() == []


async def test_a_turn_opens_with_what_the_user_said_and_closes_with_the_recorded_reply(
    turns: Turns,
):
    events = await turns.take(FakeLLM(REPLY), "  早上好\n")

    assert types(events) == ["user", "delta", "delta", "done"]
    assert events[0]["text"] == "早上好"
    assert events[-1]["message"]["ja"] == "ほわっ、"
    assert await turns.record() == [("user", "早上好", None), ("character", None, "ほわっ、")]


async def test_a_segment_is_complete_once_its_subtitle_closes_the_next_opens_or_the_model_stops(
    turns: Turns,
):
    llm = FakeLLM(["<ja>一</ja>", "<zh>一</zh>", "<ja>二</ja>", "<ja>三</ja><zh>三"])

    events = await turns.take(llm, "早上好", in_house=True)

    assert [(e["type"], e.get("segment")) for e in events[1:]] == [
        ("delta", 0), ("speech-delta", 0), ("delta", 0), ("segment-complete", 0),
        ("delta", 1), ("speech-delta", 1), ("segment-complete", 1),
        ("delta", 2), ("speech-delta", 2), ("delta", 2), ("segment-complete", 2),
        ("done", None),
    ]


@pytest.mark.parametrize(
    "llm",
    [FakeLLM(), FakeLLM(["  \n"])],  # the model call fails; the model says nothing
    ids=["model fails", "empty reply"],
)
async def test_a_failed_turn_closes_with_an_error_and_leaves_no_record(
    turns: Turns, llm: FakeLLM, caplog: pytest.LogCaptureFixture
):
    events = await turns.take(llm, "在吗")

    assert types(events)[0] == "user"
    assert events[-1] == {"type": "error", "message": "角色暂时无法回复，请稍后再试。"}
    assert await turns.record() == []
    assert "turn failed" in caplog.text  # what went wrong stays in the server log


async def test_a_turn_told_to_stop_is_recorded_with_only_its_complete_segments(turns: Turns):
    memory = FakeBackgroundLLM()
    llm = FakeLLM(["<ja>ほわっ</ja><zh>哇</zh><ja>今日", "も</ja>", HANG])

    events = await turns.take(
        llm, "早上好", stop_after=0,
        background_llm=memory, retrieval_judge=FakeJudge(), embedder=FakeEmbedder(), reranker=FakeReranker(),
    )

    assert events[-1]["type"] == "done"
    assert (events[-1]["message"]["ja"], events[-1]["message"]["zh"]) == ("ほわっ", "哇")
    assert await turns.record() == [("user", "早上好", None), ("character", None, "ほわっ")]
    assert len(memory.prompts) == 1  # what the user revealed is written down as ever


async def test_a_turn_told_to_stop_in_its_tool_round_is_recorded_with_its_complete_segments(
    turns: Turns,
):
    llm = FakeLLM(
        ["<ja>ええと</ja><zh>嗯</zh>", ToolCall("call_1", "recall_story", {"query": "公園"})],
        ["<ja>公園で</ja><zh>在公园</zh>", "<ja>歌", HANG],
    )

    events = await turns.take(
        llm, "那次在公园怎么了？", stop_after=1,
        retrieval_judge=FakeJudge(answer="nothing"), embedder=FakeEmbedder(), reranker=FakeReranker(),
        background_llm=FakeBackgroundLLM(),
    )

    assert len(llm.prompts) == 2  # the tool was called and the model asked again
    assert events[-1]["type"] == "done"
    assert (await turns.record())[-1] == ("character", None, "ええと公園で")


class HangingEmbedder(FakeEmbedder):
    """Never answers the query `hang_on`, as a service that has stopped responding."""

    def __init__(self, hang_on: str) -> None:
        super().__init__()
        self._hang_on = hang_on

    async def embed_query(self, text: str) -> list[float]:
        if text == self._hang_on:
            await asyncio.Event().wait()
        return await super().embed_query(text)


async def test_a_turn_told_to_stop_while_a_tool_runs_is_recorded_with_its_complete_segments(
    turns: Turns,
):
    llm = FakeLLM(
        ["<ja>ええと</ja><zh>嗯</zh>", ToolCall("call_1", "recall_story", {"query": "鳩の歌"})]
    )

    events = await turns.take(
        llm, "那次和鸽子唱歌怎么了？", stop_after=0,
        retrieval_judge=FakeJudge(answer="nothing"), embedder=HangingEmbedder("鳩の歌"),
        reranker=FakeReranker(), background_llm=FakeBackgroundLLM(),
    )

    assert len(llm.prompts) == 1  # the model was not asked again
    assert events[-1]["type"] == "done"
    assert (await turns.record())[-1] == ("character", None, "ええと")
