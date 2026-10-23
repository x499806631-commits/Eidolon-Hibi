"""Going out with her (GLOSSARY.md「约会」): met on the day, stop by stop, her turns told where
they are; written down as it ends; and, never met, missed."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time

import httpx

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeBackgroundLLM, FakeClock, FakeLLM, context_of
from tests.test_delete_account import user_id
from tests.test_producer_profile import REPLY
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

DAY = date(2026, 10, 25)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute), UTC)


SUMMARY = json.dumps({
    "recap": "今天和制作人去了水族馆，水母好漂亮。",
    "memory": "櫻木真乃和制作人去水族馆约会，在水母隧道里看了很久。",
    "news": "櫻木真乃和制作人去水族馆约会了。",
    "moment": "櫻木真乃在水母隧道里和制作人一起看水母。",
}, ensure_ascii=False)


def summing_up(prompt: str) -> str:
    """The background model's answer: the day out summed up, when asked to."""
    return SUMMARY if "时的全部对话" in prompt else FakeBackgroundLLM.NOTHING


async def planned(
    make_client: ClientFactory, settings: Settings, clock: FakeClock, *replies, background=()
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    client = make_client(
        llm, clock=clock, background_llm=FakeBackgroundLLM(*background),
        settings=replace(settings, schedules=True),
    )
    assert (await client.put("/api/profile", json={"timezone": "Etc/GMT"})).status_code == 200
    conversation = await start_conversation(client, "mano")
    await send_turn(client, conversation, "你好")
    me = user_id(settings.database_path, "producer")
    await backend_of(client).dates.plan(me, "mano", DAY, "去水族馆看水母", at(17, 30))
    return client, llm, conversation


async def test_she_is_met_on_the_day_and_her_turns_are_told_where_they_are_stop_by_stop(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(19, 30))
    client, llm, conversation = await planned(make_client, settings, clock, REPLY, REPLY)

    today = (await client.get("/api/outings/today", params={"character": "mano"})).json()
    met = (await client.post("/api/outings", json={"character_id": "mano"})).json()
    await send_turn(client, conversation, "久等了")
    first = context_of(llm.prompts[-1])
    moved = (await client.post(f"/api/outings/{met['id']}/next")).json()
    await send_turn(client, conversation, "（动作：牵起她的手）好漂亮")
    second = context_of(llm.prompts[-1])

    assert today["date"]["spot"] == "aquarium" and today["date"]["can_start"] is True
    assert met["conversation_id"] == conversation and met["late_minutes"] == 90
    assert "# 约会中" in first and "入口大厅" in first and "晚到了 90 分钟" in first
    assert moved["stop"] == 1
    assert "水母隧道" in second and "晚到了" not in second


async def test_when_it_ends_it_is_written_down_and_shown_in_the_conversation(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(14))
    client, llm, conversation = await planned(
        make_client, settings, clock, REPLY, REPLY, background=[summing_up] * 6
    )
    met = (await client.post("/api/outings", json={"character_id": "mano"})).json()
    await send_turn(client, conversation, "我们走吧")

    ended = (await client.post(f"/api/outings/{met['id']}/end")).json()
    record = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    again = await client.post(f"/api/outings/{met['id']}/end")
    await send_turn(client, conversation, "今天开心吗")

    assert ended["recap"] == "今天和制作人去了水族馆，水母好漂亮。"
    [card] = [e for e in record if e.get("kind") == "outing"]
    assert card["outing"]["recap"] == ended["recap"] and card["outing"]["spot"] == "aquarium"
    assert again.status_code == 409
    assert "# 约会中" not in context_of(llm.prompts[-1])
    me = user_id(settings.database_path, "producer")
    experiences = backend_of(client).experiences
    assert experiences is not None
    kept = await experiences.of(me, "mano")
    assert [e.text for e in kept] == ["櫻木真乃和制作人去水族馆约会，在水母隧道里看了很久。"]


async def test_not_met_before_ten_nor_after_ten_at_night_and_only_on_its_day(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(9))
    client, _, _ = await planned(make_client, settings, clock)

    early = await client.post("/api/outings", json={"character_id": "mano"})
    clock.advance(hours=13, minutes=30)
    late = await client.post("/api/outings", json={"character_id": "mano"})
    clock.advance(days=1)
    gone = await client.post("/api/outings", json={"character_id": "mano"})

    assert [early.status_code, late.status_code, gone.status_code] == [409, 409, 409]


async def test_never_met_she_says_he_never_came_once_the_day_is_over(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(18))
    missed = "[sadly] 待ってたのに…… <zh>我一直在等你……</zh>"
    client, llm, conversation = await planned(make_client, settings, clock, f"<ja>{missed}")
    trip_photos = backend_of(client).trip_photos

    await trip_photos.run_due()
    before = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    clock.advance(hours=4, minutes=30)
    await trip_photos.run_due()
    after = (await client.get(f"/api/conversations/{conversation}/messages")).json()

    assert len(after) == len(before) + 1
    assert after[-1]["type"] == "reach_out"
    assert "一直没来" in str(llm.prompts[-1])
