"""Reset cards (GLOSSARY.md「重置卡」): the user's own, never given to a character. One brings
every character below trust with the user (distant, cold, in a cooldown) back to the normal
value, out of any cooldown, so that she can be talked to straight away; those in trust or
above, if only just, are left as they are. Who is below trust is as of the moment it is
used, having recovered: none left, it is refused and the card kept, as it is with no card.
It leaves no trace: no gift record, no conversation event, nothing in her prompt; what
happened before (her hanging up) stays. The gifts page reads how many there are and whom one
would bring back now. Closed, none can be used. Driven through the backend's external API,
the random source drawing the cards and the clock moved by hand."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM, FakeRandom, system_of
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import keep, status
from tests.test_affinity_prompt import IN_HER_OWN_WAY, about_now
from tests.test_conversation_events import HUNG_UP, hang_up, record, shape
from tests.test_conversation_history import history, told
from tests.test_delete_account import user_id
from tests.test_gift_backpack import RESET_CARD, state
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
USE = "/api/gifts/resets"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity and gifts on in `.env`."""
    return replace(
        settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True
    )


async def holding_cards(
    make_client: ClientFactory, clock: FakeClock, cards: int, llm: FakeLLM | None = None
) -> httpx.AsyncClient:
    """A client whose user has drawn `cards` reset cards (up to 7), the days it takes having
    gone by."""
    client = make_client(llm or FakeLLM(REPLY), clock=clock, random=FakeRandom(*[RESET_CARD] * cards))
    assert (await client.get("/api/gifts")).status_code == 200
    if cards:
        clock.advance(days=cards - 1)
        res = await client.post("/api/gifts/draws", json={"all": True})
        assert res.status_code == 200, res.text
    return client


def kept(database: Path, character: str) -> tuple[float, bool]:
    """The producer's affinity with the character as kept, and whether in a cooldown."""
    with sqlite3.connect(database) as db:
        value, cooling = db.execute(
            "SELECT value, cooling FROM affinities WHERE user_id = ? AND character_id = ?",
            (user_id(database, "producer"), character),
        ).fetchone()
    return value, bool(cooling)


async def test_one_brings_those_below_trust_back_to_it_and_leaves_the_rest(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = await holding_cards(make_client, clock, 2)
    keep(settings.database_path, "producer", 25, clock.now)
    keep(settings.database_path, "producer", 40, clock.now, character="kogane")  # just in trust
    before = await state(client)

    res = await client.post(USE)

    assert (before["reset_cards"], before["resettable"]) == (
        2, [{"id": "mano", "tier": "distant", "cooling": False}]
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"brought_back": ["mano"], "reset_cards": 1}
    assert (await status(client)).json() == {"tier": "trust", "cooldown_until": None}
    assert kept(settings.database_path, "mano") == (50, False)
    assert kept(settings.database_path, "kogane") == (40, False)
    after = await state(client)
    assert (after["reset_cards"], after["resettable"]) == (1, [])


async def test_one_in_a_cooldown_can_be_talked_to_straight_away_none_the_wiser(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    llm = FakeLLM(REPLY)
    client = await holding_cards(make_client, clock, 1, llm)
    conversation = await start_conversation(client)
    hang_up(settings.database_path, conversation, clock.now)
    keep(settings.database_path, "producer", 0, clock.now, cooling=True)

    assert (await client.post(USE)).status_code == 200
    turn = await send_turn(client, conversation, "还在生气吗")

    assert turn[-1]["type"] == "done"
    assert shape(await record(client, conversation)) == ["hung_up", "user:还在生气吗", "character:好的"]
    assert history(llm) == [("user", f"{HUNG_UP}\n还在生气吗")]
    # In trust, her prompt is as it always is: nothing of the card, nor of how she was.
    assert IN_HER_OWN_WAY not in about_now(llm)
    assert "重置" not in system_of(llm.prompts[-1]) + told(llm)


async def test_with_no_card_it_is_refused(make_client: ClientFactory, settings: Settings):
    clock = FakeClock()
    client = await holding_cards(make_client, clock, 0)
    keep(settings.database_path, "producer", 0, clock.now, cooling=True)

    res = await client.post(USE)

    assert res.status_code == 409
    assert res.json()["detail"] == "没有重置卡了"
    assert (await status(client)).json()["cooldown_until"] is not None


async def test_with_no_one_below_trust_as_it_is_used_it_is_refused_and_the_card_kept(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = await holding_cards(make_client, clock, 1)
    keep(settings.database_path, "producer", 35, clock.now)
    assert [c["id"] for c in (await state(client))["resettable"]] == ["mano"]

    keep(settings.database_path, "producer", 41, clock.now)  # back in trust as the confirmation is open
    res = await client.post(USE)

    assert res.status_code == 409
    assert res.json()["detail"] == "现在没有低于信任的角色"
    read = await state(client)
    assert (read["reset_cards"], read["resettable"]) == (1, [])


async def test_closed_none_can_be_used(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = await holding_cards(make_client, clock, 1)
    keep(settings.database_path, "producer", 0, clock.now, cooling=True)

    async with admin_beside(client, github) as admin:
        assert (await admin.put("/api/admin/config/affinity", json={"enabled": False})).status_code == 200
        res = await client.post(USE)
        read = await state(client)

    assert res.status_code == 409
    assert res.json()["gifts_closed"] is True
    assert (read["reset_cards"], read["resettable"]) == (1, [])
    assert kept(settings.database_path, "mano") == (0, True)
