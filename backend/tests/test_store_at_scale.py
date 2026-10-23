"""The store with many users: what is looked up by user or by conversation is found through
an index, not by reading every row; and every user's current conversations are read
however many there are, more than one statement may bind values for."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eidolon.data_access import Database
from eidolon.memory import Memories
from tests.conftest import store_in, users_in

LOOKUPS = [
    "SELECT * FROM conversations WHERE user_id = ? AND character_id = ?",
    "SELECT MAX(m.created_at) FROM messages m JOIN conversations c ON c.id = m.conversation_id"
    " WHERE c.user_id = ? AND c.character_id = ?",
    "UPDATE memories SET conversation_id = NULL WHERE conversation_id = ?",
]


@pytest.mark.parametrize("lookup", LOOKUPS)
async def test_what_is_looked_up_by_user_or_conversation_reads_no_one_elses(
    tmp_path: Path, lookup: str
):
    path = tmp_path / "eidolon.db"
    async with store_in(path):
        pass
    database = Database(path)
    Memories(database, {}, lambda: datetime.now(UTC))
    await database.close()

    with sqlite3.connect(path) as db:
        values = ["x"] * lookup.count("?")
        plan = [row[3] for row in db.execute(f"EXPLAIN QUERY PLAN {lookup}", values)]

    assert not [step for step in plan if step.startswith("SCAN")], plan


async def test_every_users_current_conversation_however_many(tmp_path: Path):
    path = tmp_path / "eidolon.db"
    [user] = await users_in(path, "producer")
    count = 33_000  # more than SQLite's 32,766 values a statement
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO conversations (id, user_id, character_id, created_at)"
            " VALUES (?, ?, ?, '2026-10-01T00:00:00+00:00')",
            [(f"conversation-{i}", user, f"character-{i}") for i in range(count)],
        )
        db.execute(
            "INSERT INTO calls (conversation_id, connected_at, duration_s)"
            " VALUES ('conversation-7', '2026-10-02T00:00:00+00:00', 60)"
        )

    async with store_in(path) as store:
        current = {c.conversation.id: c for c in await store.current_conversations()}

    assert len(current) == count
    assert current["conversation-7"].last_contact is not None
    assert current["conversation-8"].last_contact is None
