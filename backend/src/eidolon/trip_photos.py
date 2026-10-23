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

The same goes for the photos the producer asks for with a ticket (GLOSSARY.md「体验券」,
`ask_photo`): in an outfit from her outfit list, drawn with its tags where they are known
(`outfit_tags`), or one the producer describes, or one she chooses; and for the days out
the producer invites her to with one (GLOSSARY.md「邀约」, `Dates`): she answers the
invitation at once, has it in her schedule that day (`schedule_writer`), and once it is over,
at a time drawn between `AFTER_DATE` and `AFTER_DATE_UNTIL` of that day, tells the producer
how it was, sending a photo from it where there is image generation, else in words alone.
"""

import asyncio
import logging
import re
import sqlite3
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog, image_origin, timed
from eidolon.affinity import Affinities
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.engine import ConversationEngine, EmptyReply
from eidolon.features import AllOn, Switches
from eidolon.image_generation import TIME_LIMIT, ImageFailed
from eidolon.live_config import LiveConfig
from eidolon.photo_prompt import ProducerLook, group_prompt, read_tag, worn
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
DATE_FROM = time(14)
DATE_UNTIL = time(17)
"""When a day out with the producer is, as her schedule has it."""
AFTER_DATE = time(17, 30)
AFTER_DATE_UNTIL = time(19)
"""Between when, the day out over, she tells the producer how it was."""
MAX_DAYS_AHEAD = 7
"""How many days ahead the producer may invite her."""
TRIES = 3
RETRY_AFTER = timedelta(hours=1)
NOT_NOW = frozenset({"cold", "distant"})
"""The tiers she sends none in."""
_WITH_YOU = re.compile(r"^\s*you\s*:\s*yes\b", re.IGNORECASE | re.MULTILINE)
DATE_NOW_TRIES = 3
"""How many times `date_now` asks her again where she writes no photo tag."""
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
-- The days out the producer invited her to with a ticket (GLOSSARY.md「邀约」): what to do,
-- when she is to tell them how it was once it is over, whether she has, how often tried.
CREATE TABLE IF NOT EXISTS dates (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    what TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    told INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    PRIMARY KEY (user_id, character_id, day)
);
"""


class NoConversation(Exception):
    """The user has no conversation with her to send it in."""

    def __init__(self) -> None:
        super().__init__("还没有和她的对话")


class NoImages(Exception):
    """There is no image generation to draw the photo on."""

    def __init__(self) -> None:
        super().__init__("没有配生图，拍不了照片")


class DateTaken(Exception):
    """She is already going out with the producer that day, or the day cannot be."""

    def __init__(self, why: str = "那天已经约好了") -> None:
        super().__init__(why)


