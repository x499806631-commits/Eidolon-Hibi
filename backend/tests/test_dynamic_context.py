"""What changes from turn to turn goes after the history, and the history the chat model
reads is cut back in steps, so each turn's prompt opens as the one before it did and the
service can cache all of it but the end (ADR-0020)."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from eidolon.config import Settings
from eidolon.llm import ChatMessage
from eidolon.reply_format import FORMAT_INSTRUCTIONS
from tests.conftest import ClientFactory
from tests.fakes import (
    FakeBackgroundLLM,
    FakeClock,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeServiceClients,
    context_of,
)
from tests.test_affinity_prompt import LAST_TALKED
from tests.test_call_turns import MANO_REPLY, CallUnderTest, spoken
from tests.test_reach_outs import connected_call, last_contact
from tests.test_text_turn import send_turn, start_conversation

PROFILE = {
    "form_of_address": "小林",
    "gender": "female",
    "timezone": "Asia/Tokyo",
    "birthday": {"month": 5, "day": 3},
}


def reply(n: int) -> list[str]:
    return [f"<ja>返事{n}</ja><zh>回复{n}</zh>"]


def shape(prompt: list[ChatMessage]) -> list[tuple[str, str]]:
    """What the model reads of `prompt`, without what marks a part for caching."""
    return [(m.role, m.content) for m in prompt]


async def test_what_changes_each_turn_comes_after_the_history(make_client: ClientFactory):
    llm = FakeLLM(reply(1), reply(2))
    client = make_client(llm)
    await client.put("/api/profile", json=PROFILE)
    conversation = await start_conversation(client)

    await send_turn(client, conversation, "第1句")
    await send_turn(client, conversation, "第2句")

    shared, producer, *rest = llm.prompts[-1]
    assert shared.cache_breakpoint and shared.content.endswith(FORMAT_INSTRUCTIONS)
    assert producer.role == "system"
    assert "小林" in producer.content and "5月3日" in producer.content
    assert "现在是" not in producer.content
    assert [m.role for m in rest] == ["user", "assistant", "system", "user"]
    assert rest[-2].content.startswith("# 此刻\n现在是 ")
    assert "现在是" not in "".join(m.content for m in rest if m.role != "system")


async def test_a_turn_opens_as_the_turn_before_it_did(make_client: ClientFactory):
    """The previous turn's prompt, but for what changes each turn and what the user said,
    is how the next one opens: the service's cache of it carries over."""
    clock = FakeClock()
    background = FakeBackgroundLLM(
        '{"memories": ["制作人养了一只猫"]}', '{"memories": ["制作人在学吉他"]}'
    )
    llm = FakeLLM(reply(1), reply(2), reply(3))
    client = make_client(
        llm, clock=clock, clients=FakeServiceClients(), background_llm=background,
        retrieval_judge=FakeJudge(answer="memories"), embedder=FakeEmbedder(),
        reranker=FakeReranker(),
    )
    await client.put("/api/profile", json=PROFILE)
    conversation = await start_conversation(client)

    for n in range(1, 4):
        await send_turn(client, conversation, f"第{n}句")
        clock.advance(minutes=3)

    assert "制作人养了一只猫" in context_of(llm.prompts[1])  # what changes does change
    for before, after in zip(llm.prompts, llm.prompts[1:], strict=False):
        opening = shape(before[:-2])
        assert shape(after[: len(opening)]) == opening
        assert context_of(before) != context_of(after)


@pytest.mark.parametrize(
    ("before", "carried"),
    # N = 4, so a step of 2: T = N, N + 1 (= N + step - 1), N + step, N + 2 × step.
    [(4, 4), (5, 5), (6, 4), (8, 4)],
)
async def test_the_history_is_cut_back_in_steps(
    make_client: ClientFactory, settings: Settings, before: int, carried: int
):
    llm = FakeLLM(*[reply(n) for n in range(1, before + 2)])
    client = make_client(llm, settings=replace(settings, history_turns=4))
    conversation = await start_conversation(client)

    for n in range(1, before + 2):
        await send_turn(client, conversation, f"第{n}句")

    said = [m.content for m in llm.prompts[-1] if m.role == "user"]
    assert said == [f"第{n}句" for n in range(before - carried + 1, before + 2)]


