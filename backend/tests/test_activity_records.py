"""Activity records of text turns, and the dashboard over them: each turn leaves a record
of how it turned out (as it counts towards the limit), each service call in it a record of
how it went, how long it took and what it is billed for, all of it seen only as the
admin's dashboard sums it up, with nothing anyone said. Driven through the backend's
external API with a clock the tests control."""

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from eidolon.activity import OWN, llm_origin, metered_llm
from eidolon.billing import Billing
from eidolon.config import LLMSettings, Settings, UsageLimits
from eidolon.engine import MAX_INPUT_LENGTH
from eidolon.llm import ChatLLM, ChatMessage, LLMError, Reasoning, ToolCall
from eidolon.llm.anthropic import AnthropicLLM
from eidolon.llm.openai_compat import OpenAICompatLLM
from eidolon.llm.responses import ResponsesLLM
from eidolon.rerank import SiliconFlowReranker
from tests.conftest import (
    ClientFactory,
    activity_in,
    users_in,
    agree_to_terms,
    github_identity,
    sign_in_with,
)
from tests.fakes import (
    START,
    FakeBackgroundLLM,
    FakeClock,
    FakeEmbedder,
    FakeIdentityProvider,
    FakeJudge,
    FakeLLM,
    FakeLLMService,
    FakeReranker,
    context_of,
)
from tests.test_embedding import Answer, scripted
from tests.test_retrieval_services import ranked
from tests.test_service_status import backend_of
from tests.test_delete_account import rows_mentioning, user_id
from tests.test_llm_protocols import recorded, serving
from tests.test_openai_compat import RECORDED_STREAM
from tests.test_own_llm import LLM, MINE
from tests.test_rate_limit import post_turn
from tests.test_shared_memory import CAT, memorized
from tests.test_text_turn import leave_turn, send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]
SAID = "我家的猫叫小白哦"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The tests' settings, where the GitHub user 1001 is an admin."""
    return replace(settings, admins=frozenset({("github", "1001")}))


async def admin_client(
    make_client: ClientFactory,
    github: FakeIdentityProvider,
    clock: FakeClock,
    llm: FakeLLM | None = None,
    **services: Any,
) -> httpx.AsyncClient:
    """An admin, signed in on a backend of their own, whose turns run on `llm`, with any
    other keyword passed on to `make_client`."""
    client = make_client(llm or FakeLLM(), user=None, clock=clock, **services)
    await sign_in_with(client, github, github_identity("1001", name="octo"))
    await agree_to_terms(client)
    return client


async def dashboard(client: httpx.AsyncClient, range: str = "24h") -> dict:
    res = await client.get("/api/admin/dashboard", params={"range": range})
    assert res.status_code == 200, res.text
    return res.json()


def health(board: dict, service: str, own_key: bool = False) -> dict | None:
    """The dashboard's health row of `service`, on the deployer's keys or the users' own."""
    return next(
        (h for h in board["health"] if (h["service"], h["own_key"]) == (service, own_key)),
        None,
    )


