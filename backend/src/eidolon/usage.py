"""Per-user rate limits, so one user cannot use up the API credit everyone shares: on text
turns, voice calls, and the photos she sends (GLOSSARY.md「照片」), each apart.

A turn or call exempt on the user's own keys (GLOSSARY.md「免限额密钥」) is recorded all the
same, for the admin to see, but does not count towards the limit. An admin may set a user's
own limits, apart from the deployment's: a number, unlimited, or 0, which leaves them the
exempt ones only.
Each use is kept in the database, next to the
user it belongs to, for a day; whoever's they are, uses older than that are deleted as the
backend tidies up (`housekeeping`)."""

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from eidolon.clock import Clock
from eidolon.config import UsageLimits
from eidolon.data_access import Database
from eidolon.housekeeping import in_batches

UsageKind = Literal["turn", "call", "photo"]

UserLimit = int | Literal["unlimited"] | None
"""A user's own limit on a kind of use an hour: a number (0: on their own keys only),
"unlimited", or None, the deployment's."""

WINDOW = timedelta(hours=1)

KEPT = timedelta(hours=24)
"""How long each use is kept: an hour for the limits, a day for the admin to look over."""

_NAMES: dict[UsageKind, str] = {"turn": "文字对话", "call": "语音通话", "photo": "照片"}

_SCHEMA = """
-- `at` is epoch seconds rather than ISO text, so that the window is a plain range query.
-- A use exempt on the user's own keys is `exempt`: recorded, but not limited.
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,
    at REAL NOT NULL,
    exempt INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_by_user ON usage(user_id, kind, at);
-- For forgetting everyone's uses once they are older than `KEPT`.
CREATE INDEX IF NOT EXISTS usage_by_time ON usage(at);
-- A user's own limits, set by an admin; a NULL limit is the deployment's, -1 unlimited.
CREATE TABLE IF NOT EXISTS user_limits (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    turns_per_hour INTEGER,
    calls_per_hour INTEGER,
    note TEXT,
    updated_at TEXT NOT NULL,
    photos_per_hour INTEGER
);
"""

_UNLIMITED_ROW = -1
_COLUMNS: dict[UsageKind, str] = {
    "turn": "turns_per_hour", "call": "calls_per_hour", "photo": "photos_per_hour",
}


class OverLimit(Exception):
    """The user has used up this hour's `kind`; they can go on at `retry_at`. With a
    limit of 0 they may only on their own keys, and `retry_at` is None."""

    def __init__(
        self, kind: UsageKind, limit: int, retry_at: datetime | None, now: datetime
    ) -> None:
        super().__init__(
            f"这一小时的{_NAMES[kind]}次数已经用完了（每小时 {limit} 次）" if limit
            else f"你的{_NAMES[kind]}只能用自带密钥，可以在设置里填写"
        )
        self.retry_at = retry_at
        self.now = now


@dataclass(frozen=True)
class UserLimits:
    """A user's own limits an hour, each apart (None: the deployment's), with a note for
    the admins and when they were set."""

    turns_per_hour: UserLimit
    calls_per_hour: UserLimit
    note: str | None = None
    updated_at: str = ""
    photos_per_hour: UserLimit = None


@dataclass(frozen=True)
class Tally:
    """How many text turns and calls, and how many of each were on the user's own keys."""

    turns: int = 0
    calls: int = 0
    exempt_turns: int = 0
    exempt_calls: int = 0


@dataclass(frozen=True)
class DayOfUse:
    """A user's uses in the last day: each UTC hour that has any, oldest first, and all."""

    hours: dict[datetime, Tally]
    total: Tally


class Use:
    """One counted text turn or call; given back when it fails, so the user keeps it."""

    def __init__(self, limiter: "UsageLimiter", use_id: int) -> None:
        self._limiter = limiter
        self._id: int | None = use_id

    async def give_back(self) -> None:
        if self._id is not None:
            use_id, self._id = self._id, None
            await self._limiter._give_back(use_id)


