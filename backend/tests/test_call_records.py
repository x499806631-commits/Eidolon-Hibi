"""Activity records of calls, and the dashboard over them: each call leaves a record of how
it turned out (as it counts towards the limit), how long it lasted and why it ended; the
turns in it each leave a turn's record under it, and its speech recognition, synthesis
and emotion judging a record of each service call. Driven through the backend's external
API, with a browser calling in over a real WebRTC connection and fake outside services.

How many calls are in progress is the backend's own count, and each client in the tests
has a backend of its own: the admin places the calls where that is asked."""

import asyncio
import sqlite3
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
)
from pipecat.tests.utils import SleepFrame

import eidolon.call
from eidolon.config import Settings
from tests.browser import TIMEOUT, Browser, open_browser
from tests.conftest import ClientFactory, store_in
from tests.fakes import (
    START,
    FakeClock,
    FakeIdentityProvider,
    FakeJudge,
    FakeLLM,
    FakeLLMService,
    FakeSpeech,
)
from tests.test_activity_records import admin_client, dashboard, health, settings  # noqa: F401
from tests.test_call_turns import (  # noqa: F401
    INTERRUPTED_REPLY,
    INTERRUPTING,
    MANO_REPLY,
    NOW,
    SLOW_REPLY,
    HANG,
    CallUnderTest,
    kinds,
    mano_call,
    spoken,
)
from tests.test_delete_account import user_id
from tests.test_service_status import backend_of
from tests.test_own_speech_keys import MY_FISH, MY_LLM, MY_SONIOX, limited, own_keys
from tests.test_text_turn import start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]


async def in_progress(admin: httpx.AsyncClient) -> int:
    res = await admin.get("/api/admin/calls/live")
    assert res.status_code == 200, res.text
    return res.json()["in_progress"]


async def call_recorded(admin: httpx.AsyncClient) -> dict:
    """The dashboard, once it has a call."""
    async with asyncio.timeout(TIMEOUT):
        while (board := await dashboard(admin))["calls"]["total"] == 0:
            await asyncio.sleep(0.05)
    return board


@pytest.mark.parametrize(("hanging_up", "reason"), [(True, "hung_up"), (False, "left")])
async def test_a_call_ended_by_the_user_is_a_success_and_no_longer_in_progress(
    make_client: ClientFactory,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
    hanging_up: bool,
    reason: str,
):
    """Hanging up says so before the connection closes; leaving the page just closes it."""
    admin = await admin_client(make_client, github, FakeClock(), FakeLLM(REPLY))
    conversation_id = await start_conversation(admin, "kogane")
    assert await in_progress(admin) == 0

    await browser.call(admin, conversation_id=conversation_id)
    await browser.connect()
    assert await in_progress(admin) == 1
    await speech.stts[-1].hear("你好")
    await browser.turn()
    await (browser.hang_up() if hanging_up else browser.close())
    board = await call_recorded(admin)

    async with asyncio.timeout(TIMEOUT):  # let go of just after it is recorded
        while await in_progress(admin) != 0:
            await asyncio.sleep(0.05)
    calls = board["calls"]
    assert calls["total"] == 1
    assert calls["by_result"] == {
        "success": 1, "failed": 0, "rejected": 0, "not_connected": 0
    }
    assert {r: n for r, n in calls["end_reasons"].items() if n} == {reason: 1}
    assert calls["durations"][0]["calls"] == 1  # well under a minute
    assert calls["duration_s"]["p50"] is not None
    assert [(c["character_id"], c["turns"], c["calls"]) for c in board["characters"]] == [
        ("kogane", 1, 1)
    ]
    assert sum(point["calls"] for point in board["trend"]) == 1
    assert (board["turns"]["total"], board["turns"]["in_calls"]) == (1, 1)
    assert board["active_users"] == 1
    # The call heard the user and spoke once, on the deployer's keys.
    stt, tts = health(board, "stt"), health(board, "tts")
    assert stt is not None and (stt["calls"], stt["errors"]) == (1, 0)
    assert stt["usage"][0]["unit"] == "seconds"
    assert tts is not None and (tts["calls"], tts["errors"]) == (1, 0)
    assert tts["usage"] == [{"unit": "bytes", "input": len("はいっ".encode()), "output": None}]
    for text in ["你好", "はいっ", "好的"]:
        assert text not in str(board), text