async def test_the_dashboard_counts_turns_by_how_they_turned_out_and_says_nothing_said(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    two = replace(settings, usage_limits=UsageLimits(turns_per_hour=2))
    llm = FakeLLM(
        REPLY,
        LLMError("模型返回 503: overloaded"),
        ["<ja>はい", "っ</ja>", "<zh>好", "的</zh>"],
        delay=0.2,
    )
    producer = make_client(llm, settings=two, clock=clock)
    friend = make_client(FakeLLM(REPLY), "friend", settings=two, clock=clock)
    conversation_id = await start_conversation(producer)
    admin = await admin_client(make_client, github, clock)

    succeeded = await send_turn(producer, conversation_id, SAID)
    failed = await send_turn(producer, conversation_id, "在吗")
    await leave_turn(producer, conversation_id, "你好", after="delta")  # once asked
    refused = await post_turn(producer, conversation_id)
    await send_turn(friend, await start_conversation(friend, "kogane"), "早上好")
    board = await dashboard(admin)

    assert (succeeded[-1]["type"], failed[-1]["type"], refused.status_code) == (
        "done", "error", 429
    )
    turns = board["turns"]
    assert turns["total"] == 5
    assert turns["by_result"] == {"success": 2, "failed": 1, "rejected": 1, "left": 1}
    assert turns["own_keys"] == 0
    assert board["active_users"] == 2
    assert turns["time_to_speak_ms"]["p50"] is not None
    assert [(c["character_id"], c["name"], c["turns"]) for c in board["characters"]] == [
        ("mano", "櫻木真乃", 4), ("kogane", "月岡恋鐘", 1)
    ]
    assert sum(point["turns"] for point in board["trend"]) == 5
    # Each turn that reached the model called it once; only the one that failed failed.
    chat = health(board, "chat_llm")
    assert chat is not None
    assert (chat["calls"], chat["errors"]) == (4, 1)
    assert chat["errors_by_kind"] == {"server_error": 1}
    assert chat["latency_ms"]["p50"] is not None
    # The fakes say nothing of tokens: what the calls are billed for is not known.
    assert chat["usage_unknown"] == 4
    for text in [SAID, "在吗", "你好", "早上好", "はいっ", "好的", "overloaded"]:
        assert text not in str(board), text


async def test_a_failing_judge_shows_in_health_though_the_turn_goes_on(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(
        FakeLLM(REPLY, REPLY), clock=clock, retrieval_judge=FakeJudge(None),
        embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, SAID)
    assert [m["text"] for m in (await client.get("/api/memories")).json()] == [CAT]
    events = await send_turn(client, conversation_id, "你还记得我的猫吗")
    board = await dashboard(admin)

    assert events[-1]["type"] == "done"
    assert board["turns"]["by_result"]["success"] == 2
    judge = health(board, "judge")
    assert judge is not None and (judge["calls"], judge["errors"]) == (2, 2)
    # Retrieving anyway, the turns looked up references and memories, the second finding
    # and reranking the one the first wrote down with the background model, embedding it.
    for service in ["embedding", "rerank", "memory_llm", "chat_llm"]:
        row = health(board, service)
        assert row is not None and row["calls"] >= 1 and row["errors"] == 0, service
    assert CAT not in str(board)


async def test_a_request_made_again_counts_as_a_call_of_its_own(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    answers: list[Answer] = [httpx.Response(503, text="unavailable")]
    reranker = SiliconFlowReranker(
        "sk-test", "m", "http://sf.test/v1",
        httpx.AsyncClient(transport=httpx.MockTransport(scripted(answers, ranked))),
    )
    llm = FakeLLM(REPLY, REPLY)
    client = make_client(
        llm, clock=clock, retrieval_judge=FakeJudge("memories"),
        embedder=FakeEmbedder(), reranker=reranker,
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, SAID)
    events = await send_turn(client, conversation_id, "你还记得我的猫吗")
    board = await dashboard(admin)

    assert events[-1]["type"] == "done"
    assert CAT in context_of(llm.prompts[-1])  # found once asked again
    rerank = health(board, "rerank")
    assert rerank is not None
    assert (rerank["calls"], rerank["errors"], rerank["errors_by_kind"]) == (
        2, 1, {"server_error": 1}
    )


async def test_calls_on_the_users_own_keys_are_counted_apart(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    own_llm: FakeLLMService,
):
    clock = FakeClock()
    own_llm.llm = FakeLLM(REPLY, LLMError("402 Payment Required"))
    deployers = FakeLLM(REPLY)
    client = make_client(deployers, clock=clock)
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "在吗", own_llm=LLM)
    await send_turn(client, conversation_id, "在吗", own_llm=LLM)
    board = await dashboard(admin)

    own = health(board, "chat_llm", own_key=True)
    assert own is not None and (own["calls"], own["errors"]) == (2, 1)
    assert health(board, "chat_llm") is None
    assert board["turns"]["own_keys"] == 2
    assert board["turns"]["by_result"]["failed"] == 1
    assert MINE not in str(board)


async def test_a_database_from_before_forgets_the_hosts_and_models_of_users_own_llms(
    make_client: ClientFactory, settings: Settings
):
    """Calls to users' own LLMs were recorded with their hosts and models; the deployer's
    calls, and those to users' own speech services, keep theirs."""
    make_client(FakeLLM())
    kept = [
        ("chat_llm", "llm.invalid", "test-model", 0),
        ("tts", "api.fish.audio", "s1", 1),
    ]
    with sqlite3.connect(settings.database_path) as db:
        db.executemany(
            "INSERT INTO service_calls (at, service, provider, model, own_key)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (START.timestamp(), *row)
                for row in [
                    ("chat_llm", "llm.example.com", "my-model", 1),
                    ("memory_llm", "llm.example.com", "my-model", 1),
                    *kept,
                ]
            ],
        )

    for start in ["first", "again"]:
        make_client(FakeLLM())

        with sqlite3.connect(settings.database_path) as db:
            rows = db.execute(
                "SELECT service, provider, model, own_key FROM service_calls ORDER BY id"
            ).fetchall()
        assert rows == [
            ("chat_llm", OWN, OWN, 1), ("memory_llm", OWN, OWN, 1), *kept
        ], start


async def test_each_range_takes_the_turns_since_that_long_ago(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)
    admin = await admin_client(make_client, github, clock)
    await send_turn(client, await start_conversation(client), "在吗")

    clock.advance(hours=23, minutes=59)
    day = await dashboard(admin, "24h")
    assert day["turns"]["total"] == 1
    assert day["bucket"] == "hour" and len(day["trend"]) == 24
    clock.advance(minutes=1)
    # The range begins where the slot of 15 minutes 24 hours ago does, the turn's.
    assert (await dashboard(admin, "24h"))["turns"]["total"] == 1
    clock.advance(minutes=14, seconds=59)
    assert (await dashboard(admin, "24h"))["turns"]["total"] == 1
    clock.advance(seconds=1)
    assert (await dashboard(admin, "24h"))["turns"]["total"] == 0
    week = await dashboard(admin, "7d")
    assert week["turns"]["total"] == 1
    assert sum(point["turns"] for point in week["trend"]) == 1
    assert datetime.fromisoformat(week["from"]) <= START
    assert [len((await dashboard(admin, r))["trend"]) for r in ["7d", "30d", "90d"]] == [
        28, 30, 90
    ]
    assert (await admin.get("/api/admin/dashboard", params={"range": "1y"})).status_code == 422


async def test_records_are_forgotten_after_90_days(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)
    await send_turn(client, await start_conversation(client), "在吗")

    # Its rollups go with the slot of 15 minutes it began in, which a range may begin with.
    clock.advance(days=90, minutes=15)
    later = make_client(FakeLLM(REPLY), clock=clock)  # signed in again
    await send_turn(later, await start_conversation(later), "在吗")
    admin = await admin_client(make_client, github, clock)
    await backend_of(later).activity.forget_old()  # as the backend tidies up

    assert (await dashboard(admin, "90d"))["turns"]["total"] == 1
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM activity_records").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM service_calls").fetchone() == (1,)
        # Its day's are kept till the day is over: a range begins with its slots before that.
        for slots in ["record_rollups WHERE length = 900", "taken_rollups", "call_rollups"]:
            [(first,)] = db.execute(f"SELECT MIN(start) FROM {slots}").fetchall()
            assert first > START.timestamp(), slots