async def test_with_one_turn_the_history_steps_by_one(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(*[reply(n) for n in range(1, 4)])
    client = make_client(llm, settings=replace(settings, history_turns=1))
    conversation = await start_conversation(client)

    for n in range(1, 4):
        await send_turn(client, conversation, f"第{n}句")

    assert [m.content for m in llm.prompts[-1] if m.role == "user"] == ["第2句", "第3句"]


async def test_without_a_limit_the_whole_history_is_carried(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(*[reply(n) for n in range(1, 8)])
    client = make_client(llm, settings=replace(settings, history_turns=None))
    conversation = await start_conversation(client)

    for n in range(1, 8):
        await send_turn(client, conversation, f"第{n}句")

    assert len([m for m in llm.prompts[-1] if m.role == "user"]) == 7



TYPING = "制作人正在用文字和你聊天"
IN_A_CALL = "制作人正在和你打电话"


async def test_she_is_told_whether_the_turn_is_typed_or_in_a_call_after_what_is_cached(
    make_client: ClientFactory, settings: Settings
):
    """Typed or in a call, the turn's prompt says which, where what changes each turn is,
    and opens as the other does, for a service to cache it."""
    typed_llm = FakeLLM(reply(1))
    client = make_client(typed_llm)
    conversation = await start_conversation(client, "mano")
    await send_turn(client, conversation, "早上好")
    call = await CallUnderTest.of(settings, client, conversation)
    call_llm = FakeLLM(MANO_REPLY)
    await call.run(call_llm, [spoken("早上好")])

    [typed], [in_call] = typed_llm.prompts, call_llm.prompts
    assert TYPING in context_of(typed) and IN_A_CALL not in context_of(typed)
    assert IN_A_CALL in context_of(in_call) and TYPING not in context_of(in_call)
    assert LAST_TALKED not in context_of(in_call)  # a call's turns are not told when
    assert in_call[:2] == typed[:2]
    assert not any(TYPING in m.content or IN_A_CALL in m.content for m in typed[:2])



ASMR = "# ASMR 模式通话"


async def test_an_asmr_calls_turns_are_told_to_speak_softly_whispering_as_the_voice_reads_it(
    make_client: ClientFactory, settings: Settings
):
    """Told after what is cached, as the rest that changes from turn to turn; an ordinary
    call's are not. Voiced with the default model, she is told to whisper with cues."""
    client = make_client(FakeLLM())
    conversation = await start_conversation(client, "mano")
    told = []
    for asmr in (True, False):
        call = await CallUnderTest.of(settings, client, conversation)
        llm = FakeLLM(MANO_REPLY)
        await call.run(llm, [spoken("晚上好")], asmr=asmr)
        [prompt] = llm.prompts
        told.append(ASMR in context_of(prompt))
        if asmr:
            assert "[whispering]" in context_of(prompt)
            assert not any(ASMR in m.content for m in prompt[:2])

    assert told == [True, False]


async def test_each_typed_turn_is_told_when_they_last_talked_and_how_long_ago(
    make_client: ClientFactory, settings: Settings
):
    """By her last turn or call that connected with the producer before it, in whichever
    conversation; not before there was one."""
    llm = FakeLLM(reply(1), reply(2), reply(3), reply(4))
    client = make_client(llm, clock=FakeClock(datetime(2026, 10, 19, 12, tzinfo=UTC)))
    await client.put("/api/profile", json=PROFILE)  # Asia/Tokyo
    first = await start_conversation(client)
    database = settings.database_path

    await send_turn(client, first, "第1句")
    last_contact(database, first, datetime(2026, 10, 17, 1, tzinfo=UTC))
    await send_turn(client, first, "第2句")
    last_contact(database, first, datetime(2026, 10, 17, 1, tzinfo=UTC))
    connected_call(database, first, datetime(2026, 10, 19, 10, tzinfo=UTC), 1800)
    await send_turn(client, first, "第3句")
    last_contact(database, first, datetime(2025, 12, 31, 11, 55, 30, tzinfo=UTC))
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM calls")
    await send_turn(client, await start_conversation(client), "第4句")

    told = [
        [line for line in context_of(p).splitlines() if line.startswith(LAST_TALKED)]
        for p in llm.prompts
    ]
    assert told == [
        [],
        ["上次和制作人说话是 10月17日 10:00，隔了 2 天。"],
        ["上次和制作人说话是 10月19日 19:30，隔了 1 小时。"],
        ["上次和制作人说话是 2025年12月31日 20:55，隔了 292 天。"],
    ]
