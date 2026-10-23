"""Announcements (GLOSSARY.md「公告」): what admins write in the admin console for every
user to see, kept in the database (`announcements`).

An announcement is only its body, in Markdown, with no title and no author. It is
written as a draft, which only admins see, and is published, withdrawn back to a draft,
or deleted. When it was published (`published_at`) is what tells users it is new: editing
a published one keeps it, so that fixing a typo does not show it to everyone again;
withdrawing it clears it, and publishing it again sets it anew. `updated_at` is when its
body was last written.

One published announcement may be pinned (`pinned`, GLOSSARY.md「置顶公告」): users see it
first, however old it is, besides the latest. Pinning another unpins it; withdrawing it
unpins it for good. Pinning leaves when it was published, so it does not make it new.

Whether a user has read them is kept in their browser, not here.
"""

import sqlite3
import uuid
from dataclasses import asdict, dataclass
from typing import Literal

from eidolon.clock import Clock
from eidolon.data_access import Database

MAX_BODY = 5000
"""The most characters an announcement's body may have."""

SHOWN = 20
"""How many of the announcements published users see at most, the latest, besides the one
pinned."""

Status = Literal["draft", "published"]

_SCHEMA = """
-- The announcements admins write; `published_at` NULL while a draft; `pinned` 1 for the
-- one published that users see first, if any.
CREATE TABLE IF NOT EXISTS announcements (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0
);
"""

_COLUMNS = "id, body, published_at, created_at, updated_at, pinned"


class NoSuchAnnouncement(Exception):
    """There is no announcement with that id."""


class NotPublished(Exception):
    """A draft cannot be pinned."""


@dataclass(frozen=True)
class Announcement:
    id: str
    body: str
    published_at: str | None
    """When it was published, ISO 8601; None while a draft."""
    created_at: str
    updated_at: str
    pinned: bool
    """Whether it is the one users see first."""

    @property
    def status(self) -> Status:
        return "draft" if self.published_at is None else "published"

    def described(self) -> dict:
        """What admins see of it."""
        return asdict(self) | {"status": self.status}

    def shown(self) -> dict:
        """What users see of it."""
        return {
            "id": self.id,
            "body": self.body,
            "published_at": self.published_at,
            "pinned": self.pinned,
        }


class Announcements:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        db.prepare(_create)
        self._clock = clock

    async def all(self) -> list[Announcement]:
        """Every announcement: the drafts, last written first, then those published, the
        one pinned first, then last published first."""
        return await self._db.read(
            _select,
            " ORDER BY published_at IS NOT NULL, pinned DESC, published_at DESC,"
            " created_at DESC, rowid DESC",
            (),
        )

    async def published(self) -> list[Announcement]:
        """The one pinned, if any, then the `SHOWN` others published last, last first."""
        return await self._db.read(
            _select,
            " WHERE pinned OR id IN (SELECT id FROM announcements"
            " WHERE published_at IS NOT NULL AND NOT pinned"
            " ORDER BY published_at DESC, rowid DESC LIMIT ?)"
            " ORDER BY pinned DESC, published_at DESC, rowid DESC",
            (SHOWN,),
        )

    async def get(self, id: str) -> Announcement:
        return await self._db.read(_get, id)

    async def write(self, body: str) -> Announcement:
        """A new draft of `body`."""
        now = self._clock().isoformat()
        return await self._db.write(_insert, uuid.uuid4().hex, body, now)

    async def edit(self, id: str, body: str) -> Announcement:
        """Replace its body, keeping whether and when it was published."""
        return await self._db.write(
            _update, id, "body = ?, updated_at = ?", (body, self._clock().isoformat())
        )

    async def publish(self, id: str) -> Announcement:
        """Publish it now; nothing for one published already."""
        return await self._db.write(
            _update, id, "published_at = COALESCE(published_at, ?)", (self._clock().isoformat(),)
        )

    async def withdraw(self, id: str) -> Announcement:
        """Make it a draft again, not pinned."""
        return await self._db.write(_update, id, "published_at = NULL, pinned = 0", ())

    async def pin(self, id: str) -> Announcement:
        """Make it the one users see first, unpinning the one that was; `NotPublished` for
        a draft."""
        return await self._db.write(_pin, id)

    async def unpin(self, id: str) -> Announcement:
        """Make it no longer the one users see first; nothing for one not pinned."""
        return await self._db.write(_update, id, "pinned = 0", ())

    async def delete(self, id: str) -> None:
        await self._db.write(_delete, id)


def _create(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(announcements)")}
    if "pinned" not in columns:  # kept from before pinning: none pinned
        db.execute("ALTER TABLE announcements ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")


def _insert(db: sqlite3.Connection, id: str, body: str, now: str) -> Announcement:
    db.execute(
        f"INSERT INTO announcements ({_COLUMNS}) VALUES (?, ?, NULL, ?, ?, 0)",
        (id, body, now, now),
    )
    return _get(db, id)


def _pin(db: sqlite3.Connection, id: str) -> Announcement:
    found = db.execute("SELECT published_at FROM announcements WHERE id = ?", (id,)).fetchone()
    if found is None:
        raise NoSuchAnnouncement(id)
    if found[0] is None:
        raise NotPublished(id)
    db.execute("UPDATE announcements SET pinned = (id = ?)", (id,))
    return _get(db, id)


def _delete(db: sqlite3.Connection, id: str) -> None:
    deleted = db.execute("DELETE FROM announcements WHERE id = ?", (id,))
    if deleted.rowcount == 0:
        raise NoSuchAnnouncement(id)


def _update(db: sqlite3.Connection, id: str, assignments: str, values: tuple) -> Announcement:
    updated = db.execute(f"UPDATE announcements SET {assignments} WHERE id = ?", (*values, id))
    if updated.rowcount == 0:
        raise NoSuchAnnouncement(id)
    return _get(db, id)


def _get(db: sqlite3.Connection, id: str) -> Announcement:
    found = _select(db, " WHERE id = ?", (id,))
    if not found:
        raise NoSuchAnnouncement(id)
    return found[0]


def _select(db: sqlite3.Connection, rest: str, values: tuple) -> list[Announcement]:
    rows = db.execute(f"SELECT {_COLUMNS} FROM announcements{rest}", values).fetchall()
    return [Announcement(*row[:-1], pinned=bool(row[-1])) for row in rows]
