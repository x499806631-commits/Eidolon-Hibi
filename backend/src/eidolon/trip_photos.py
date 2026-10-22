"""Trip photos (GLOSSARY.md「外景照片」): the photo she sends the producer from a trip away
from Tokyo her schedule has her on (GLOSSARY.md「行程」), without being asked, with a few words.

Only where the deployer has schedules on (`Settings.schedules`) and image generation, and the
user has not switched them off (GLOSSARY.md「玩法开关」, off with her schedule too); for one
who has her photo tags. Every `TICK`, each user's current conversation with each character
is looked at, if it has a turn, she has not ended it, and they were last in touch in it
within `WINDOW`: a day her schedule has her on a trip, a time is drawn for it once, between
`FROM` and `UNTIL` of the producer's day, by five minutes. Once it has come she sends one,
one trip one photo: not while she is cold or distant to the user, or in a cooldown, nor while
a text turn is under way or a call, when it is looked at again at the next tick; one the
model fails on is tried again `RETRY_AFTER` later, `TRIES` times in all, then let go.

She writes it as a reach-out (`ConversationEngine.send_trip_photo`), on the deployer's chat
model, with a photo tag as a turn's photo has; with others of her unit on the trip with her
(`Schedule.trip_with`), she may take one of them all (`group: yes`), drawn with each one a
character prompt of her own (`photo_prompt.group_prompt`), those of them with their photo tags.
It is kept as a reach-out with a photo (`Store.add_photo_reach_out`), being generated, then
generated on the deployer's image generation, counted towards no limit of the user's, and
kept sent or not as a reply's photo is. Each has an activity record of its own (`trip_photo`).
"""

import asyncio
import logging
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog, image_origin, timed
from eidolon.affinity import Affinities
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.engine import ConversationEngine
from eidolon.features import AllOn, Switches
from eidolon.image_generation import TIME_LIMIT, ImageFailed
from eidolon.live_config import LiveConfig
from eidolon.photo_prompt import group_prompt, image_prompt, read_tag
from eidolon.producer import effective_timezone
from eidolon.randomness import Random
from eidolon.schedules import Schedules
from eidolon.store import Conversation, NewPhoto, PhotoFailure, Store

log = logging.getLogger(__name__)

TICK = timedelta(minutes=5)
WINDOW = timedelta(days=14)
"""How long after they were last in touch she still sends one."""
FROM = time(11)
UNTIL = time(17)
"""Between when in the producer's day she sends one."""
TRIES = 3
RETRY_AFTER = timedelta(hours=1)
NOT_NOW = frozenset({"cold", "distant"})
"""The tiers she sends none in."""
_GROUP = re.compile(r"^\s*group\s*:\s*yes\b", re.IGNORECASE | re.MULTILINE)

_SCHEMA = """
-- When she sends the producer a photo from her trip on a day (GLOSSARY.md「外景照片」), once
-- a day: when, by the producer's day; whether she has (`sent`); how often it was tried.
CREATE TABLE IF NOT EXISTS trip_photo_due (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    due_at TEXT NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    PRIMARY KEY (user_id, character_id, day)
);
"""


@dataclass(frozen=True)
class _Due:
    user_id: str
    character_id: str
    day: date