class Dates:
    """The days out the producer invited each character to (GLOSSARY.md「邀约」), in the
    database at `path`; their schedules read them as they are written."""

    def __init__(self, db: Database) -> None:
        self._db = db
        db.prepare(_open)

    async def plan(self, user_id: str, character_id: str, day: date, what: str, due_at: datetime) -> None:
        """Keep the day out. Raises DateTaken if there is one with her that day already."""
        return await self._db.write(self._plan_body, user_id, character_id, day, what, due_at)

    def _plan_body(self, db: sqlite3.Connection, user_id: str, character_id: str, day: date, what: str, due_at: datetime) -> None:
        cursor = by_name(db).execute(
            "INSERT OR IGNORE INTO dates (user_id, character_id, day, what, due_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, character_id, day.isoformat(), what,
             due_at.astimezone(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
        if cursor.rowcount == 0:
            raise DateTaken()

    async def forget(self, user_id: str, character_id: str, day: date) -> None:
        return await self._db.write(self._forget_body, user_id, character_id, day)

    def _forget_body(self, db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> None:
        by_name(db).execute(
            "DELETE FROM dates WHERE user_id = ? AND character_id = ? AND day = ?",
            (user_id, character_id, day.isoformat()),
        )

    async def upcoming(self, user_id: str, today: date) -> list[tuple[str, date, str]]:
        """The user's days out from `today` on, soonest first: with whom, which day, and
        what they are to do (GLOSSARY.md「地图」)."""
        rows = await self._db.read(_upcoming, user_id, today)
        return [(r["character_id"], date.fromisoformat(r["day"]), r["what"]) for r in rows]

    async def on(self, user_id: str, character_id: str, day: date) -> str | None:
        """What she is to do with the producer on `day`, if anything."""
        return await self._db.read(self._on_body, user_id, character_id, day)

    def _on_body(self, db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> str | None:
        row = by_name(db).execute(
            "SELECT what FROM dates WHERE user_id = ? AND character_id = ? AND day = ?",
            (user_id, character_id, day.isoformat()),
        ).fetchone()
        return row["what"] if row else None

    async def due(self, now: datetime) -> list[sqlite3.Row]:
        """The days out over whose telling is due and not told, nor tried too often lately."""
        return await self._db.read(self._due_body, now)

    def _due_body(self, db: sqlite3.Connection, now: datetime) -> list[sqlite3.Row]:
        rows = by_name(db).execute(
            "SELECT * FROM dates WHERE told = 0 AND tries < ? AND due_at <= ?",
            (TRIES, now.astimezone(UTC).isoformat()),
        ).fetchall()
        return [
            r for r in rows
            if r["last_at"] is None or now - datetime.fromisoformat(r["last_at"]) >= RETRY_AFTER
        ]

    async def told(self, user_id: str, character_id: str, day: str) -> None:
        return await self._db.write(self._told_body, user_id, character_id, day)

    def _told_body(self, db: sqlite3.Connection, user_id: str, character_id: str, day: str) -> None:
        by_name(db).execute(
            "UPDATE dates SET told = 1 WHERE user_id = ? AND character_id = ? AND day = ?",
            (user_id, character_id, day),
        )

    async def tried(self, user_id: str, character_id: str, day: str, now: datetime) -> None:
        return await self._db.write(self._tried_body, user_id, character_id, day, now)

    def _tried_body(self, db: sqlite3.Connection, user_id: str, character_id: str, day: str, now: datetime) -> None:
        by_name(db).execute(
            "UPDATE dates SET tries = tries + 1, last_at = ?"
            " WHERE user_id = ? AND character_id = ? AND day = ?",
            (now.astimezone(UTC).isoformat(), user_id, character_id, day),
        )



def _when(today: date, day: date) -> str:
    """`day` as she would say it, from `today`."""
    days = (day - today).days
    if days == 1:
        return "明天"
    if days == 2:
        return "后天"
    return f"{day.month}月{day.day}日"


@dataclass(frozen=True)
class _Due:
    user_id: str
    character_id: str
    day: date


class TripPhotos:
    def __init__(
        self,
        db: Database,
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
        dates: Dates | None = None,
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
        self._drawing: set[asyncio.Task] = set()
        self._dates = dates if dates is not None else Dates(db)
        self._invited: list[Callable[[str, str, date, str], Awaitable[None]]] = []
        self._played: Callable[[str, str, str], Awaitable[bool]] | None = None
        self._closes: Callable[[date, ZoneInfo], datetime] | None = None
        self._db = db
        db.prepare(_open)

    def available(self) -> bool:
        """Whether the deployer has them: schedules, and image generation now."""
        return self._enabled and self._config.image_generator() is not None

    async def _on(self, user_id: str, character: Character) -> bool:
        return (
            self.available() and await self._features.on(user_id, "trip_photos")
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
        """Plan the day's, and send those whose time has come; tell how the days out over
        were; one going wrong keeps none of the others from it."""
        now = self._clock()
        for row in await self._dates.due(now):
            try:
                await self._tell_date(row, now)
            except Exception:
                log.exception("telling of a day out in %s failed", row["character_id"])
        if not self.available():
            return
        for conversation, day in await self._plan(now):
            try:
                await self._send(conversation, day, now)
            except Exception:
                log.exception("sending a trip photo in conversation %s failed", conversation.id)

    async def send_now(
        self, user_id: str, character_id: str, place: str = "京都", trip_with: tuple[str, ...] = (),
        all_together: bool = False,
    ) -> bool:
        """Have her send the user a photo from a trip to `place` now, with `trip_with` there
        too, whatever her schedule says: for trying it out under the development sign-in.
        False if they have no conversation, or image generation is off."""
        conversation = await self._current(user_id, character_id)
        if conversation is None or not self.available():
            return False
        await self._send(conversation, None, self._clock(), place, trip_with, all_together)
        return True

    async def date_now(
        self, user_id: str, character_id: str, what: str, with_you: bool = True
    ) -> bool:
        """Have her tell the user of a day out with them (`what`) now, with a photo, as after
        a date (GLOSSARY.md「邀约」), the producer in it, `with_you`, whatever she writes:
        for trying it out under the development sign-in.
        False if they have no conversation, or image generation is off."""
        conversation = await self._current(user_id, character_id)
        if conversation is None or not self.available():
            return False
        attempt = 0
        while True:
            attempt += 1
            recording = self._activity.begin(
                "trip_photo", user_id, character_id, own_llm=False, exempt=False
            )
            lease = self._config.lease()
            try:
                with recording.current():
                    said, tag = await self._engine.after_date(
                        conversation, lease.keys.chat_llm, what, True, with_you
                    )
            except EmptyReply:
                await recording.finish("failed")
                if attempt >= DATE_NOW_TRIES:
                    raise
                continue
            except Exception:
                await recording.finish("failed")
                raise
            finally:
                lease.release()
            break
        assert tag is not None
        if with_you and not _WITH_YOU.search(tag):
            tag = re.sub(r"(?im)^\s*you\s*:.*$\n?", "", tag).rstrip("\n") + "\nyou: yes"
        await self._sent_with_photo(conversation, said, tag, [], recording, producer_there=True)
        return True

    async def _plan(self, now: datetime) -> list[tuple[Conversation, date]]:
        """Draw a time for each day's trip not drawn for yet; those whose time has come."""
        due: list[tuple[Conversation, date]] = []
        for current in await self._store.current_conversations():
            conversation, contact = current.conversation, current.last_contact
            character = self._characters.get(conversation.character_id)
            if character is None or current.ended or not current.has_turns:
                continue
            if contact is None or now - contact > WINDOW:
                continue
            if not await self._on(conversation.user_id, character):
                continue
            zone = await self._zone(conversation.user_id)
            day = now.astimezone(zone).date()
            schedule = await self._schedules.of(conversation.user_id, character.id, day)
            if schedule is None or schedule.trip is None:
                continue
            row = await self._db.read(_row, conversation.user_id, character.id, day)
            if row is None:
                minutes = (UNTIL.hour - FROM.hour) * 60
                at = datetime.combine(day, FROM, zone) + timedelta(
                    minutes=5 * self._random(minutes // 5)
                )
                row = await self._db.write(_planned, conversation.user_id, character.id, day, at)
            if row["sent"] or row["tries"] >= TRIES:
                continue
            if datetime.fromisoformat(row["due_at"]) > now:
                continue
            if row["last_at"] and now - datetime.fromisoformat(row["last_at"]) < RETRY_AFTER:
                continue
            due.append((conversation, day))
        return due

    async def _send(
        self, conversation: Conversation, day: date | None, now: datetime, place: str | None = None,
        trip_with: tuple[str, ...] = (), all_together: bool = False,
    ) -> None:
        user_id, character_id = conversation.user_id, conversation.character_id
        status = await self._affinities.status(user_id, character_id)
        if status is not None and (status.tier in NOT_NOW or status.cooling):
            return
        if self._store.is_taking_turn(conversation.id) or self._in_call(conversation.id):
            return
        if day is not None:
            schedule = await self._schedules.of(user_id, character_id, day)
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
                    conversation, lease.keys.chat_llm, place, [f"{c.name}（{c.id}）" for c in others],
                    all_together and bool(others),
                )
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("a trip photo in conversation %s failed: %s", conversation.id, type(e).__name__)
            await recording.finish("failed")
            if day is not None:
                await self._db.write(_tried, user_id, character_id, day, now)
            return
        finally:
            lease.release()
        if day is not None:
            await self._db.write(_sent, user_id, character_id, day)
        await self._sent_with_photo(conversation, said, tag, others, recording)

    async def send_with_photo(
        self, conversation: Conversation, said: list, tag: str, others: list[Character],
        producer_there: bool = False,
    ) -> None:
        """Keep what she said with the photo `tag` says and draw it, of her and of `others` if
        her tag says so (a party's, GLOSSARY.md「拜托」), and of the producer if
        `producer_there` and her tag says so."""
        recording = self._activity.begin(
            "trip_photo", conversation.user_id, conversation.character_id, own_llm=False,
            exempt=False,
        )
        await self._sent_with_photo(
            conversation, said, tag, others, recording, producer_there=producer_there
        )

    async def send_outing_photo(self, conversation: Conversation, said: list, tag: str, recording) -> None:
        """What she said as a photo of her and the producer was taken out together
        (GLOSSARY.md「约会」), the photo drawn in the background."""
        await self._sent_with_photo(conversation, said, tag, [], recording, background=True, producer_there=True)

    async def date_met(self, user_id: str, character_id: str, day: str) -> None:
        """The day out of `day` was met (GLOSSARY.md「约会」): she tells of it no more."""
        await self._dates.told(user_id, character_id, day)

    def with_outings(self, played: Callable[[str, str, str], Awaitable[bool]], closes: Callable[[date, ZoneInfo], datetime]) -> None:
        """Have a day out, met or not, told of as `played` says: one met, not at all; one not
        met by `closes`, that the producer never came."""
        self._played = played
        self._closes = closes

    async def _sent_with_photo(
        self, conversation: Conversation, said: list, tag: str, others: list[Character],
        recording, outfit_tags: str | None = None, background: bool = False,
        producer_there: bool = False,
    ) -> None:
        """Keep what she said with the photo `tag` says, being generated, and draw it, of her
        and of `others` if her tag says so, in `outfit_tags` if any, and of the producer, as
        their profile has them (a woman or not), if `producer_there` and her tag says so;
        in the background if `background`, the recording finished as it is."""
        character = self._characters[conversation.character_id]
        photo_id = uuid.uuid4().hex
        reach_out = await self._store.add_photo_reach_out(
            conversation.id, said, NewPhoto(photo_id, tag)
        )
        generator = self._config.image_generator()
        if reach_out is None or generator is None:
            if reach_out is not None:
                await self._store.finish_photo(photo_id, None, "service_error")
            await recording.finish("failed")
            return
        settings = generator.settings
        read = read_tag(tag)
        if outfit_tags:
            read = worn(read, outfit_tags)
        if not producer_there:
            read = replace(read, you=None)
        look: ProducerLook = (
            "female" if (await self._store.get_profile(conversation.user_id)).gender == "female"
            else "male"
        )
        together = others if _GROUP.search(tag) else []
        prompt = group_prompt([character, *together], read, settings.style, look)
        drawing = self._draw(photo_id, generator, prompt, read.stripped, recording)
        if background:
            task = asyncio.create_task(drawing)
            self._drawing.add(task)
            task.add_done_callback(self._drawing.discard)
        else:
            await drawing

    async def _draw(self, photo_id: str, generator, prompt, stripped, recording) -> None:
        settings = generator.settings
        image: bytes | None = None
        reason: PhotoFailure | None = None
        until = asyncio.get_running_loop().time() + TIME_LIMIT
        try:
            with recording.current():
                image = (await timed(
                    self._activity, image_origin(settings), generator.generate(prompt, until),
                    stripped,
                )).webp
        except ImageFailed as e:
            log.warning("trip photo %s was not generated: %s", photo_id, e)
            reason = e.reason
        except Exception:
            log.exception("generating trip photo %s failed", photo_id)
            reason = "service_error"
        await self._store.finish_photo(photo_id, image, reason)
        await recording.finish("success" if image is not None else "failed")

    async def ask_photo(
        self, user_id: str, character_id: str, wish: str, outfit_tags: str | None = None
    ) -> str:
        """Have her send the photo the producer asked for with a ticket, `wish` saying what
        they asked for, in the outfit `outfit_tags` says if any: the conversation it is in.
        Its picture is drawn after this returns. Raises NoConversation, NoImages, and what
        the model raises."""
        conversation = await self._current(user_id, character_id)
        if conversation is None or await self._store.ended(conversation.id) is not None:
            raise NoConversation()
        generator = self._config.image_generator()
        if generator is None:
            raise NoImages()
        recording = self._activity.begin("trip_photo", user_id, character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                said, tag = await self._engine.send_asked_photo(
                    conversation, lease.keys.chat_llm, wish, outfit_tags
                )
        except Exception:
            await recording.finish("failed")
            raise
        finally:
            lease.release()
        await self._sent_with_photo(
            conversation, said, tag, [], recording, outfit_tags=outfit_tags, background=True
        )
        return conversation.id

    async def invite(self, user_id: str, character_id: str, day: date, what: str) -> str:
        """Invite her out with the producer on `day` to `what` (GLOSSARY.md「邀约」): she
        answers at once; the conversation it is in. Raises NoConversation, DateTaken (one
        with her that day already, or a day not from tomorrow to `MAX_DAYS_AHEAD` days on),
        and what the model raises, the day out forgotten then."""
        conversation = await self._current(user_id, character_id)
        if conversation is None or await self._store.ended(conversation.id) is not None:
            raise NoConversation()
        zone = await self._zone(user_id)
        today = self._clock().astimezone(zone).date()
        if not 1 <= (day - today).days <= MAX_DAYS_AHEAD:
            raise DateTaken("只能约明天到一周以内")
        minutes = (AFTER_DATE_UNTIL.hour * 60 + AFTER_DATE_UNTIL.minute) - (
            AFTER_DATE.hour * 60 + AFTER_DATE.minute
        )
        due = datetime.combine(day, AFTER_DATE, zone) + timedelta(minutes=5 * self._random(minutes // 5))
        await self._dates.plan(user_id, character_id, day, what, due)
        when = _when(today, day)
        recording = self._activity.begin("trip_photo", user_id, character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                said = await self._engine.accept_date(conversation, lease.keys.chat_llm, when, what)
        except Exception:
            await recording.finish("failed")
            await self._dates.forget(user_id, character_id, day)
            raise
        finally:
            lease.release()
        await self._store.add_photo_reach_out(conversation.id, said, None)
        await recording.finish("success")
        for listener in self._invited:
            try:
                await listener(user_id, character_id, day, what)
            except Exception:
                log.exception("telling of a day out planned failed")
        return conversation.id

    def on_invited(self, listener: Callable[[str, str, date, str], Awaitable[None]]) -> None:
        """Have `listener` told of each day out she said yes to: the user, her, the day, what."""
        self._invited.append(listener)

    async def _tell_date(self, row: sqlite3.Row, now: datetime) -> None:
        """Once the day out is over, have her tell the producer how it was, with a photo
        where there is image generation and the user has photos on."""
        user_id, character_id, day = row["user_id"], row["character_id"], row["day"]
        conversation = await self._current(user_id, character_id)
        if conversation is None or await self._store.ended(conversation.id) is not None:
            await self._dates.told(user_id, character_id, day)  # nowhere to tell it
            return
        if self._store.is_taking_turn(conversation.id) or self._in_call(conversation.id):
            return
        if self._played is not None and self._closes is not None:
            # 约会要当面去（GLOSSARY.md「约会」）：去过的不再讲；到了截止时间还没去，她说他没来
            if await self._played(user_id, character_id, day):
                await self._dates.told(user_id, character_id, day)
                return
            zone = await self._zone(user_id)
            if now < self._closes(date.fromisoformat(day), zone):
                return
            await self._tell_missed(conversation, row, now)
            return
        character = self._characters.get(character_id)
        with_photo = (
            self._config.image_generator() is not None
            and await self._features.on(user_id, "photos")
            and character is not None and character.photo_tag is not None
            and character.photo_appearance is not None
        )
        recording = self._activity.begin("trip_photo", user_id, character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                said, tag = await self._engine.after_date(
                    conversation, lease.keys.chat_llm, row["what"], with_photo
                )
        except Exception as e:
            log.warning("telling of a day out failed: %s", type(e).__name__)
            await recording.finish("failed")
            await self._dates.tried(user_id, character_id, day, now)
            return
        finally:
            lease.release()
        await self._dates.told(user_id, character_id, day)
        if tag is None:
            await self._store.add_photo_reach_out(conversation.id, said, None)
            await recording.finish("success")
            return
        await self._sent_with_photo(conversation, said, tag, [], recording, producer_there=True)

    async def _tell_missed(self, conversation: Conversation, row: sqlite3.Row, now: datetime) -> None:
        user_id, character_id, day = row["user_id"], row["character_id"], row["day"]
        recording = self._activity.begin("trip_photo", user_id, character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                said = await self._engine.missed_date(conversation, lease.keys.chat_llm, row["what"])
        except Exception as e:
            log.warning("telling of a day out missed failed: %s", type(e).__name__)
            await recording.finish("failed")
            await self._dates.tried(user_id, character_id, day, now)
            return
        finally:
            lease.release()
        await self._dates.told(user_id, character_id, day)
        await self._store.add_photo_reach_out(conversation.id, said, None)
        await recording.finish("success")

    async def _current(self, user_id: str, character_id: str) -> Conversation | None:
        return next(
            (
                c for c, _, _ in await self._store.latest_conversations(user_id)
                if c.character_id == character_id
            ),
            None,
        )

    async def _zone(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(effective_timezone(await self._store.get_profile(user_id), None))

    def close(self) -> None:
        for task in self._drawing:
            task.cancel()


def _upcoming(db: sqlite3.Connection, user_id: str, today: date) -> list[sqlite3.Row]:
    return by_name(db).execute(
        "SELECT character_id, day, what FROM dates WHERE user_id = ? AND day >= ?"
        " ORDER BY day, character_id",
        (user_id, today.isoformat()),
    ).fetchall()


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _row(db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> sqlite3.Row | None:
    return by_name(db).execute(
        "SELECT * FROM trip_photo_due WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day.isoformat()),
    ).fetchone()


def _planned(
    db: sqlite3.Connection, user_id: str, character_id: str, day: date, at: datetime
) -> sqlite3.Row:
    db.execute(
        "INSERT OR IGNORE INTO trip_photo_due (user_id, character_id, day, due_at)"
        " VALUES (?, ?, ?, ?)",
        (user_id, character_id, day.isoformat(), at.astimezone(UTC).isoformat()),
    )
    row = _row(db, user_id, character_id, day)
    assert row is not None
    return row


def _sent(db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> None:
    db.execute(
        "UPDATE trip_photo_due SET sent = 1 WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day.isoformat()),
    )


def _tried(
    db: sqlite3.Connection, user_id: str, character_id: str, day: date, now: datetime
) -> None:
    db.execute(
        "UPDATE trip_photo_due SET tries = tries + 1, last_at = ?"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (now.astimezone(UTC).isoformat(), user_id, character_id, day.isoformat()),
    )
