"""The data access layer (ADR-0028): one connection writes, on a thread of its own, each
write in the order it was handed in, in a transaction of its own; a few read-only ones
read, each read in a transaction of its own; none is taken on the event loop's thread."""

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest

import eidolon.data_access
from eidolon.data_access import Closed, Database, OnTheEventLoop


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "eidolon.db")
    database.prepare(lambda conn: conn.execute("CREATE TABLE things (name TEXT)"))
    yield database
    await database.close()


def add(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("INSERT INTO things (name) VALUES (?)", (name,))


def names(conn: sqlite3.Connection) -> list[str]:
    return [name for (name,) in conn.execute("SELECT name FROM things ORDER BY rowid")]


async def test_writes_are_written_in_the_order_they_were_handed_in(db: Database):
    def slow_add(conn: sqlite3.Connection, name: str) -> None:
        time.sleep(0.05)
        add(conn, name)

    await asyncio.gather(
        db.write(slow_add, "first"), db.write(add, "second"), db.write(add, "third")
    )

    assert await db.read(names) == ["first", "second", "third"]


async def test_a_read_need_not_wait_for_a_long_write(db: Database):
    await db.write(add, "before")
    writing, finish = threading.Event(), threading.Event()

    def long_add(conn: sqlite3.Connection) -> None:
        add(conn, "during")
        writing.set()
        finish.wait(10)

    written = asyncio.create_task(db.write(long_add))
    await asyncio.to_thread(writing.wait, 10)

    assert await db.read(names) == ["before"]
    finish.set()
    await written
    assert await db.read(names) == ["before", "during"]


async def test_a_write_holds_the_database_from_its_first_statement(
    db: Database, tmp_path: Path
):
    def read_then_add(conn: sqlite3.Connection) -> str:
        names(conn)
        other = sqlite3.connect(tmp_path / "eidolon.db", timeout=0)
        try:
            other.execute("INSERT INTO things (name) VALUES ('meanwhile')")
            other.commit()
            return "written meanwhile"
        except sqlite3.OperationalError as e:
            return str(e)
        finally:
            other.close()

    assert await db.write(read_then_add) == "database is locked"


async def test_a_write_that_raises_is_rolled_back_and_raises_to_whoever_waits(db: Database):
    def fails(conn: sqlite3.Connection) -> None:
        add(conn, "half")
        raise ValueError("no")

    with pytest.raises(ValueError, match="no"):
        await db.write(fails)

    assert await db.read(names) == []


async def test_a_read_sees_the_database_as_it_was_at_one_moment(
    db: Database, tmp_path: Path
):
    def twice(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
        before = names(conn)
        other = sqlite3.connect(tmp_path / "eidolon.db")
        with other:
            other.execute("INSERT INTO things (name) VALUES ('meanwhile')")
        other.close()
        return before, names(conn)

    await db.write(add, "first")

    assert await db.read(twice) == (["first"], ["first"])


async def test_a_write_handed_in_is_written_though_whoever_waits_for_it_is_cancelled(
    db: Database,
):
    writing, finish = threading.Event(), threading.Event()

    def held(conn: sqlite3.Connection) -> None:
        writing.set()
        finish.wait(10)

    first = asyncio.create_task(db.write(held))
    second = asyncio.create_task(db.write(add, "queued"))
    await asyncio.to_thread(writing.wait, 10)
    first.cancel()
    second.cancel()
    finish.set()
    await asyncio.gather(first, second, return_exceptions=True)

    await db.write(add, "after")
    assert await db.read(names) == ["queued", "after"]


async def test_closing_writes_what_was_handed_in_and_refuses_what_comes_after(tmp_path: Path):
    db = Database(tmp_path / "eidolon.db")
    db.prepare(lambda conn: conn.execute("CREATE TABLE things (name TEXT)"))

    def slow_add(conn: sqlite3.Connection, name: str) -> None:
        time.sleep(0.05)
        add(conn, name)

    handed_in = [asyncio.create_task(db.write(slow_add, name)) for name in ("a", "b")]
    await asyncio.sleep(0)
    await db.close()

    assert all(task.done() for task in handed_in)
    with pytest.raises(Closed):
        await db.write(add, "late")
    with pytest.raises(Closed):
        await db.read(names)
    with sqlite3.connect(tmp_path / "eidolon.db") as other:
        assert names(other) == ["a", "b"]
    other.close()



class OnTheCallersThread(ThreadPoolExecutor):
    """Runs what it is given at once, on the thread that gives it: the event loop's, as
    the default executor."""

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        done: Future = Future()
        try:
            done.set_result(fn(*args, **kwargs))
        except BaseException as e:
            done.set_exception(e)
        return done


async def test_a_connection_taken_on_the_event_loop_raises_in_tests(db: Database):
    asyncio.get_running_loop().set_default_executor(OnTheCallersThread())

    with pytest.raises(OnTheEventLoop, match="names"):
        await db.read(names)


async def test_a_connection_taken_on_the_event_loop_is_logged_as_an_error_and_used_in_production(
    db: Database, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(eidolon.data_access, "ON_THE_LOOP_RAISES", False)
    await db.write(add, "first")
    asyncio.get_running_loop().set_default_executor(OnTheCallersThread())

    assert await db.read(names) == ["first"]
    (record,) = [r for r in caplog.records if r.name == "eidolon.data_access"]
    assert record.levelname == "ERROR" and "names" in record.getMessage()


def warnings_of(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == "eidolon.data_access" and r.levelname == "WARNING"
    ]


async def test_a_write_waiting_or_taking_over_200_ms_is_warned_of_with_its_name_and_the_queue(
    db: Database, caplog: pytest.LogCaptureFixture
):
    def slow_add(conn: sqlite3.Connection, name: str) -> None:
        time.sleep(0.25)
        add(conn, name)

    await asyncio.gather(
        db.write(slow_add, "slow"), db.write(add, "behind"), db.write(add, "last")
    )

    took, waited, *_ = warnings_of(caplog)
    assert "slow_add took" in took and "2 waiting" in took, took
    assert "write task add waited" in waited and "1 waiting" in waited, waited


async def test_a_read_waiting_over_200_ms_for_a_connection_is_warned_of(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    db = Database(tmp_path / "eidolon.db", readers=1)
    db.prepare(lambda conn: conn.execute("CREATE TABLE things (name TEXT)"))
    reading, finish = threading.Event(), threading.Event()

    def held(conn: sqlite3.Connection) -> None:
        reading.set()
        finish.wait(10)

    first = asyncio.create_task(db.read(held))
    await asyncio.to_thread(reading.wait, 10)
    second = asyncio.create_task(db.read(names))
    await asyncio.sleep(0.25)
    finish.set()
    await asyncio.gather(first, second)
    await db.close()

    (waited,) = warnings_of(caplog)
    assert "names" in waited and "waited" in waited and "1 waiting" in waited, waited


async def test_a_function_defined_is_on_every_connection(db: Database):
    db.function("shout", 1, lambda name: name.upper(), deterministic=True)

    await db.write(lambda conn: conn.execute("INSERT INTO things SELECT shout('quiet')"))

    shouted = await db.read(lambda conn: conn.execute("SELECT shout(name) FROM things").fetchall())
    assert shouted == [("QUIET",)]


def errors_of(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == "eidolon.data_access" and r.levelname == "ERROR"
    ]


def log_of(path: Path) -> int:
    """How big the write-ahead log of the database at `path` is, in bytes."""
    log = path.with_name(path.name + "-wal")
    return log.stat().st_size if log.exists() else 0


async def emptied(path: Path) -> None:
    """Wait for the write-ahead log of the database at `path` to be emptied."""
    async with asyncio.timeout(5):
        while log_of(path):
            await asyncio.sleep(0.02)


def reading(path: Path) -> sqlite3.Connection:
    """A connection of its own to the database at `path`, in the middle of a read, as a
    backup's is."""
    reader = sqlite3.connect(path, isolation_level=None)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM sqlite_master").fetchall()
    return reader


async def test_a_checkpoint_a_reader_holds_up_holds_up_no_write_and_is_done_once_it_is_gone(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(eidolon.data_access, "CHECKPOINT_RETRY", 0.1)
    path = tmp_path / "eidolon.db"
    await db.write(add, "kept")
    reader = reading(path)
    try:
        started = time.monotonic()
        await db.checkpoint()
        await db.write(add, "after")
        assert time.monotonic() - started < 1
        assert log_of(path)
    finally:
        reader.close()

    await emptied(path)
    assert await db.read(names) == ["kept", "after"]


async def test_a_checkpoint_left_undone_for_long_is_logged_as_an_error_and_still_retried(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(eidolon.data_access, "CHECKPOINT_RETRY", 0.05)
    monkeypatch.setattr(eidolon.data_access, "CHECKPOINT_OVERDUE", 0.3)
    path = tmp_path / "eidolon.db"
    await db.write(add, "kept")
    reader = reading(path)
    try:
        await db.checkpoint()
        async with asyncio.timeout(5):
            while not errors_of(caplog):
                await asyncio.sleep(0.02)
    finally:
        reader.close()

    await emptied(path)
    (error,) = errors_of(caplog)
    assert "checkpoint" in error


async def test_opening_first_finishes_a_checkpoint_left_undone(tmp_path: Path):
    path = tmp_path / "eidolon.db"
    other = sqlite3.connect(path)  # kept open, so that closing it checkpoints nothing
    try:
        other.execute("PRAGMA journal_mode = WAL")
        with other:
            other.execute("CREATE TABLE things (name TEXT)")
            other.execute("INSERT INTO things (name) VALUES ('left in the log')")
        assert log_of(path)

        db = Database(path)
        try:
            await emptied(path)
        finally:
            await db.close()
    finally:
        other.close()
