"""Promises (约定): what the producer said they would do for an idol on a day, and whether they did.

The background model writes them down as it writes memories, from the same answer: 「给冬优子
一块布丁」 with the day it is for, taken from a date said outright or worked out from 「明天」
「后天」. A promise for a day already past is dropped, and one already made for that day is kept
once.

On the day it is for she asks about it (`to_ask`, marked `asked` once what she said is kept), at a minute drawn once between `ASK_FROM` and
midnight of the producer's own day, at the first turn after it — a turn or the reach-out that
is one. It is fulfilled when the gift of its name is given to her (`by_gift`) or when the user
says so themselves (`fulfil`), and is deleted the moment it is.

One not fulfilled that day is one she complains of (`to_complain`, then `complained`), from the next day on, once a
day, in her turns and reach-outs alike; it is kept `KEEP_AFTER_COMPLAINT` after she first
complains and then deleted (`sweep`). Nothing outlives that.
"""

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.randomness import Random, secure_random

log = logging.getLogger(__name__)

ASK_FROM = 12
"""From what hour of the producer's day she asks about it at the earliest: the afternoon, so
that a promise made in the morning is not asked about before the day has begun to show."""

KEEP_AFTER_COMPLAINT = timedelta(days=3)
"""How long a promise she complained of is kept before it is deleted."""

ASKING = timedelta(days=2)
"""How long after she asked about a promise the judge model is still asked, turn by turn,
whether the producer's words kept it (`promise_judge`): his answer comes in a turn or two, not
days later, and each ask costs a request."""

MAX_TEXT = 120
"""How long a promise may be, in characters; a longer one is cut."""

_SCHEMA = """
-- A promise the producer made an idol for a day: `due_on` in their own time zone, `ask_at`
-- when she would ask about it, `asked_at` once she has, and `complained_at` the first day she
-- complained and `complained_on` the day she last did. Deleted once fulfilled, or once a
-- complaint has had its days.
CREATE TABLE IF NOT EXISTS promises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    text TEXT NOT NULL,
    due_on TEXT NOT NULL,
    made_at TEXT NOT NULL,
    ask_at TEXT NOT NULL,
    asked_at TEXT,
    complained_at TEXT,
    complained_on TEXT
);
CREATE INDEX IF NOT EXISTS promises_by_due ON promises(user_id, character_id, due_on);
"""


@dataclass(frozen=True)
class NewPromise:
    """A promise the background model wrote down: what, and the day it is for."""

    text: str
    due: date


@dataclass(frozen=True)
class Promise:
    """A promise kept, and how far it has come."""

    id: int
    user_id: str
    character_id: str
    text: str
    due: date
    asked_at: datetime | None = None
    complained_at: datetime | None = None

    def shown(self) -> dict:
        """As the calendar page is told it: the day it falls on, and what was promised."""
        return {
            "id": self.id,
            "character_id": self.character_id,
            "month": self.due.month,
            "day": self.due.day,
            "text": self.text,
        }


