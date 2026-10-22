"""Promises (约定): what the producer said they would do for an idol on a day, and whether they did.

The background model writes them down as it writes memories, from the same answer: 「给冬优子
一块布丁」 with the day it is for, taken from a date said outright or worked out from 「明天」
「后天」. A promise for a day already past is dropped, and one already made for that day is kept
once. It is shown the promises already kept with her, numbered, so that it writes none of them
again, and says by their numbers which the producer put off to another day (`reschedule`) or
called off (`cancel`): `parse_promise_changes`.

On the day it is for she asks about it (`to_ask`, marked `asked` once what she said is kept), at a minute drawn once between `ASK_FROM` and
midnight of the producer's own day, at the first turn after it — a turn or the reach-out that
is one. It is fulfilled when the gift of its name is given to her (`by_gift`: a promise to give,
send or buy it, for no later than tomorrow), when the judge model finds the producer's words
say it is done (`awaiting`: any for a day from `JUDGED_FOR` ago to today, asked about or not,
complained of or not, `MAX_JUDGED` at a turn), or when the user says so themselves (`fulfil`),
and is deleted the moment it is.

A promise may be for a stretch of days rather than one (「这周末」「月底前」): from its first day
(`starts`) to its last (`due`), asked about on the last and complained of after it. The day
before its first day she reminds the producer of it, once (`to_remind`, `reminded`). A promise
to call her, or to come and talk to her, is kept by doing so within its days (`by_action`): a
call with her, or any turn. How each ends up — kept, kept only after she complained, put off,
called off, put off or called off at the last minute or after, broken — is told to whoever
listens (`on_settled`), for her affinity to go by.

One not fulfilled that day is one she complains of (`to_complain`, then `complained`), from the next day on, once a
day, in her turns and reach-outs alike; it is kept `KEEP_AFTER_COMPLAINT` after she first
complains and then deleted (`sweep`). Nothing outlives that.
"""

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.features import AllOn, Switches
from eidolon.randomness import Random, secure_random

log = logging.getLogger(__name__)

ASK_FROM = 12
"""From what hour of the producer's day she asks about it at the earliest: the afternoon, so
that a promise made in the morning is not asked about before the day has begun to show."""

KEEP_AFTER_COMPLAINT = timedelta(days=3)
"""How long a promise she complained of is kept before it is deleted."""

JUDGED_FOR = 3
"""For how many days after the day it is for the judge model is still asked, turn by turn,
whether the producer's words kept a promise (`promise_judge`): from the day itself, before she
asks too, and after she complains, as long as a complaint keeps it."""

MAX_JUDGED = 3
"""How many promises the judge model is asked about at a turn at most, each a request."""

GIVING = ("给", "送", "买", "请")
"""What a promise kept by a gift says the producer does: give, send, buy, treat her to it."""

MAX_TEXT = 120
"""How long a promise may be, in characters; a longer one is cut."""

_SCHEMA = """
-- A promise the producer made an idol for a day: `due_on` in their own time zone, `ask_at`
-- when she would ask about it, `asked_at` once she has, and `complained_at` the first day she
-- complained and `complained_on` the day she last did. `from_on` is the first of its days for
-- one for a stretch of them (None for one day), `put_off` how many times it was put off, and
-- `reminded_on` the day she reminded the producer of it. Deleted once fulfilled, or once a
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
    complained_on TEXT,
    from_on TEXT,
    put_off INTEGER NOT NULL DEFAULT 0,
    reminded_on TEXT
);
CREATE INDEX IF NOT EXISTS promises_by_due ON promises(user_id, character_id, due_on);
"""


@dataclass(frozen=True)
class NewPromise:
    """A promise the background model wrote down: what, and the day it is for; for a stretch
    of days, its last, and its first (`starts`)."""

    text: str
    due: date
    starts: date | None = None


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
    starts: date | None = None
    """The first of its days, for one for a stretch of them; None for one for a day."""
    put_off: int = 0
    """How many times it was put off."""

    @property
    def first_day(self) -> date:
        return self.starts or self.due

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
        first = entry.get("from")
        starts = _a_day(first) if isinstance(first, str) else None
        found.setdefault(
            (text, day), NewPromise(text, day, starts if starts is not None and starts < day else None)
        )
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