async def test_drama_3_is_billed_for_the_characters_it_is_given(
    make_client: ClientFactory,
    settings: Settings,  # noqa: F811
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    admin = await admin_client(
        make_client, github, FakeClock(), FakeLLM(["<ja>[sigh] はいっ</ja><zh>好的</zh>"]),
        settings=replace(settings, fish_audio_model="drama-3"),
    )
    await browser.call(admin, conversation_id=await start_conversation(admin))
    await browser.connect()
    await speech.stts[-1].hear("你好")
    await browser.turn()
    await browser.hang_up()

    voiced = health(await call_recorded(admin), "tts")

    assert voiced is not None
    assert voiced["usage"] == [{"unit": "characters", "input": len("[sigh]はいっ"), "output": None}]


async def test_calls_over_the_limit_are_refused_and_calls_on_own_keys_counted_apart(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    own_llm: FakeLLMService,
):
    own_llm.llm = FakeLLM(REPLY)
    clock = FakeClock()
    client = make_client(FakeLLM(), settings=limited(settings, calls_per_hour=1), clock=clock)
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)
    async with open_browser() as first:
        await first.call(client, conversation_id=conversation_id)
        await first.connect()
        await first.hang_up()
    async with open_browser() as second:
        assert (await second.call(client, conversation_id=conversation_id)).status_code == 429

    async with open_browser() as third:
        await third.call(
            client, conversation_id=conversation_id, own_keys=own_keys("llm", "stt", "tts")
        )
        await third.connect()
        await speech.stts[-1].hear("你好")
        await third.turn()
        await third.hang_up()
    async with asyncio.timeout(TIMEOUT):
        while (board := await dashboard(admin))["calls"]["total"] < 3:
            await asyncio.sleep(0.05)

    calls = board["calls"]
    assert (calls["by_result"]["success"], calls["by_result"]["rejected"]) == (2, 1)
    assert calls["own_keys"] == 1
    assert board["turns"]["own_keys"] == 1
    for service in ["chat_llm", "stt", "tts"]:
        own = health(board, service, own_key=True)
        assert own is not None and own["calls"] == 1, service
    deployers = health(board, "stt")
    assert deployers is not None and deployers["calls"] == 1  # the first call's
    for key in [MY_FISH, MY_SONIOX, MY_LLM["api_key"]]:
        assert key not in str(board)


async def test_a_database_from_before_calls_were_recorded_keeps_its_records_and_takes_calls(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    """Service calls then always had a latency; speech recognition has none."""
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "CREATE TABLE service_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, record_id"
            " INTEGER, at REAL NOT NULL, service TEXT NOT NULL, provider TEXT NOT NULL,"
            " model TEXT NOT NULL, own_key INTEGER NOT NULL DEFAULT 0, error TEXT,"
            " latency_ms REAL NOT NULL, usage_unit TEXT, usage_input REAL, usage_output REAL)"
        )
        db.execute(
            "INSERT INTO service_calls (at, service, provider, model, latency_ms)"
            " VALUES (?, 'chat_llm', 'llm.invalid', 'test-model', 800)",
            (START.timestamp(),),
        )
    admin = await admin_client(make_client, github, FakeClock(), FakeLLM())
    await browser.call(admin, conversation_id=await start_conversation(admin))
    await browser.connect()

    await browser.hang_up()
    board = await call_recorded(admin)

    chat, stt = health(board, "chat_llm"), health(board, "stt")
    assert chat is not None and chat["latency_ms"]["p50"] == 800
    assert stt is not None and stt["calls"] == 1 and stt["latency_ms"]["p50"] is None


