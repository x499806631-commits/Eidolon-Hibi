"""Drawing gifts (GLOSSARY.md「抽奖」「礼物」「重置卡」): each draw uses a chance and gives a
reset card or a gift, the rarer the less likely, any of a rarity's not retired as likely as
another; drawing them all uses every chance there is. Gifts drawn stack in the backpack,
retired ones held all the same. Closed, draws are refused in a way the page tells apart,
and what the user has is kept for when they open. Driven through the backend's external
API, the random source giving the numbers each test means."""

import asyncio
import sqlite3
from dataclasses import replace

import httpx
import pytest

from eidolon.config import Settings
from eidolon.gift_catalog import GIFTS_FILE
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM, FakeRandom
from tests.test_gift_catalog import gift

RESET_CARD, COMMON, UNCOMMON, RARE, TICKET = 0, 1, 70, 90, 95
"""A number for each of what a draw can give, the first of its range out of 100."""

FIRST, SECOND = 0, 1
"""Which of a rarity's gifts not retired a draw gives."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


@pytest.fixture(autouse=True)
def catalog(characters: CharacterConfig) -> None:
    """Two common gifts not retired with one retired between them, and one of each rarer."""
    characters.write(
        GIFTS_FILE,
        gift("cake") + gift("candy", retired=True) + gift("tea", category="drink")
        + gift("cocoa", "uncommon") + gift("plush", "rare", "trinket"),
    )


async def draw(client: httpx.AsyncClient, *, all: bool = False) -> dict:
    res = await client.post("/api/gifts/draws", json={"all": all})
    assert res.status_code == 200, res.text
    return res.json()


async def state(client: httpx.AsyncClient) -> dict:
    res = await client.get("/api/gifts")
    assert res.status_code == 200, res.text
    return res.json()


def gift_drawn(id: str) -> dict:
    return {"kind": "gift", "gift_id": id}


CARD_DRAWN = {"kind": "reset_card"}


@pytest.mark.parametrize(
    ("numbers", "drawn"),
    [
        ((RESET_CARD,), CARD_DRAWN),
        ((COMMON, FIRST), gift_drawn("cake")),
        ((66, FIRST), gift_drawn("cake")),
        ((UNCOMMON, FIRST), gift_drawn("cocoa")),
        ((89, FIRST), gift_drawn("cocoa")),
        ((RARE, FIRST), gift_drawn("plush")),
        ((94, FIRST), gift_drawn("plush")),
        ((TICKET, FIRST), {"kind": "ticket", "ticket": "liked_gift"}),
        ((99, FIRST), {"kind": "ticket", "ticket": "liked_gift"}),
    ],
)
async def test_a_draw_gives_what_the_number_falls_on(
    make_client: ClientFactory, numbers: tuple[int, ...], drawn: dict
):
    client = make_client(FakeLLM(), clock=FakeClock(), random=FakeRandom(*numbers))

    assert await draw(client) == {"drawn": [drawn], "draws": 0}


async def test_a_retired_gift_is_never_drawn(make_client: ClientFactory):
    client = make_client(FakeLLM(), clock=FakeClock(), random=FakeRandom(COMMON, SECOND))

    assert (await draw(client))["drawn"] == [gift_drawn("tea")]  # candy, retired, is passed over


async def test_a_retired_gift_already_had_is_still_in_the_backpack(
    make_client: ClientFactory, characters: CharacterConfig
):
    clock = FakeClock()
    await draw(make_client(FakeLLM(), clock=clock, random=FakeRandom(UNCOMMON, FIRST)))
    characters.write(
        GIFTS_FILE,
        gift("cake") + gift("cocoa", "uncommon", retired=True) + gift("milk", "uncommon")
        + gift("plush", "rare"),
    )

    after = await state(make_client(FakeLLM(), clock=clock))

    assert after["backpack"] == [{"gift_id": "cocoa", "count": 1}]


async def test_drawing_them_all_uses_every_chance_and_what_they_give_stacks_in_the_backpack(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(
        FakeLLM(), clock=clock,
        random=FakeRandom(UNCOMMON, FIRST, COMMON, FIRST, RESET_CARD, UNCOMMON, FIRST),
    )
    await state(client)
    clock.advance(days=3)

    drawn = await draw(client, all=True)

    assert drawn == {
        "drawn": [gift_drawn("cocoa"), gift_drawn("cake"), CARD_DRAWN, gift_drawn("cocoa")],
        "draws": 0,
    }
    after = await state(client)
    assert after["backpack"] == [
        {"gift_id": "cake", "count": 1}, {"gift_id": "cocoa", "count": 2},
    ]
    assert (after["draws"], after["reset_cards"]) == (0, 1)


async def test_a_draw_first_gives_the_chances_due(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock, random=FakeRandom(COMMON, FIRST, COMMON, FIRST))
    await draw(client)
    clock.advance(days=2)

    assert (await draw(client))["draws"] == 1


async def test_with_no_chances_left_a_draw_is_refused(make_client: ClientFactory):
    client = make_client(FakeLLM(), clock=FakeClock(), random=FakeRandom(COMMON, FIRST))
    await draw(client)

    res = await client.post("/api/gifts/draws", json={"all": True})

    assert res.status_code == 409
    assert "gifts_closed" not in res.json()
    after = await state(client)
    assert (after["draws"], after["backpack"]) == (0, [{"gift_id": "cake", "count": 1}])


async def test_two_tabs_drawing_at_once_draw_no_more_than_there_are_chances(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), clock=FakeClock(), random=FakeRandom(COMMON, FIRST))
    await state(client)

    results = await asyncio.gather(
        *(client.post("/api/gifts/draws", json={"all": all}) for all in (False, True))
    )

    assert sorted(r.status_code for r in results) == [200, 409]
    assert (await state(client))["backpack"] == [{"gift_id": "cake", "count": 1}]


async def test_closed_a_draw_is_refused_saying_so_and_what_was_had_is_kept_for_when_it_opens(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    await draw(make_client(FakeLLM(), clock=clock, random=FakeRandom(RESET_CARD)))
    clock.advance(days=1)
    await draw(make_client(FakeLLM(), clock=clock, random=FakeRandom(RARE, FIRST)))
    clock.advance(days=1)
    await state(make_client(FakeLLM(), clock=clock))  # one more, kept

    closed = make_client(
        FakeLLM(), clock=clock, settings=replace(settings, affinity_and_gifts=False)
    )
    res = await closed.post("/api/gifts/draws", json={"all": True})
    reopened = await state(make_client(FakeLLM(), clock=clock))

    assert res.status_code == 409
    assert res.json()["gifts_closed"] is True
    assert reopened["backpack"] == [{"gift_id": "plush", "count": 1}]
    assert (reopened["draws"], reopened["reset_cards"]) == (1, 1)


async def test_deleting_the_user_deletes_their_backpack(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), clock=FakeClock(), random=FakeRandom(COMMON, FIRST))
    await draw(client)

    assert (await client.post("/api/account/delete", json={"confirm": True})).status_code == 204

    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM gift_backpacks").fetchone() == (0,)
