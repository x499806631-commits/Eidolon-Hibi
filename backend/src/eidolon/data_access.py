"""The data access layer (ADR-0028): the only way into the database, so that the event
loop, which every call's audio runs on, never waits on it.

One connection writes, on a thread of its own: each write task handed in (`write`) is a
body run there in a transaction of its own, one at a time, in the order they came, so
that they queue in sight rather than wait on each other inside SQLite. A few read-only
connections read, on the default thread pool: each read (`read`) is a body run on one of
them in a read transaction, which sees the database as it was at one moment, and need not
wait for a write under way. The bodies are the modules' own, synchronous, given the
connection; their SQL stays in them.

Every connection is opened by `database.connect`, the same way, with the SQL functions the
modules define (`function`) on each.

What is deleted for good must leave the write-ahead log too (ADR-0006): `checkpoint` empties
it into the database file, waiting for readers only briefly so as to hold up no write for
long, and, left undone for a reader, tries again a few seconds later until it is done. The
database is first checkpointed so as it is opened, in case one was left undone as it was
last closed.
"""

import asyncio
import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, TypeVarTuple, Unpack

from eidolon.database import connect

log = logging.getLogger(__name__)

T = TypeVar("T")
A = TypeVarTuple("A")

ON_THE_LOOP_RAISES = False
"""Whether a connection taken on the event loop's thread raises `OnTheEventLoop`, as in
tests; else, in production, it is logged as an error and used all the same, so that what
slipped through fails no turn nor call."""

READERS = 4
"""How many read-only connections there are: some to spare on a server of 2 cores."""

SLOW = 0.2
"""Seconds a write task may wait in the queue, or take, and a read wait for a connection,
before it is warned of: to tell whether the writing thread holds things up."""


CHECKPOINT_BUSY = 0.1
"""Seconds a checkpoint waits for readers, holding up every write meanwhile, before it is
left undone for now."""

CHECKPOINT_RETRY = 3.0
"""Seconds after a checkpoint left undone that it is tried again."""

CHECKPOINT_OVERDUE = 600.0
"""Seconds a checkpoint may go undone before it is logged as an error, for what was
deleted is in the write-ahead log still; it is tried again all the same."""


class Closed(RuntimeError):
    """The database is closed: no more is read or written."""


class OnTheEventLoop(RuntimeError):
    """A connection was taken on the event loop's thread, for the task named."""


@dataclass
class _Task:
    body: Callable[..., Any]
    args: tuple[Any, ...]
    done: Future[Any]
    handed_in: float
    """When, on the monotonic clock."""
    in_transaction: bool = True
    """Whether the body is run in a transaction, as all are but the checkpoint, which
    cannot be."""


