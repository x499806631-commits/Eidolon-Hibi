"""Writing her schedule for each day (GLOSSARY.md「行程」), ahead of the turns that read it.

Only where the deployer turns it on (`Settings.schedules`). Every `TICK`, each user's current
conversation with each character (GLOSSARY.md「当前对话」) is looked at, if it has a turn, she
has not ended it, and they were last in touch in it within `WINDOW`: from `WRITE_FROM` of the
producer's day, in the profile's time zone, her schedule for it is written if it has not been
tried, `PER_TICK` at most at a tick, the oldest contacts first. The day's twists are drawn on
`random` (`schedules.TWISTS`, none to `MAX_TWISTS`), and she is told the day before's to go
on from. One the model fails on, or writes nothing usable in, is kept as none and not tried
again that day. Written on the deployer's chat model as letters are, each with an activity
record of its own (`schedule`). A turn never waits for one: without it, she has none that day.
A night she goes to bed, her bath before it is drawn on `random` too (`schedules.draw_bath`),
in the tub unless her heart says she showers (GLOSSARY.md「洗澡」). A day she has a live away
from Tokyo, or one day in `TRIP_ODDS` otherwise (a twist of its own), she is away on a trip,
kept with the day, for the souvenir she brings the day after (GLOSSARY.md「纪念品」); a day
the producer invited her out on (GLOSSARY.md「邀约」), that too, a twist of its own. One trip
in `TOGETHER_ODDS`, others of her unit whose day is not written yet go with her; their days,
written after, have them go on it too (GLOSSARY.md「外景照片」).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.engine import ConversationEngine
from eidolon.features import AllOn, Switches
from eidolon.live_config import LiveConfig
from eidolon.producer import effective_timezone
from eidolon.randomness import Random
from eidolon.schedules import (
    TRIP_ODDS,
    TRIP_PLACES,
    TRIP_TWIST,
    TRIP_WITH_TWIST,
    DATE_TWIST,
    TOGETHER_ODDS,
    TWISTS,
    Bath,
    Schedules,
    Slot,
    draw_bath,
    parse_rest,
    parse_schedule,
)
from eidolon.schedules import said as schedule_said
from eidolon.store import Conversation, Store

log = logging.getLogger(__name__)

TICK = timedelta(minutes=10)
"""How often it is looked whose schedule is to be written."""

WINDOW = timedelta(days=7)
"""How long after the last contact she still has one written."""

WRITE_FROM = time(5)
"""From when in the producer's day hers for it is written."""

PER_TICK = 5
"""How many are written at a tick at most."""

MAX_TWISTS = 2
"""How many twists a day has at most."""


async def _no_date(*_: object) -> str | None:
    return None


async def _no_favors(*_: object) -> list[str]:
    return []


