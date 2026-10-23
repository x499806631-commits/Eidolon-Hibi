"""Letters (GLOSSARY.md「来信」): which idols write the user a letter on a day, when it comes,
and writing it ahead of that.

Only where the deployer turns them on (`Settings.letters`). Every `TICK` each user's current
conversation with each character (GLOSSARY.md「当前对话」) is looked at, if it has a turn, she
has not ended it, and they were last in touch in it within `WINDOW`. Once for each day of the
producer's, in the profile's time zone, it is planned whether she writes a letter coming that
day, what for, when it comes and when she writes it, and kept (`letter_days`), so that it is
planned and drawn once:

- the producer's birthday (if she is close or intimate, or affinity is off): planned the day
  before, written that evening (`LEAD` before), coming at midnight;
- her own birthday, or a holiday of one day from the calendar (a holiday only if she is close
  or intimate, or affinity is off): coming in the evening (`EVENING`), telling how she spent
  the day, written a little before;
- else, at most once in `GAP` days, by chance (`CHANCES`, the closer the likelier), coming in
  the day's hours (`ORDINARY`);
- none in a cooldown, nor cold or distant.

A day planned once its hours are over has no letter; one planned within them comes at a time
drawn from now to their end. A letter is written once its writing time has come, and kept to
come when planned: till then the user does not see it. One the model fails to write is tried
again at the next tick, till `GIVE_UP` after it was to come. Each user gets at most `PER_DAY`
letters a day; one over it is not written. Letters are written on the deployer's chat model
as reach-outs are, held only while she writes, each with an activity record of its own
(`letter`). Missed as the backend was down, they are not made up for.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog
from eidolon.affinity import Affinities, Tier
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.features import AllOn, Switches
from eidolon.engine import ConversationEngine
from eidolon.live_config import LiveConfig
from eidolon.producer import ProducerProfile, effective_timezone, is_birthday
from eidolon.randomness import Random
from eidolon.special_days import SpecialDays
from eidolon.store import Conversation, CurrentConversation, Letter, Occasion, Store

log = logging.getLogger(__name__)

TICK = timedelta(minutes=5)
"""How often it is looked whose letter is to be written."""

WINDOW = timedelta(days=30)
"""How long after the last contact she may still write."""

GAP = timedelta(days=5)
"""How long after her last letter she writes no ordinary one."""

CHANCES: dict[Tier, int] = {"intimate": 7, "close": 12, "trust": 20}
"""On a day she may write an ordinary letter, one chance in how many that she does, by her
tier. None in the tiers not here."""

ORDINARY = (time(9), time(21))
"""When in the producer's day an ordinary letter comes: from the first to the second."""

EVENING = (time(19), time(22))
"""When a letter on her birthday or a holiday comes, telling how she spent it."""

MORNING = (time(8), time(11))
"""When a birthday letter planned too late for midnight comes instead."""

LEAD: dict[Occasion, timedelta] = {
    "producer_birthday": timedelta(hours=3),
    "her_birthday": timedelta(hours=1),
    "holiday": timedelta(hours=1),
    "ordinary": timedelta(minutes=30),
}
"""How long before it comes she writes each kind of letter."""

GIVE_UP = timedelta(hours=1)
"""How long after a letter was to come it is no longer tried."""

PER_DAY = 3
"""How many letters a user gets a day at most."""

PLACES = 1000
"""How many places a time may be drawn at."""

_SCHEMA = """
-- Whether she writes the user a letter coming on a day of the producer's (GLOSSARY.md「来信」):
-- none (`occasion` NULL), or one written at `write_at` to come at `deliver_at`, `done` once
-- written (or given up).
CREATE TABLE IF NOT EXISTS letter_days (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    occasion TEXT,
    about TEXT NOT NULL DEFAULT '',
    write_at TEXT,
    deliver_at TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, character_id, day)
);
"""


@dataclass(frozen=True)
class _Plan:
    """A letter planned: what for (a holiday's, `about` which), when she writes it, when it
    comes, and whether it is written."""

    occasion: Occasion | None
    about: str = ""
    write_at: datetime | None = None
    deliver_at: datetime | None = None
    done: bool = False


@dataclass(frozen=True)
class _Due:
    """A letter whose writing time has come: in which conversation, its plan, and the
    producer's day it comes on."""

    conversation: Conversation
    plan: _Plan
    day: date
    zone: ZoneInfo