class TripPhotos:
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
        schedules: Schedules,
        affinities: Affinities,
        in_call: Callable[[str], bool] = lambda _: False,
        features: Switches = AllOn(),
    ) -> None:
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._activity = activity
        self._clock = clock
        self._random = random
        self._enabled = enabled
        self._schedules = schedules
        self._affinities = affinities
        self._in_call = in_call
        self._features = features
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def available(self) -> bool:
        """Whether the deployer has them: schedules, and image generation now."""
        return self._enabled and self._config.image_generator() is not None

    def _on(self, user_id: str, character: Character) -> bool:
        return (
            self.available() and self._features.on(user_id, "trip_photos")
            and character.photo_tag is not None and character.photo_appearance is not None
        )

    async def keep_sending(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("sending trip photos failed")

    async def run_due(self) -> None:
        """Plan the day's, and send those whose time has come; one going wrong keeps none of
        the others from it."""
        if not self.available():
            return
        now = self._clock()
        for conversation, day in await asyncio.to_thread(self._plan, now):
            try:
                await self._send(conversation, day, now)
            except Exception:
                log.exception("sending a trip photo in conversation %s failed", conversation.id)

    async def send_now(self, user_id: str, character_id: str, place: str = "京都") -> bool:
        """Have her send the user a photo from a trip to `place` now, whatever her schedule
        says: for trying it out under the development sign-in. False if they have no
        conversation, or image generation is off."""
        conversation = self._current(user_id, character_id)
        if conversation is None or not self.available():
            return False
        await self._send(conversation, None, self._clock(), place)
        return True

    def _plan(self, now: datetime) -> list[tuple[Conversation, date]]:
        """Draw a time for each day's trip not drawn for yet; those whose time has come."""
        due: list[tuple[Conversation, date]] = []
        for current in self._store.current_conversations():
            conversation, contact = current.conversation, current.last_contact
            character = self._characters.get(conversation.character_id)
            if character is None or current.ended or not current.has_turns:
                continue
            if contact is None or now - contact > WINDOW or not self._on(conversation.user_id, character):
                continue
            zone = self._zone(conversation.user_id)
            day = now.astimezone(zone).date()
            schedule = self._schedules.of(conversation.user_id, character.id, day)
            if schedule is None or schedule.trip is None:
                continue
            row = self._row(conversation.user_id, character.id, day)
            if row is None:
                minutes = (UNTIL.hour - FROM.hour) * 60
                at = datetime.combine(day, FROM, zone) + timedelta(
                    minutes=5 * self._random(minutes // 5)
                )
                with self._lock, self._db:
                    self._db.execute(
                        "INSERT OR IGNORE INTO trip_photo_due (user_id, character_id, day, due_at)"
                        " VALUES (?, ?, ?, ?)",
                        (conversation.user_id, character.id, day.isoformat(),
                         at.astimezone(UTC).isoformat()),
                    )
                row = self._row(conversation.user_id, character.id, day)
                assert row is not None
            if row["sent"] or row["tries"] >= TRIES:
                continue
            if datetime.fromisoformat(row["due_at"]) > now:
                continue
            if row["last_at"] and now - datetime.fromisoformat(row["last_at"]) < RETRY_AFTER:
                continue
            due.append((conversation, day))
        return due

    async def _send(
        self, conversation: Conversation, day: date | None, now: datetime, place: str | None = None
    ) -> None:
        user_id, character_id = conversation.user_id, conversation.character_id
        character = self._characters[character_id]
        status = self._affinities.status(user_id, character_id)
        if status is not None and (status.tier in NOT_NOW or status.cooling):
            return
        if self._store.is_taking_turn(conversation.id) or self._in_call(conversation.id):
            return
        trip_with: tuple[str, ...] = ()
        if day is not None:
            schedule = self._schedules.of(user_id, character_id, day)
            if schedule is None or schedule.trip is None:
                return
            place, trip_with = schedule.trip, schedule.trip_with
        assert place is not None
        others = [
            self._characters[c] for c in trip_with
            if c in self._characters and self._characters[c].photo_tag is not None
            and self._characters[c].photo_appearance is not None
        ]
        generator = self._config.image_generator()
        if generator is None:
            return
        recording = self._activity.begin("trip_photo", user_id, character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                said, tag = await self._engine.send_trip_photo(
                    conversation, lease.keys.chat_llm, place, [c.name for c in others]
                )
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("a trip photo in conversation %s failed: %s", conversation.id, type(e).__name__)
            recording.finish("failed")
            if day is not None:
                self._tried(user_id, character_id, day, now)
            return
        finally:
            lease.release()
        photo_id = uuid.uuid4().hex
        reach_out = self._store.add_photo_reach_out(conversation.id, said, NewPhoto(photo_id, tag))
        if day is not None:
            self._sent(user_id, character_id, day)
        if reach_out is None:
            recording.finish("failed")
            return
        settings = generator.settings
        read = read_tag(tag)
        together = others if _GROUP.search(tag) else []
        prompt = (
            group_prompt([character, *together], read, settings.style) if together
            else image_prompt(character, read, settings.style)
        )
        image: bytes | None = None
        reason: PhotoFailure | None = None
        until = asyncio.get_running_loop().time() + TIME_LIMIT
        try:
            with recording.current():
                image = (await timed(
                    self._activity, image_origin(settings), generator.generate(prompt, until),
                    read.stripped,
                )).webp
        except ImageFailed as e:
            log.warning("trip photo %s was not generated: %s", photo_id, e)
            reason = e.reason
        except Exception:
            log.exception("generating trip photo %s failed", photo_id)
            reason = "service_error"
        self._store.finish_photo(photo_id, image, reason)
        recording.finish("success" if image is not None else "failed")

    def _current(self, user_id: str, character_id: str) -> Conversation | None:
        return next(
            (c for c, _, _ in self._store.latest_conversations(user_id) if c.character_id == character_id),
            None,
        )

    def _row(self, user_id: str, character_id: str, day: date) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM trip_photo_due WHERE user_id = ? AND character_id = ? AND day = ?",
                (user_id, character_id, day.isoformat()),
            ).fetchone()

    def _sent(self, user_id: str, character_id: str, day: date) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE trip_photo_due SET sent = 1 WHERE user_id = ? AND character_id = ? AND day = ?",
                (user_id, character_id, day.isoformat()),
            )

    def _tried(self, user_id: str, character_id: str, day: date, now: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE trip_photo_due SET tries = tries + 1, last_at = ?"
                " WHERE user_id = ? AND character_id = ? AND day = ?",
                (now.astimezone(UTC).isoformat(), user_id, character_id, day.isoformat()),
            )

    def _zone(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(effective_timezone(self._store.get_profile(user_id), None))

    def close(self) -> None:
        with self._lock:
            self._db.close()