async def test_deleting_a_user_keeps_the_numbers_but_not_who_it_was(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY, REPLY), clock=clock)
    friend = make_client(FakeLLM(REPLY), "friend", clock=clock)
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "在吗")
    await send_turn(client, conversation_id, "在吗")
    await send_turn(friend, await start_conversation(friend), "在吗")
    producer = user_id(settings.database_path, "producer")
    before = await dashboard(admin)

    assert (await client.post("/api/account/delete", json={"confirm": True})).status_code == 204

    after = await dashboard(admin)
    # The first look has the price catalogs fetched in the background, so when they were
    # may change in between.
    for board in [before, after]:
        del board["cost"]["catalog_as_of"]
    assert after == before
    assert after["active_users"] == 2
    assert rows_mentioning(settings.database_path, producer) == {}


async def test_the_dashboard_is_for_admins_only(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM())

    assert (await client.get("/api/admin/dashboard")).status_code == 404


Protocol = Callable[[httpx.AsyncClient], ChatLLM]
PROTOCOLS: dict[str, tuple[Protocol, str, tuple[int, int, int | None]]] = {
    # Each protocol's recorded reply, and the tokens it says the request was billed for: in,
    # out, and of the input, those read from the prompt cache, if it says.
    "chat-completions": (
        lambda http: OpenAICompatLLM("https://llm.example/v1", "sk", "m", client=http),
        RECORDED_STREAM,
        (10, 5, None),
    ),
    "responses": (
        lambda http: ResponsesLLM("https://llm.example/v1", "sk", "m", client=http),
        recorded("responses_text"),
        (57, 518, 0),
    ),
    "anthropic": (
        lambda http: AnthropicLLM("https://llm.example/v1", "sk", "m", client=http),
        recorded("anthropic_text"),
        (57, 124, None),
    ),
}