def parse_promises(answer: str) -> list[NewPromise]:
    """The promises written beside the memories in the background model's answer; nothing
    where it wrote none, or wrote them in a way that cannot be read."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0:
        return []
    try:
        written = json.loads(answer[start : end + 1])
    except ValueError:
        return []
    if not isinstance(written, dict):
        return []
    found: dict[tuple[str, date], NewPromise] = {}
    for entry in written.get("promises") or []:
        if not isinstance(entry, dict):
            continue
        text, due = entry.get("text"), entry.get("due")
        if not isinstance(text, str) or not (text := text.strip()[:MAX_TEXT]):
            continue
        day = _a_day(due) if isinstance(due, str) else None
        if day is None:
            continue
        found.setdefault((text, day), NewPromise(text, day))
    return list(found.values())


def _a_day(text: str) -> date | None:
    """The day `text` names as `YYYY-MM-DD`, and only written that way."""
    text = text.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


class Promises:
    """Every user's promises to every idol, in the database at `path` beside the users they
    belong to; `random` draws the minute she asks about one at."""

    def __init__(self, path: Path, clock: Clock, random: Random = secure_random) -> None:
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._clock = clock
        self._random = random
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def record(
        self, user_id: str, character_id: str, made: Sequence[NewPromise], now: datetime
    ) -> None:
        """Keep `made`, promised in the turn recorded at `now` (in the producer's own time
        zone): a promise for a day already gone is dropped, one already made for that day is
        not written twice."""
        today = now.date()
        with self._lock, self._db:
            for promise in made:
                if promise.due < today:
                    continue
                if self._db.execute(
                    "SELECT 1 FROM promises WHERE user_id = ? AND character_id = ?"
                    " AND text = ? AND due_on = ?",
                    (user_id, character_id, promise.text, promise.due.isoformat()),
                ).fetchone():
                    continue
                self._db.execute(
                    "INSERT INTO promises (user_id, character_id, text, due_on, made_at, ask_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        character_id,
                        promise.text,
                        promise.due.isoformat(),
                        now.astimezone(UTC).isoformat(),
                        self._ask_at(promise.due, now).isoformat(),
                    ),
                )

    def to_ask(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise for today she would ask about now, changing nothing: whose asking
        minute has come and that she has not asked about. Only once what she says is kept is
        it marked asked (`asked`), so that a turn that fails, a reach-out not sent and a
        letter written ahead do not use it up."""
        today = now.astimezone(ZoneInfo(timezone)).date().isoformat()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ? AND due_on = ?"
                " AND asked_at IS NULL AND complained_at IS NULL AND ask_at <= ?"
                " ORDER BY id LIMIT 1",
                (user_id, character_id, today, now.astimezone(UTC).isoformat()),
            ).fetchone()
        return _promise(row) if row is not None else None

    def asked(self, promise_id: int, now: datetime) -> None:
        """She asked about it at `now`, in what was kept."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE promises SET asked_at = COALESCE(asked_at, ?) WHERE id = ?",
                (now.astimezone(UTC).isoformat(), promise_id),
            )

    def asking(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise for today she asks about now, marked asked at once (`to_ask`, `asked`)."""
        found = self.to_ask(user_id, character_id, now, timezone)
        if found is not None:
            self.asked(found.id, now)
        return found

    def awaiting(self, user_id: str, character_id: str, now: datetime) -> Promise | None:
        """The promise she asked about, that the turn taken at `now` may have kept: asked
        about within `ASKING` of now, and neither fulfilled nor complained of."""
        since = (now.astimezone(UTC) - ASKING).isoformat()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " AND asked_at IS NOT NULL AND asked_at >= ? AND complained_at IS NULL"
                " ORDER BY id LIMIT 1",
                (user_id, character_id, since),
            ).fetchone()
        return _promise(row) if row is not None else None

    def to_complain(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise for a day gone by she would complain of now, changing nothing: the
        earliest one for a day before today that she has not complained of today. Marked
        only once what she says is kept (`complained`)."""
        today = now.astimezone(ZoneInfo(timezone)).date().isoformat()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ? AND due_on < ?"
                " AND (complained_on IS NULL OR complained_on <> ?)"
                " ORDER BY due_on, id LIMIT 1",
                (user_id, character_id, today, today),
            ).fetchone()
        return _promise(row) if row is not None else None

    def complained(self, promise_id: int, now: datetime, timezone: str) -> None:
        """She complained of it at `now`, in what was kept: not again that day."""
        today = now.astimezone(ZoneInfo(timezone)).date().isoformat()
        with self._lock, self._db:
            self._db.execute(
                "UPDATE promises SET complained_at = COALESCE(complained_at, ?), complained_on = ?"
                " WHERE id = ?",
                (now.astimezone(UTC).isoformat(), today, promise_id),
            )

    def complaining(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise she complains of now, marked complained of at once (`to_complain`,
        `complained`)."""
        found = self.to_complain(user_id, character_id, now, timezone)
        if found is not None:
            self.complained(found.id, now, timezone)
        return found

    def fulfil(self, user_id: str, promise_id: int) -> bool:
        """It was done: the promise is gone. False where it is not theirs, or already gone."""
        with self._lock, self._db:
            return self._db.execute(
                "DELETE FROM promises WHERE id = ? AND user_id = ?", (promise_id, user_id)
            ).rowcount > 0

    def by_gift(self, user_id: str, character_id: str, gift_name: str) -> bool:
        """A gift named in the promise was given to her: that promise is gone. Judged by the
        promise's own words, since what wrote it knows no gift catalog."""
        if not gift_name:
            return False
        found = False
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT id, text FROM promises WHERE user_id = ? AND character_id = ?",
                (user_id, character_id),
            ).fetchall()
            for row in rows:
                if gift_name in row["text"]:
                    found = True
                    self._db.execute("DELETE FROM promises WHERE id = ?", (row["id"],))
        return found

    def open_for(self, user_id: str) -> list[Promise]:
        """Every promise still standing with any idol: for the calendar."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? ORDER BY due_on, id", (user_id,)
            ).fetchall()
        return [_promise(row) for row in rows]

    def due(self, user_id: str, character_id: str) -> Promise | None:
        """The earliest promise this user has with this character, for the calendar's day."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " ORDER BY due_on, id LIMIT 1",
                (user_id, character_id),
            ).fetchone()
        return _promise(row) if row is not None else None

    def sweep(self, now: datetime) -> None:
        """Delete the promises complained of `KEEP_AFTER_COMPLAINT` ago or longer."""
        cutoff = (now.astimezone(UTC) - KEEP_AFTER_COMPLAINT).isoformat()
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM promises WHERE complained_at IS NOT NULL AND complained_at <= ?",
                (cutoff,),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _ask_at(self, due: date, now: datetime) -> datetime:
        """When she would ask about the promise for `due`: a minute of that day from `ASK_FROM`
        to midnight, drawn once, in the producer's own time zone."""
        drawn = ASK_FROM * 60 + self._random((24 - ASK_FROM) * 60)
        hour, minute = divmod(drawn, 60)
        asks = datetime.combine(due, time(hour, minute), now.tzinfo or UTC)
        return asks.astimezone(UTC)


def _promise(row: sqlite3.Row) -> Promise:
    return Promise(
        id=row["id"],
        user_id=row["user_id"],
        character_id=row["character_id"],
        text=row["text"],
        due=date.fromisoformat(row["due_on"]),
        asked_at=_at(row["asked_at"]),
        complained_at=_at(row["complained_at"]),
    )


def _at(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None