@dataclass(frozen=True)
class PromiseChange:
    """A promise kept put off to another day (`due`), or called off (`due` None)."""

    promise_id: int
    due: date | None


def parse_promise_changes(answer: str, numbered: Sequence["Promise"]) -> list[PromiseChange]:
    """The promises the background model says were put off or called off, by their numbers in
    `numbered` (from 1), as it was shown them: `{"promise_changes": [{"id": 2, "due":
    "YYYY-MM-DD"}, {"id": 1, "cancel": true}]}`. A number not shown, or a change that cannot be
    read, is passed over."""
    start, end = answer.find("{"), answer.rfind("}")
    try:
        written = json.loads(answer[start : end + 1]) if start >= 0 else None
    except ValueError:
        return []
    if not isinstance(written, dict):
        return []
    changes: dict[int, PromiseChange] = {}
    for entry in written.get("promise_changes") or []:
        if not isinstance(entry, dict):
            continue
        number = entry.get("id")
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(numbered):
            continue
        promise = numbered[number - 1]
        if entry.get("cancel") is True:
            changes[promise.id] = PromiseChange(promise.id, None)
            continue
        due = entry.get("due")
        day = _a_day(due) if isinstance(due, str) else None
        if day is not None and day != promise.due:
            changes[promise.id] = PromiseChange(promise.id, day)
    return list(changes.values())


PromiseListener = Callable[[str, str, str, datetime], None]

Settled = Literal[
    "kept", "kept_late", "put_off", "put_off_late", "called_off", "called_off_late", "broken"
]
"""How a promise ended up, or came on: kept; kept only after she complained of it; put off ahead
of its days, the first time; put off on or after its first day, or once more; called off ahead;
called off on or after its first day; broken, as she first complains of it."""

SettledListener = Callable[["Promise", Settled, datetime], None]

CALLING = ("电话", "打给她", "通话")
"""What a promise kept by a call with her says."""

TALKING = ("来找她", "去找她", "陪她聊", "和她聊", "找她聊")
"""What a promise kept by any turn with her says: a call keeps it too."""
"""Told the user, the idol, a promise's words, and when."""


