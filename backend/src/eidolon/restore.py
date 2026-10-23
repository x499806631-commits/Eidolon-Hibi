"""Restoring the database from a backup (`eidolon restore`, ADR-0012 revised 2026-10-15),
run by `deploy/db-restore.sh` in a container of its own: once to check the file while the
server goes on, then, the database backed up and the server stopped, to put it in place.

The file is a backup the deployer decrypted on their own computer, `.db.gz` as `age -d`
leaves it or `.db` once uncompressed; which, is told by what is in it. It is written
beside the database, in the same volume, and checked there; only then put in its place,
by a rename, so the database is either the old one or the new one, never part of each.
The old database's write-ahead log goes first: left beside the new one, it would be
taken as part of it."""

import gzip
import os
import shutil
import sqlite3
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from eidolon.backup import abandon_unfinished
from eidolon.clock import Clock
from eidolon.database import connect

_GZIP = b"\x1f\x8b"
_UNPACKED = ".restoring-"
"""The start of the name of the file a backup is unpacked into, beside the database."""


class NotRestored(Exception):
    """Why the database was left as it was."""


@dataclass(frozen=True)
class Restored:
    users: int
    """The users in the database restored."""
    abandoned: int
    """The backup runs it caught under way, now taken never to finish."""


def check(source: Path, database: Path) -> int:
    """How many users the database in `source` has, if it will do to put in place of the
    one at `database`, raising NotRestored if not; nothing is changed either way."""
    unpacked = _unpack(source, database)
    try:
        return _users_in(unpacked)
    finally:
        _delete(unpacked)


def restore(source: Path, database: Path, clock: Clock) -> Restored:
    """Put the database in `source` in place of the one at `database`, if it will do,
    raising NotRestored, the database left as it was, if not. The server is to be
    stopped."""
    unpacked = _unpack(source, database)
    try:
        users = _users_in(unpacked)
        abandoned = abandon_unfinished(unpacked, clock)
        if database.exists():
            shutil.copymode(database, unpacked)
        # What the stopped server left in its log is in the database it is replacing.
        _delete_log(database)
        os.replace(unpacked, database)
    except BaseException:
        _delete(unpacked)
        raise
    return Restored(users, abandoned)


def _unpack(source: Path, database: Path) -> Path:
    """`source` uncompressed into a new file beside `database`, those an earlier restore
    left there, killed, deleted."""
    if not source.is_file():
        raise NotRestored(f"找不到文件 {source}")
    database.parent.mkdir(parents=True, exist_ok=True)
    for left in database.parent.glob(f"{_UNPACKED}*"):
        left.unlink(missing_ok=True)
    fd, name = tempfile.mkstemp(prefix=_UNPACKED, suffix=".db", dir=database.parent)
    os.close(fd)
    unpacked = Path(name)
    try:
        with source.open("rb") as file:
            compressed = file.read(len(_GZIP)) == _GZIP
        with (gzip.open if compressed else open)(source, "rb") as file, unpacked.open("wb") as copy:
            shutil.copyfileobj(file, copy)
    except BaseException as e:
        _delete(unpacked)
        if isinstance(e, (EOFError, gzip.BadGzipFile, zlib.error)):
            raise NotRestored(f"{source} 解压失败，可能没下载完整或者解密出错：{e}") from None
        if isinstance(e, OSError):  # unreadable, a full disk
            raise NotRestored(f"没能把 {source} 写进数据库所在的目录：{e}") from None
        raise
    return unpacked


def _users_in(unpacked: Path) -> int:
    """How many users the database at `unpacked` has, if it is whole and has them."""
    try:
        db = connect(unpacked)
        try:
            check = [row for (row,) in db.execute("PRAGMA integrity_check")]
            if check != ["ok"]:
                raise NotRestored(f"文件没有通过完整性检查：{'；'.join(check[:5])}")
            tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master")}
            if "users" not in tables:
                raise NotRestored("文件是 SQLite 数据库，但不是本项目的（没有 users 表）")
            (users,) = db.execute("SELECT count(*) FROM users").fetchone()
        finally:
            db.close()
    except sqlite3.DatabaseError as e:  # not a database, or too broken to check
        raise NotRestored(f"文件不是 SQLite 数据库，或者已经损坏：{e}") from None
    return users


def _delete_log(database: Path) -> None:
    for suffix in ["-wal", "-shm"]:
        Path(f"{database}{suffix}").unlink(missing_ok=True)


def _delete(database: Path) -> None:
    database.unlink(missing_ok=True)
    _delete_log(database)