class UsageLimiter:
    """Counts each user's text turns, voice calls and photos over the last hour, whichever
    device or sign-in method they come with."""

    def __init__(
        self, db: Database, limits: UsageLimits | Callable[[], UsageLimits], clock: Clock
    ) -> None:
        """Uses kept in `db`, next to the users they belong to, limited by `limits`, or by
        what it says as each use is taken (the live configuration's)."""
        self._db = db
        db.prepare(_open)
        self._limits = limits if callable(limits) else lambda: limits
        self._clock = clock

    @property
    def limits(self) -> UsageLimits:
        """The limits in effect now."""
        return self._limits()

    async def take(self, user_id: str, kind: UsageKind, exempt: bool = False) -> Use:
        """Record one `kind` by the user, counting it against their limit unless it is
        `exempt`; raises OverLimit, recording nothing, when they have none left this hour,
        that is, when the last hour already holds as many counting uses as the limit: the
        user's own, if an admin set one, else the deployment's. Told and taken in one write
        task, so that two at once never both take the last one."""
        return Use(self, await self._db.write(
            _take, user_id, kind, exempt, self._clock(), self._limits()
        ))

    async def forget_old(self) -> None:
        """Delete everyone's uses older than `KEPT`, so that a user who does not come back
        leaves none behind; in batches (`housekeeping.in_batches`)."""
        await in_batches(self._db, _forget_before, (self._clock() - KEPT).timestamp())

    def check(self, db: sqlite3.Connection, user_id: str, kind: UsageKind) -> None:
        """Raise OverLimit, as `take` would, if the user has no `kind` left this hour that
        counts; take nothing. Read through `db`, in the body of a read task of the
        caller's."""
        _refuse_over(db, user_id, kind, self._clock(), self._limits())

    async def user_limits(self, user_id: str) -> UserLimits | None:
        """The user's own limits; None if an admin set none."""
        return await self._db.read(read_user_limits, user_id)

    async def limits_of(self, user_ids: Sequence[str]) -> dict[str, UserLimits | None]:
        """Each of the users' own limits, as `user_limits`, read at once."""
        return await self._db.read(
            lambda db: {user_id: read_user_limits(db, user_id) for user_id in user_ids}
        )

    async def set_user_limits(self, user_id: str, limits: UserLimits | None) -> None:
        """Set the user's own limits, as of now, replacing those they had; None takes them
        off, and the deployment's hold again."""
        await self._db.write(_set_user_limits, user_id, limits, self._clock())

    async def last_day(self, user_id: str) -> DayOfUse:
        """The user's uses in the last day, by hour, for the admin to look over."""
        rows = await self._db.read(_last_day, user_id, (self._clock() - KEPT).timestamp())
        hours: dict[datetime, list[tuple[UsageKind, bool]]] = {}
        for kind, at, exempt in rows:
            hour = datetime.fromtimestamp(at, UTC).replace(minute=0, second=0, microsecond=0)
            hours.setdefault(hour, []).append((kind, bool(exempt)))
        return DayOfUse(
            {hour: _tally(uses) for hour, uses in hours.items()},
            _tally([use for uses in hours.values() for use in uses]),
        )

    async def _give_back(self, use_id: int) -> None:
        """Forget a use, as if it had never been taken."""
        await self._db.write(_give_back, use_id)


