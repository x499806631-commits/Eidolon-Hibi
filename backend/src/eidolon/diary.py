"""Her diary (GLOSSARY.md「日记」): what an idol writes down of a day that mattered, and how
much of it the producer may read.

Only days that mattered are written of. What makes one is a moment (`Moment`), noted as it
happens, in the producer's own day (the profile's time zone): her affinity with the user first
reaching close or intimate; a gift she liked; a promise kept, or broken (`promises`); a day
they have known each other a hundred days or a whole year (`anniversaries`), her birthday or
the producer's, as they talk on it; and what the background model, writing memories, finds
mattered to her in a turn (`Memories.on_moment`). Noting one is a row, nothing more.

Once a day with moments is over, every `TICK` looks for those still unwritten and has her
write one entry for each, on the deployer's chat model as letters are
(`ConversationEngine.write_diary`), each with an activity record of its own (`diary`); at
most `PER_TICK` at a tick. A day is written of once: one the model fails on is tried again
`RETRY_AFTER` later, `TRIES` times in all, and one more than `KEEP_TRYING` past is let go.
Only where the deployer turns it on (`Settings.diary`).

How much of an entry the producer reads goes by her tier as they read it, not as she wrote it
(`Entry.shown`): cold or distant, or in a cooldown, the diary is closed, but for the day; in
trust, its opening; close, all of it, and that there is a page she did not mean them to see if
there is; intimate, that page too. Where affinity is off it is all but that page. What is not
shown never leaves the server.
"""

import asyncio
import json
import logging
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from eidolon import anniversaries
from eidolon.activity import ActivityLog
from eidolon.affinity import Affinities, Change, Status, Tier
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.features import AllOn, Switches
from eidolon.engine import ConversationEngine
from eidolon.live_config import LiveConfig
from eidolon.producer import effective_timezone, is_birthday
from eidolon.reply_format import Segment
from eidolon.special_days import SpecialDays
from eidolon.store import Conversation, Message, NotFound, Store

log = logging.getLogger(__name__)

TICK = timedelta(minutes=10)
"""How often it is looked whose diary is to be written."""

KEEP_TRYING = 3
"""How many days after a day she may still write of it."""

TRIES = 2
"""How many times an entry is tried before the day is let go."""

RETRY_AFTER = timedelta(hours=1)
"""How long after a failed try the next is made."""

PER_TICK = 3
"""How many entries are written at a tick at most."""

SECRET_MARK = "※"
"""What a paragraph she did not mean the producer to see opens with, in both languages."""

MomentKind = Literal[
    "closer", "scene", "gift", "promise_kept", "promise_broken", "anniversary",
    "her_birthday", "producer_birthday", "chat",
]

Shown = Literal["closed", "opening", "full", "secret"]
"""How much of an entry the producer reads: none of it but the day; its opening; all of it but
the page she did not mean them to see; all of it."""

_TIER_NAMES = {"close": "亲近", "intimate": "亲密"}

_SCHEMA = """
-- What made a day of the producer's matter to her (`Moment`): `what` is said as she is told it.
CREATE TABLE IF NOT EXISTS diary_moments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    what TEXT NOT NULL,
    at TEXT NOT NULL,
    UNIQUE (user_id, character_id, day, kind, what)
);
CREATE INDEX IF NOT EXISTS diary_moments_by_day ON diary_moments(day);
-- Her entry for a day: `segments` and `secret` (the page she did not mean the producer to
-- see, '[]' for none) as the model wrote them, `read_at` once the producer opened it.
CREATE TABLE IF NOT EXISTS diary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    segments TEXT NOT NULL,
    secret TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (user_id, character_id, day)
);
-- How many times an entry for a day has been tried, and when last.
CREATE TABLE IF NOT EXISTS diary_tries (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    tries INTEGER NOT NULL,
    last_at TEXT NOT NULL,
    PRIMARY KEY (user_id, character_id, day)
);
"""


class NothingToUnlock(Exception):
    """All of the entry is shown already: a diary key would show nothing more."""

    def __init__(self) -> None:
        super().__init__("这一页已经能全部看到了")


