"""Achievements (GLOSSARY.md「成就」) worked out once as they launch, from what was recorded
before: turns, calls that connected, gifts given and her affinity's tiers, data packages
imported and all; the stages reached unlocked as of then and rewarded in full, a character
complete given her reset card, the times drawn starting from none. Done once ever, however
often the backend starts again, recording nothing for the dashboard. Driven through the
backend's external API, the history put straight into a database from before them, as the
backend starting on it finds it."""

import json
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, START, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_achievements import (
    REPLY,
    achievement,
    goals,
    hers,
    state,
    turns,
    unlocked,
    with_goals,
)
from tests.test_activity_records import dashboard
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_data_package import import_package
from tests.test_data_package_conversations import conversations_package, packaged_conversation
from tests.test_text_turn import start_conversation

LAUNCH = START + timedelta(days=1)
"""When the backend starts with the achievements."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True)


class History:
    """What the producer did before the achievements, put straight into the database of a
    backend that ran before them."""

    def __init__(self, make_client: ClientFactory, settings: Settings) -> None:
        self._make_client = make_client
        self.client = make_client(FakeLLM(), clock=FakeClock())
        """The producer, on the backend from before."""
        self._db = sqlite3.connect(settings.database_path)
        (self._user,) = self._db.execute(
            "SELECT id FROM users WHERE name = 'producer'"
        ).fetchone()
        self._conversations = 0

    def conversation(self, character: str = "mano") -> str:
        self._conversations += 1
        id = f"c{self._conversations}"
        with self._db:
            self._db.execute(
                "INSERT INTO conversations (id, user_id, character_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (id, self._user, character, START.isoformat()),
            )
        return id

    def turns(
        self, conversation: str, count: int, at: datetime = START, tier: str | None = None
    ) -> None:
        """`count` turns, each the user saying something and her replying, her reply to the
        last leaving her affinity in `tier` (None for unchanged)."""
        for i in range(count):
            change = tier if i == count - 1 else None
            with self._db:
                self._db.execute(
                    "INSERT INTO messages (conversation_id, role, text, created_at)"
                    " VALUES (?, 'user', '你好', ?)",
                    (conversation, at.isoformat()),
                )
                self._db.execute(
                    "INSERT INTO messages (conversation_id, role, zh, created_at, affinity)"
                    " VALUES (?, 'character', '好的', ?, ?)",
                    (conversation, at.isoformat(), _change(change)),
                )

    def gift(self, conversation: str, at: datetime = START, tier: str | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO conversation_events"
                " (conversation_id, kind, created_at, affinity, gift_id, liked)"
                " VALUES (?, 'gift', ?, ?, 'cake', 1)",
                (conversation, at.isoformat(), _change(tier)),
            )

    def call(self, conversation: str, seconds: float) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO calls (conversation_id, connected_at, duration_s) VALUES (?, ?, ?)",
                (conversation, START.isoformat(), seconds),
            )

    def affinity(self, character: str, value: float) -> None:
        """Her affinity now."""
        with self._db:
            self._db.execute(
                "INSERT INTO affinities (user_id, character_id, value, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (self._user, character, value, START.isoformat()),
            )

    def launch(self, llm: FakeLLM | None = None, **settings: Settings) -> httpx.AsyncClient:
        """The backend starting with the achievements for the first time, on the database
        as it was before them."""
        with self._db:
            for table in ["achievement_progress", "achievement_stages", "achievements_launched"]:
                self._db.execute(f"DROP TABLE {table}")
        return self._make_client(llm or FakeLLM(), clock=FakeClock(LAUNCH), **settings)

    def close(self) -> None:
        self._db.close()


def _change(tier: str | None) -> str | None:
    """A change of her affinity, up into `tier`, as kept."""
    return tier and json.dumps(
        {"up": True, "tier_changed": True, "tier": tier, "cooldown": False, "thawed": False}
    )


@pytest.fixture
def history(make_client: ClientFactory, settings: Settings):
    history = History(make_client, settings)
    yield history
    history.close()


def restart(make_client: ClientFactory) -> httpx.AsyncClient:
    """The backend starting again with the achievements."""
    return make_client(FakeLLM(), clock=FakeClock(LAUNCH))


async def test_turns_with_her_before_unlock_their_stages_as_of_the_launch(
    make_client: ClientFactory, history: History
):
    history.turns(history.conversation(), 10)
    history.turns(history.conversation("kogane"), 9)

    client = history.launch()
    got = await hers(client)

    assert achievement(got, "turns")["progress"] == 10
    assert achievement(got, "turns")["stages"][0]["unlocked_at"] == LAUNCH.isoformat()
    assert unlocked(await hers(client, "kogane"), "turns") == []
    # The new user's one, then the stage's one.
    assert (await state(client))["draws"] == 2


async def test_calls_that_connected_before_count_their_time_together(
    make_client: ClientFactory, history: History
):
    history.call(history.conversation(), 300.5)
    history.call(history.conversation(), 299.5)

    got = await hers(history.launch())

    assert achievement(got, "call_time")["progress"] == 600
    assert unlocked(got, "call_time") == [1]


async def test_gifts_given_her_before_count(make_client: ClientFactory, history: History):
    conversation = history.conversation()
    for _ in range(10):
        history.gift(conversation)

    got = await hers(history.launch())

    assert achievement(got, "gifts")["progress"] == 10
    assert unlocked(got, "gifts") == [1, 2]


async def test_the_tiers_her_affinity_reached_before_count_fallen_back_since_or_not(
    make_client: ClientFactory, history: History
):
    history.turns(history.conversation(), 1, tier="close")
    history.affinity("mano", 50)
    history.gift(history.conversation("kogane"), tier="intimate")

    client = history.launch()

    assert achievement(await hers(client), "tier")["progress"] == "close"
    assert unlocked(await hers(client), "tier") == [1]
    assert unlocked(await hers(client, "kogane"), "tier") == [1, 2]
    assert achievement(await state(client), "intimate_characters")["progress"] == 1


async def test_the_tier_her_affinity_is_in_counts(make_client: ClientFactory, history: History):
    history.turns(history.conversation(), 1)
    history.affinity("mano", 70)

    got = await hers(history.launch())

    assert unlocked(got, "tier") == [1]


async def test_the_days_come_before_count_by_beijing_time_the_last_of_them_kept(
    make_client: ClientFactory, history: History
):
    conversation = history.conversation()
    for days in range(5):
        history.turns(conversation, 1, START - timedelta(days=days))
    # 01:00 in Beijing, the same day as the day before's turn at 20:00.
    history.gift(conversation, START - timedelta(days=4, hours=19))
    history.gift(conversation, START - timedelta(days=9))
    history.call(conversation, 60)
    # 19:00 in Beijing, the day of the launch.
    history.turns(conversation, 1, LAUNCH - timedelta(hours=1))

    client = history.launch(FakeLLM(REPLY))
    launched = achievement(await state(client), "days")
    await turns(client, await start_conversation(client), 1)

    assert launched["progress"] == 7
    assert [s["unlocked_at"] for s in launched["stages"]][:2] == [LAUNCH.isoformat(), None]
    assert achievement(await state(client), "days")["progress"] == 7


async def test_the_characters_had_a_turn_with_before_count_together(
    make_client: ClientFactory, history: History, monkeypatch: pytest.MonkeyPatch
):
    with_goals(
        monkeypatch, "ALL_CHARACTERS",
        characters=goals(2), intimate_characters=goals(1), days=goals(7), draws=goals(10),
    )
    history.turns(history.conversation(), 1)
    history.turns(history.conversation("kogane"), 1)

    got = await state(history.launch())

    assert achievement(got, "characters")["progress"] == 2
    assert achievement(got, "characters")["stages"][0]["unlocked_at"] == LAUNCH.isoformat()


async def test_a_character_complete_before_gives_her_reset_card_and_every_draw_in_full(
    make_client: ClientFactory, history: History, monkeypatch: pytest.MonkeyPatch
):
    with_goals(
        monkeypatch, "PER_CHARACTER",
        turns=goals(1, 2, 3, 4, 5), call_time=goals(1), gifts=goals(1), tier=goals(1),
    )
    conversation = history.conversation()
    history.turns(conversation, 5)
    history.call(conversation, 1)
    history.gift(conversation, tier="close")

    client = history.launch()
    got = await hers(client)
    held = await state(client)

    assert got is not None
    assert got["complete"] == {"completed_at": LAUNCH.isoformat(), "reset_cards": 0}
    # The new user's one, then 15 for turns' five stages and one each for the other three:
    # more than the most kept of those given each day.
    assert held["draws"] == 1 + 15 + 3
    assert held["reset_cards"] == 1


async def test_the_times_drawn_start_from_none(make_client: ClientFactory, history: History):
    await history.client.get("/api/gifts")
    res = await history.client.post("/api/gifts/draws", json={"all": True})
    assert res.status_code == 200, res.text
    history.turns(history.conversation(), 1)

    got = await state(history.launch())

    assert achievement(got, "draws")["progress"] == 0


async def test_what_data_packages_brought_before_counts(
    make_client: ClientFactory, history: History
):
    res = await import_package(history.client, conversations_package(
        *(packaged_conversation(f"p{i}", f"2026-09-{10 + i}") for i in range(10))
    ), "conversations")
    assert res.json()["conversations"] == 10

    client = history.launch()

    assert achievement(await hers(client), "turns")["progress"] == 10
    assert achievement(await state(client), "days")["progress"] == 10


async def test_it_is_done_once_however_often_the_backend_starts_again_recording_nothing(
    make_client: ClientFactory, history: History, caplog: pytest.LogCaptureFixture,
    github: FakeIdentityProvider,
):
    history.turns(history.conversation(), 10)

    with caplog.at_level(logging.INFO, "eidolon.happenings"):
        launched = history.launch()
        held = await state(launched)
        again = await state(restart(make_client))
    async with admin_beside(launched, github) as admin:
        board = await dashboard(admin, "7d")

    assert held["draws"] == again["draws"] == 2
    assert unlocked(await hers(restart(make_client)), "turns") == [1]
    assert [r.getMessage() for r in caplog.records if r.name == "eidolon.happenings"] == [
        "achievements launched: 1 users, 1 stages, 1 draw chances, 0 reset cards"
    ]
    assert board["achievements"] == {"stages": 0, "draws": 0, "reset_cards": 0}


async def test_it_is_done_with_affinity_and_gifts_closed_the_draws_there_once_open(
    make_client: ClientFactory, history: History, settings: Settings
):
    history.turns(history.conversation(), 10)

    history.launch(settings=replace(settings, affinity_and_gifts=False))
    opened = restart(make_client)

    assert unlocked(await hers(opened), "turns") == [1]
    assert (await state(opened))["draws"] == 2
