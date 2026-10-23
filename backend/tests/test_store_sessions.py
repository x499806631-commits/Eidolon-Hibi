"""Sessions in the store: one that has expired is deleted, not only refused, whoever's it
is, once anyone signs in."""

import sqlite3
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from eidolon.store import SESSION_LIFETIME, Store
from tests.conftest import store_in
from tests.fakes import START


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    async with store_in(tmp_path / "eidolon.db") as store:
        yield store


def sessions_of(path: Path) -> list[str]:
    with sqlite3.connect(path) as db:
        users = [user for (user,) in db.execute("SELECT user_id FROM sessions ORDER BY expires_at")]
    db.close()
    return users


async def test_signing_in_deletes_everyones_expired_sessions(store: Store, tmp_path: Path):
    away = (await store.create_user("away")).id  # and never comes back
    still = (await store.create_user("still")).id
    newcomer = (await store.create_user("newcomer")).id
    await store.create_session(away, START)
    await store.create_session(still, START + timedelta(days=1))

    await store.create_session(newcomer, START + SESSION_LIFETIME)

    assert sessions_of(tmp_path / "eidolon.db") == [still, newcomer]


async def test_deleting_expired_sessions_needs_no_scan_of_the_whole_table(
    store: Store, tmp_path: Path
):
    with sqlite3.connect(tmp_path / "eidolon.db") as db:
        plan = db.execute("EXPLAIN QUERY PLAN DELETE FROM sessions WHERE expires_at <= 0").fetchall()
    db.close()

    assert "SEARCH" in " ".join(detail for *_, detail in plan)