class ScheduleWriter:
    def __init__(
        self,
        schedules: Schedules,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        activity: ActivityLog,
        clock: Clock,
        random: Random,
        enabled: bool,
        features: Switches = AllOn(),
        date_on: Callable[[str, str, date], Awaitable[str | None]] = _no_date,
        favors_on: Callable[[str, str, date], Awaitable[list[str]]] = _no_favors,
    ) -> None:
        """`date_on` says what she is to do with the producer on a day, if anything
        (GLOSSARY.md「邀约」); `favors_on`, what the producer's favors have her do that day
        (GLOSSARY.md「拜托」), as twists of their own."""
        self._date_on = date_on
        self._favors_on = favors_on
        self._features = features
        self._schedules = schedules
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._activity = activity
        self._clock = clock
        self._random = random
        self._enabled = enabled

    async def keep_writing(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("writing schedules failed")

    async def run_due(self) -> None:
        """Write the schedules due, `PER_TICK` at most; one going wrong keeps none of the
        others from it. The days long gone are forgotten."""
        if not self._enabled:
            return
        now = self._clock()
        due = await self._due(now)
        for conversation, day in due[:PER_TICK]:
            try:
                await self._write(conversation, day)
            except Exception:
                log.exception("writing a schedule in conversation %s failed", conversation.id)
        await self._schedules.sweep(now.date())

    async def write_now(self, user_id: str, character_id: str) -> bool:
        """Have her schedule for today written now, whatever the hour and whether it was: for
        trying it out under the development sign-in. False if they have no conversation."""
        newest = [
            c for c, _, _ in await self._store.latest_conversations(user_id)
            if c.character_id == character_id
        ]
        if not newest:
            return False
        await self._write(newest[0], await self._today(user_id, self._clock()), again=True)
        return True

    async def _today(self, user_id: str, now: datetime) -> date:
        zone = ZoneInfo(effective_timezone(await self._store.get_profile(user_id), None))
        return now.astimezone(zone).date()

    async def _due(self, now: datetime) -> list[tuple[Conversation, date]]:
        found = []
        for current in await self._store.current_conversations():
            conversation, contact = current.conversation, current.last_contact
            if conversation.character_id not in self._characters or current.ended:
                continue
            if not current.has_turns or contact is None or now - contact > WINDOW:
                continue
            if not await self._features.on(conversation.user_id, "schedules"):  # GLOSSARY.md「玩法开关」
                continue
            profile = await self._store.get_profile(conversation.user_id)
            zone = ZoneInfo(effective_timezone(profile, None))
            local = now.astimezone(zone)
            if local.time() < WRITE_FROM:
                continue
            day = local.date()
            if await self._schedules.tried(conversation.user_id, conversation.character_id, day):
                continue
            found.append((contact, conversation, day))
        found.sort(key=lambda f: f[0])
        return [(conversation, day) for _, conversation, day in found]

    def _twists(self) -> list[str]:
        left = list(TWISTS)
        drawn = []
        for _ in range(self._random(MAX_TWISTS + 1)):
            drawn.append(left.pop(self._random(len(left))))
        return drawn

    async def _write(self, conversation: Conversation, day: date, again: bool = False) -> None:
        user_id, character_id = conversation.user_id, conversation.character_id
        twists = self._twists()
        if (what := await self._date_on(user_id, character_id, day)) is not None:
            twists.append(DATE_TWIST.format(what=what))
        twists.extend(await self._favors_on(user_id, character_id, day))
        names = {c.id: c.name for c in self._characters.values()}
        trip_with: tuple[str, ...] = ()
        joining = await self._schedules.joining(user_id, character_id, day)
        if joining is not None:
            trip, trip_with = joining
            twists.append(TRIP_WITH_TWIST.format(
                names="、".join(names.get(c, c) for c in trip_with), place=trip
            ))
        else:
            trip = self._engine.live_away(character_id, day)
        if trip is None and self._random(TRIP_ODDS) == 0:
            trip = TRIP_PLACES[self._random(len(TRIP_PLACES))]
            free = [
                c for c in self._engine.unit_mates(character_id)
                if not await self._schedules.tried(user_id, c, day)
            ]
            if free and self._random(TOGETHER_ODDS) == 0:
                trip_with = tuple(free)
                twists.append(TRIP_WITH_TWIST.format(
                    names="、".join(names.get(c, c) for c in trip_with), place=trip
                ))
            else:
                twists.append(TRIP_TWIST.format(place=trip))
        before = await self._schedules.of(user_id, character_id, day - timedelta(days=1))
        yesterday = schedule_said(before, names)
        recording = self._activity.begin("schedule", user_id, character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        slots: list[Slot] = []
        wake = sleep = None
        bath: Bath | None = None
        try:
            with recording.current():
                answer = await self._engine.write_schedule(
                    conversation, lease.keys.chat_llm, day, twists, yesterday
                )
            slots = parse_schedule(answer, self._characters)
            wake, sleep = parse_rest(answer)
            if sleep is not None:
                heart = self._characters[character_id].heart
                bath = draw_bath(sleep, heart.bath if heart else "tub", self._random)
            await recording.finish("success" if slots else "failed")
        except Exception as e:
            # Not logged with what was raised: a service's error may repeat what it was asked.
            log.warning("writing a schedule in conversation %s failed: %s", conversation.id,
                        type(e).__name__)
            await recording.finish("failed")
        finally:
            lease.release()
        if again:
            await self._schedules.forget(user_id, character_id, day)
        await self._schedules.keep(
            user_id, character_id, day, slots, twists, wake, sleep, bath,
            trip if slots else None, trip_with,
        )