class Database:
    """The database at `path`, read on `readers` read-only connections and written on one
    connection on a thread of its own."""

    def __init__(self, path: Path, readers: int = READERS) -> None:
        self.path = path
        self._writer = connect(path)
        self._readers: list[sqlite3.Connection] = []
        try:
            for _ in range(readers):
                reader = connect(path)
                reader.execute("PRAGMA query_only = ON")
                self._readers.append(reader)
        except BaseException:
            for conn in (self._writer, *self._readers):
                conn.close()
            raise
        self._idle: queue.SimpleQueue[sqlite3.Connection] = queue.SimpleQueue()
        for reader in self._readers:
            self._idle.put(reader)
        self._tasks: queue.SimpleQueue[_Task | None] = queue.SimpleQueue()
        self._counts = threading.Condition()
        """Guards whether it is closed, and the counts of reads."""
        self._reading = 0
        """How many reads are under way, or waiting for a connection or a thread."""
        self._waiting = 0
        """How many reads wait for a connection."""
        self._closed = False
        self._checkpoint_due: float | None = time.monotonic()
        """When the checkpoint left undone is to be tried again, on the monotonic clock;
        None if none is. On the writing thread only, which first tries one at once."""
        self._undone_since: float | None = None
        """Since when a checkpoint has been left undone, if one is."""
        self._overdue_told = False
        """Whether it has been logged that the checkpoint left undone is overdue."""
        self._thread = threading.Thread(target=self._write_all, name="eidolon-writer", daemon=True)
        self._thread.start()

    async def write(self, body: Callable[[sqlite3.Connection, Unpack[A]], T], *args: Unpack[A]) -> T:
        """`body` run on the writing connection with `args`, in a transaction of its own
        that holds the database from its first statement: committed if it returns, rolled
        back if it raises, which is raised here. Cancelling the wait leaves it to be
        written all the same."""
        return await _awaited(self._submit(body, args))

    async def read(self, body: Callable[[sqlite3.Connection, Unpack[A]], T], *args: Unpack[A]) -> T:
        """`body` run on a read-only connection with `args`, in a read transaction of its
        own: all it reads is the database as it was at one moment."""
        with self._counts:
            if self._closed:
                raise Closed()
            self._reading += 1
        try:
            running = asyncio.get_running_loop().run_in_executor(None, self._read, body, args)
        except BaseException:
            self._done_reading()
            raise
        running.add_done_callback(_retrieved)
        # Cancelling the wait leaves the read to finish, so that closing waits for it.
        return await asyncio.shield(running)

    async def checkpoint(self) -> None:
        """Have the write-ahead log emptied into the database file, as a write task handed
        in now but not waited for, which waits `CHECKPOINT_BUSY` at most for readers; left
        undone, it is tried again every `CHECKPOINT_RETRY` until it is done, and logged as
        an error once overdue."""
        self._submit(self._checkpoint, (), in_transaction=False)

    def prepare(self, body: Callable[[sqlite3.Connection, Unpack[A]], T], *args: Unpack[A]) -> T:
        """`body` written as `write` writes it, waited for here, blocking: for a module's
        tables and migrations as it is made, which is before the server serves anything."""
        return self._submit(body, args).result()

    def function(
        self, name: str, arity: int, implementation: Callable[..., Any], *,
        deterministic: bool = False,
    ) -> None:
        """Define the SQL function `name` of `arity` arguments on every connection, as a
        module that uses it is made, waiting for the connections to be free."""
        def define(conn: sqlite3.Connection) -> None:
            conn.create_function(name, arity, implementation, deterministic=deterministic)

        self.prepare(define)
        free = [self._idle.get() for _ in self._readers]
        try:
            for reader in free:
                define(reader)
        finally:
            for reader in free:
                self._idle.put(reader)

    async def close(self) -> None:
        """Write what was handed in and finish the reads under way, then close every
        connection; nothing is read or written after."""
        with self._counts:
            if self._closed:
                return
            self._closed = True
            self._tasks.put(None)
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        self._thread.join()
        with self._counts:
            self._counts.wait_for(lambda: self._reading == 0)
        for reader in self._readers:
            reader.close()

    def _submit(
        self, body: Callable[..., T], args: tuple[Any, ...], *, in_transaction: bool = True
    ) -> "Future[T]":
        done: Future[T] = Future()
        with self._counts:
            if self._closed:
                raise Closed()
            self._tasks.put(_Task(body, args, done, time.monotonic(), in_transaction))
        return done

    def _write_all(self) -> None:
        while (task := self._next()) is not None:
            started = time.monotonic()
            if started - task.handed_in > SLOW:
                log.warning(
                    "write task %s waited %.0f ms in the queue, %d waiting behind it",
                    _name(task.body), (started - task.handed_in) * 1000, self._tasks.qsize(),
                )
            try:
                _off_the_loop(task.body)
                if task.in_transaction:
                    # Held from the first statement, so that what the body reads before it
                    # writes holds still for every other connection too.
                    self._writer.execute("BEGIN IMMEDIATE")
                    with self._writer:
                        result = task.body(self._writer, *task.args)
                else:
                    result = task.body(self._writer, *task.args)
            except BaseException as e:
                task.done.set_exception(e)
            else:
                task.done.set_result(result)
            if (took := time.monotonic() - started) > SLOW:
                log.warning(
                    "write task %s took %.0f ms, %d waiting behind it",
                    _name(task.body), took * 1000, self._tasks.qsize(),
                )
        self._writer.close()

    def _next(self) -> _Task | None:
        """The next write task, once it comes; trying the checkpoint left undone again
        whenever it is due meanwhile."""
        while True:
            due = self._checkpoint_due
            if due is not None and time.monotonic() >= due:
                self._checkpoint(self._writer)
                continue
            try:
                return self._tasks.get(
                    timeout=None if due is None else max(0.0, due - time.monotonic())
                )
            except queue.Empty:
                pass

    def _checkpoint(self, conn: sqlite3.Connection) -> None:
        """Empty the write-ahead log into the database file through the writing connection,
        `conn`, waiting `CHECKPOINT_BUSY` at most for readers; left undone, it is due again
        `CHECKPOINT_RETRY` later."""
        try:
            busy = _truncated_log(conn)
        except sqlite3.OperationalError as e:  # another connection writes meanwhile
            log.info("checkpointing the database was held up: %s", e)
            busy = True
        except sqlite3.Error:
            log.exception("checkpointing the database failed")
            busy = True
        now = time.monotonic()
        if not busy:
            self._checkpoint_due = self._undone_since = None
            self._overdue_told = False
            return
        self._checkpoint_due = now + CHECKPOINT_RETRY
        if self._undone_since is None:
            self._undone_since = now
        elif now - self._undone_since > CHECKPOINT_OVERDUE and not self._overdue_told:
            self._overdue_told = True
            log.error(
                "the checkpoint has been left undone for %.0f s, readers holding it up:"
                " what was deleted is in the write-ahead log still; trying again",
                now - self._undone_since,
            )

    def _read(self, body: Callable[..., T], args: tuple[Any, ...]) -> T:
        try:
            _off_the_loop(body)
            conn = self._connection(body)
            try:
                conn.execute("BEGIN")
                try:
                    return body(conn, *args)
                finally:
                    conn.rollback()
            finally:
                self._idle.put(conn)
        finally:
            self._done_reading()

    def _done_reading(self) -> None:
        with self._counts:
            self._reading -= 1
            self._counts.notify_all()

    def _connection(self, body: Callable[..., Any]) -> sqlite3.Connection:
        """A read-only connection for `body`, once one is free."""
        try:
            return self._idle.get_nowait()
        except queue.Empty:
            pass
        with self._counts:
            self._waiting += 1
        started = time.monotonic()
        try:
            conn = self._idle.get()
        finally:
            with self._counts:
                waiting = self._waiting
                self._waiting -= 1
        if (waited := time.monotonic() - started) > SLOW:
            log.warning(
                "read %s waited %.0f ms for a connection, %d waiting then",
                _name(body), waited * 1000, waiting,
            )
        return conn


