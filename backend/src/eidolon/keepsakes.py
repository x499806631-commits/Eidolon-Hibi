"""Keepsakes (GLOSSARY.md「纪念品」): the things she gives the producer, and the cabinet they are
kept in.

Only where the deployer turns them on (`Settings.keepsakes`) and the user has not switched
them off (GLOSSARY.md「玩法开关」). She gives one:

- after her scene at a door is played through (`scene_played`);
- on an anniversary of theirs, the hundredth day or a whole year, from `MORNING` of it;
- on the producer's birthday, with her birthday letter where letters come (`letter_written`),
  else from `MORNING` of it in the conversation;
- now and then for a gift she liked (`gift_given`, one in `GIFT_ODDS`), the next day;
- now and then for a promise kept (`promise_settled`, one in `PROMISE_ODDS`), soon after;
- the day after a trip by her schedule (GLOSSARY.md「行程」), something from where she went;
- now and then with a holiday's letter (one in `HOLIDAY_ODDS`).

Each is put down as due (`keepsake_due`), once for what it is for, and given at a tick once
its time has come: `PER_TICK` at a tick, `PER_DAY` a day to a user at most, the rest put off to
the next day, but for her scene's and a birthday's. She chooses it and says what she says
giving it on the deployer's chat model (`ConversationEngine.write_keepsake`), each with an
activity record of its own (`keepsake`); one the model fails on is tried again `RETRY_AFTER`
later, `TRIES` times in all, then let go. Its picture is drawn on the deployer's image
generation, square, the thing alone (`photo_prompt.keepsake_prompt`), where there is one; where
there is none, or it fails, it is kept without, an emoji standing for it, and the tags written
for it kept, for its picture to be drawn later (`fill_images`), all the user's missing ones in
turn, one at a time.

A keepsake given in a conversation is an event of it (`Store.give_keepsake`), her current one
with the producer, which she is told of; one with a letter comes with it, and is in the cabinet
from when the letter comes. All kept with the user, deleted with them; a data package does not
carry them.
"""

import asyncio
import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from eidolon import anniversaries
from eidolon.activity import ActivityLog, image_origin, timed
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.engine import ConversationEngine, KeepsakeDraft
from eidolon.features import AllOn, Switches
from eidolon.image_generation import TIME_LIMIT, ImageFailed
from eidolon.live_config import LiveConfig
from eidolon.photo_prompt import keepsake_prompt
from eidolon.producer import effective_timezone, is_birthday
from eidolon.promises import Promise
from eidolon.randomness import Random
from eidolon.reply_format import Segment
from eidolon.schedules import Schedules
from eidolon.store import Conversation, Letter, Store

log = logging.getLogger(__name__)

Occasion = Literal["scene", "anniversary", "birthday", "gift", "promise", "souvenir", "holiday"]
"""What a keepsake is for: her scene at a door, an anniversary, the producer's birthday, a gift
she liked, a promise kept, a trip she came back from, a holiday's letter."""

TICK = timedelta(minutes=5)
PER_TICK = 3
PER_DAY = 3
"""How many keepsakes a user is given a day at most, but for her scene's and a birthday's."""
TRIES = 3
RETRY_AFTER = timedelta(hours=1)
WINDOW = timedelta(days=14)
"""How long after they were last in touch she still gives one for a day of hers."""
MORNING = time(9)
"""From when in the producer's day she gives one for the day."""
GIFT_ODDS = 3
PROMISE_ODDS = 4
HOLIDAY_ODDS = 3
ANYWAY: frozenset[Occasion] = frozenset({"scene", "birthday"})
"""What she gives whatever the day's number."""

TIER_NAMES = {"close": "第一次走到了「亲近」", "intimate": "第一次走到了「亲密」"}

