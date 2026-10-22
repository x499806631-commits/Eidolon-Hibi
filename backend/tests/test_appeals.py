"""Appeals (GLOSSARY.md「我不是这个意思」): her latest reply, as its turn lowered her affinity,
can be appealed once, a few times a day with every character together. What the turn took
off is given back, it no longer counts towards her ending the conversation, and as she
replies next she is told the user did not mean it so; never in a cooldown, nor for a reply
that is not her latest or did not lower it. Driven through the backend's external API."""

import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from eidolon.affinity import EXPLAINED
from eidolon.appeals import APPEALS_PER_DAY
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeClock,
    FakeLLM,
    FakeServiceClients,
    Judging,
    context_of,
    level,
)
from tests.test_affinity import keep
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model, and affinity on in `.env`."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


class Talk:
    def __init__(
        self, make_client: ClientFactory, settings: Settings, judgings: list[Judging]
    ) -> None:
        self.llm = FakeLLM(*[REPLY] * len(judgings))
        clients = FakeServiceClients()
        clients.judge.judgings = list(judgings)
        self.clock = FakeClock()
        self.client = make_client(self.llm, clients=clients, clock=self.clock, settings=settings)
        self.database = settings.database_path

    async def start(self) -> None:
        self.conversation = await start_conversation(self.client)
        keep(self.database, "producer", 50, self.clock.now)

    async def turn(self, text: str = "嗯") -> int:
        """Her reply's id."""
        events = await send_turn(self.client, self.conversation, text)
        return next(e for e in events if e["type"] == "done")["message"]["id"]

    async def appeal(self, reply: int) -> httpx.Response:
        return await self.client.post(f"/api/messages/{reply}/appeal")


def value(database: Path) -> float:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT value FROM affinities WHERE character_id = 'mano'").fetchone()[0]


def hurts(database: Path) -> list[float]:
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT hurt FROM messages WHERE role = 'user' ORDER BY id").fetchall()
    return [row[0] for row in rows]


async def test_what_a_turn_took_off_is_given_back_once(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(0)])
    await talk.start()
    reply = await talk.turn()
    assert value(talk.database) == 45

    res = await talk.appeal(reply)

    assert res.status_code == 200
    assert res.json() == {
        "affinity": {"tier": "trust", "cooldown_until": None}, "left": APPEALS_PER_DAY - 1,
    }
    assert value(talk.database) == 50
    assert hurts(talk.database) == [0]  # no longer counts towards her ending it
    messages = (await talk.client.get(f"/api/conversations/{talk.conversation}/messages")).json()
    assert messages[-1]["appealed"] is True
    again = await talk.appeal(reply)
    assert again.status_code == 409 and again.json()["code"] == "appealed"
    assert value(talk.database) == 50


async def test_she_is_told_as_she_replies_next_and_only_then(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(0), level(2), level(2)])
    await talk.start()
    reply = await talk.turn()
    assert (await talk.appeal(reply)).status_code == 200

    await talk.turn()
    await talk.turn()

    told = [EXPLAINED in context_of(prompt) for prompt in talk.llm.prompts]
    assert told == [False, True, False]


async def test_only_her_latest_reply_and_one_that_lowered_it(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(0), level(3)])
    await talk.start()
    hurtful = await talk.turn()
    happy = await talk.turn()

    late = await talk.appeal(hurtful)
    assert late.status_code == 409 and late.json()["code"] == "not_latest"
    unchanged = await talk.appeal(happy)
    assert unchanged.status_code == 409 and unchanged.json()["code"] == "no_change"


async def test_a_few_a_day_with_every_character_together(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(0)] * (APPEALS_PER_DAY + 2))
    await talk.start()
    for _ in range(APPEALS_PER_DAY):
        assert (await talk.appeal(await talk.turn())).status_code == 200
    assert (await talk.client.get("/api/appeals")).json() == {"left": 0}

    refused = await talk.appeal(await talk.turn())
    assert refused.status_code == 409 and refused.json()["code"] == "none_left"

    talk.clock.advance(days=1)
    assert (await talk.client.get("/api/appeals")).json() == {"left": APPEALS_PER_DAY}
    assert (await talk.appeal(await talk.turn())).status_code == 200


async def test_never_in_a_cooldown(make_client: ClientFactory, settings: Settings):
    talk = Talk(make_client, settings, [level(0)])
    await talk.start()
    reply = await talk.turn()
    keep(talk.database, "producer", 2, talk.clock.now, cooling=True)

    res = await talk.appeal(reply)

    assert res.status_code == 409 and res.json()["code"] == "cooling"
    assert value(talk.database) == 2


async def test_only_her_replies_to_the_user(make_client: ClientFactory, settings: Settings):
    talk = Talk(make_client, settings, [level(0)])
    await talk.start()
    reply = await talk.turn()
    other = make_client(FakeLLM(), "other", settings=settings)

    assert (await other.post(f"/api/messages/{reply}/appeal")).status_code == 404
    assert (await talk.appeal(reply - 1)).status_code == 404  # the user's own message