async def test_a_call_cancelled_as_it_is_let_in_never_connected_and_takes_none_of_the_limit(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    monkeypatch: pytest.MonkeyPatch,
):
    """Letting in goes on as the request is cancelled (the server shutting down), for what
    it writes would be written all the same: what it let in is settled all the same."""
    clock = FakeClock()
    client = make_client(FakeLLM(), settings=limited(settings, calls_per_hour=1), clock=clock)
    admin = await admin_client(make_client, github, clock)
    conversation_id = await start_conversation(client)
    backend = backend_of(client)
    letting_in, may_go_on = asyncio.Event(), asyncio.Event()
    let_in = backend.admissions.call

    async def call(*args: Any) -> Any:
        letting_in.set()
        await may_go_on.wait()
        return await let_in(*args)

    monkeypatch.setattr(backend.admissions, "call", call)

    async with open_browser() as browser:
        offering = asyncio.create_task(browser.call(client, conversation_id=conversation_id))
        await asyncio.wait_for(letting_in.wait(), TIMEOUT)
        offering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await offering
        may_go_on.set()
    board = await call_recorded(admin)

    assert board["calls"]["by_result"]["not_connected"] == 1
    day = await backend.usage.last_day(user_id(settings.database_path, "producer"))
    assert day.total.calls == 0


async def test_a_call_whose_speech_synthesis_fails_before_any_turn_never_connected(
    make_client: ClientFactory,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    admin = await admin_client(make_client, github, clock)
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()

    await speech.ttss[-1].break_down()
    ended = await browser.next_server_message("ended")
    board = await call_recorded(admin)

    assert (ended["reason"], ended["counts"]) == ("not_connected", False)
    calls = board["calls"]
    assert calls["by_result"]["not_connected"] == 1
    assert {r: n for r, n in calls["end_reasons"].items() if n} == {"failed": 1}
    tts = health(board, "tts")
    assert tts is not None and (tts["calls"], tts["errors"]) == (1, 1)
    stt = health(board, "stt")
    assert stt is not None and stt["errors"] == 0


async def test_a_call_that_took_a_turn_and_then_failed_is_a_failure(
    make_client: ClientFactory,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    """The turn the failure cuts short failed too, rather than being left."""
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY, ["<ja>今日", HANG]), clock=clock)
    admin = await admin_client(make_client, github, clock)
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()
    await speech.stts[-1].hear("你好")
    await browser.turn()
    await speech.stts[-1].hear("还在吗")
    await browser.next_server_message("user")

    await speech.stts[-1].break_down()
    await browser.next_server_message("ended")
    board = await call_recorded(admin)

    assert board["turns"]["by_result"] == {"success": 1, "failed": 1, "rejected": 0, "left": 0}
    assert board["calls"]["by_result"]["failed"] == 1
    assert board["calls"]["end_reasons"]["failed"] == 1
    stt = health(board, "stt")
    assert stt is not None and (stt["calls"], stt["errors"]) == (1, 1)


async def test_a_call_that_reaches_its_time_limit_ends_for_it(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    limits = replace(settings.usage_limits, call_minutes=0.01)  # pyright: ignore[reportArgumentType]
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), settings=replace(settings, usage_limits=limits), clock=clock)
    admin = await admin_client(make_client, github, clock)
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()

    ended = await browser.next_server_message("ended")  # 0.6 s after connecting
    board = await call_recorded(admin)

    assert ended["reason"] == "duration_limit"
    assert board["calls"]["by_result"]["success"] == 1
    assert {r: n for r, n in board["calls"]["end_reasons"].items() if n} == {
        "duration_limit": 1
    }