@pytest.mark.parametrize("protocol", PROTOCOLS)
async def test_the_tokens_each_protocol_bills_are_recorded(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    protocol: str,
):
    adapter, stream, (input_tokens, output_tokens, cached) = PROTOCOLS[protocol]
    requests: list[httpx.Request] = []
    clock = FakeClock()
    client = make_client(adapter(serving(stream, requests)), clock=clock)  # pyright: ignore[reportArgumentType]
    admin = await admin_client(make_client, github, clock)

    events = await send_turn(client, await start_conversation(client), "你好")
    chat = health(await dashboard(admin), "chat_llm")

    assert events[-1]["type"] == "done"
    assert chat is not None
    assert chat["usage"] == [
        {"unit": "tokens", "input": input_tokens, "output": output_tokens, "cached": cached}
    ]
    assert chat["usage_unknown"] == 0
    if protocol == "chat-completions":  # which sends the usage only when asked to
        assert b'"stream_options":{"include_usage":true}' in requests[0].content.replace(
            b" ", b""
        )


async def test_a_turn_rejected_for_its_input_is_recorded_rejected(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)

    rejected = await send_turn(client, conversation_id, "啊" * (MAX_INPUT_LENGTH + 1))
    board = await dashboard(admin)

    assert rejected[-1] == {"type": "error", "message": f"这句话太长了，最多 {MAX_INPUT_LENGTH} 字。"}
    assert board["turns"]["by_result"] == {"success": 0, "failed": 0, "rejected": 1, "left": 0}


SLOW = 0.2
"""How long, in seconds, the model below waits before each piece after its first."""


async def drain(llm: ChatLLM) -> None:
    async for _ in llm.stream([ChatMessage("user", "你好")]):
        pass


async def test_an_llm_call_records_its_first_words_time_but_not_its_thinking_and_its_effort(
    tmp_path: Path,
):
    """A tool call counts as the first word, as text does (which the dashboard's test times)."""
    async with activity_in(tmp_path / "eidolon.db") as activity:
        model = LLMSettings("http://llm.invalid/v1", "k", "m", reasoning_effort="high")
        llm = metered_llm(
            FakeLLM([Reasoning({"text": "想想"}), ToolCall("c1", "recall", {}), "っ"], delay=SLOW),
            activity, llm_origin("chat_llm", model),
        )

        await drain(llm)
        [call] = (await activity.since(START - timedelta(days=1)))[1]

    assert (call.effort, call.error) == ("high", None)
    assert call.first_token_ms is not None and call.latency_ms is not None
    # Thinking came at once, the first word only after it; the whole reply later still.
    assert call.first_token_ms >= SLOW * 1000 * 0.75
    assert call.latency_ms - call.first_token_ms >= SLOW * 1000 * 0.75


async def test_an_llm_call_failing_before_any_word_has_no_first_words_time(tmp_path: Path):
    async with activity_in(tmp_path / "eidolon.db") as activity:
        llm = metered_llm(
            FakeLLM(LLMError("模型返回 503: overloaded")), activity,
            llm_origin("memory_llm", LLMSettings("http://llm.invalid/v1", "k", "m")),
        )

        with pytest.raises(LLMError):
            await drain(llm)
        [call] = (await activity.since(START - timedelta(days=1)))[1]

    assert (call.error, call.first_token_ms, call.effort) == ("server_error", None, None)


