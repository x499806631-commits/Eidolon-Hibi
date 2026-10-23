"""This fork's affinity rules (GLOSSARY.md「好感度」): each character takes one gift a day; cold,
she takes no calls, is not run into and goes on no days out."""

import pytest

import eidolon.affinity
import eidolon.gifts
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeClock
from tests.test_affinity import keep
from tests.test_delete_account import user_id
from tests.test_encounters import SURPRISED, at_lesson
from tests.test_gift_giving import TEA, draw_them, give, holding
from tests.test_gift_giving import catalog, settings  # noqa: F401 礼物目录；判断模型、好感度和礼物开着
from tests.test_reach_outs import backend_of


@pytest.fixture
def one_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eidolon.gifts, "ONE_A_DAY", True)


async def test_each_takes_one_gift_a_day(
    make_client: ClientFactory, settings: Settings, one_a_day: None  # noqa: F811
):
    clock = FakeClock()
    client = holding(make_client, clock, TEA, TEA, TEA)
    await draw_them(client, clock, 3)

    first = await give(client, "tea")
    again = await give(client, "tea")
    other = await give(client, "tea", "kogane")
    clock.advance(days=1)
    tomorrow = await give(client, "tea")

    assert [first.status_code, again.status_code, other.status_code, tomorrow.status_code] == [
        200, 409, 200, 200,
    ]
    assert again.json()["detail"] == "今天已经送过她礼物了，明天再送吧"


@pytest.fixture
def cold_keeps_away(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eidolon.affinity, "COLD_KEEPS_AWAY", True)


async def test_cold_she_is_not_run_into_nor_called_nor_invited(
    make_client: ClientFactory, settings: Settings, cold_keeps_away: None  # noqa: F811
):
    client, _, home = await at_lesson(make_client, settings, SURPRISED)
    me = user_id(settings.database_path, "producer")
    keep(settings.database_path, "producer", 15, backend_of(client).clock())

    met = await client.post("/api/outings/encounter", json={"character_id": "mano"})
    call = await client.get("/api/ice-servers", params={"conversation": home})
    affinities = backend_of(client).affinities

    assert met.status_code == 409 and "冷淡" in met.json()["detail"]
    assert await affinities.too_cold(me, "mano") is True
    assert call.status_code == 409 and call.json().get("too_cold") is True
    keep(settings.database_path, "producer", 25, backend_of(client).clock())
    assert await affinities.too_cold(me, "mano") is False