async def test_a_banned_users_call_ends_for_the_ban(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    browser: Browser,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(eidolon.call, "USER_CHECK_INTERVAL", 0.05)
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    admin = await admin_client(make_client, github, clock)
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()

    producer = user_id(settings.database_path, "producer")
    assert (await admin.post(f"/api/admin/users/{producer}/ban", json={})).status_code == 200
    await browser.hung_up()
    board = await call_recorded(admin)  # once the backend has let go of it

    assert board["calls"]["by_result"]["success"] == 1
    assert {r: n for r, n in board["calls"]["end_reasons"].items() if n} == {"banned": 1}


async def test_a_call_whose_user_is_deleted_elsewhere_ends_for_the_deletion(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    browser: Browser,
    monkeypatch: pytest.MonkeyPatch,
):
    """Deleted by another process, say, rather than through this server."""
    monkeypatch.setattr(eidolon.call, "USER_CHECK_INTERVAL", 0.05)
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    admin = await admin_client(make_client, github, clock)
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()

    async with store_in(settings.database_path) as store:
        await store.delete_user(user_id(settings.database_path, "producer"))
    await browser.hung_up()
    board = await call_recorded(admin)

    assert {r: n for r, n in board["calls"]["end_reasons"].items() if n} == {"deleted": 1}
    assert board["active_users"] == 1  # anonymously, as the deleted are


async def test_the_turns_of_a_call_are_recorded_under_it_as_they_turned_out(
    mano_call: CallUnderTest, make_client: ClientFactory, github: FakeIdentityProvider
):
    """A follow-on joins the turn, whose dropped reply is not a turn of its own; an
    interrupted reply is a turn done. Each segment's emotion is judged for the call. How
    each turn's result is decided is `TurnOutcome`'s (`test_turn_outcome`)."""
    llm = FakeLLM(SLOW_REPLY, INTERRUPTED_REPLY, MANO_REPLY, delay=0.3)

    _, messages = await mano_call.run(
        llm,
        [
            spoken("早上好"), SleepFrame(0.1), InterruptionFrame(), SleepFrame(0.1),
            spoken("在吗"), *INTERRUPTING, SleepFrame(0.1), spoken("还在吗"),
        ],
        judge=FakeJudge(),
    )
    admin = await admin_client(make_client, github, FakeClock(NOW))
    board = await call_recorded(admin)

    assert [k for k in kinds(messages) if k != "emotion"] == [
        "user", "user", "done", "user", "done"
    ]
    turns = board["turns"]
    assert (turns["total"], turns["in_calls"]) == (2, 2)
    assert turns["by_result"] == {"success": 2, "failed": 0, "rejected": 0, "left": 0}
    assert board["calls"]["total"] == 1
    judge = health(board, "judge")
    assert judge is not None and judge["calls"] >= 1


async def test_a_turn_a_failure_cuts_short_fails_even_once_the_call_was_settled_before_it(
    mano_call: CallUnderTest,
):
    """Should cancelling the call be held up longer than the pipeline waits for it, the
    call is settled while the turn is still in progress (GLOSSARY.md「回合」)."""
    await mano_call.fail_while_cancelling_is_held_up(
        FakeLLM(["<ja>今日", HANG]), "早上好", cancel_timeout=0.2
    )

    assert [result for result, _ in mano_call.turn_records()] == ["failed"]


async def test_a_turns_time_to_speak_is_not_the_last_replys_voice_starting_late(
    mano_call: CallUnderTest,
):
    """Her voice starting before any of the reply has gone to TTS is the last reply's
    (GLOSSARY.md「开口」): the turn's time to speak is when its own voice starts."""
    llm = FakeLLM(["<ja>はいっ</ja><zh>好的</zh>"], ["<ja>", "うん。</ja><zh>嗯。</zh>"], delay=0.5)

    await mano_call.run(
        llm,
        [
            spoken("早上好"), SleepFrame(0.3),
            spoken("在吗"), SleepFrame(0.1),
            BotStartedSpeakingFrame(), SleepFrame(0.1), BotStoppedSpeakingFrame(),  # late
            SleepFrame(0.6), BotStartedSpeakingFrame(), SleepFrame(0.1),
        ],
    )

    [(first, _), (second, time_to_speak)] = mano_call.turn_records()
    assert (first, second) == ("success", "success")
    assert time_to_speak is not None and time_to_speak >= 500  # its Japanese went to TTS


async def test_a_turn_replied_to_while_the_last_replys_voice_is_still_playing_speaks(
    mano_call: CallUnderTest,
):
    """Her voice does not start again for the next reply (GLOSSARY.md「开口」)."""
    llm = FakeLLM(["<ja>はいっ</ja><zh>好的</zh>"], ["<ja>うん。</ja><zh>嗯。</zh>"])

    await mano_call.run(
        llm,
        [spoken("早上好"), SleepFrame(0.2), BotStartedSpeakingFrame(), SleepFrame(0.1),
         spoken("在吗"), SleepFrame(0.3)],
    )

    assert [time is not None for _, time in mano_call.turn_records()] == [True, True]
