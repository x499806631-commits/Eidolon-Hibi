"""Per-user rate limits: text turns and voice calls count against the user's hourly limits,
whichever device they come from, driven through the backend's external API
with a clock the tests control."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest

from eidolon.config import Settings, UsageLimits
from tests.conftest import ClientFactory
from tests.browser import open_browser
from tests.fakes import START, FakeClock, FakeLLM, FakeSpeech
from tests.test_text_turn import leave_turn, send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]


def limited(settings: Settings, *, turns: int | None = 60, calls: int | None = 5) -> Settings:
    return replace(settings, usage_limits=UsageLimits(turns_per_hour=turns, calls_per_hour=calls))


async def post_turn(client: httpx.AsyncClient, conversation_id: str) -> httpx.Response:
    return await client.post(f"/api/conversations/{conversation_id}/turns", json={"text": "在吗"})


async def test_text_turns_over_the_hourly_limit_are_refused_until_the_oldest_ages_out(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    llm = FakeLLM(REPLY, REPLY, REPLY)
    client = make_client(llm, settings=limited(settings, turns=2), clock=clock)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "早上好")
    clock.advance(minutes=20)
    await send_turn(client, conversation_id, "在吗")
    clock.advance(minutes=10)

    refused = await post_turn(client, conversation_id)

    assert refused.status_code == 429
    body = refused.json()
    assert "文字对话" in body["detail"] and "每小时 2 次" in body["detail"]
    assert datetime.fromisoformat(body["retry_at"]) == START + timedelta(hours=1)
    assert refused.headers["Retry-After"] == str(30 * 60)
    assert len(llm.prompts) == 2  # the model was never asked

    clock.advance(minutes=30)
    events = await send_turn(client, conversation_id, "现在呢")
    assert events[-1]["type"] == "done"


async def test_all_of_a_users_devices_share_one_limit_and_other_users_have_their_own(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    settings = limited(settings, turns=2)
    desktop = make_client(FakeLLM(REPLY), settings=settings, clock=clock)
    phone = make_client(FakeLLM(REPLY), settings=settings, clock=clock)
    friend = make_client(FakeLLM(REPLY), "friend", settings=settings, clock=clock)
    conversation_id = await start_conversation(desktop)
    await send_turn(desktop, conversation_id, "在吗")
    await send_turn(phone, conversation_id, "在吗")

    assert (await post_turn(desktop, conversation_id)).status_code == 429
    assert (await post_turn(phone, conversation_id)).status_code == 429
    events = await send_turn(friend, await start_conversation(friend), "在吗")
    assert events[-1]["type"] == "done"


async def test_two_turns_at_once_never_both_take_the_last_turn_of_the_hour(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(REPLY, REPLY), settings=limited(settings, turns=2))
    with_mano = await start_conversation(client)
    with_kogane = await start_conversation(client, "kogane")
    await send_turn(client, with_mano, "在吗")

    statuses = sorted(
        r.status_code for r in await asyncio.gather(
            post_turn(client, with_mano), post_turn(client, with_kogane)
        )
    )

    assert statuses == [200, 429]


async def test_voice_calls_have_their_own_hourly_limit_and_renegotiating_is_free(
    make_client: ClientFactory, settings: Settings, speech: FakeSpeech
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), settings=limited(settings, calls=2), clock=clock)
    conversation_id = await start_conversation(client)
    async with open_browser() as first:
        await first.call(client, conversation_id=conversation_id)
        await first.connect()
        assert first.pc_id is not None
        renegotiation = {**await first.offer(conversation_id=conversation_id), "pc_id": first.pc_id}
        assert (await client.post("/api/offer", json=renegotiation)).status_code == 200
    clock.advance(minutes=5)
    async with open_browser() as second:  # hangs up the first, which connected: it counts
        assert (await second.call(client, conversation_id=conversation_id)).status_code == 200
        await second.connect()
    clock.advance(minutes=5)

    async with open_browser() as third:
        refused = await third.call(client, conversation_id=conversation_id)

    assert refused.status_code == 429
    assert "语音通话" in refused.json()["detail"]
    assert datetime.fromisoformat(refused.json()["retry_at"]) == START + timedelta(hours=1)
    assert len(speech.stts) == 2
    # Calls and text turns are counted apart.
    events = await send_turn(client, conversation_id, "在吗")
    assert events[-1]["type"] == "done"


def test_limits_come_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    for name in [
        "TURNS_PER_HOUR", "CALLS_PER_HOUR", "CALL_MINUTES", "TURNS_PER_CALL", "PHOTOS_PER_HOUR",
        "CONCURRENT_CALLS",
    ]:
        monkeypatch.delenv(f"EIDOLON_{name}", raising=False)
    assert UsageLimits.from_env() == UsageLimits(
        turns_per_hour=60, calls_per_hour=5, call_minutes=10, turns_per_call=40,
        photos_per_hour=5, concurrent_calls=10,
    )

    monkeypatch.setenv("EIDOLON_TURNS_PER_HOUR", "30")
    monkeypatch.setenv("EIDOLON_CALLS_PER_HOUR", "0")
    monkeypatch.setenv("EIDOLON_CALL_MINUTES", "15")
    monkeypatch.setenv("EIDOLON_TURNS_PER_CALL", "0")
    monkeypatch.setenv("EIDOLON_PHOTOS_PER_HOUR", "2")
    monkeypatch.setenv("EIDOLON_CONCURRENT_CALLS", "0")
    assert UsageLimits.from_env() == UsageLimits(
        turns_per_hour=30, calls_per_hour=None, call_minutes=15, turns_per_call=None,
        photos_per_hour=2, concurrent_calls=None,
    )

    monkeypatch.setenv("EIDOLON_CALLS_PER_HOUR", "-1")
    with pytest.raises(RuntimeError, match="EIDOLON_CALLS_PER_HOUR"):
        UsageLimits.from_env()


@pytest.mark.parametrize(
    "text",
    # The model fails; blank, rejected before the model as input too long is.
    ["在吗", " "],
    ids=["failed", "blank"],
)
async def test_a_turn_that_fails_or_is_rejected_gives_its_use_back(
    make_client: ClientFactory, settings: Settings, text: str
):
    llm = FakeLLM()  # no scripted reply: a turn that reaches the model fails
    client = make_client(llm, settings=limited(settings, turns=1), clock=FakeClock())
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, text)
    assert (events[0]["type"], events[-1]["type"]) == ("user", "error")

    llm._replies.append(list(REPLY))
    assert (await send_turn(client, conversation_id, "在吗"))[-1]["type"] == "done"


async def test_a_turn_left_before_the_reply_is_done_leaves_no_record_but_counts(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(["<ja>はい", "っ</ja>", "<zh>好", "的</zh>"], delay=0.2)
    client = make_client(llm, settings=limited(settings, turns=1), clock=FakeClock())
    conversation_id = await start_conversation(client)

    events = await leave_turn(client, conversation_id, "在吗", after="user")

    assert {"done", "error"}.isdisjoint(e["type"] for e in events)  # left mid-reply
    messages = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert messages.json() == []
    assert (await post_turn(client, conversation_id)).status_code == 429


async def test_a_call_that_cannot_be_set_up_gives_its_use_back(
    make_client: ClientFactory, settings: Settings, speech: FakeSpeech
):
    client = make_client(FakeLLM(), settings=limited(settings, calls=1), clock=FakeClock())
    conversation_id = await start_conversation(client)
    async with open_browser() as pc:
        broken = {**await pc.offer(conversation_id=conversation_id), "type": "answer"}
        failed = await client.post("/api/offer", json=broken)
    assert failed.status_code == 500
    assert failed.json()["detail"] == "无法建立通话"

    async with open_browser() as pc:
        assert (await pc.call(client, conversation_id=conversation_id)).status_code == 200
