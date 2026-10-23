"""Her reach-outs in the turns after them (GLOSSARY.md「搭话」, ADR-0026): her prompt tells of
each in a line of narration, by the producer's time zone, followed by her words as her replies
are written in it, before the user's words that came next among the conversation's events, by
time; the one not answered yet, before what the user is saying now. So the history is cut back
by turns as ever, each going with the turn after it, its roles in the order every protocol
takes, and each turn's prompt opens as the one before it did. Having reached out, she has
responded to the gifts before it. The judge model, retrieval and memory read the one the user
answers as her latest words before theirs; in a call, as in a typed turn.

Driven through the backend's external API, the reach-out had as `test_reach_outs` has it
(`ReachOuts.run_due`), then set in the database at a time of the test's choosing."""

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeBackgroundLLM,
    FakeClock,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeRandom,
    FakeReranker,
    FakeServiceClients,
)
from tests.test_call_turns import MANO_REPLY, CallUnderTest, spoken
from tests.test_conversation_events import HUNG_UP
from tests.test_conversation_history import history, told
from tests.test_dynamic_context import shape
from tests.test_llm_cache_hits import (
    ANTHROPIC,
    CHAT,
    RESPONSES,
    anthropic,
    chat_completions,
    responses,
)
from tests.test_llm_protocols import serving
from tests.test_reach_outs import (
    MIDDLE,
    REACH_OUT,
    REPLY,
    backend_of,
    last_contact,
    live_in,
    talked,
    write_event,
)
from tests.test_text_turn import send_turn

