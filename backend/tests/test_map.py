"""The map (GLOSSARY.md「地图」): where on it each idol is now, read off her schedule, and the
user's days out at the places they name."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest

from eidolon.city_map import place_of, spot_of
from eidolon.config import Settings
from eidolon.schedules import Slot, SlotKind
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeLLM
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of

TODAY = date(2026, 10, 23)
NOON = datetime.combine(TODAY, time(12), UTC)


def slot(what: str, where: str, kind: SlotKind) -> Slot:
    return Slot(time(10), time(14), what, where, kind)


@pytest.mark.parametrize(
    ("what", "where", "kind", "place"),
    [
        ("和制作人去水族馆", "品川的水族馆", "private", "aquarium"),
        ("拍杂志", "摄影棚", "work", "studio"),
        ("开会", "283事务所", "work", "office"),
        ("舞蹈课", "练习室", "lesson", "lesson"),
        ("上课", "学校", "school", "school"),
        ("彩排", "会场", "live", "venue"),
        ("睡午觉", "家里", "rest", "home"),
        ("打游戏", "自己房间", "private", "home"),
        ("和朋友见面", "车站前", "private", "town"),
        ("和朋友逛街", "原宿", "private", "shopping"),
    ],
)
def test_where_on_the_map_is_read_off_her_schedule(
    what: str, where: str, kind: SlotKind, place: str
):
    assert place_of(slot(what, where, kind)) == place


def test_nothing_planned_is_out_and_about_and_a_day_away_is_at_the_edge():
    assert place_of(None) == "town"
    assert place_of(slot("拍摄", "摄影棚", "work"), away=True) == "away"


def test_a_day_out_is_at_the_place_it_names_if_the_map_has_it():
    assert spot_of("一起去上野动物园看熊猫") == "zoo"
    assert spot_of("去东京塔看夜景") == "tower"
    assert spot_of("在家做饭") is None


async def keep(client: httpx.AsyncClient, me: str, character_id: str, slots: list[Slot], **day) -> None:
    await backend_of(client).schedules.keep(me, character_id, TODAY, slots, [], **day)


async def test_the_map_has_each_idol_with_a_schedule_where_it_says_and_the_days_out(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(
        FakeLLM(), clock=FakeClock(NOON), settings=replace(settings, schedules=True)
    )
    assert (await client.put("/api/profile", json={"timezone": "Etc/GMT"})).status_code == 200
    me = user_id(settings.database_path, "producer")
    await keep(client, me, "mano", [slot("拍杂志", "摄影棚", "work")])
    await keep(client, me, "kogane", [slot("外景", "大阪", "work")], trip="大阪")
    await backend_of(client).dates.plan(
        me, "mano", TODAY + timedelta(days=2), "去水族馆看水母", NOON + timedelta(days=2)
    )

    got = (await client.get("/api/map")).json()

    assert got["schedules"] is True
    assert sorted((i["character_id"], i["place"]) for i in got["idols"]) == [
        ("kogane", "away"), ("mano", "studio"),
    ]
    mano = next(i for i in got["idols"] if i["character_id"] == "mano")
    assert (mano["what"], mano["until"]) == ("拍杂志", "14:00")
    assert got["dates"] == [{
        "character_id": "mano", "day": (TODAY + timedelta(days=2)).isoformat(),
        "what": "去水族馆看水母", "spot": "aquarium",
    }]


async def test_with_schedules_off_nobody_is_on_the_map_but_the_days_out_are(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(
        FakeLLM(), clock=FakeClock(NOON), settings=replace(settings, schedules=True)
    )
    me = user_id(settings.database_path, "producer")
    await keep(client, me, "mano", [slot("拍杂志", "摄影棚", "work")])
    await backend_of(client).dates.plan(me, "mano", TODAY + timedelta(days=1), "看电影", NOON)
    switched = await client.put("/api/account/features/schedules", json={"on": False})
    assert switched.status_code == 204

    got = (await client.get("/api/map")).json()

    assert (got["schedules"], got["idols"]) == (False, [])
    assert [d["spot"] for d in got["dates"]] == ["cinema"]
