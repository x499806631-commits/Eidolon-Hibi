"""Her own doings (GLOSSARY.md「她们的经历」): what an idol said she had done — went to an
exhibition, played a new game, spent the day asleep — kept so that she remembers having done
it, and forgotten when nothing brings it up.

Unlike memories, these belong to one character: what 甜花 did is not 凛世's, and each is
found among her own only. Unlike memories too, they are not superseded: the same words are
kept once (`UNIQUE`), and the background model, shown her latest, is told not to write down
again what is among them, even told more fully.

Each is kept `KEPT` from when it was last recalled (`last_used_at`, or `created_at` where
nothing has recalled it), and then forgotten by `sweep`; one `pinned` is forgotten never. The
days left tell the UI when to say so (`SOON`).
"""

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.embedding import Document, Embedder, EmbeddingError, nearest, to_blob

log = logging.getLogger(__name__)

KEPT = timedelta(days=30)
"""How long one is kept after it was last recalled, unless pinned."""

SOON = timedelta(days=7)
"""How long before it is forgotten the UI says so."""

MAX_TEXT = 200
MAX_SUMMARY = 300
SHOWN_BEFORE_WRITING = 10
"""How many of her own, newest first, the background model is shown before writing more."""

MAX_FOUND = 2
"""How many of hers a turn is told of at most."""

MAX_NEW = 2
"""How many of hers one turn may write down at most."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    text TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    url TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, character_id, text)
);
CREATE INDEX IF NOT EXISTS experiences_by_character
    ON experiences(user_id, character_id, created_at);
-- The vectors, one per experience and model, as memories keep theirs.
CREATE TABLE IF NOT EXISTS experience_vectors (
    experience_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    PRIMARY KEY (experience_id, model)
);
CREATE INDEX IF NOT EXISTS experience_vectors_by_user
    ON experience_vectors(user_id, model);
"""


@dataclass(frozen=True)
class NewExperience:
    """One the background model wrote down: what she did, what was looked up about it, and
    where that came from."""

    text: str
    summary: str = ""
    url: str | None = None


@dataclass(frozen=True)
class Experience:
    """One kept: what she did, and whether anything has brought it up lately."""

    id: int
    user_id: str
    character_id: str
    text: str
    summary: str
    url: str | None
    created_at: datetime
    last_used_at: datetime | None
    pinned: bool

    def left(self, now: datetime) -> timedelta | None:
        """How long until it is forgotten, or None where it is pinned and so never is."""
        if self.pinned:
            return None
        return KEPT - (now - (self.last_used_at or self.created_at))

    def shown(self, now: datetime) -> dict:
        """As the browser is told it: its words, what was looked up, and the days left."""
        left = self.left(now)
        return {
            "id": self.id,
            "character_id": self.character_id,
            "text": self.text,
            "summary": self.summary,
            "url": self.url,
            "created_at": self.created_at.isoformat(),
            "pinned": self.pinned,
            "days_left": None if left is None else max(0, left.days),
            "due_soon": left is not None and left <= SOON,
        }


def parse_experiences(written: object) -> list[NewExperience]:
    """The experiences in the background model's answer, read from the `experiences` array of
    the same JSON as its memories and promises; nothing where it wrote none, or wrote them in
    a way that cannot be read. Read on its own, so a broken array costs only these."""
    if not isinstance(written, list):
        return []
    found: dict[str, NewExperience] = {}
    for entry in written:
        if isinstance(entry, str):
            text, summary, url = entry, "", None
        elif isinstance(entry, dict):
            text, summary, url = entry.get("text"), entry.get("summary"), entry.get("url")
        else:
            continue
        if not isinstance(text, str) or not (text := text.strip()[:MAX_TEXT]):
            continue
        found.setdefault(text, NewExperience(
            text,
            summary.strip()[:MAX_SUMMARY] if isinstance(summary, str) else "",
            url.strip()[:500] if isinstance(url, str) and url.strip() else None,
        ))
    return list(found.values())[:MAX_NEW]


