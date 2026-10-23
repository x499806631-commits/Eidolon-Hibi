"""What happens between the user and the characters, as it happens (GLOSSARY.md「每日任务」):
a turn recorded, typed or in a call alike, and a gift given, each with the tier it leaves her
affinity in; a call that connected ended; and drawing. Each is kept here once, a gift given,
a call kept and drawing here altogether, and whatever it counts towards is kept and rewarded
with it: today's daily tasks, turns and gifts alone, and the achievements (`achievements`).

The daily tasks are the same each day (`DAILY_TASKS`), the day in Beijing wherever the user
is (`affinity.day_of`), by the clock as each happens: what was done the day before counts
not. Each done gives its draw chances through the gifts (`Gifts.reward`), once a day, in the
same transaction as what was done is kept, so that two done at once are rewarded once. So
does each achievement's stage unlocked, and each character complete.

The achievements were worked out once as they launched, from what was recorded before
(`achievements.launch`): rewarded open or not, logged, with no activity record.

Only what is told here counts: a turn refused or failed is never recorded, so never told; a
data package's turns and gifts are put in the database, not told; deleting a past
conversation takes nothing back. Nothing is kept while affinity and gifts are closed
(`LiveConfig.affinity_on`); what was kept before stays. What happened is kept in the same
transaction as the turn, the gift, the call or the draw it was told of: keeping it failing,
none of them is kept either (ADR-0028).

Each task done, once its reward is given, has an activity record (`ActivityLog.daily_task_done`)
for the dashboard to count, and so has each stage unlocked and each character complete
(`ActivityLog.achievement_unlocked`, `ActivityLog.character_complete`): written apart, once
what it records is kept, writing them failing is only logged.

Each user's progress today and with the achievements is theirs, deleted with them
(`database.USER_DATA`), and goes in no data package."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from eidolon import achievements
from eidolon.achievements import Completed, Hers, Progress, Tally, Unlocked
from eidolon.activity import ActivityLog
from eidolon.affinity import Tier, day_of, next_day
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.gifts import Drawn, Given, Gifts, Reward
from eidolon.store import keep_call

log = logging.getLogger(__name__)

_SCHEMA = """
-- One row per user who has done anything towards a daily task: the day it was last on
-- (`affinity.day_of`), and how many turns and gifts that day.
CREATE TABLE IF NOT EXISTS daily_task_progress (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    day TEXT NOT NULL,
    turns INTEGER NOT NULL,
    gifts INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Counts:
    """What a user did in a day that daily tasks count: turns and gifts given."""

    turns: int
    gifts: int

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(self.turns + other.turns, self.gifts + other.gifts)


@dataclass(frozen=True)
class DailyTask:
    """A daily task: done as the day's `counts` reach `goal`, giving `draws` draw chances."""

    id: str
    counts: Literal["turns", "gifts"]
    goal: int
    draws: int = 1

    def of(self, counts: Counts) -> int:
        """How far `counts` take it, past its goal or not."""
        return getattr(counts, self.counts)


DAILY_TASKS = (
    DailyTask("one_turn", "turns", 1),
    DailyTask("one_gift", "gifts", 1),
    DailyTask("ten_turns", "turns", 10),
)
"""Each day's daily tasks, in the order the gifts page shows them."""


@dataclass(frozen=True)
class Rewarded:
    """What something the user did was rewarded with: draw chances, reset cards, the daily
    tasks it did, the achievements' stages it unlocked and the characters it completed; and
    when, by the time the rewards are kept by, for the browser to tell what it has seen."""

    draws: int
    reset_cards: int
    daily_tasks: list[str]
    rewarded_at: datetime
    achievements: list[Unlocked] = field(default_factory=list)
    completed: list[Completed] = field(default_factory=list)

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "draws": self.draws,
            "reset_cards": self.reset_cards,
            "daily_tasks": self.daily_tasks,
            "achievements": [u.shown() for u in self.achievements],
            "completed": [c.shown() for c in self.completed],
            "rewarded_at": self.rewarded_at.isoformat(),
        }


@dataclass(frozen=True)
class DailyTasks:
    """How far the user is with today's daily tasks, and when they begin again."""

    progress: list[tuple[DailyTask, int]]
    """Each task, and how far towards its goal, no further than it."""
    renews_at: datetime

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "tasks": [
                {"id": t.id, "done": p >= t.goal, "progress": p, "goal": t.goal, "draws": t.draws}
                for t, p in self.progress
            ],
            "renews_at": self.renews_at.isoformat(),
        }