async def test_the_dashboard_shows_an_llm_at_every_effort_on_one_row_with_its_first_words_times(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    high = replace(settings, chat_llm=replace(settings.chat_llm, reasoning_effort="high"))
    thinking = make_client(FakeLLM(REPLY, REPLY), settings=high, clock=clock)
    unset = make_client(
        FakeLLM(REPLY), "friend", clock=clock, retrieval_judge=FakeJudge(None),
        embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
    )
    admin = await admin_client(make_client, github, clock)

    conversation_id = await start_conversation(thinking)
    await send_turn(thinking, conversation_id, "在吗")
    await send_turn(thinking, conversation_id, "在吗")
    await send_turn(unset, await start_conversation(unset), "在吗")
    board = await dashboard(admin)

    [chat] = [h for h in board["health"] if h["service"] == "chat_llm"]
    assert chat["calls"] == 3 and "effort" not in chat
    assert chat["first_token_ms"]["p50"] is not None
    assert chat["first_token_ms"]["p95"] is not None
    judge = health(board, "judge")
    assert judge is not None and judge["calls"] == 1
    assert "first_token_ms" not in judge


async def test_a_database_from_before_first_words_were_timed_takes_them_now(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    """Service calls recorded before had no first word's time nor effort; they keep
    neither."""
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "CREATE TABLE service_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, record_id"
            " INTEGER, at REAL NOT NULL, service TEXT NOT NULL, provider TEXT NOT NULL,"
            " model TEXT NOT NULL, own_key INTEGER NOT NULL DEFAULT 0, error TEXT,"
            " latency_ms REAL, usage_unit TEXT, usage_input REAL, usage_output REAL)"
        )
        db.execute(
            "INSERT INTO service_calls (at, service, provider, model, latency_ms)"
            " VALUES (?, 'chat_llm', 'llm.invalid', 'test-model', 900)",
            (START.timestamp(),),
        )
    clock = FakeClock()

    admin = await admin_client(make_client, github, clock)
    with sqlite3.connect(settings.database_path) as db:
        kept = db.execute("SELECT first_token_ms, reasoning_effort FROM service_calls").fetchall()
    chat = health(await dashboard(admin), "chat_llm")

    assert kept == [(None, None)]
    assert chat is not None
    assert (chat["calls"], chat["latency_ms"]["p50"]) == (1, 900)
    assert chat["first_token_ms"] == {"p50": None, "p95": None}


async def test_a_database_from_before_reach_outs_were_answered_has_none_answered(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    """Reach-outs recorded before had no column for whether they were answered: none were."""
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "CREATE TABLE activity_records (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT"
            " NOT NULL, user_id TEXT, anonymous_id TEXT, character_id TEXT NOT NULL,"
            " started_at REAL NOT NULL, result TEXT NOT NULL, own_keys INTEGER NOT NULL"
            " DEFAULT 0, time_to_speak_ms REAL, call_id INTEGER, duration_s REAL, end_reason"
            " TEXT, affinity_level INTEGER, out_of_line INTEGER, manipulation INTEGER, cooled"
            " INTEGER, ended_conversation TEXT)"
        )
        db.execute(
            "INSERT INTO activity_records (kind, anonymous_id, character_id, started_at, result)"
            " VALUES ('reach_out', 'someone', 'mano', ?, 'success')",
            ((START - timedelta(hours=1)).timestamp(),),
        )

    admin = await admin_client(make_client, github, FakeClock())

    assert (await dashboard(admin))["reach_outs"] == {
        "succeeded": 1, "failed": 0, "answered_share": 0,
    }


async def test_an_llm_call_records_how_much_of_its_input_hit_the_cache(tmp_path: Path):
    async with activity_in(tmp_path / "eidolon.db") as activity:
        origin = llm_origin("chat_llm", LLMSettings("http://llm.invalid/v1", "k", "m"))

        await drain(metered_llm(FakeLLM(REPLY, tokens=(1000, 50, 800)), activity, origin))
        await drain(metered_llm(FakeLLM(REPLY, tokens=(1000, 50)), activity, origin))
        calls = (await activity.since(START - timedelta(days=1)))[1]

    assert [(c.usage_input, c.usage_output, c.usage_cached) for c in calls] == [
        (1000, 50, 800), (1000, 50, None)
    ]


async def test_the_dashboard_shows_each_llms_cache_hit_rate(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    own_llm: FakeLLMService,
):
    """Of all the input of an LLM's calls, the share read from the cache; a call that did
    not say hit nothing. The users' own LLMs are told too, as `OWN`."""
    clock = FakeClock()
    own_llm.llm = FakeLLM(REPLY, tokens=(400, 10, 100))
    told = make_client(FakeLLM(REPLY, REPLY, tokens=(1000, 50, 900)), clock=clock)
    untold = make_client(
        FakeLLM(REPLY, tokens=(2000, 50)), "friend", clock=clock,
        retrieval_judge=FakeJudge(None), embedder=FakeEmbedder(), reranker=FakeReranker("制作人"),
    )
    admin = await admin_client(make_client, github, clock)

    conversation_id = await start_conversation(told)
    await send_turn(told, conversation_id, "在吗")
    await send_turn(told, conversation_id, "在吗", own_llm=LLM)
    await send_turn(untold, await start_conversation(untold), "在吗")
    board = await dashboard(admin)

    chat, own = health(board, "chat_llm"), health(board, "chat_llm", own_key=True)
    assert chat is not None and own is not None
    assert chat["usage"] == [{"unit": "tokens", "input": 3000, "output": 100, "cached": 900}]
    assert chat["cache_hit_rate"] == pytest.approx(0.3)
    assert own["cache_hit_rate"] == pytest.approx(0.25)
    judge = health(board, "judge")
    assert judge is not None and "cache_hit_rate" not in judge


async def test_an_llm_that_said_nothing_of_its_input_has_no_cache_hit_rate(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)
    admin = await admin_client(make_client, github, clock)

    await send_turn(client, await start_conversation(client), "在吗")
    chat = health(await dashboard(admin), "chat_llm")

    assert chat is not None
    assert (chat["usage"], chat["cache_hit_rate"]) == ([], None)


async def test_a_database_from_before_cache_hits_were_recorded_takes_them_now(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    """Service calls recorded before said nothing of the cache; they hit none of it."""
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "CREATE TABLE service_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, record_id"
            " INTEGER, at REAL NOT NULL, service TEXT NOT NULL, provider TEXT NOT NULL,"
            " model TEXT NOT NULL, own_key INTEGER NOT NULL DEFAULT 0, error TEXT,"
            " latency_ms REAL, usage_unit TEXT, usage_input REAL, usage_output REAL,"
            " first_token_ms REAL, reasoning_effort TEXT)"
        )
        db.execute(
            "INSERT INTO service_calls (at, service, provider, model, latency_ms, usage_unit,"
            " usage_input, usage_output) VALUES (?, 'chat_llm', 'llm.invalid', 'test-model',"
            " 900, 'tokens', 1000, 50)",
            (START.timestamp(),),
        )
    clock = FakeClock()

    admin = await admin_client(make_client, github, clock)
    with sqlite3.connect(settings.database_path) as db:
        kept = db.execute("SELECT usage_input, usage_cached FROM service_calls").fetchall()
    chat = health(await dashboard(admin), "chat_llm")

    assert kept == [(1000, None)]
    assert chat is not None
    assert (chat["usage"][0]["cached"], chat["cache_hit_rate"]) == (None, 0)


async def test_a_service_call_recorded_as_its_turns_record_is_written_goes_under_it(
    tmp_path: Path,
):
    """As a photo's that comes out once the turn is over may, its record being written."""
    [user] = await users_in(tmp_path / "eidolon.db", "producer")
    async with activity_in(tmp_path / "eidolon.db") as activity:
        origin = llm_origin("chat_llm", LLMSettings("http://llm.invalid/v1", "k", "m"))
        turn = activity.begin("turn", user, "mano", own_llm=False, exempt=False)
        await activity.record(activity.ended_call(origin, 0.0, None, Billing()), turn)

        finishing = asyncio.create_task(turn.finish("success"))
        await asyncio.sleep(0)  # handed in to be written
        await activity.record(activity.ended_call(origin, 0.0, None, Billing()), turn)
        await finishing
        await activity.record(activity.ended_call(origin, 0.0, None, Billing()), turn)

    with closing(sqlite3.connect(tmp_path / "eidolon.db")) as db:
        [(record_id,)] = db.execute("SELECT id FROM activity_records").fetchall()
        under = db.execute("SELECT record_id FROM service_calls").fetchall()
    assert under == [(record_id,)] * 3
