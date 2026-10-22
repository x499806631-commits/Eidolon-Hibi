"""Draw chances (GLOSSARY.md「抽奖」): one a day at midnight Beijing time, wherever the user
is, kept up to seven, given as the gifts' state is read rather than on a schedule; the state
tells when the next is given. The days affinity and gifts are off give none, and the day
they open again gives one. Off, the state is read all the same, saying so. Deleting the user deletes their
chances; when affinity and gifts last opened is no one's and stays. Driven through the
backend's external API, with the clock moved."""

import sqlite3
from dataclasses import replace
from datetime import datetime

import httpx
import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_admin_users import admin_beside

AFFINITY = "/api/admin/config/affinity"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity and gifts on in `.env`."""
    return replace(
        settings,
        admins=frozenset({("github", "9001")}),
        judge=FAKE_JUDGE,
        affinity_and_gifts=True,
    )


async def state(client: httpx.AsyncClient) -> dict:
    res = await client.get("/api/gifts")
    assert res.status_code == 200, res.text
    return res.json()


async def draws(client: httpx.AsyncClient) -> int:
    return (await state(client))["draws"]


async def test_a_new_user_has_one_draw_the_day_they_come_and_nothing_else_yet(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), clock=FakeClock())

    assert await state(client) == {
        "open": True,
        "draws": 1,
        "max_draws": 7,
        "backpack": [],
        "reset_cards": 0,
        "tickets": [],
        "to_rare": 20,
        "next_draw_at": "2026-09-25T16:00:00+00:00",
        "resettable": [],
        "characters": [
            {"id": "mano", "tier": "trust", "cooling": False, "cooldown_until": None},
            {"id": "kogane", "tier": "trust", "cooling": False, "cooldown_until": None},
        ],
    }


async def test_reading_again_the_same_day_gives_no_more(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)

    first = await draws(client)
    clock.advance(hours=3)  # 23:00 in Shanghai

    assert [first, await draws(client), await draws(client)] == [1, 1, 1]


async def test_days_away_are_made_up_for_when_they_come_back(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)

    await draws(client)
    clock.advance(days=3)

    assert await draws(client) == 4


async def test_no_more_than_seven_are_kept(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)

    await draws(client)
    clock.advance(days=10)

    assert await draws(client) == 7


async def test_the_day_turns_at_midnight_where_the_producer_is_by_their_profile(
    make_client: ClientFactory,
):
    clock = FakeClock(datetime.fromisoformat("2026-09-25T14:30:00+00:00"))  # 22:30 in Beijing
    client = make_client(FakeLLM(), clock=clock)
    client.headers["X-Timezone"] = "Asia/Tokyo"
    await client.put("/api/profile", json={"timezone": "Asia/Tokyo"})

    before = await draws(client)
    clock.advance(hours=1)  # 00:30 on the 26th in Tokyo, 23:30 on the 25th in Beijing
    in_tokyo = await draws(client)
    next_at = (await state(client))["next_draw_at"]

    assert [before, in_tokyo] == [1, 2]
    assert next_at == "2026-09-26T15:00:00+00:00"  # midnight in Tokyo


async def test_without_a_time_zone_of_their_own_the_next_draw_is_at_midnight_in_beijing(make_client: ClientFactory):
    clock = FakeClock(datetime.fromisoformat("2026-09-25T16:30:00+00:00"))  # 00:30 the 26th
    client = make_client(FakeLLM(), clock=clock)
    client.headers["X-Timezone"] = "America/Los_Angeles"  # the 25th there, which counts not

    first = (await state(client))["next_draw_at"]
    clock.advance(hours=23)  # 23:30 the 26th
    later = (await state(client))["next_draw_at"]

    assert [first, later] == ["2026-09-26T16:00:00+00:00"] * 2


async def test_the_days_it_is_off_give_none_and_the_day_it_opens_again_gives_one(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    await draws(client)
    clock.advance(days=1)
    await draws(client)  # 2

    async with admin_beside(client, github) as admin:
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        clock.advance(days=3)
        closed = await state(client)
        clock.advance(days=2)
        still_closed = await state(client)
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200
    opened = await draws(client)
    clock.advance(days=1)

    assert (closed["open"], closed["draws"]) == (False, 2)
    assert (still_closed["open"], still_closed["draws"]) == (False, 2)
    assert [opened, await draws(client)] == [3, 4]


async def test_off_the_state_is_read_all_the_same_saying_so(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    await draws(make_client(FakeLLM(), clock=clock))
    clock.advance(days=2)

    closed = await state(
        make_client(FakeLLM(), clock=clock, settings=replace(settings, affinity_and_gifts=False))
    )

    assert closed == {
        "open": False,
        "draws": 1,
        "max_draws": 7,
        "backpack": [],
        "reset_cards": 0,
        "tickets": [],
        "to_rare": 20,
        "next_draw_at": None,
        "resettable": [],
        "characters": [],
    }


async def test_off_a_new_user_is_given_none_until_it_opens(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    closed = await draws(
        make_client(FakeLLM(), clock=clock, settings=replace(settings, judge=None))
    )

    clock.advance(days=3)
    opened = await draws(make_client(FakeLLM(), clock=clock))

    assert [closed, opened] == [0, 1]


async def test_starting_with_the_judge_model_once_more_opens_it_that_day(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    await draws(make_client(FakeLLM(), clock=clock))  # 1
    clock.advance(days=1)
    await draws(make_client(FakeLLM(), clock=clock))  # 2

    make_client(FakeLLM(), clock=clock, settings=replace(settings, judge=None))
    clock.advance(days=4)
    opened = await draws(make_client(FakeLLM(), clock=clock))

    assert opened == 3


async def test_deleting_the_user_deletes_their_chances_but_not_when_it_opened(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), clock=FakeClock())
    await draws(client)

    assert (await client.post("/api/account/delete", json={"confirm": True})).status_code == 204

    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM gift_balances").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM gifts_opened").fetchone() == (1,)