_SCHEMA = """
-- The keepsakes she gave the producer (GLOSSARY.md「纪念品」): what for (`occasion`, `about`),
-- what it is, the emoji standing for it, the English tags its picture is drawn from, what she
-- said giving it (JSON segments), its picture (WebP) once drawn, the letter it came with if
-- any, when it is given (a letter's, when the letter comes), and when the user first saw it.
CREATE TABLE IF NOT EXISTS keepsakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    occasion TEXT NOT NULL,
    about TEXT NOT NULL,
    name TEXT NOT NULL,
    look TEXT NOT NULL,
    emoji TEXT NOT NULL,
    tags TEXT NOT NULL,
    said TEXT NOT NULL,
    image BLOB,
    letter_id INTEGER,
    given_at TEXT NOT NULL,
    seen_at TEXT
);
CREATE INDEX IF NOT EXISTS keepsakes_by_user ON keepsakes(user_id, given_at);

-- A keepsake due: what for, from when, the letter it is to come with and when that comes, if
-- any, and how often it was tried. `key` is what it is for, once (`scene:close`, ...).
CREATE TABLE IF NOT EXISTS keepsake_due (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    key TEXT NOT NULL,
    occasion TEXT NOT NULL,
    about TEXT NOT NULL,
    due_at TEXT NOT NULL,
    letter_id INTEGER,
    given_at TEXT,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    UNIQUE (user_id, character_id, key)
);
"""


@dataclass(frozen=True)
class Keepsake:
    """A keepsake she gave, as the cabinet shows it."""

    id: int
    character_id: str
    occasion: Occasion
    about: str
    name: str
    look: str
    emoji: str
    said: list[Segment]
    has_image: bool
    letter_id: int | None
    given_at: str
    seen: bool

    def shown(self) -> dict:
        return {
            "id": self.id,
            "character_id": self.character_id,
            "occasion": self.occasion,
            "about": self.about,
            "name": self.name,
            "look": self.look,
            "emoji": self.emoji,
            "said": [{"ja": s.ja, "zh": s.zh} for s in self.said],
            "has_image": self.has_image,
            "letter_id": self.letter_id,
            "given_at": self.given_at,
            "seen": self.seen,
        }


@dataclass(frozen=True)
class _Due:
    id: int
    user_id: str
    character_id: str
    occasion: Occasion
    about: str
    letter_id: int | None
    given_at: str | None


