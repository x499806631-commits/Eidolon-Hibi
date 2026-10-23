"""Housekeeping: what ages out is deleted as the backend starts and every 10 minutes after,
each apart from the others, in batches that let other writes in between."""

import asyncio
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from eidolon import housekeeping
from eidolon.data_access import Database


async def test_one_failing_keeps_none_of_the_others_from_tidying_up():
    tidied: list[str] = []

    async def failing() -> None:
        raise RuntimeError("database is locked")

    async def activity() -> None:
        tidied.append("activity")

    await housekeeping.tidy(failing, activity)

    assert tidied == ["activity"]


async def test_it_tidies_up_as_it_starts_then_every_tick(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(housekeeping, "TICK", timedelta(seconds=0.01))
    tidied: list[bool] = []

    async def forget() -> None:
        tidied.append(True)

    tidying = asyncio.create_task(housekeeping.keep_tidy(forget))
    await asyncio.sleep(0.1)
    tidying.cancel()

    assert len(tidied) >= 2


async def test_each_batch_is_a_write_task_of_its_own_and_others_come_in_between(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(housekeeping, "BATCH", 2)
    db = Database(tmp_path / "eidolon.db")
    try:
        await db.write(lambda conn: conn.executescript(
            "CREATE TABLE aged (id INTEGER PRIMARY KEY);"
            "INSERT INTO aged VALUES (1), (2), (3), (4), (5);"
        ))
        happened: list[str] = []
        deleting, may_go_on = threading.Event(), threading.Event()

        def forget(conn: sqlite3.Connection, batch: int) -> int:
            deleting.set()
            may_go_on.wait(5)
            deleted = conn.execute(
                "DELETE FROM aged WHERE id IN (SELECT id FROM aged LIMIT ?)", (batch,)
            ).rowcount
            happened.append(f"deleted {deleted}")
            return deleted

        def other(conn: sqlite3.Connection) -> None:
            happened.append("another write")

        forgetting = asyncio.create_task(housekeeping.in_batches(db, forget))
        await asyncio.to_thread(deleting.wait, 5)
        writing = asyncio.create_task(db.write(other))  # handed in as the first batch runs
        await asyncio.sleep(0)
        may_go_on.set()
        await asyncio.gather(forgetting, writing)

        assert happened == ["deleted 2", "another write", "deleted 2", "deleted 1"]
    finally:
        await db.close()
