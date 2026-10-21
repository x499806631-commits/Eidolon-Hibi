"""Reach-outs (GLOSSARY.md「搭话」): what she says first in her current conversation with the
user, the user not talking to her, once her time comes; whether or not the user is there to
see it.

Only where the deployer turns them on (`LiveConfig.reach_outs`). Every `TICK` the backend
runs, each user's current conversation with each character (GLOSSARY.md「当前对话」) is looked
at, if they were last in touch in it within `WINDOW`: when her last turn was recorded, or a
call that connected ended, whichever was later; gifts are not being in touch. Her time is
then the last contact and a time drawn from her tier's range (`INTERVALS`), at a place in it
drawn once for each contact (`PLACES`), drawn anew as the contact changes; trust's where
affinity is off. A conversation she is not to reach out in is passed over, its time left as
it is, to be looked at again at the next tick: one she has ended; one without a turn yet;
one she has reached out in since the last contact, so that she waits for the user to answer
first; one she is in a cooldown in, or cold or distant; one with a text turn under way or a
call; one of a user banned, or one an admin lets take no turns on the deployer's keys; one of
a user who has switched their own switch off (`switch`), on by default.

Her time goes by the producer's day, in the profile's time zone (or `Asia/Shanghai`): one
that falls in the `NIGHT`, or comes while it is night, is put off to the `MORNING` after,
at a place in it drawn once for each contact. On a special day (`special_days`: the
producer's birthday, her own, or a holiday of one day), if they have not been in touch yet
that day, her time is brought forward to that day's `MORNING`, at the same place, so that
she comes to say something about the day; still not while she waits for the user to answer
her, and not if her time from the range comes sooner. One brought forward is not missed as
the backend was down: whenever it starts that day, she comes. One past `WINDOW` after the last contact is
given up till they are in touch again. Those whose time has come at a tick come in the order
of their times, each user reached out to at most as many times a day as the deployer sets;
one over it is put off to a time drawn in the next day's `MORNING`. One whose time came
before the backend started is not made up for, nor one that fails, the model failing or
saying nothing she could say: each is put off from now by a time drawn anew from her tier's
range.

She reaches out on the deployer's chat model as text turns use it, whoever's LLM the user
brings, held only while she does (`ConversationEngine.reach_out`): nothing counts towards the
user's limits. Each reach-out has an activity record of its own (`reach_out`), on the
deployer's keys, its service calls under it, so that the dashboard's usage and costs count
them; one that fails is recorded failed; one the user and she were in touch meanwhile,
looked at again as it is recorded (`Store.add_reach_out`), is not recorded, refused.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog
from eidolon.affinity import Affinities, Tier
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.engine import ConversationEngine
from eidolon.live_config import LiveConfig
from eidolon.producer import ProducerProfile, effective_timezone, is_birthday
from eidolon.randomness import Random
from eidolon.special_days import SpecialDays
from eidolon.store import Conversation, CurrentConversation, ReachOutRefused, Store
from eidolon.usage import UsageLimiter

log = logging.getLogger(__name__)

TICK = timedelta(minutes=1)
"""How often it is looked whose time has come."""

WINDOW = timedelta(days=7)
"""How long after the last contact she may still reach out."""

INTERVALS: dict[Tier, tuple[timedelta, timedelta]] = {
    "intimate": (timedelta(hours=3), timedelta(hours=6)),
    "close": (timedelta(hours=8), timedelta(hours=16)),
    "trust": (timedelta(hours=20), timedelta(hours=36)),
}
"""How long after the last contact she reaches out, by her tier: the closer, the sooner. None
in the tiers not here."""

PLACES = 1000
"""How many places in a tier's range her time may be drawn at, evenly apart; and in the
`MORNING`."""

NIGHT = (time(22), time(8))
"""When in the producer's day she does not reach out: from the first to the second."""

MORNING = timedelta(hours=2)
"""How long after the night ends her time put off to the morning may be."""

_SCHEMA = """
-- When she is to reach out in a conversation (GLOSSARY.md「搭话」): the last contact it was
-- drawn for (`contact_at`), and where in her tier's range (`place`, from 0 to 1). The other
-- two are for times put off: to the morning, where in its hours; and a time set instead.
CREATE TABLE IF NOT EXISTS reach_out_schedules (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    contact_at TEXT NOT NULL,
    place REAL NOT NULL,
    morning_place REAL,
    due_at TEXT
);

-- The users who have turned reach-outs off, by their own switch; on for all the others.
CREATE TABLE IF NOT EXISTS reach_outs_off (
    user_id TEXT PRIMARY KEY REFERENCES users(id)
);
"""


class ReachOutsOff(Exception):
    """The deployer has reach-outs off: no user's own switch may be set."""


@dataclass(frozen=True)
class _Due:
    """A conversation whose time has come, for the last contact in it: her tier, her time,
    and the producer's time zone."""

    conversation: Conversation
    contact: datetime
    tier: Tier
    at: datetime
    zone: ZoneInfo