@dataclass(frozen=True)
class Entry:
    """One day of her diary, as written: the paragraphs, and the page she did not mean the
    producer to see (none if empty)."""

    id: int
    character_id: str
    day: date
    segments: tuple[Segment, ...]
    secret: tuple[Segment, ...]
    created_at: str
    read_at: str | None
    unlocked: bool = False
    """Opened with a diary key (GLOSSARY.md「日记钥匙」): all of it shown, whatever her tier."""

    def shown(self, status: Status | None) -> dict:
        """As the producer reads it now, `status` her affinity with them (None while affinity
        is off): what of it they may read, and only that; and whether a diary key would show
        them more (`unlockable`)."""
        how: Shown = "secret" if self.unlocked else shown_by(status)
        segments: tuple[Segment, ...] = ()
        if how == "opening":
            segments = self.segments[:1]
        elif how in ("full", "secret"):
            segments = self.segments
        return {
            "id": self.id,
            "character_id": self.character_id,
            "day": self.day.isoformat(),
            "shown": how,
            "segments": [{"ja": s.ja, "zh": s.zh} for s in segments],
            "more": len(segments) < len(self.segments),
            "secret": [{"ja": s.ja, "zh": s.zh} for s in self.secret] if how == "secret" else [],
            "secret_hidden": how == "full" and status is not None and bool(self.secret),
            "unlockable": how in ("closed", "opening") or (how == "full" and bool(self.secret)),
            "read": self.read_at is not None,
        }


def shown_by(status: Status | None) -> Shown:
    """How much of her diary the producer reads with her affinity `status` (None while
    affinity is off)."""
    if status is None:
        return "full"
    if status.cooling or status.tier in ("cold", "distant"):
        return "closed"
    if status.tier == "trust":
        return "opening"
    return "secret" if status.tier == "intimate" else "full"


def split_secret(segments: Sequence[Segment]) -> tuple[list[Segment], list[Segment]]:
    """Her entry as the model wrote it, apart from the page she did not mean the producer to
    see: the paragraphs that open with `SECRET_MARK`, the mark taken off."""
    kept: list[Segment] = []
    secret: list[Segment] = []
    for segment in segments:
        if segment.ja.lstrip().startswith(SECRET_MARK):
            secret.append(Segment(
                segment.ja.lstrip().removeprefix(SECRET_MARK).lstrip(),
                segment.zh.lstrip().removeprefix(SECRET_MARK).lstrip(),
            ))
        else:
            kept.append(segment)
    return kept, secret