class Letters:
    """Her letters, in `db` beside the conversations of `store` (ADR-0028), written on
    the deployer's models of `config` as `engine` writes them, each recorded in `activity`;
    `random` draws whether and when; `special_days` has the days she writes for."""

    def __init__(
        self,
        db: Database,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        affinities: Affinities,
        activity: ActivityLog,
        clock: Clock,
        random: Random,
        enabled: bool,
        special_days: SpecialDays | None = None,
        features: Switches = AllOn(),
    ) -> None:
        self._features = features
        self._written: list[Callable[[Conversation, Letter, str], Awaitable[None]]] = []
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._affinities = affinities
        self._activity = activity
        self._clock = clock
        self._random = random
        self._enabled = enabled
        self._special_days = special_days
        self._db = db
        db.prepare(_open)

    def on_written(self, listener: Callable[[Conversation, Letter, str], Awaitable[None]]) -> None:
        """Have `listener` told each letter written ahead as it is kept: in which conversation,
        the letter, and what it is about (a holiday's, which)."""
        self._written.append(listener)

    async def keep_writing(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("writing letters failed")

    async def run_due(self) -> None:
        """Plan the days not planned yet, and write every letter whose writing time has come,
        in the order they come; one going wrong keeps none of the others from it."""
        if not self._enabled:
            return
        now = self._clock()
        due: list[_Due] = []
        for current in await self._store.current_conversations():
            try:
                due += await self._due(current, now)
            except Exception:
                log.exception("planning letters in conversation %s failed", current.conversation.id)
        for found in sorted(due, key=lambda d: d.plan.deliver_at or now):
            try:
                await self._write(found, now)
            except Exception:
                log.exception("writing a letter in conversation %s failed", found.conversation.id)

    async def write_now(self, user_id: str, character_id: str, occasion: Occasion, about: str = "") -> bool:
        """Have her write the user a letter for `occasion` now, in her newest conversation with
        them, coming at once: for trying letters out under the development sign-in, whatever
        the schedule says. False if they have no conversation; raises what the model raises."""
        newest = [
            c for c, _, _ in await self._store.latest_conversations(user_id)
            if c.character_id == character_id
        ]
        if not newest:
            return False
        now = self._clock()
        lease = self._config.lease()
        try:
            said = await self._engine.write_letter(newest[0], lease.keys.chat_llm, occasion, now, about)
        finally:
            lease.release()
        letter = await self._store.add_letter(user_id, character_id, occasion, said, now)
        for written in self._written:
            await written(newest[0], letter, about)
        return True

    async def _due(self, current: CurrentConversation, now: datetime) -> list[_Due]:
        """Her letters in the conversation whose writing time has come: today's, planned
        first if not yet, and tomorrow's, planned ahead if it is the producer's birthday."""
        conversation, contact = current.conversation, current.last_contact
        if conversation.character_id not in self._characters or current.ended:
            return []
        if not current.has_turns or contact is None or now - contact > WINDOW:
            return []
        if not await self._features.on(conversation.user_id, "letters"):  # GLOSSARY.md「玩法开关」
            return []
        tier = await self._tier(conversation)
        if tier is None:
            return []
        profile = await self._store.get_profile(conversation.user_id)
        zone = ZoneInfo(effective_timezone(profile, None))
        today = now.astimezone(zone).date()
        due = []
        for day in (today, today + timedelta(days=1)):
            plan = await self._db.read(_plan, conversation, day)
            if plan is None:
                if day == today:
                    plan = await self._make_plan(conversation, day, profile, tier, now, zone)
                elif self._birthday(profile, day) and await self._close(conversation, tier):
                    plan = await self._make_plan(conversation, day, profile, tier, now, zone)
                else:
                    continue
            if plan.occasion is None or plan.done or plan.write_at is None:
                continue
            if now >= plan.write_at:
                due.append(_Due(conversation, plan, day, zone))
        return due

    async def _make_plan(
        self, conversation: Conversation, day: date, profile: ProducerProfile, tier: Tier,
        now: datetime, zone: ZoneInfo,
    ) -> _Plan:
        """Plan whether she writes a letter coming on `day`, and when, and keep it."""
        occasion, about = await self._occasion(conversation, day, profile, tier)
        plan = _Plan(None)
        if occasion is not None:
            deliver_at = self._deliver_at(occasion, day, now, zone)
            if deliver_at is not None:
                write_at = max(deliver_at - LEAD[occasion], min(now, deliver_at))
                plan = _Plan(occasion, about, write_at, deliver_at)
        await self._db.write(_keep_plan, conversation, day, plan)
        return plan

    async def _occasion(
        self, conversation: Conversation, day: date, profile: ProducerProfile, tier: Tier
    ) -> tuple[Occasion | None, str]:
        """What she writes for on `day`, if she does, and for a holiday which."""
        close = await self._close(conversation, tier)
        days = self._special_days
        if self._birthday(profile, day) and close:
            return "producer_birthday", ""
        if days is not None and conversation.character_id in days.birthdays_on(day):
            return "her_birthday", ""
        holidays = [h for h in days.holidays_on(day) if h.end is None] if days else []
        if holidays and close:
            holiday = holidays[0]
            return "holiday", f"{holiday.name}。{holiday.note}"
        letters = await self._store.letters(conversation.user_id, conversation.character_id)
        if letters and self._clock() - datetime.fromisoformat(letters[0].created_at) < GAP:
            return None, ""
        if self._random(CHANCES[tier]) != 0:
            return None, ""
        return "ordinary", ""

    def _deliver_at(
        self, occasion: Occasion, day: date, now: datetime, zone: ZoneInfo
    ) -> datetime | None:
        """When a letter for `occasion` planned `now` comes on `day`; None if too late."""
        if occasion == "producer_birthday":
            midnight = datetime.combine(day, time(), zone)
            if now < midnight:
                return midnight
            hours = MORNING
        else:
            hours = ORDINARY if occasion == "ordinary" else EVENING
        start = max(datetime.combine(day, hours[0], zone), now)
        end = datetime.combine(day, hours[1], zone)
        if start >= end:
            return None
        return start + (end - start) * (self._random(PLACES) / PLACES)

    def _birthday(self, profile: ProducerProfile, day: date) -> bool:
        return profile.birthday is not None and is_birthday(profile.birthday, day)

    async def _close(self, conversation: Conversation, tier: Tier) -> bool:
        """Whether she writes for his birthday and holidays: close or intimate, or affinity off."""
        if tier in ("intimate", "close"):
            return True
        status = await self._affinities.status(conversation.user_id, conversation.character_id)
        return status is None

    async def _tier(self, conversation: Conversation) -> Tier | None:
        """The tier she writes by, trust where affinity is off; None if she is not to write,
        in a cooldown or in a tier without a chance."""
        status = await self._affinities.status(conversation.user_id, conversation.character_id)
        if status is None:
            return "trust"
        if status.cooling or status.tier not in CHANCES:
            return None
        return status.tier

    async def _done(self, due: _Due) -> None:
        await self._db.write(_done, due)

    async def _coming_that_day(self, due: _Due) -> int:
        began = datetime.combine(due.day, time(), due.zone)
        ends = began + timedelta(days=1)
        return sum(
            began <= datetime.fromisoformat(letter.created_at) < ends
            for letter in await self._store.letters(due.conversation.user_id)
        )

    async def _write(self, due: _Due, now: datetime) -> None:
        """Write the letter, to come when planned, unless the user has `PER_DAY` coming that
        day already, or it is past `GIVE_UP`: then never. One that fails is tried again."""
        conversation, plan = due.conversation, due.plan
        assert plan.occasion is not None and plan.deliver_at is not None
        if now > plan.deliver_at + GIVE_UP or await self._coming_that_day(due) >= PER_DAY:
            await self._done(due)
            return
        recording = self._activity.begin(
            "letter", conversation.user_id, conversation.character_id,
            own_llm=False, exempt=False,
        )
        lease = self._config.lease()
        try:
            with recording.current():
                said = await self._engine.write_letter(
                    conversation, lease.keys.chat_llm, plan.occasion, plan.deliver_at, plan.about
                )
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("writing a letter in conversation %s failed: %s", conversation.id,
                        type(e).__name__)
            await recording.finish("failed")
            return
        finally:
            lease.release()
        letter = await self._store.add_letter(
            conversation.user_id, conversation.character_id, plan.occasion, said, plan.deliver_at,
        )
        await self._done(due)
        for written in self._written:
            try:
                await written(conversation, letter, plan.about)
            except Exception:
                log.exception("telling of letter %s failed", letter.id)

        await recording.finish("success")


def _at(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at else None


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _plan(db: sqlite3.Connection, conversation: Conversation, day: date) -> _Plan | None:
    row = db.execute(
        "SELECT occasion, about, write_at, deliver_at, done FROM letter_days"
        " WHERE user_id = ? AND character_id = ? AND day = ?",
        (conversation.user_id, conversation.character_id, day.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return _Plan(row[0], row[1], _at(row[2]), _at(row[3]), bool(row[4]))


def _keep_plan(db: sqlite3.Connection, conversation: Conversation, day: date, plan: _Plan) -> None:
    db.execute(
        "INSERT OR IGNORE INTO letter_days"
        " (user_id, character_id, day, occasion, about, write_at, deliver_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            conversation.user_id, conversation.character_id, day.isoformat(),
            plan.occasion, plan.about, _iso(plan.write_at), _iso(plan.deliver_at),
        ),
    )


def _done(db: sqlite3.Connection, due: _Due) -> None:
    db.execute(
        "UPDATE letter_days SET done = 1 WHERE user_id = ? AND character_id = ? AND day = ?",
        (due.conversation.user_id, due.conversation.character_id, due.day.isoformat()),
    )