SAID = "<ja>はい</ja><zh>好的</zh>"
WRITTEN = (
    "<ja>[happily] プロデューサーさん！</ja><zh>制作人！</zh>"
    "<ja>レッスン終わったよ</ja><zh>练习结束了哦</zh>"
)
"""Her reach-out (`REACH_OUT`) as her replies are written in her prompt."""
TOKYO = ZoneInfo("Asia/Tokyo")
USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "input_tokens": 1, "output_tokens": 1}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With reach-outs on in `.env`."""
    return replace(settings, reach_outs=True)


def reached(at: datetime) -> str:
    """The narration of her reach-out at `at`, the producer living in Tokyo."""
    local = at.astimezone(TOKYO)
    return f"（{local.month}月{local.day}日 {local:%H:%M}，你先给制作人发了：）{WRITTEN}\n"


def place(database: Path, conversation: str, at: datetime) -> datetime:
    """Have her reach-outs in the conversation been said at `at`, to the minute."""
    at = at.replace(second=0, microsecond=0)
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE reach_outs SET created_at = ? WHERE conversation_id = ?",
            (at.isoformat(), conversation),
        )
    return at


async def reached_out(
    make_client: ClientFactory, settings: Settings, *replies, **services
) -> tuple[httpx.AsyncClient, FakeLLM, FakeClock, str, datetime]:
    """A client on `settings` whose user has had a turn with Mano, and then she reached
    out, an hour ago, the producer living in Tokyo; her chat model then replying with
    `replies`."""
    client, llm, clock, conversation = await talked(
        make_client, REACH_OUT, *replies,
        settings=settings, random=FakeRandom(MIDDLE), **services,
    )
    database = settings.database_path
    last_contact(database, conversation, clock.now - timedelta(hours=28))
    await backend_of(client).reach_outs.run_due()
    at = place(database, conversation, clock.now - timedelta(hours=1))
    await live_in(client, "Asia/Tokyo")
    return client, llm, clock, conversation, at


async def test_the_one_he_answers_is_told_before_his_words_and_stays_there_after(
    make_client: ClientFactory, settings: Settings
):
    client, llm, _, conversation, at = await reached_out(make_client, settings, REPLY, REPLY)

    await send_turn(client, conversation, "辛苦了")

    assert history(llm)[-1] == ("user", f"{reached(at)}辛苦了")

    await send_turn(client, conversation, "然后呢")

    assert history(llm) == [
        ("user", "我去练习了"), ("assistant", SAID),
        ("user", f"{reached(at)}辛苦了"), ("assistant", SAID),
        ("user", "然后呢"),
    ]


async def test_cut_back_by_turns_it_goes_with_the_turn_after_it(
    make_client: ClientFactory, settings: Settings
):
    one_turn = replace(settings, history_turns=1)
    client, llm, _, conversation, at = await reached_out(
        make_client, one_turn, REPLY, REPLY
    )
    await send_turn(client, conversation, "辛苦了")

    await send_turn(client, conversation, "然后呢")

    assert history(llm) == [  # the turn before it cut, it goes on with the one after it
        ("user", f"{reached(at)}辛苦了"), ("assistant", SAID), ("user", "然后呢"),
    ]


async def test_among_the_events_by_time_having_reached_out_she_has_responded_to_the_gift(
    make_client: ClientFactory, settings: Settings
):
    client, llm, _, conversation, at = await reached_out(make_client, settings, REPLY)
    write_event(settings.database_path, conversation, "gift", at - timedelta(minutes=30))
    write_event(settings.database_path, conversation, "hung_up", at + timedelta(minutes=30))

    await send_turn(client, conversation, "辛苦了")

    assert history(llm)[-1] == (
        "user", f"（制作人送给你一份蛋糕。）\n{reached(at)}{HUNG_UP}\n辛苦了"
    )
    assert "回应收到的礼物" not in told(llm)


async def test_each_turn_opens_as_the_one_before_it_did(
    make_client: ClientFactory, settings: Settings
):
    client, llm, _, conversation, _ = await reached_out(make_client, settings, REPLY, REPLY)
    await send_turn(client, conversation, "辛苦了")
    await send_turn(client, conversation, "然后呢")

    before, after = llm.prompts[-2:]
    opening = shape(before[:-2])
    assert shape(after[: len(opening)]) == opening


@pytest.mark.parametrize(
    ("adapter", "stream"),
    [
        (CHAT, chat_completions(USAGE)),
        (RESPONSES, responses(USAGE)),
        (ANTHROPIC, anthropic(USAGE, USAGE)),
    ],
    ids=["chat-completions", "responses", "anthropic"],
)
async def test_every_protocol_takes_the_turn_answering_it(
    make_client: ClientFactory, settings: Settings, adapter, stream: str
):
    client, llm, _, conversation, _ = await reached_out(make_client, settings, REPLY)
    await send_turn(client, conversation, "辛苦了")
    requests: list[httpx.Request] = []

    async for _ in adapter(serving(stream, requests)).stream(llm.prompts[-1]):
        pass

    body = json.loads(requests[0].content)
    said = [m["role"] for m in body.get("messages", body.get("input", []))]
    said = [role for role in said if role != "system"]
    assert said == ["user", "assistant", "user"]


async def test_the_judge_model_retrieval_and_memory_read_it_as_her_latest_words(
    make_client: ClientFactory, settings: Settings
):
    clients = FakeServiceClients()
    embedder = FakeEmbedder()
    memory = FakeBackgroundLLM()
    client, _, _, conversation, _ = await reached_out(
        make_client, replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True), REPLY,
        clients=clients, retrieval_judge=FakeJudge(answer="memories"), embedder=embedder,
        reranker=FakeReranker(), background_llm=memory,
    )

    await send_turn(client, conversation, "辛苦了")
    await client.get("/api/memories")  # once what the turn writes down is written

    state = clients.judge.states[-1]
    assert state["上文"] == [
        "制作人：我去练习了", "櫻木真乃：はい", "櫻木真乃：プロデューサーさん！レッスン終わったよ",
    ]
    assert "制作人！练习结束了哦\n辛苦了" in embedder.queries
    [*_, extraction] = [p for p in memory.prompts if "起标题" not in p[0].content]
    assert extraction[-1].content.endswith(
        "櫻木真乃：好的\n櫻木真乃：制作人！练习结束了哦\n"
        "制作人（最新的一句话）：辛苦了\n櫻木真乃：好的"
    )


async def test_in_a_call_the_one_before_it_connected_is_told_before_his_words(
    make_client: ClientFactory, settings: Settings
):
    client, _, _, conversation, at = await reached_out(make_client, settings)
    call = await CallUnderTest.of(settings, client, conversation)
    call_llm = FakeLLM(MANO_REPLY)
    await call.run(call_llm, [spoken("看到了")])

    assert history(call_llm)[-1] == ("user", f"{reached(at)}看到了")