class Diary:
    """Her diary, on the database at `path` beside the conversations of `store`, written on the
    deployer's models of `config` as `engine` writes it, each entry recorded in `activity`;
    `special_days` has the birthdays that make a day matter."""

    def __init__(
        self,
        path: Path,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        affinities: Affinities,
        activity: ActivityLog,
        clock: Clock,
        enabled: bool,
        special_days: SpecialDays | None = None,
        features: Switches = AllOn(),
    ) -> None:
        self._features = features
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._affinities = affinities
        self._activity = activity
        self._clock = clock
        self._enabled = enabled
        self._special_days = special_days
        self._db = connect(path)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(diary_entries)")}
            if "unlocked" not in columns:
                self._db.execute(
                    "ALTER TABLE diary_entries ADD COLUMN unlocked INTEGER NOT NULL DEFAULT 0"
                )

    # What makes a day matter.

    def note(
        self, user_id: str, character_id: str, kind: MomentKind, what: str, at: datetime
    ) -> None:
        """Note a moment of the producer's day `at` falls on, once."""
        if not self._enabled or character_id not in self._characters:
            return
        day = at.astimezone(self._zone(user_id)).date()
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO diary_moments"
                " (user_id, character_id, day, kind, what, at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, character_id, day.isoformat(), kind, what[:200], at.isoformat()),
            )

    def turn_recorded(
        self, conversation: Conversation, reply: Message, at: datetime, timezone: str
    ) -> None:
        """A turn with her was recorded `at`: her affinity first reaching close or intimate,
        a day they have known each other a hundred days or a whole year, her birthday or the
        producer's, are moments."""
        if not self._enabled:
            return
        user_id, character_id = conversation.user_id, conversation.character_id
        change = reply.affinity
        if change is not None:
            self.affinity_changed(user_id, character_id, change, at)
        day = at.astimezone(ZoneInfo(timezone)).date()
        first = self._store.first_records(user_id).get(character_id)
        found = None if first is None else anniversaries.of(
            anniversaries.day_of(first, timezone), day
        )
        if found is not None:
            said = (
                f"今天是和制作人认识的第 {found.days} 天" if found.days % anniversaries.HUNDRED == 0
                else f"今天是和制作人认识的第 {found.years} 周年"
            )
            self.note(user_id, character_id, "anniversary", said, at)
        days = self._special_days
        if days is not None and character_id in days.birthdays_on(day):
            self.note(user_id, character_id, "her_birthday", "今天是自己的生日，和制作人说了话", at)
        birthday = self._store.get_profile(user_id).birthday
        if birthday is not None and is_birthday(birthday, day):
            self.note(user_id, character_id, "producer_birthday", "今天是制作人的生日", at)

    def affinity_changed(
        self, user_id: str, character_id: str, change: Change, at: datetime
    ) -> None:
        """Her affinity changed `at`: first reaching close or intimate is a moment, the first
        time only."""
        if not (change.up and change.tier_changed and change.tier in _TIER_NAMES):
            return
        what = f"和制作人的关系第一次变成了「{_TIER_NAMES[change.tier]}」"
        with self._lock:
            before = self._db.execute(
                "SELECT 1 FROM diary_moments WHERE user_id = ? AND character_id = ?"
                " AND ((kind = 'closer' AND what = ?) OR (kind = 'scene' AND what = ?))",
                (user_id, character_id, what, _scene_what(change.tier)),
            ).fetchone()
        if before is None:
            self.note(user_id, character_id, "closer", what, at)

    def scene_played(self, user_id: str, character_id: str, tier: Tier, at: datetime) -> None:
        """Her scene at the door of `tier` was played through `at` (GLOSSARY.md「重要的一幕」):
        the moment of the day, which her affinity reaching `tier` with it needs no other."""
        if tier in _TIER_NAMES:
            self.note(user_id, character_id, "scene", _scene_what(tier), at)

    def gift_given(
        self, user_id: str, character_id: str, gift_name: str, liked: bool,
        change: Change | None, at: datetime,
    ) -> None:
        """The producer gave her a gift `at`: one she likes is a moment, and so is her affinity
        first reaching close or intimate with it."""
        if liked:
            self.note(user_id, character_id, "gift", f"制作人送了自己喜欢的{gift_name}", at)
        if change is not None:
            self.affinity_changed(user_id, character_id, change, at)

    def promise_kept(self, user_id: str, character_id: str, text: str, at: datetime) -> None:
        self.note(user_id, character_id, "promise_kept", f"制作人做到了答应过的事：{text}", at)

    def promise_broken(self, user_id: str, character_id: str, text: str, at: datetime) -> None:
        self.note(user_id, character_id, "promise_broken", f"制作人答应过的事没有做到：{text}", at)

    def moment(self, user_id: str, character_id: str, what: str, at: datetime) -> None:
        """What the background model found mattered to her in a turn (`Memories.on_moment`)."""
        self.note(user_id, character_id, "chat", what, at)

    # Writing it.

    async def keep_writing(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("writing diaries failed")

    async def run_due(self) -> None:
        """Write the entries of the days over and unwritten, oldest first, `PER_TICK` at most;
        one going wrong keeps none of the others from it."""
        if not self._enabled:
            return
        now = self._clock()
        due = await asyncio.to_thread(self._due, now)
        for user_id, character_id, day in due[:PER_TICK]:
            try:
                await self._write(user_id, character_id, day, now)
            except Exception:
                log.exception("writing a diary of %s failed", day)

    async def write_now(self, user_id: str, character_id: str, what: str) -> Entry | None:
        """Note `what` as a moment of today and have her write today's entry now, whatever the
        hour: for trying the diary out under the development sign-in. None if they have no
        conversation; raises what the model raises."""
        now = self._clock()
        self.note(user_id, character_id, "chat", what, now)
        day = now.astimezone(self._zone(user_id)).date()
        return await self._write(user_id, character_id, day, now, raises=True)

    def entries(self, user_id: str) -> list[dict]:
        """The user's diary, every character's, newest day first, as they may read it now."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM diary_entries WHERE user_id = ? ORDER BY day DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [self._shown(user_id, _entry(row)) for row in rows]

    def unread(self, user_id: str) -> int:
        """How many entries the user may read and has not opened."""
        with self._lock:
            rows = self._db.execute(
                "SELECT character_id FROM diary_entries WHERE user_id = ? AND read_at IS NULL",
                (user_id,),
            ).fetchall()
        return sum(
            shown_by(self._affinities.status(user_id, row[0])) != "closed" for row in rows
        )

    def read(self, user_id: str, entry_id: int) -> dict:
        """Open an entry: read, if the user may read it now. Raises NotFound if it is not
        theirs."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM diary_entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
            ).fetchone()
            if row is None:
                raise NotFound(entry_id)
            entry = _entry(row)
            if shown_by(self._affinities.status(user_id, entry.character_id)) != "closed":
                self._db.execute(
                    "UPDATE diary_entries SET read_at = COALESCE(read_at, ?) WHERE id = ?",
                    (self._clock().isoformat(), entry_id),
                )
                row = self._db.execute(
                    "SELECT * FROM diary_entries WHERE id = ?", (entry_id,)
                ).fetchone()
        return self._shown(user_id, _entry(row))

    def unlock(self, user_id: str, entry_id: int) -> dict:
        """Open an entry with a diary key (GLOSSARY.md「日记钥匙」): all of it shown from now
        on, read. Raises NotFound if it is not theirs, NothingToUnlock if all of it is shown
        already."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM diary_entries WHERE id = ? AND user_id = ?", (entry_id, user_id)
            ).fetchone()
            if row is None:
                raise NotFound(entry_id)
            entry = _entry(row)
            if not entry.shown(self._affinities.status(user_id, entry.character_id))["unlockable"]:
                raise NothingToUnlock()
            self._db.execute(
                "UPDATE diary_entries SET unlocked = 1, read_at = COALESCE(read_at, ?)"
                " WHERE id = ?",
                (self._clock().isoformat(), entry_id),
            )
            row = self._db.execute("SELECT * FROM diary_entries WHERE id = ?", (entry_id,)).fetchone()
        return self._shown(user_id, _entry(row))

    def all_of(self, user_id: str) -> list[Entry]:
        """Every character's entries, whole, the page she did not mean them to see among them:
        for a data package, which is the user's own to keep."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM diary_entries WHERE user_id = ? ORDER BY character_id, day",
                (user_id,),
            ).fetchall()
        return [_entry(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _shown(self, user_id: str, entry: Entry) -> dict:
        return entry.shown(self._affinities.status(user_id, entry.character_id))

    def _zone(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(effective_timezone(self._store.get_profile(user_id), None))

    def _due(self, now: datetime) -> list[tuple[str, str, date]]:
        """The days with moments that are over in the producer's time zone, not yet written
        of, not let go, and not tried too lately: oldest first."""
        since = (now.date() - timedelta(days=KEEP_TRYING + 1)).isoformat()
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT m.user_id, m.character_id, m.day,"
                " t.tries, t.last_at FROM diary_moments m"
                " LEFT JOIN diary_tries t ON t.user_id = m.user_id"
                "  AND t.character_id = m.character_id AND t.day = m.day"
                " WHERE m.day >= ? AND NOT EXISTS (SELECT 1 FROM diary_entries e"
                "  WHERE e.user_id = m.user_id AND e.character_id = m.character_id"
                "  AND e.day = m.day)"
                " ORDER BY m.day, m.user_id, m.character_id",
                (since,),
            ).fetchall()
        due = []
        for user_id, character_id, day_text, tries, last_at in rows:
            if character_id not in self._characters:
                continue
            if not self._features.on(user_id, "diary"):  # GLOSSARY.md「玩法开关」
                continue
            day = date.fromisoformat(day_text)
            today = now.astimezone(self._zone(user_id)).date()
            if not (today - timedelta(days=KEEP_TRYING) <= day < today):
                continue
            if tries is not None and (
                tries >= TRIES or now - datetime.fromisoformat(last_at) < RETRY_AFTER
            ):
                continue
            due.append((user_id, character_id, day))
        return due

    def _moments(self, user_id: str, character_id: str, day: date) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT what FROM diary_moments WHERE user_id = ? AND character_id = ? AND day = ?"
                " ORDER BY id",
                (user_id, character_id, day.isoformat()),
            ).fetchall()
        return [row[0] for row in rows]

    def _tried(self, user_id: str, character_id: str, day: date, now: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO diary_tries (user_id, character_id, day, tries, last_at)"
                " VALUES (?, ?, ?, 1, ?) ON CONFLICT (user_id, character_id, day)"
                " DO UPDATE SET tries = tries + 1, last_at = excluded.last_at",
                (user_id, character_id, day.isoformat(), now.isoformat()),
            )

    def _keep(
        self, user_id: str, character_id: str, day: date, said: Sequence[Segment], now: datetime
    ) -> Entry:
        kept, secret = split_secret(said)
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO diary_entries"
                " (user_id, character_id, day, segments, secret, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id, character_id, day.isoformat(), _stored(kept), _stored(secret),
                    now.isoformat(),
                ),
            )
            row = self._db.execute(
                "SELECT * FROM diary_entries WHERE user_id = ? AND character_id = ? AND day = ?",
                (user_id, character_id, day.isoformat()),
            ).fetchone()
        return _entry(row)

    async def _write(
        self, user_id: str, character_id: str, day: date, now: datetime, raises: bool = False
    ) -> Entry | None:
        """Have her write of `day`, in her newest conversation with the user: the entry kept,
        or None if they have none, or the model failed (tried again later, unless `raises`)."""
        newest = [
            c for c, _, _ in await asyncio.to_thread(self._store.latest_conversations, user_id)
            if c.character_id == character_id
        ]
        if not newest:
            return None
        conversation = newest[0]
        moments = await asyncio.to_thread(self._moments, user_id, character_id, day)
        zone = self._zone(user_id)
        evening = min(datetime.combine(day, time(23, 30), zone), now)
        recording = self._activity.begin(
            "diary", user_id, character_id, own_llm=False, exempt=False
        )
        lease = self._config.lease()
        try:
            with recording.current():
                said = await self._engine.write_diary(
                    conversation, lease.keys.chat_llm, moments, evening
                )
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("writing a diary in conversation %s failed: %s", conversation.id,
                        type(e).__name__)
            recording.finish("failed")
            await asyncio.to_thread(self._tried, user_id, character_id, day, now)
            if raises:
                raise
            return None
        finally:
            lease.release()
        entry = await asyncio.to_thread(self._keep, user_id, character_id, day, said, now)
        recording.finish("success")
        return entry


