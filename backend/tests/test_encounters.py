"""Running into her (GLOSSARY.md「偶遇」): from the map, where her schedule has her now, in a
conversation of its own, she speaking first, surprised; once a day; written down as it ends,
her affinity moved a little by how she liked it."""

import json
from dataclasses import replace

import httpx

from eidolon.config import Settings
from eidolon.engine import WANTS_TO_GREET_ENCOUNTER
from tests.conftest import ClientFactory
from tests.fakes import FakeBackgroundLLM, FakeClock, FakeLLM, context_of
from tests.test_delete_account import user_id
from tests.test_map import NOON, keep, slot
from tests.test_outings import record, spy_on_affinity
from tests.test_producer_profile import REPLY
from tests.test_text_turn import send_turn, start_conversation

SURPRISED = "<ja>プロデューサーさん、どうしてここに？</ja><zh>制作人怎么会在这里？</zh>"
SUMMARY = json.dumps({
    "recap": "在练习室碰到了制作人，他给我带了水。",
    "memory": "櫻木真乃在练习室碰到了制作人。",
    "news": "櫻木真乃在练习室碰到了制作人。",
    "moment": "櫻木真乃在练习室碰到了制作人。",
    "score": 9,
}, ensure_ascii=False)


def summing_up(prompt: str) -> str:
    return SUMMARY if "碰巧遇到" in prompt and "时的全部对话" in prompt else FakeBackgroundLLM.NOTHING


async def at_lesson(
    make_client: ClientFactory, settings: Settings, *replies, background=()
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    client = make_client(
        llm, clock=FakeClock(NOON), background_llm=FakeBackgroundLLM(*background),
        settings=replace(settings, schedules=True),
    )
    assert (await client.put("/api/profile", json={"timezone": "Etc/GMT"})).status_code == 200
    home = await start_conversation(client, "mano")
    await send_turn(client, home, "你好")
    me = user_id(settings.database_path, "producer")
    await keep(client, me, "mano", [slot("舞蹈课", "练习室", "lesson")])
    await keep(client, me, "kogane", [slot("睡午觉", "家里", "rest")])
    return client, llm, home


async def test_run_into_her_where_she_is_she_speaks_first_in_a_conversation_of_its_own(
    make_client: ClientFactory, settings: Settings
):
    client, llm, home = await at_lesson(make_client, settings, SURPRISED, REPLY)

    met = (await client.post("/api/outings/encounter", json={"character_id": "mano"})).json()
    opening = llm.prompts[-1]
    today = (await client.get("/api/outings/today", params={"character": "mano"})).json()
    await send_turn(client, met["conversation_id"], "路过看看你")
    told = context_of(llm.prompts[-1])
    listed = (await client.get("/api/conversations", params={"character_id": "mano"})).json()

    assert met["kind"] == "encounter" and met["spot"] == "lesson"
    assert opening[-1].content.endswith(WANTS_TO_GREET_ENCOUNTER) and "练习室" in str(opening)
    assert (await record(client, met["conversation_id"]))[0]["segments"][0]["zh"] == "制作人怎么会在这里？"
    assert today["outing"]["id"] == met["id"]
    assert "# 偶遇中" in told and "练习室" in told
    assert listed[0]["id"] == home and "在练习室偶遇櫻木真乃" in listed[1]["title"]


async def test_not_where_she_cannot_be_met_and_once_a_day(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await at_lesson(make_client, settings, SURPRISED, background=[summing_up] * 6)

    at_home = await client.post("/api/outings/encounter", json={"character_id": "kogane"})
    me = user_id(settings.database_path, "producer")
    await keep(client, me, "kogane", [slot("开会", "283事务所", "work")])
    met = (await client.post("/api/outings/encounter", json={"character_id": "mano"})).json()
    again = (await client.post("/api/outings/encounter", json={"character_id": "mano"})).json()
    other = await client.post("/api/outings/encounter", json={"character_id": "kogane"})
    await client.post(f"/api/outings/{met['id']}/end")
    after = await client.post("/api/outings/encounter", json={"character_id": "mano"})

    assert at_home.status_code == 409
    assert again["id"] == met["id"]  # 还没结束：还是那一次
    assert other.status_code == 409 and after.status_code == 409


async def test_when_it_ends_her_affinity_moves_at_most_five_and_no_photos(
    make_client: ClientFactory, settings: Settings
):
    client, _, home = await at_lesson(make_client, settings, SURPRISED, REPLY, background=[summing_up] * 6)
    moved: list[float] = []
    spy_on_affinity(client, moved)
    met = (await client.post("/api/outings/encounter", json={"character_id": "mano"})).json()
    await send_turn(client, met["conversation_id"], "给你带了水")

    photo = await client.post(f"/api/outings/{met['id']}/photo", json={"wish": ""})
    ended = (await client.post(f"/api/outings/{met['id']}/end")).json()
    [card] = [e for e in await record(client, home) if e.get("kind") == "outing"]

    assert photo.status_code == 409
    assert moved == [5.0]
    assert ended["recap"] == "在练习室碰到了制作人，他给我带了水。"
    assert card["outing"]["kind"] == "encounter"