class Keepsakes:
    def __init__(
        self,
        path: Path,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        activity: ActivityLog,
        clock: Clock,
        random: Random,
        enabled: bool,
        schedules: Schedules | None = None,
        letters_on: Callable[[str], bool] = lambda _: False,
        features: Switches = AllOn(),
    ) -> None:
        """`letters_on` says whether a user's birthday keepsake comes with a letter."""
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._activity = activity
        self._clock = clock
        self._random = random
        self._enabled = enabled
        self._schedules = schedules
        self._letters_on = letters_on
        self._features = features
        self._given: list[Callable[[str, str, str, datetime], None]] = []
        self._filling: dict[str, asyncio.Task] = {}
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def on_given(self, listener: Callable[[str, str, str, datetime], None]) -> None:
        """Have `listener` told the user, the character, the keepsake's name and when, as she
        gives one."""
        self._given.append(listener)

    def _on(self, user_id: str) -> bool:
        return self._enabled and self._features.on(user_id, "keepsakes")

    # What she gives one for.

    def scene_played(self, user_id: str, character_id: str, tier: str, at: datetime) -> None:
        self._put_down(user_id, character_id, f"scene:{tier}", "scene",
                       TIER_NAMES.get(tier, tier), at + timedelta(minutes=10))

    def gift_given(
        self, user_id: str, character_id: str, gift_name: str, liked: bool, _: object,
        at: datetime,
    ) -> None:
        if not liked or self._random(GIFT_ODDS) != 0:
            return
        zone = self._zone(user_id)
        tomorrow = at.astimezone(zone).date() + timedelta(days=1)
        due = datetime.combine(tomorrow, MORNING, zone) + timedelta(minutes=self._random(240))
        self._put_down(user_id, character_id, f"gift:{tomorrow}", "gift", gift_name, due)

    def promise_settled(self, promise: Promise, settled: str, at: datetime) -> None:
        if settled not in ("kept", "kept_late") or self._random(PROMISE_ODDS) != 0:
            return
        self._put_down(
            promise.user_id, promise.character_id, f"promise:{promise.id}", "promise",
            promise.text, at + timedelta(minutes=30 + self._random(120)),
        )

    def letter_written(self, conversation: Conversation, letter: Letter, about: str) -> None:
        """A letter of hers was written: a birthday's comes with a keepsake, a holiday's now
        and then."""
        if letter.occasion == "producer_birthday":
            occasion: Occasion = "birthday"
        elif letter.occasion == "holiday" and self._random(HOLIDAY_ODDS) == 0:
            occasion = "holiday"
        else:
            return
        self._put_down(
            conversation.user_id, conversation.character_id, f"letter:{letter.id}", occasion,
            about, self._clock(), letter.id, letter.created_at,
        )

    def _put_down(
        self, user_id: str, character_id: str, key: str, occasion: Occasion, about: str,
        due: datetime, letter_id: int | None = None, given_at: str | None = None,
    ) -> None:
        if not self._on(user_id) or character_id not in self._characters:
            return
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO keepsake_due"
                " (user_id, character_id, key, occasion, about, due_at, letter_id, given_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, character_id, key, occasion, about, due.astimezone(UTC).isoformat(),
                 letter_id, given_at),
            )

    def _plan(self, now: datetime) -> None:
        """Put down the keepsakes of the day for each current conversation: an anniversary's,
        a birthday's without a letter, a trip's the day after."""
        for current in self._store.current_conversations():
            conversation, contact = current.conversation, current.last_contact
            user_id, character_id = conversation.user_id, conversation.character_id
            if character_id not in self._characters or current.ended or not current.has_turns:
                continue
            if contact is None or now - contact > WINDOW or not self._on(user_id):
                continue
            profile = self._store.get_profile(user_id)
            timezone = effective_timezone(profile, None)
            local = now.astimezone(ZoneInfo(timezone))
            if local.time() < MORNING:
                continue
            today = local.date()
            first = self._store.first_records(user_id).get(character_id)
            found = (
                anniversaries.of(anniversaries.day_of(first, timezone), today) if first else None
            )
            if found is not None:
                about = (
                    f"你们认识满 {found.years} 年的日子" if found.years
                    else f"你们认识的第 {found.days} 天"
                )
                self._put_down(user_id, character_id, f"anniversary:{today}", "anniversary",
                               about, now)
            if (
                profile.birthday is not None and is_birthday(profile.birthday, today)
                and not self._letters_on(user_id)
            ):
                self._put_down(user_id, character_id, f"birthday:{today.year}", "birthday", "",
                               now)
            if self._schedules is not None and self._features.on(user_id, "schedules"):
                yesterday = self._schedules.of(user_id, character_id, today - timedelta(days=1))
                if yesterday is not None and yesterday.trip:
                    self._put_down(user_id, character_id, f"souvenir:{yesterday.day}",
                                   "souvenir", yesterday.trip, now)

    # Giving them.

    async def keep_giving(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("giving keepsakes failed")

    async def run_due(self) -> None:
        """Give the keepsakes due, `PER_TICK` at most; one going wrong keeps none of the others
        from it."""
        if not self._enabled:
            return
        now = self._clock()
        await asyncio.to_thread(self._plan, now)
        for due in (await asyncio.to_thread(self._due, now))[:PER_TICK]:
            try:
                await self._give(due, now)
            except Exception:
                log.exception("giving keepsake %s failed", due.id)

    async def give_now(self, user_id: str, character_id: str, occasion: Occasion, about: str) -> bool:
        """Have her give the user a keepsake for `occasion` now: for trying it out under the
        development sign-in. False if they have no conversation."""
        if not any(c.character_id == character_id for c, _, _ in self._store.latest_conversations(user_id)):
            return False
        now = self._clock()
        key = f"now:{now.isoformat()}"
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO keepsake_due (user_id, character_id, key, occasion, about, due_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, character_id, key, occasion, about, now.astimezone(UTC).isoformat()),
            )
            row = self._db.execute("SELECT * FROM keepsake_due WHERE key = ?", (key,)).fetchone()
        await self._give(_due_of(row), now, anyway=True)
        return True

    def _due(self, now: datetime) -> list[_Due]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM keepsake_due WHERE due_at <= ? AND tries < ? ORDER BY due_at",
                (now.astimezone(UTC).isoformat(), TRIES),
            ).fetchall()
        return [
            _due_of(r) for r in rows
            if r["last_at"] is None or now - datetime.fromisoformat(r["last_at"]) >= RETRY_AFTER
        ]

    async def _give(self, due: _Due, now: datetime, anyway: bool = False) -> None:
        conversation = next(
            (c for c, _, _ in self._store.latest_conversations(due.user_id)
             if c.character_id == due.character_id),
            None,
        )
        if (
            conversation is None or not self._on(due.user_id)
            or self._store.ended(conversation.id) is not None
        ):
            self._drop(due.id)
            return
        if not anyway and due.occasion not in ANYWAY and self._given_today(due.user_id, now) >= PER_DAY:
            self._put_off(due.id, now + timedelta(days=1))
            return
        recording = self._activity.begin(
            "keepsake", due.user_id, due.character_id, own_llm=False, exempt=False
        )
        lease = self._config.lease()
        try:
            with recording.current():
                draft = await self._engine.write_keepsake(
                    conversation, lease.keys.chat_llm, due.occasion, due.about,
                    self._names(due.user_id, due.character_id),
                )
                image = await self._draw(draft.tags)
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("giving keepsake %s failed: %s", due.id, type(e).__name__)
            recording.finish("failed")
            self._tried(due.id, now)
            return
        finally:
            lease.release()
        recording.finish("success")
        given_at = due.given_at or now.astimezone(UTC).isoformat()
        keepsake_id = self._keep(due, draft, image, given_at)
        if due.letter_id is None:
            self._store.give_keepsake(conversation.id, keepsake_id, draft.name)
        for listener in self._given:
            try:
                listener(due.user_id, due.character_id, draft.name, datetime.fromisoformat(given_at))
            except Exception:
                log.exception("telling of keepsake %s failed", keepsake_id)

    async def _draw(self, tags: str) -> bytes | None:
        """Its picture, on the deployer's image generation; None without one, or failing."""
        generator = self._config.image_generator()
        if generator is None:
            return None
        settings = generator.settings
        prompt, stripped = keepsake_prompt(tags, settings.style)
        until = asyncio.get_running_loop().time() + TIME_LIMIT
        try:
            image = await timed(
                self._activity, image_origin(settings), generator.generate(prompt, until), stripped
            )
        except (ImageFailed, TimeoutError) as e:
            log.warning("a keepsake's picture was not drawn: %s", e)
            return None
        return image.webp

    def _keep(self, due: _Due, draft: KeepsakeDraft, image: bytes | None, given_at: str) -> int:
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO keepsakes (user_id, character_id, occasion, about, name, look, emoji,"
                " tags, said, image, letter_id, given_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    due.user_id, due.character_id, due.occasion, due.about, draft.name,
                    draft.look, draft.emoji, draft.tags,
                    json.dumps([{"ja": s.ja, "zh": s.zh} for s in draft.said], ensure_ascii=False),
                    image, due.letter_id, given_at,
                ),
            )
            self._db.execute("DELETE FROM keepsake_due WHERE id = ?", (due.id,))
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def _drop(self, due_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM keepsake_due WHERE id = ?", (due_id,))

    def _put_off(self, due_id: int, to: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE keepsake_due SET due_at = ? WHERE id = ?",
                (to.astimezone(UTC).isoformat(), due_id),
            )

    def _tried(self, due_id: int, now: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE keepsake_due SET tries = tries + 1, last_at = ? WHERE id = ?",
                (now.isoformat(), due_id),
            )

    def _given_today(self, user_id: str, now: datetime) -> int:
        start = datetime.combine(now.astimezone(self._zone(user_id)).date(), time(), self._zone(user_id))
        with self._lock:
            [count] = self._db.execute(
                "SELECT COUNT(*) FROM keepsakes WHERE user_id = ? AND given_at >= ?",
                (user_id, start.astimezone(UTC).isoformat()),
            ).fetchone()
        return count

    def _names(self, user_id: str, character_id: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT name FROM keepsakes WHERE user_id = ? AND character_id = ?"
                " ORDER BY id DESC LIMIT 10",
                (user_id, character_id),
            ).fetchall()
        return [name for (name,) in rows]

    def _zone(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(effective_timezone(self._store.get_profile(user_id), None))

    # The cabinet.

    def of(self, user_id: str, now: datetime) -> list[Keepsake]:
        """The keepsakes she gave the user by `now`, the newest first."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_COLUMNS} FROM keepsakes WHERE user_id = ? AND given_at <= ?"
                " ORDER BY given_at DESC, id DESC",
                (user_id, now.astimezone(UTC).isoformat()),
            ).fetchall()
        return [_keepsake(r) for r in rows]

    def one(self, user_id: str, keepsake_id: int) -> Keepsake | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS} FROM keepsakes WHERE id = ? AND user_id = ?",
                (keepsake_id, user_id),
            ).fetchone()
        return _keepsake(row) if row else None

    def for_letter(self, user_id: str, letter_id: int) -> Keepsake | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLUMNS} FROM keepsakes WHERE letter_id = ? AND user_id = ?",
                (letter_id, user_id),
            ).fetchone()
        return _keepsake(row) if row else None

    def image(self, user_id: str, keepsake_id: int) -> bytes | None:
        with self._lock:
            row = self._db.execute(
                "SELECT image FROM keepsakes WHERE id = ? AND user_id = ?", (keepsake_id, user_id)
            ).fetchone()
        return row[0] if row else None

    def unseen(self, user_id: str, now: datetime) -> int:
        with self._lock:
            [count] = self._db.execute(
                "SELECT COUNT(*) FROM keepsakes WHERE user_id = ? AND given_at <= ?"
                " AND seen_at IS NULL",
                (user_id, now.astimezone(UTC).isoformat()),
            ).fetchone()
        return count

    def seen(self, user_id: str, now: datetime) -> None:
        """The user opened the cabinet: all given by `now` are seen."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE keepsakes SET seen_at = ? WHERE user_id = ? AND given_at <= ?"
                " AND seen_at IS NULL",
                (now.isoformat(), user_id, now.astimezone(UTC).isoformat()),
            )

    # Drawing the pictures missing.

    def missing(self, user_id: str) -> int:
        with self._lock:
            [count] = self._db.execute(
                "SELECT COUNT(*) FROM keepsakes WHERE user_id = ? AND image IS NULL", (user_id,)
            ).fetchone()
        return count

    def filling(self, user_id: str) -> bool:
        task = self._filling.get(user_id)
        return task is not None and not task.done()

    def fill_images(self, user_id: str) -> bool:
        """Begin drawing the pictures of all the user's keepsakes without one, one at a time,
        on the deployer's image generation; False if there is none, or it is under way."""
        if self._config.image_generator() is None or self.filling(user_id):
            return False
        self._filling[user_id] = asyncio.create_task(self._fill(user_id))
        return True

    async def _fill(self, user_id: str) -> None:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, character_id, tags FROM keepsakes WHERE user_id = ? AND image IS NULL"
                " ORDER BY id",
                (user_id,),
            ).fetchall()
        for keepsake_id, character_id, tags in rows:
            recording = self._activity.begin(
                "keepsake", user_id, character_id, own_llm=False, exempt=False
            )
            try:
                with recording.current():
                    image = await self._draw(tags)
            except Exception:
                log.exception("drawing keepsake %s failed", keepsake_id)
                image = None
            recording.finish("success" if image else "failed")
            if image is None:
                continue
            with self._lock, self._db:
                self._db.execute(
                    "UPDATE keepsakes SET image = ? WHERE id = ? AND user_id = ?",
                    (image, keepsake_id, user_id),
                )

    def close(self) -> None:
        for task in self._filling.values():
            task.cancel()
        with self._lock:
            self._db.close()


_COLUMNS = (
    "id, character_id, occasion, about, name, look, emoji, said, image IS NOT NULL AS has_image,"
    " letter_id, given_at, seen_at"
)


def _keepsake(row: Sequence) -> Keepsake:
    (id, character_id, occasion, about, name, look, emoji, said, has_image, letter_id,
     given_at, seen_at) = row
    return Keepsake(
        id, character_id, occasion, about, name, look, emoji,
        [Segment(s["ja"], s["zh"]) for s in json.loads(said)], bool(has_image), letter_id,
        given_at, seen_at is not None,
    )


def _due_of(row) -> _Due:
    return _Due(
        row["id"], row["user_id"], row["character_id"], row["occasion"], row["about"],
        row["letter_id"], row["given_at"],
    )