def read_user_limits(db: sqlite3.Connection, user_id: str) -> UserLimits | None:
    """The user's own limits, read through `db`; None if an admin set none."""
    row = db.execute(
        "SELECT turns_per_hour, calls_per_hour, photos_per_hour, note, updated_at"
        " FROM user_limits WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    turns, calls, photos, note, updated_at = row
    return UserLimits(
        _from_row(turns), _from_row(calls), note, updated_at, photos_per_hour=_from_row(photos)
    )


def _open(db: sqlite3.Connection) -> None:
    """Make the tables, and bring those of an older version up to date."""
    # What is given back or forgotten is overwritten, not just freed (see `database`).
    db.executescript(_SCHEMA)
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(usage)")}
    if "exempt" not in columns:
        db.execute("ALTER TABLE usage ADD COLUMN exempt INTEGER NOT NULL DEFAULT 0")
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(user_limits)")}
    # Whether an admin let the user use the deployer's LLM where users had to bring their
    # own, which no longer is a rule.
    if "deployer_llm_allowed" in columns:
        db.execute("ALTER TABLE user_limits DROP COLUMN deployer_llm_allowed")
    if "photos_per_hour" not in columns:
        db.execute("ALTER TABLE user_limits ADD COLUMN photos_per_hour INTEGER")


def _take(
    db: sqlite3.Connection,
    user_id: str,
    kind: UsageKind,
    exempt: bool,
    now: datetime,
    limits: UsageLimits,
) -> int:
    """Take one `kind` for the user `now`, under `limits` unless `exempt`: its id."""
    if not exempt:
        _refuse_over(db, user_id, kind, now, limits)
    cursor = db.execute(
        "INSERT INTO usage (user_id, kind, at, exempt) VALUES (?, ?, ?, ?)",
        (user_id, kind, now.timestamp(), int(exempt)),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _refuse_over(
    db: sqlite3.Connection, user_id: str, kind: UsageKind, now: datetime, limits: UsageLimits
) -> None:
    """Raise OverLimit if the last hour already holds as many of the user's counting
    `kind` as their limit: their own, else the deployment's in `limits`."""
    deployment = {
        "turn": limits.turns_per_hour,
        "call": limits.calls_per_hour,
        "photo": limits.photos_per_hour,
    }[kind]
    own = _own_limit(db, user_id, kind)
    limit = deployment if own is None else None if own == "unlimited" else own
    if limit == 0:
        raise OverLimit(kind, 0, None, now)
    if limit is None:
        return
    recent = [
        at
        for (at,) in db.execute(
            "SELECT at FROM usage WHERE user_id = ? AND kind = ? AND NOT exempt"
            " AND at > ? ORDER BY at",
            (user_id, kind, (now - WINDOW).timestamp()),
        )
    ]
    if len(recent) >= limit:
        blocking = recent[len(recent) - limit]  # this one must age out first
        retry_at = datetime.fromtimestamp(blocking, UTC) + WINDOW
        raise OverLimit(kind, limit, retry_at, now)


def _own_limit(db: sqlite3.Connection, user_id: str, kind: UsageKind) -> UserLimit:
    row = db.execute(
        f"SELECT {_COLUMNS[kind]} FROM user_limits WHERE user_id = ?", (user_id,)
    ).fetchone()
    return _from_row(row[0]) if row else None


def _set_user_limits(
    db: sqlite3.Connection, user_id: str, limits: UserLimits | None, now: datetime
) -> None:
    if limits is None:
        db.execute("DELETE FROM user_limits WHERE user_id = ?", (user_id,))
        return
    db.execute(
        "INSERT OR REPLACE INTO user_limits"
        " (user_id, turns_per_hour, calls_per_hour, photos_per_hour, note, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, _to_row(limits.turns_per_hour), _to_row(limits.calls_per_hour),
         _to_row(limits.photos_per_hour), limits.note, now.isoformat()),
    )


def _last_day(
    db: sqlite3.Connection, user_id: str, after: float
) -> list[tuple[UsageKind, float, int]]:
    return db.execute(
        "SELECT kind, at, exempt FROM usage WHERE user_id = ? AND at > ? ORDER BY at",
        (user_id, after),
    ).fetchall()


def _give_back(db: sqlite3.Connection, use_id: int) -> None:
    db.execute("DELETE FROM usage WHERE id = ?", (use_id,))


def _forget_before(db: sqlite3.Connection, at: float, batch: int) -> int:
    """Delete `batch` at most of everyone's uses up to `at`: how many it deleted."""
    return db.execute(
        "DELETE FROM usage WHERE id IN (SELECT id FROM usage WHERE at <= ? LIMIT ?)",
        (at, batch),
    ).rowcount


def _to_row(limit: UserLimit) -> int | None:
    return _UNLIMITED_ROW if limit == "unlimited" else limit


def _from_row(value: int | None) -> UserLimit:
    return "unlimited" if value == _UNLIMITED_ROW else value


def _tally(uses: list[tuple[UsageKind, bool]]) -> Tally:
    return Tally(
        turns=sum(kind == "turn" for kind, _ in uses),
        calls=sum(kind == "call" for kind, _ in uses),
        exempt_turns=sum(kind == "turn" and exempt for kind, exempt in uses),
        exempt_calls=sum(kind == "call" and exempt for kind, exempt in uses),
    )