class ReachOuts:
    """Her reach-outs, on the database at `path` beside the conversations of `store`, on the
    deployer's models of `config`, each recorded in `activity`; `usage` has the users' own
    limits, `in_call` says whether there is a call in a conversation, and `random` draws
    when; `special_days` brings her time forward on the days she comes for. A time that came
    before `clock` says it is as they begin is missed."""

    def __init__(
        self,
        path: Path,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        affinities: Affinities,
        activity: ActivityLog,
        usage: UsageLimiter,
        in_call: Callable[[str], bool],
        clock: Clock,
        random: Random,
        special_days: SpecialDays | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._affinities = affinities
        self._activity = activity
        self._usage = usage
        self._in_call = in_call
        self._clock = clock
        self._random = random
        self._special_days = special_days
        self._started = clock()
        self._db = connect(path)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    async def keep_reaching_out(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("reaching out failed")

    async def run_due(self) -> None:
        """Reach out in every conversation whose time has come, one after another in the order
        of their times; one going wrong keeps none of the others from it."""
        if not self._config.reach_outs.enabled:
            return
        now = self._clock()
        due: list[_Due] = []
        for current in await asyncio.to_thread(self._store.current_conversations):
            try:
                if (found := await asyncio.to_thread(self._due, current, now)) is not None:
                    due.append(found)
            except Exception:
                log.exception("reaching out in conversation %s failed", current.conversation.id)
        for found in sorted(due, key=lambda d: d.at):
            try:
                if await asyncio.to_thread(self._within_the_day, found, now):
                    await self._reach_out(found, now)
            except Exception:
                log.exception("reaching out in conversation %s failed", found.conversation.id)

    def switch_of(self, user_id: str) -> bool | None:
        """Whether the user has their own switch on, as it is by default; None where the
        deployer has reach-outs off."""
        if not self._config.reach_outs.enabled:
            return None
        return self._switched_on(user_id)

    def switch(self, user_id: str, on: bool) -> None:
        """Switch the user's own switch on or off, for all the characters, from the next tick;
        raise `ReachOutsOff` where the deployer has reach-outs off."""
        if not self._config.reach_outs.enabled:
            raise ReachOutsOff
        with self._lock, self._db:
            if on:
                self._db.execute("DELETE FROM reach_outs_off WHERE user_id = ?", (user_id,))
            else:
                self._db.execute(
                    "INSERT OR IGNORE INTO reach_outs_off (user_id) VALUES (?)", (user_id,)
                )

    def _switched_on(self, user_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM reach_outs_off WHERE user_id = ?", (user_id,)
            ).fetchone()
        return row is None

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _due(self, current: CurrentConversation, now: datetime) -> _Due | None:
        """Her time to reach out in the conversation, if it has come and she is to; one that
        came before the backend started is put off instead."""
        conversation, contact = current.conversation, current.last_contact
        if conversation.character_id not in self._characters or current.ended:
            return None
        if not current.has_turns or contact is None:
            return None
        if now - contact > WINDOW:
            return None
        if current.reached_out_at is not None and current.reached_out_at >= contact:
            return None
        limits = self._usage.user_limits(conversation.user_id)
        if limits is not None and limits.turns_per_hour == 0:
            return None
        if not self._switched_on(conversation.user_id):
            return None
        tier = self._tier(conversation)
        if tier is None:
            return None
        place, morning, due = self._schedule(conversation.id, contact)
        if due is None:
            due = contact + self._interval(tier, place)
        profile = self._store.get_profile(conversation.user_id)
        zone = ZoneInfo(effective_timezone(profile, None))
        if now < due and (
            special := self._special_morning(conversation, profile, contact, morning, now, zone)
        ) is not None and special < due:
            # Brought forward to the special day's morning, which has come: not missed.
            if now < special or self._talking(conversation.id):
                return None
            return _Due(conversation, contact, tier, special, zone)
        if _at_night(due, zone):
            night = due
        elif due <= now and _at_night(now, zone):
            night = now
        else:
            night = None
        if night is not None:
            if morning is None:
                morning = self._draw_morning(conversation.id, contact)
            due = _morning_after(night, zone) + MORNING * morning
        if now < due:  # one past `WINDOW` never comes, as none is looked at then
            return None
        if self._talking(conversation.id):
            return None
        if due < self._started:  # missed, the backend not running
            self._put_off(conversation.id, contact, now + self._interval(tier))
            return None
        return _Due(conversation, contact, tier, due, zone)

    def _special_morning(
        self,
        conversation: Conversation,
        profile: ProducerProfile,
        contact: datetime,
        morning: float | None,
        now: datetime,
        zone: ZoneInfo,
    ) -> datetime | None:
        """When in the morning she comes on the producer's today, if it is a special day for
        her and they have not been in touch yet today; None if not, or if it is night now."""
        if _at_night(now, zone):
            return None
        today = now.astimezone(zone).date()
        if contact >= datetime.combine(today, time(), zone):
            return None
        birthday = profile.birthday is not None and is_birthday(profile.birthday, today)
        greets = self._special_days is not None and self._special_days.greets(
            conversation.character_id, today
        )
        if not birthday and not greets:
            return None
        if morning is None:
            morning = self._draw_morning(conversation.id, contact)
        return datetime.combine(today, NIGHT[1], zone) + MORNING * morning

    def _within_the_day(self, due: _Due, now: datetime) -> bool:
        """Whether the user may be reached out to once more today; if not, her time is put
        off to the next day's morning."""
        today = now.astimezone(due.zone).date()
        began = datetime.combine(today, time(), due.zone)
        if self._activity.reached_out_since(due.conversation.user_id, began) < (
            self._config.reach_outs.per_day
        ):
            return True
        tomorrow = datetime.combine(today + timedelta(days=1), NIGHT[1], due.zone)
        self._put_off(due.conversation.id, due.contact, tomorrow + MORNING * self._draw())
        return False

    def _talking(self, conversation_id: str) -> bool:
        """Whether a text turn is under way in the conversation, or a call."""
        return self._store.is_taking_turn(conversation_id) or self._in_call(conversation_id)

    def _tier(self, conversation: Conversation) -> Tier | None:
        """The tier she reaches out by, trust where affinity is off; None if she is not to,
        in a cooldown or in a tier without a range."""
        status = self._affinities.status(conversation.user_id, conversation.character_id)
        if status is None:
            return "trust"
        if status.cooling or status.tier not in INTERVALS:
            return None
        return status.tier

    def _interval(self, tier: Tier, place: float | None = None) -> timedelta:
        """How long after a contact she reaches out in `tier`, at `place` in its range, or at
        one drawn."""
        low, high = INTERVALS[tier]
        return low + (high - low) * (self._draw() if place is None else place)

    def _draw(self) -> float:
        return self._random(PLACES) / PLACES

    def _schedule(
        self, conversation_id: str, contact: datetime
    ) -> tuple[float, float | None, datetime | None]:
        """Where in her tier's range her time is, drawn for the last `contact`; where in the
        morning, if drawn yet; and the time set instead, if one is."""
        at = contact.isoformat()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT contact_at, place, morning_place, due_at FROM reach_out_schedules"
                " WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is not None and row[0] == at:
                return row[1], row[2], datetime.fromisoformat(row[3]) if row[3] else None
            place = self._draw()
            self._db.execute(
                "INSERT OR REPLACE INTO reach_out_schedules (conversation_id, contact_at, place)"
                " VALUES (?, ?, ?)",
                (conversation_id, at, place),
            )
        return place, None, None

    def _draw_morning(self, conversation_id: str, contact: datetime) -> float:
        """Draw where in the morning her time for `contact` is, put off from the night."""
        morning = self._draw()
        with self._lock, self._db:
            self._db.execute(
                "UPDATE reach_out_schedules SET morning_place = ?"
                " WHERE conversation_id = ? AND contact_at = ?",
                (morning, conversation_id, contact.isoformat()),
            )
        return morning

    def _put_off(self, conversation_id: str, contact: datetime, due: datetime) -> None:
        """Set her time for `contact` to `due` instead."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE reach_out_schedules SET due_at = ?"
                " WHERE conversation_id = ? AND contact_at = ?",
                (due.isoformat(), conversation_id, contact.isoformat()),
            )

    async def _reach_out(self, due: _Due, now: datetime) -> None:
        """Reach out, unless the user and she have begun talking since her time was looked
        at, as those before took their time."""
        conversation, contact = due.conversation, due.contact
        if self._talking(conversation.id):
            return
        recording = self._activity.begin(
            "reach_out", conversation.user_id, conversation.character_id,
            own_llm=False, exempt=False,
        )
        lease = self._config.lease()
        try:
            with recording.current():
                said = await self._engine.reach_out(conversation, lease.keys.chat_llm)
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("reaching out in conversation %s failed: %s", conversation.id,
                        type(e).__name__)
            recording.finish("failed")
            await asyncio.to_thread(
                self._put_off, conversation.id, contact, now + self._interval(due.tier)
            )
            return
        finally:
            lease.release()
        try:
            reach_out = await asyncio.to_thread(
                self._store.add_reach_out, conversation.id, said, contact, self._in_call
            )
        except ReachOutRefused:  # they were in touch meanwhile
            recording.finish("rejected")
            return
        recording.finish("success")
        await asyncio.to_thread(self._store.reach_out_recorded_as, reach_out.id, recording.row_id)


def _at_night(at: datetime, zone: ZoneInfo) -> bool:
    local = at.astimezone(zone).time()
    return local >= NIGHT[0] or local < NIGHT[1]


def _morning_after(at: datetime, zone: ZoneInfo) -> datetime:
    """When the night `at` is in ends."""
    local = at.astimezone(zone)
    day = local.date() if local.time() < NIGHT[1] else local.date() + timedelta(days=1)
    return datetime.combine(day, NIGHT[1], zone)