class Promises:
    """Every user's promises to every idol, in the database at `path` beside the users they
    belong to; `random` draws the minute she asks about one at."""

    def __init__(
        self, path: Path, clock: Clock, random: Random = secure_random,
        features: Switches = AllOn(),
    ) -> None:
        """A user who has promises off (GLOSSARY.md「玩法开关」) has none kept, nor any she
        asks about or complains of."""
        self._features = features
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._clock = clock
        self._random = random
        self._kept: list[PromiseListener] = []
        self._broken: list[PromiseListener] = []
        self._settled: list[SettledListener] = []
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(promises)")}
            for column, kind in (
                ("from_on", "TEXT"), ("put_off", "INTEGER NOT NULL DEFAULT 0"),
                ("reminded_on", "TEXT"),
            ):
                if column not in columns:
                    self._db.execute(f"ALTER TABLE promises ADD COLUMN {column} {kind}")

    def on_kept(self, listener: "PromiseListener") -> None:
        """Have `listener` told the user, the idol, the promise's words and when, as one is
        kept, however it is (`fulfil`, `by_gift`)."""
        self._kept.append(listener)

    def on_broken(self, listener: "PromiseListener") -> None:
        """Have `listener` told the same as she first complains of one (`complained`)."""
        self._broken.append(listener)

    def on_settled(self, listener: SettledListener) -> None:
        """Have `listener` told each promise and how it ended up, or came on (`Settled`), and
        when: kept however it is, put off, called off, or broken as she first complains."""
        self._settled.append(listener)

    def _tell(self, listeners: "Sequence[PromiseListener]", promises: "Sequence[Promise]") -> None:
        now = self._clock()
        for promise in promises:
            for listener in listeners:
                try:
                    listener(promise.user_id, promise.character_id, promise.text, now)
                except Exception:
                    log.exception("telling of promise %s failed", promise.id)

    def _settle(self, promises: "Sequence[tuple[Promise, Settled]]") -> None:
        now = self._clock()
        for promise, settled in promises:
            for listener in self._settled:
                try:
                    listener(promise, settled, now)
                except Exception:
                    log.exception("telling how promise %s ended up failed", promise.id)

    def _kept_all(self, promises: "Sequence[Promise]") -> None:
        """Tell of `promises`, kept: whether only after she complained of each, too."""
        self._tell(self._kept, promises)
        self._settle([(p, "kept_late" if p.complained_at else "kept") for p in promises])

    def record(
        self, user_id: str, character_id: str, made: Sequence[NewPromise], now: datetime
    ) -> None:
        """Keep `made`, promised in the turn recorded at `now` (in the producer's own time
        zone): a promise for a day already gone is dropped, one already made for that day is
        not written twice."""
        if not self._features.on(user_id, "promises"):
            return
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
                starts = promise.starts if promise.starts and promise.starts > today else None
                self._db.execute(
                    "INSERT INTO promises"
                    " (user_id, character_id, text, due_on, made_at, ask_at, from_on)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        character_id,
                        promise.text,
                        promise.due.isoformat(),
                        now.astimezone(UTC).isoformat(),
                        self._ask_at(promise.due, now).isoformat(),
                        starts.isoformat() if starts else None,
                    ),
                )

    def to_ask(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise for today she would ask about now, changing nothing: whose asking
        minute has come and that she has not asked about. Only once what she says is kept is
        it marked asked (`asked`), so that a turn that fails, a reach-out not sent and a
        letter written ahead do not use it up."""
        if not self._features.on(user_id, "promises"):
            return None
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

    def awaiting(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> list[Promise]:
        """The promises the turn taken at `now` may have kept: for a day from `JUDGED_FOR`
        days ago to today in the producer's own time zone, asked about or not, complained of
        or not; the earliest `MAX_JUDGED`."""
        today = now.astimezone(ZoneInfo(timezone)).date()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " AND COALESCE(from_on, due_on) <= ? AND due_on >= ?"
                " ORDER BY due_on, id LIMIT ?",
                (
                    user_id, character_id, today.isoformat(),
                    (today - timedelta(days=JUDGED_FOR)).isoformat(), MAX_JUDGED,
                ),
            ).fetchall()
        return [_promise(row) for row in rows]

    def standing(self, user_id: str, character_id: str) -> list[Promise]:
        """Every promise still standing with her, the earliest first: for the background
        model to be shown, numbered, as it writes promises down; none where the user has
        promises off."""
        if not self._features.on(user_id, "promises"):
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " ORDER BY due_on, id",
                (user_id, character_id),
            ).fetchall()
        return [_promise(row) for row in rows]

    def change(self, user_id: str, changes: Sequence[PromiseChange], now: datetime) -> None:
        """Put off or call off the user's promises as `changes` say, at `now` (in the
        producer's own time zone): one put off is as though made anew for its new day, to be
        asked about then and not complained of meanwhile; one put off to a day gone is left as
        it is; one called off is gone, neither kept nor broken."""
        today = now.date()
        settled: list[tuple[Promise, Settled]] = []
        with self._lock, self._db:
            for change in changes:
                row = self._db.execute(
                    "SELECT * FROM promises WHERE id = ? AND user_id = ?",
                    (change.promise_id, user_id),
                ).fetchone()
                if row is None:
                    continue
                promise = _promise(row)
                late = promise.first_day <= today
                if change.due is None:
                    self._db.execute("DELETE FROM promises WHERE id = ?", (promise.id,))
                    settled.append((promise, "called_off_late" if late else "called_off"))
                elif change.due >= today:
                    self._db.execute(
                        "UPDATE promises SET due_on = ?, ask_at = ?, asked_at = NULL,"
                        " complained_at = NULL, complained_on = NULL, from_on = NULL,"
                        " reminded_on = NULL, put_off = put_off + 1 WHERE id = ?",
                        (
                            change.due.isoformat(), self._ask_at(change.due, now).isoformat(),
                            promise.id,
                        ),
                    )
                    again = late or promise.put_off > 0
                    settled.append((promise, "put_off_late" if again else "put_off"))
        self._settle(settled)

    def to_complain(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise for a day gone by she would complain of now, changing nothing: the
        earliest one for a day before today that she has not complained of today. Marked
        only once what she says is kept (`complained`)."""
        if not self._features.on(user_id, "promises"):
            return None
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
            row = self._db.execute(
                "SELECT * FROM promises WHERE id = ?", (promise_id,)
            ).fetchone()
            self._db.execute(
                "UPDATE promises SET complained_at = COALESCE(complained_at, ?), complained_on = ?"
                " WHERE id = ?",
                (now.astimezone(UTC).isoformat(), today, promise_id),
            )
        if row is not None and row["complained_at"] is None:
            self._tell(self._broken, [_promise(row)])
            self._settle([(_promise(row), "broken")])

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
            row = self._db.execute(
                "SELECT * FROM promises WHERE id = ? AND user_id = ?", (promise_id, user_id)
            ).fetchone()
            if row is not None:
                self._db.execute("DELETE FROM promises WHERE id = ?", (promise_id,))
        if row is None:
            return False
        self._kept_all([_promise(row)])
        return True

    def by_gift(self, user_id: str, character_id: str, gift_name: str) -> bool:
        """A gift named in the promise was given to her: that promise is gone. Judged by the
        promise's own words, since what wrote it knows no gift catalog: one to give, send or
        buy her the gift (`GIVING`), not one that merely names it (「带她去书店」 is not kept by a
        book); and only one for no later than tomorrow, not the next week's."""
        if not gift_name:
            return False
        found: list[Promise] = []
        latest = (self._clock().date() + timedelta(days=1)).isoformat()
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " AND COALESCE(from_on, due_on) <= ?",
                (user_id, character_id, latest),
            ).fetchall()
            for row in rows:
                text = row["text"]
                if gift_name in text and any(verb in text for verb in GIVING):
                    found.append(_promise(row))
                    self._db.execute("DELETE FROM promises WHERE id = ?", (row["id"],))
        self._kept_all(found)
        return bool(found)

    def by_action(
        self, user_id: str, character_id: str, called: bool, now: datetime, timezone: str
    ) -> bool:
        """The producer talked to her at `now`, in a call if `called`: the promises of these
        days to do so are kept (`TALKING`, and `CALLING` for a call), as the judge model's
        days go (`JUDGED_FOR`). True if any was."""
        today = now.astimezone(ZoneInfo(timezone)).date()
        words = TALKING + CALLING if called else TALKING
        found: list[Promise] = []
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " AND COALESCE(from_on, due_on) <= ? AND due_on >= ?",
                (
                    user_id, character_id, today.isoformat(),
                    (today - timedelta(days=JUDGED_FOR)).isoformat(),
                ),
            ).fetchall()
            for row in rows:
                if any(word in row["text"] for word in words):
                    found.append(_promise(row))
                    self._db.execute("DELETE FROM promises WHERE id = ?", (row["id"],))
        self._kept_all(found)
        return bool(found)

    def to_remind(
        self, user_id: str, character_id: str, now: datetime, timezone: str
    ) -> Promise | None:
        """The promise whose first day is tomorrow she would remind the producer of now,
        changing nothing; marked once what she says is kept (`reminded`)."""
        if not self._features.on(user_id, "promises"):
            return None
        today = now.astimezone(ZoneInfo(timezone)).date()
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM promises WHERE user_id = ? AND character_id = ?"
                " AND COALESCE(from_on, due_on) = ? AND reminded_on IS NULL ORDER BY id LIMIT 1",
                (user_id, character_id, (today + timedelta(days=1)).isoformat()),
            ).fetchone()
        return _promise(row) if row is not None else None

    def reminded(self, promise_id: int, now: datetime, timezone: str) -> None:
        """She reminded the producer of it at `now`, in what was kept."""
        today = now.astimezone(ZoneInfo(timezone)).date().isoformat()
        with self._lock, self._db:
            self._db.execute(
                "UPDATE promises SET reminded_on = ? WHERE id = ?", (today, promise_id)
            )

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
        starts=date.fromisoformat(row["from_on"]) if row["from_on"] else None,
        put_off=row["put_off"],
    )


def _at(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None
