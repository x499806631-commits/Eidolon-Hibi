"""Going out with her (GLOSSARY.md「约会」): she says she is waiting as its time comes; met on
the day, in a conversation of its own, she speaking first, stop by stop, her turns told where
they are; written down as it ends, her affinity moved by how she liked it; never met, missed."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time

import httpx

from eidolon.config import Settings
from eidolon.engine import WANTS_TO_GREET_ON_DATE, WANTS_TO_SAY_WAITING
from tests.conftest import ClientFactory
from tests.fakes import FakeBackgroundLLM, FakeClock, FakeLLM, context_of
from tests.test_delete_account import user_id
from tests.test_producer_profile import REPLY
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

DAY = date(2026, 10, 25)
HELLO = "<ja>やっと来た！</ja><zh>你终于来了！</zh>"


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute), UTC)


SUMMARY = json.dumps({
    "recap": "今天和制作人去了水族馆，水母好漂亮。",
    "memory": "櫻木真乃和制作人去水族馆约会，在水母隧道里看了很久。",
    "news": "櫻木真乃和制作人去水族馆约会了。",
    "moment": "櫻木真乃在水母隧道里和制作人一起看水母。",
    "score": 6,
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
    await backend_of(client).dates.plan(me, "mano", DAY, "去水族馆看水母", at(13))
    return client, llm, conversation


def spy_on_affinity(client: httpx.AsyncClient, moved: list[float]) -> None:
    """Keep each step the day out moves her affinity by (affinity being off in these tests)."""
    async def moved_by(user_id: str, character_id: str, step: float) -> None:
        moved.append(step)

    backend_of(client).affinities.moved_by = moved_by  # type: ignore[method-assign]


async def record(client: httpx.AsyncClient, conversation: str) -> list[dict]:
    return (await client.get(f"/api/conversations/{conversation}/messages")).json()


async def test_met_in_a_conversation_of_its_own_she_speaks_first_and_is_told_where_they_are(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(19, 30))
    client, llm, home = await planned(make_client, settings, clock, HELLO, REPLY, REPLY)

    today = (await client.get("/api/outings/today", params={"character": "mano"})).json()
    met = (await client.post("/api/outings", json={"character_id": "mano"})).json()
    opening = llm.prompts[-1]
    await send_turn(client, met["conversation_id"], "久等了")
    first = context_of(llm.prompts[-1])
    moved = (await client.post(f"/api/outings/{met['id']}/next")).json()
    await send_turn(client, met["conversation_id"], "（动作：牵起她的手）好漂亮")
    second = context_of(llm.prompts[-1])
    listed = (await client.get("/api/conversations", params={"character_id": "mano"})).json()

    assert today["date"]["spot"] == "aquarium" and today["date"]["can_start"] is True
    assert met["conversation_id"] != home and met["late_minutes"] == 90
    assert opening[-1].content.endswith(WANTS_TO_GREET_ON_DATE) and "晚到了 90 分钟" in str(opening)
    assert (await record(client, met["conversation_id"]))[0].get("type") == "reach_out"
    assert "# 约会中" in first and "入口大厅" in first and "面对面" in first
    assert moved["stop"] == 1 and "水母隧道" in second
    assert "约会" not in str([m for m in await record(client, home) if m.get("type") == "reach_out"])
    assert listed[0]["id"] == home and listed[1]["id"] == met["conversation_id"]  # 当前对话照旧是当前的
    assert "和櫻木真乃约会" in listed[1]["title"]


async def test_when_it_ends_it_is_written_down_her_affinity_moved_and_carded_at_home(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(14))
    client, llm, home = await planned(
        make_client, settings, clock, HELLO, REPLY, REPLY, background=[summing_up] * 6
    )
    me = user_id(settings.database_path, "producer")
    moved: list[float] = []
    spy_on_affinity(client, moved)
    met = (await client.post("/api/outings", json={"character_id": "mano"})).json()
    await send_turn(client, met["conversation_id"], "我们走吧")

    ended = (await client.post(f"/api/outings/{met['id']}/end")).json()
    [card] = [e for e in await record(client, home) if e.get("kind") == "outing"]
    again = await client.post(f"/api/outings/{met['id']}/end")
    await send_turn(client, home, "今天开心吗")

    assert ended["recap"] == "今天和制作人去了水族馆，水母好漂亮。"
    assert card["outing"]["recap"] == ended["recap"] and card["outing"]["spot"] == "aquarium"
    assert again.status_code == 409
    told = context_of(llm.prompts[-1])
    assert "# 约会中" not in told and "你们的约会结束了" in str(llm.prompts[-1])
    assert moved == [6.0]
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


async def test_she_says_she_is_waiting_and_never_met_that_he_never_came(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(13, 5))
    waiting = "<ja>待ってるね</ja><zh>我在等你哦</zh>"
    missed = "<ja>来なかった……</ja><zh>你没来……</zh>"
    client, llm, conversation = await planned(make_client, settings, clock, waiting, missed)
    me = user_id(settings.database_path, "producer")
    trip_photos = backend_of(client).trip_photos
    moved: list[float] = []
    spy_on_affinity(client, moved)

    await trip_photos.run_due()
    told_waiting = llm.prompts[-1][-1].content
    await trip_photos.run_due()  # 只说一次
    clock.advance(hours=9)
    await trip_photos.run_due()
    reach_outs = [m for m in await record(client, conversation) if m.get("type") == "reach_out"]

    assert told_waiting.endswith(WANTS_TO_SAY_WAITING)
    assert [r["segments"][0]["zh"] for r in reach_outs] == ["我在等你哦", "你没来……"]
    assert moved == [-10.0] and me


async def test_one_of_them_at_a_place_a_day(make_client: ClientFactory, settings: Settings):
    client, _, _ = await planned(make_client, settings, FakeClock(at(9)))
    me = user_id(settings.database_path, "producer")
    dates = backend_of(client).dates

    assert await dates.spot_taken(me, "kogane", DAY, "aquarium") is True
    assert await dates.spot_taken(me, "mano", DAY, "aquarium") is False  # 她自己的那次
    assert await dates.spot_taken(me, "kogane", DAY, "zoo") is False