def carry(
    db: sqlite3.Connection,
    user_id: str,
    character_id: str,
    day: date,
    segments: Sequence[Segment],
    secret: Sequence[Segment],
    read: bool,
    now: datetime,
) -> bool:
    """Keep an entry a data package carried, through `db`, the import's own connection: one
    for a day she has an entry of already is left as it is. Whether it was kept."""
    return db.execute(
        "INSERT OR IGNORE INTO diary_entries"
        " (user_id, character_id, day, segments, secret, created_at, read_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            user_id, character_id, day.isoformat(), _stored(segments), _stored(secret),
            now.isoformat(), now.isoformat() if read else None,
        ),
    ).rowcount > 0


def _scene_what(tier: Tier) -> str:
    return f"终于对制作人说出了一直藏在心里的话，两个人的关系变成了「{_TIER_NAMES.get(tier, tier)}」"


def _stored(segments: Sequence[Segment]) -> str:
    return json.dumps([{"ja": s.ja, "zh": s.zh} for s in segments], ensure_ascii=False)


def _segments(text: str) -> tuple[Segment, ...]:
    return tuple(Segment(s["ja"], s["zh"]) for s in json.loads(text))


def _entry(row: tuple) -> Entry:
    id_, _user, character_id, day, segments, secret, created_at, read_at = row[:8]
    return Entry(
        id_, character_id, date.fromisoformat(day), _segments(segments), _segments(secret),
        created_at, read_at, bool(row[8]) if len(row) > 8 else False,
    )