class Happenings:
    """What happens between users and characters, kept in the database next to the gifts
    they are rewarded with. What a turn, a gift, a call or a draw counts towards is kept and
    rewarded in the transaction the turn, the gift, the call or the draw is kept in, with it
    or not at all: a turn's is a body of the write task the turn is recorded in
    (`turn_recorded`); giving a gift, keeping a call and drawing are each such a task here
    (`give`, `call_ended`, `draw`)."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        is_open: Callable[[], bool],
        gifts: Gifts,
        activity: ActivityLog,
    ) -> None:
        """`is_open` tells whether affinity and gifts are open now."""
        self._db = db
        self._clock = clock
        self._is_open = is_open
        self._gifts = gifts
        self._activity = activity
        db.prepare(_open)
        self._launch()

    @property
    def open(self) -> bool:
        """Whether affinity and gifts are open now, so that what happens is kept towards
        them: told on the event loop, before handing in the write task it is kept in
        (`turn_recorded`)."""
        return self._is_open()

    def _launch(self) -> None:
        """Reward everyone, once ever, for how far they had come before the achievements
        launched, open or not: no activity record, as it did not happen now."""
        what: dict[tuple[str, str], Unlocked | Completed] = {}
        """Each stage unlocked or character completed, by the user and key it is rewarded
        by."""
        launched = False

        def due(db: sqlite3.Connection, now: datetime) -> list[tuple[str, Reward]]:
            nonlocal launched
            unlocked = achievements.launch(db, now)
            launched = unlocked is not None
            what.update({(u, r.key): w for u, (r, w) in unlocked or []})
            return [(u, r) for u, (r, _) in unlocked or []]

        given = self._db.prepare(self._gifts.reward_everyone, due)
        if launched:
            log.info(
                "achievements launched: %d users, %d stages, %d draw chances, %d reset cards",
                len({u for u, _ in given}),
                sum(isinstance(what[u, r.key], Unlocked) for u, r in given),
                sum(r.draws for _, r in given),
                sum(r.reset_cards for _, r in given),
            )

    def turn_recorded(
        self,
        db: sqlite3.Connection,
        user_id: str,
        character_id: str,
        tier: Tier | None,
        open: bool,
    ) -> Rewarded | None:
        """A turn of the user's with the character is recorded, now, leaving her affinity in
        `tier` (None if not known), through `db`, in the transaction it is recorded in, while
        affinity and gifts are `open` or not, as `open` told as it was handed in: what it is
        rewarded with; None if nothing. Its activity records are `write_records`'s."""
        if not open:
            return None
        return self._happened(db, user_id, character_id, Counts(turns=1, gifts=0), tier=tier)

    async def give(
        self, user_id: str, gift_id: str, character_id: str
    ) -> tuple[Given, Rewarded | None]:
        """Give the character one of the gift (`Gifts.give`), and keep it towards the daily
        tasks and the achievements, in one transaction: what giving it did, and what it was
        rewarded with (None if nothing). Raises GiftsClosed if gifts are not open,
        NoSuchCharacter for no such character, NoneLeft if the user has none of it."""
        await self._gifts.refuse_unless_open()
        self._gifts.refuse_unknown(gift_id, character_id)
        given, rewarded = await self._db.write(self._give, user_id, gift_id, character_id)
        await self.write_records(user_id, rewarded)
        await self._gifts.after_giving(user_id, character_id, gift_id, given)
        return given, rewarded

    async def call_ended(
        self,
        conversation_id: str,
        user_id: str,
        character_id: str,
        connected_at: datetime,
        seconds: float,
    ) -> Rewarded | None:
        """A call of the user's with the character in the conversation, that connected at
        `connected_at`, ended, now, having lasted `seconds` once it had: kept for stats
        (`store.keep_call`) and towards the achievements, in one transaction; what it was
        rewarded with, None if nothing, or if the conversation is gone meanwhile."""
        rewarded = await self._db.write(
            self._call_ended,
            conversation_id, user_id, character_id, connected_at, seconds, self.open,
        )
        await self.write_records(user_id, rewarded)
        return rewarded

    async def draw(self, user_id: str, *, all: bool = False) -> tuple[Drawn, Rewarded | None]:
        """Draw once, or every chance if `all` (`Gifts.draw`), and keep it towards the
        achievements, in one transaction: what drawing gave, and what it was rewarded with
        (None if nothing). Raises GiftsClosed if gifts are not open, NoDraws if there are no
        chances to draw."""
        await self._gifts.refuse_unless_open()
        drawn, rewarded = await self._db.write(self._draw, user_id, all)
        await self.write_records(user_id, rewarded)
        return drawn, rewarded

    async def write_records(self, user_id: str, rewarded: Rewarded | None) -> None:
        """Write the activity records of what the user was `rewarded` for, once it is kept:
        each daily task done, each stage unlocked and each character complete, each a write
        task of its own, apart from what it records; failing, only logged. Only what happens
        is recorded: those the launch works out from before it are rewarded without coming
        here."""
        if rewarded is None:
            return
        at = rewarded.rewarded_at
        for task in rewarded.daily_tasks:
            await self._activity.daily_task_done(user_id, task, at)
        for stage in rewarded.achievements:
            await self._activity.achievement_unlocked(
                user_id, stage.character_id, stage.achievement, stage.stage, at
            )
        for complete in rewarded.completed:
            await self._activity.character_complete(user_id, complete.character_id, at)

    async def daily_tasks(self, user_id: str) -> DailyTasks | None:
        """How far the user is with today's daily tasks; None while closed."""
        if not self._is_open():
            return None
        now = self._clock()
        counts = await self._db.read(_counts, user_id, day_of(now))
        return DailyTasks([(t, min(t.of(counts), t.goal)) for t in DAILY_TASKS], next_day(now))

    async def hers(self, user_id: str, character_id: str) -> Hers | None:
        """How far the user has come with the character's own achievements; None while
        closed."""
        if not self._is_open():
            return None
        return await self._db.read(achievements.hers, user_id, character_id)

    async def of_all(self, user_id: str) -> list[Progress] | None:
        """How far the user has come with the achievements of all the characters together;
        None while closed."""
        if not self._is_open():
            return None
        return await self._db.read(achievements.of_all, user_id)

    async def tally(self, user_id: str) -> Tally | None:
        """How many stages the user has unlocked and how many characters are complete;
        None while closed."""
        if not self._is_open():
            return None
        return await self._db.read(achievements.tally, user_id)

    def _give(
        self, db: sqlite3.Connection, user_id: str, gift_id: str, character_id: str
    ) -> tuple[Given, Rewarded | None]:
        given = self._gifts.give(db, user_id, gift_id, character_id)
        return given, self._happened(
            db, user_id, character_id, Counts(turns=0, gifts=1), tier=given.affinity.tier
        )

    def _call_ended(
        self,
        db: sqlite3.Connection,
        conversation_id: str,
        user_id: str,
        character_id: str,
        connected_at: datetime,
        seconds: float,
        open: bool,
    ) -> Rewarded | None:
        if not keep_call(db, conversation_id, connected_at, seconds) or not open:
            return None
        return self._happened(db, user_id, character_id, Counts(0, 0), call_time=seconds)

    def _draw(
        self, db: sqlite3.Connection, user_id: str, all: bool
    ) -> tuple[Drawn, Rewarded | None]:
        drawn = self._gifts.draw(db, user_id, all=all)
        return drawn, self._happened(db, user_id, None, Counts(0, 0), draws=len(drawn.drawn))

    def _happened(
        self,
        db: sqlite3.Connection,
        user_id: str,
        character_id: str | None,
        happened: Counts,
        *,
        call_time: float = 0,
        draws: int = 0,
        tier: Tier | None = None,
    ) -> Rewarded | None:
        """Keep what `happened` with the character (None for none) towards today's daily
        tasks, if anything, and with it the time talked in a call, the times drawn and the
        tier her affinity was left in towards the achievements, and give the rewards for the
        tasks it did and the stages it unlocked, through `db`, in the transaction what
        happened is kept in, affinity and gifts open: what it was rewarded with; None if
        nothing."""
        done: dict[str, str | Unlocked | Completed] = {}
        """Each task's id, stage unlocked or character completed, by the key it is rewarded
        by."""

        def due(db: sqlite3.Connection, now: datetime) -> list[Reward]:
            today = day_of(now)
            rewards = []
            if happened != Counts(0, 0):
                counts = _counts(db, user_id, today) + happened
                db.execute(
                    "INSERT INTO daily_task_progress (user_id, day, turns, gifts)"
                    " VALUES (?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE"
                    " SET day = excluded.day, turns = excluded.turns, gifts = excluded.gifts",
                    (user_id, today.isoformat(), counts.turns, counts.gifts),
                )
                done.update({_key(today, t): t.id for t in DAILY_TASKS})
                rewards = [
                    Reward(_key(today, t), t.draws) for t in DAILY_TASKS
                    if t.of(counts) >= t.goal
                ]
            unlocked = achievements.keep(
                db, user_id, character_id, now, today,
                turns=happened.turns, gifts=happened.gifts,
                call_time=call_time, draws=draws, tier=tier,
            )
            done.update({reward.key: what for reward, what in unlocked})
            return rewards + [reward for reward, _ in unlocked]

        given, at = self._gifts.reward(db, user_id, due)
        if not given:
            return None
        what = [done[r.key] for r in given]
        return Rewarded(
            sum(r.draws for r in given), sum(r.reset_cards for r in given),
            [w for w in what if isinstance(w, str)], at,
            [w for w in what if isinstance(w, Unlocked)],
            [w for w in what if isinstance(w, Completed)],
        )


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA + achievements.SCHEMA)


def _counts(db: sqlite3.Connection, user_id: str, today: date) -> Counts:
    """The user's turns and gifts kept for `today`; none if those kept were another day's."""
    row = db.execute(
        "SELECT day, turns, gifts FROM daily_task_progress WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None or row[0] != today.isoformat():
        return Counts(0, 0)
    return Counts(row[1], row[2])


def _key(day: date, task: DailyTask) -> str:
    """The key the task's reward is given by on `day`."""
    return f"daily_task:{day.isoformat()}:{task.id}"