class Experiences:
    """Every character's own doings, in the database at `path` beside the users they belong
    to; `clock` says when one was last recalled, and so when it is forgotten."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._clock = clock
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def record(
        self, user_id: str, character_id: str, made: Sequence[NewExperience], now: datetime
    ) -> int:
        """Keep `made`, said in the turn recorded at `now`: the same words are kept once, what
        was looked up about them the latest."""
        kept = 0
        with self._lock, self._db:
            for made_one in made:
                at = now.astimezone(UTC).isoformat()
                self._db.execute(
                    "INSERT INTO experiences"
                    " (user_id, character_id, text, summary, url, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT (user_id, character_id, text) DO UPDATE SET"
                    " summary = excluded.summary, url = COALESCE(excluded.url, url)",
                    (user_id, character_id, made_one.text, made_one.summary, made_one.url, at),
                )
                kept += 1
        return kept

    def recent(self, user_id: str, character_id: str) -> list[Experience]:
        """Her own, newest first, as many as `SHOWN_BEFORE_WRITING`: what the background model
        is shown before writing more, so that it writes the same thing once."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM experiences WHERE user_id = ? AND character_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (user_id, character_id, SHOWN_BEFORE_WRITING),
            ).fetchall()
        return [_one(row) for row in rows]

    def all_of(self, user_id: str) -> list[Experience]:
        """Everyone's, for a data package: hers and hers and hers, none of them told apart."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM experiences WHERE user_id = ? ORDER BY character_id, id",
                (user_id,),
            ).fetchall()
        return [_one(row) for row in rows]

    def carry(
        self,
        user_id: str,
        character_id: str,
        made: NewExperience,
        now: datetime,
        used_at: datetime | None,
        pinned: bool,
    ) -> None:
        """Keep one carried in by a data package (`carry`), aged as it was there: `used_at`
        is when it was last recalled, so a package does not make it young again."""
        at = now.astimezone(UTC).isoformat()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO experiences"
                " (user_id, character_id, text, summary, url, created_at, last_used_at, pinned)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (user_id, character_id, text) DO UPDATE SET"
                " summary = excluded.summary, url = COALESCE(excluded.url, url)",
                (
                    user_id, character_id, made.text, made.summary, made.url, at,
                    used_at.astimezone(UTC).isoformat() if used_at else None,
                    1 if pinned else 0,
                ),
            )

    def of(self, user_id: str, character_id: str) -> list[Experience]:
        """Her own, newest first: for the page."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM experiences WHERE user_id = ? AND character_id = ?"
                " ORDER BY created_at DESC, id DESC",
                (user_id, character_id),
            ).fetchall()
        return [_one(row) for row in rows]

    def due_soon(self, user_id: str) -> list[Experience]:
        """Everyone's that are about to be forgotten, soonest first: for the notice."""
        now = self._clock()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM experiences WHERE user_id = ? AND pinned = 0"
                " ORDER BY COALESCE(last_used_at, created_at)",
                (user_id,),
            ).fetchall()
        soon = [
            e for e in (_one(row) for row in rows)
            if (left := e.left(now)) is not None and left <= SOON
        ]
        return soon

    def remember_used(self, ids: Sequence[int]) -> None:
        """Note that these were recalled: each is kept `KEPT` from now."""
        if not ids:
            return
        at = self._clock().astimezone(UTC).isoformat()
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE experiences SET last_used_at = ? WHERE id = ?",
                [(at, i) for i in ids],
            )

    async def nearest(
        self,
        embedder: "Embedder",
        user_id: str,
        character_id: str,
        query_vector: Sequence[float],
        limit: int,
    ) -> list["Experience"]:
        """As many as `limit` of hers nearest the turn's query, nearest first, her own only;
        those without a vector yet are embedded now, and left for next time if that fails."""
        mine = await asyncio.to_thread(self.of, user_id, character_id)
        if not mine:
            return []
        vectors = await self._vectors(embedder, user_id, mine)
        order = nearest([vectors[e.id] for e in mine if e.id in vectors], query_vector, limit)
        holding = [e for e in mine if e.id in vectors]
        return [holding[i] for i, _ in order]

    async def _vectors(
        self, embedder: "Embedder", user_id: str, mine: Sequence[Experience]
    ) -> dict[int, bytes]:
        """The vectors these have, embedding those that have none yet; those that cannot be
        embedded are left without, to be tried again next time."""
        model = embedder.model
        with self._lock:
            rows = self._db.execute(
                "SELECT experience_id, embedding FROM experience_vectors"
                " WHERE user_id = ? AND model = ?",
                (user_id, model),
            ).fetchall()
        vectors = {row["experience_id"]: row["embedding"] for row in rows}
        missing = [e for e in mine if e.id not in vectors]
        if not missing:
            return vectors
        try:
            embedded = await embedder.embed_documents([Document(text=e.text) for e in missing])
        except EmbeddingError:
            log.warning("embedding her own doings failed; finding them without", exc_info=True)
            return vectors
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO experience_vectors"
                " (experience_id, user_id, model, embedding) VALUES (?, ?, ?, ?)",
                [(e.id, user_id, model, to_blob(v)) for e, v in zip(missing, embedded, strict=True)],
            )
        vectors |= {
            e.id: to_blob(v) for e, v in zip(missing, embedded, strict=True)
        }
        return vectors

    def keep(self, user_id: str, experience_id: int) -> bool:
        """Kept `KEPT` from now, as being recalled keeps it."""
        at = self._clock().astimezone(UTC).isoformat()
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE experiences SET last_used_at = ? WHERE id = ? AND user_id = ?",
                (at, experience_id, user_id),
            ).rowcount > 0

    def pin(self, user_id: str, experience_id: int, pinned: bool) -> bool:
        """Kept for good, or counted again from when it was last recalled."""
        with self._lock, self._db:
            return self._db.execute(
                "UPDATE experiences SET pinned = ? WHERE id = ? AND user_id = ?",
                (1 if pinned else 0, experience_id, user_id),
            ).rowcount > 0

    def delete(self, user_id: str, experience_id: int) -> bool:
        """Gone, the vectors with it."""
        with self._lock, self._db:
            gone = self._db.execute(
                "DELETE FROM experiences WHERE id = ? AND user_id = ?",
                (experience_id, user_id),
            ).rowcount
            if gone:
                self._db.execute(
                    "DELETE FROM experience_vectors WHERE experience_id = ?", (experience_id,)
                )
        return gone > 0

    def sweep(self, now: datetime) -> None:
        """Forget what nothing has recalled for `KEPT`, and what is not pinned."""
        cutoff = (now - KEPT).astimezone(UTC).isoformat()
        with self._lock, self._db:
            forgotten = self._db.execute(
                "DELETE FROM experiences WHERE pinned = 0"
                " AND COALESCE(last_used_at, created_at) <= ?",
                (cutoff,),
            ).rowcount
            if forgotten:
                self._db.execute(
                    "DELETE FROM experience_vectors WHERE experience_id NOT IN"
                    " (SELECT id FROM experiences)"
                )

    def close(self) -> None:
        with self._lock:
            self._db.close()


def _one(row: sqlite3.Row) -> Experience:
    return Experience(
        id=row["id"],
        user_id=row["user_id"],
        character_id=row["character_id"],
        text=row["text"],
        summary=row["summary"],
        url=row["url"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_used_at=datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None,
        pinned=bool(row["pinned"]),
    )


def prompt_of(found: Sequence[Experience], name: str) -> str | None:
    """What she is told of her own that the turn brought up, or None where nothing did. Her
    doings read as her own (`name` is hers), with what was looked up about them beside them, so
    that she remembers the facts of it rather than making them up."""
    if not found:
        return None
    lines = [
        f"# 你自己经历过的事（{name}自己的事，不是制作人的，可以当成亲身经历过）",
        *[
            f"- {e.text}" + (f"（{e.summary}）" if e.summary else "")
            for e in found
        ],
    ]
    return "\n".join(lines)