def _truncated_log(conn: sqlite3.Connection) -> bool:
    """Empty the write-ahead log into the database file through `conn`, waiting
    `CHECKPOINT_BUSY` at most for readers: whether readers held it up, left undone."""
    [(waits,)] = conn.execute("PRAGMA busy_timeout").fetchall()
    conn.execute(f"PRAGMA busy_timeout = {round(CHECKPOINT_BUSY * 1000)}")
    try:
        [(busy, _, _)] = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.execute(f"PRAGMA busy_timeout = {waits}")
    return bool(busy)


def _off_the_loop(body: Callable[..., Any]) -> None:
    """Check that `body` is not given a connection on the event loop's thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:  # no loop runs on this thread
        return
    if ON_THE_LOOP_RAISES:
        raise OnTheEventLoop(_name(body))
    log.error("%s took a connection to the database on the event loop's thread", _name(body))


def _name(body: Callable[..., Any]) -> str:
    """The name a body goes by in the logs: its qualified name, as `ConversationEngine._record`."""
    return getattr(body, "__qualname__", None) or repr(body)


async def _awaited(done: "Future[T]") -> T:
    """What `done` comes to, awaited on the running loop; cancelling the wait leaves the
    task to finish all the same."""
    loop = asyncio.get_running_loop()
    waiting: asyncio.Future[T] = loop.create_future()

    def hand_over(_: "Future[T]") -> None:
        try:
            loop.call_soon_threadsafe(_deliver, done, waiting)
        except RuntimeError:  # the loop is closed: no one waits any more
            pass

    done.add_done_callback(hand_over)
    return await waiting


def _retrieved(running: "asyncio.Future[Any]") -> None:
    """Take what a read no one waits for any more raised, so that it is not logged as
    never retrieved."""
    if not running.cancelled():
        running.exception()


def _deliver(done: "Future[T]", waiting: "asyncio.Future[T]") -> None:
    if waiting.cancelled():
        return
    if (error := done.exception()) is not None:
        waiting.set_exception(error)
    else:
        waiting.set_result(done.result())
