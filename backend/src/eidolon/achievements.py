"""Achievements (GLOSSARY.md「成就」): how far each user has come with the characters, kept
as it happens (`happenings`), and the stages it has unlocked.

Four are each character's own (`PER_CHARACTER`): turns with her, the time talked with her in
calls, gifts given her, and the highest tier her affinity has reached. Four are of all the
characters together (`ALL_CHARACTERS`): how many characters the user has had a turn with and
how many have reached intimate, worked out from her own; how many days they came, a turn or a
gift given that day in Beijing (`affinity.day_of`), not one after another; and how many times
they drew.

Each stage is unlocked once, the first time what it counts reaches its goal by the goals as
they are then, and stays unlocked whatever the goals become; stages are told apart by their
number, so goals may be changed but not reordered. Stage n gives n draw chances
(`Stage.draws`). Every stage of a character's own achievements unlocked, she is complete,
giving `COMPLETE_RESET_CARDS` once ever for her, however many stages there come to be.

As they launch, once ever, how far each user had come before is worked out from what is
recorded (`launch`): turns, calls that connected and gifts given, data packages imported
and all; the tiers her replies and gifts left her affinity in, and the one it is in; the
days of the turns and the gifts. The times drawn were never recorded, so start from none.

What is kept and unlocked is kept on the connection and in the transaction it is rewarded in
(`Gifts.reward`), so that two at once unlock and reward a stage once. Each user's is theirs,
deleted with them (`database.USER_DATA`), and goes in no data package."""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from eidolon.affinity import TIERS, Tier, day_of, tier_of
from eidolon.gifts import Reward

SCHEMA = """
-- One row per user, achievement and character (empty for those of all the characters
-- together) with anything kept towards it: how much (seconds, not whole ones, for the time
-- talked in calls; a tier's rank among `achievements.TIERS_REACHED` for the tier reached),
-- and for the days come the last of them (`affinity.day_of`). A character complete has a
-- row too, `complete`, of the reset cards it gave.
CREATE TABLE IF NOT EXISTS achievement_progress (
    user_id TEXT NOT NULL REFERENCES users(id),
    achievement TEXT NOT NULL,
    character_id TEXT NOT NULL,
    count INTEGER NOT NULL,
    last_day TEXT,
    PRIMARY KEY (user_id, achievement, character_id)
);
-- One row per stage each user unlocked, by its number from 1, and when.
CREATE TABLE IF NOT EXISTS achievement_stages (
    user_id TEXT NOT NULL REFERENCES users(id),
    achievement TEXT NOT NULL,
    character_id TEXT NOT NULL,
    stage INTEGER NOT NULL,
    unlocked_at TEXT NOT NULL,
    PRIMARY KEY (user_id, achievement, character_id, stage)
);
-- One row once the achievements have launched (`launch`): when. No one's.
CREATE TABLE IF NOT EXISTS achievements_launched (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    launched_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Stage:
    """A stage of an achievement: unlocked as what it counts reaches `goal`, giving `draws`
    draw chances."""

    goal: int
    draws: int


def _stages(*goals: int) -> tuple[Stage, ...]:
    """Stages reaching `goals` in order, stage n giving n draw chances."""
    return tuple(Stage(goal, n) for n, goal in enumerate(goals, 1))


@dataclass(frozen=True)
class Achievement:
    """An achievement, its stages in order."""

    id: str
    stages: tuple[Stage, ...]


TIERS_REACHED: tuple[Tier, ...] = ("close", "intimate")
"""The tiers the tier reached counts, by rank from 1: reaching one reaches those below it."""

PER_CHARACTER = (
    Achievement("turns", _stages(10, 100, 300, 1000, 3000)),
    Achievement("call_time", _stages(10 * 60, 60 * 60, 5 * 60 * 60, 20 * 60 * 60)),
    Achievement("gifts", _stages(1, 10, 30, 100)),
    Achievement("tier", _stages(1, 2)),
)
"""Each character's own, in the order her panel shows them: turns, seconds talked in calls
since connected, gifts given, the rank of the tier reached."""

ALL_CHARACTERS = (
    Achievement("characters", _stages(3, 10, 29)),  # the last, every character
    Achievement("intimate_characters", _stages(1, 3, 10)),
    Achievement("days", _stages(7, 30, 100, 365)),
    Achievement("draws", _stages(10, 50, 200, 500)),
)
"""Those of all the characters together, in the order the gifts page shows them."""

COMPLETE_RESET_CARDS = 1
"""The reset cards a character complete gives, once ever for her."""

_ALL = ""
"""The character of those of all the characters together."""

_COMPLETE = "complete"
"""What a character complete is kept as, next to the achievements."""


@dataclass(frozen=True)
class Unlocked:
    """A stage unlocked: of which achievement, for which character (None for those of all
    the characters together), by number, and the draw chances it gave."""

    achievement: str
    character_id: str | None
    stage: int
    draws: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "achievement": self.achievement,
            "character_id": self.character_id,
            "stage": self.stage,
            "draws": self.draws,
        }


@dataclass(frozen=True)
class Completed:
    """A character complete, and the reset cards it gave."""

    character_id: str
    reset_cards: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return {"character_id": self.character_id, "reset_cards": self.reset_cards}


@dataclass(frozen=True)
class Progress:
    """How far the user has come with an achievement, and when each stage unlocked, by
    number."""

    achievement: Achievement
    progress: float
    unlocked: dict[int, datetime]

    def shown(self) -> dict:
        """As the browser is told it: the tier reached by its name, never her affinity; the
        time talked in whole seconds."""
        tier = self.achievement.id == "tier"
        progress = int(self.progress)
        return {
            "id": self.achievement.id,
            "progress": _tier(progress) if tier else progress,
            "stages": [
                {
                    "goal": _tier(s.goal) if tier else s.goal,
                    "draws": s.draws,
                    "unlocked_at": (at := self.unlocked.get(n)) and at.isoformat(),
                }
                for n, s in enumerate(self.achievement.stages, 1)
            ],
        }


@dataclass(frozen=True)
class Hers:
    """A character's own achievements with the user, when she was complete (None if not),
    and the reset cards being complete gives: none once it has given them, ever."""

    achievements: list[Progress]
    completed_at: datetime | None
    reset_cards: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "achievements": [p.shown() for p in self.achievements],
            "complete": {
                "completed_at": self.completed_at and self.completed_at.isoformat(),
                "reset_cards": self.reset_cards,
            },
        }


def keep(
    db: sqlite3.Connection, user_id: str, character_id: str | None, now: datetime, today: date,
    *, turns: int = 0, gifts: int = 0, call_time: float = 0, draws: int = 0,
    tier: Tier | None = None,
) -> list[tuple[Reward, Unlocked | Completed]]:
    """Keep that the user, `now`, on `today`, had `turns` turns with the character, gave her
    `gifts` gifts, talked with her `call_time` seconds in a call, drew `draws` times (no
    character for that alone), and left her affinity in `tier` (None for unchanged); and
    unlock the stages reached by now: the rewards due for every stage unlocked and her being
    complete, newly or not, each with what it is for. A turn or a gift makes `today` a day
    come; a call or a draw alone does not."""
    for achievement, character, count in (
        ("turns", character_id, turns),
        ("gifts", character_id, gifts),
        ("call_time", character_id, call_time),
        ("draws", _ALL, draws),
    ):
        if count and character is not None:
            db.execute(
                "INSERT INTO achievement_progress (user_id, achievement, character_id, count)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (user_id, achievement, character_id)"
                " DO UPDATE SET count = count + excluded.count",
                (user_id, achievement, character, count),
            )
    if tier in TIERS_REACHED and character_id is not None:
        db.execute(
            "INSERT INTO achievement_progress (user_id, achievement, character_id, count)"
            " VALUES (?, 'tier', ?, ?) ON CONFLICT (user_id, achievement, character_id)"
            " DO UPDATE SET count = MAX(count, excluded.count)",
            (user_id, character_id, TIERS_REACHED.index(tier) + 1),
        )
    if turns or gifts:
        db.execute(
            "INSERT INTO achievement_progress"
            " (user_id, achievement, character_id, count, last_day) VALUES (?, 'days', ?, 1, ?)"
            " ON CONFLICT (user_id, achievement, character_id) DO UPDATE"
            " SET count = count + 1, last_day = excluded.last_day"
            " WHERE last_day < excluded.last_day",
            (user_id, _ALL, today.isoformat()),
        )
    return _unlock(db, user_id, character_id, now)


def launch(
    db: sqlite3.Connection, now: datetime
) -> list[tuple[str, tuple[Reward, Unlocked | Completed]]] | None:
    """Keep, once ever, how far every user had come before the achievements launched,
    `now`, by what is recorded of them, data packages imported and all; and unlock the
    stages reached by now: the rewards due each user for every stage unlocked and every
    character complete, each with what it is for. None if launched before."""
    if not db.execute(
        "INSERT INTO achievements_launched (id, launched_at) VALUES (1, ?)"
        " ON CONFLICT DO NOTHING",
        (now.isoformat(),),
    ).rowcount:
        return None
    progress = list(db.execute(
        "SELECT c.user_id, 'turns', c.character_id, COUNT(*) FROM messages m"
        " JOIN conversations c ON c.id = m.conversation_id WHERE m.role = 'user'"
        " GROUP BY c.user_id, c.character_id"
        " UNION ALL SELECT c.user_id, 'call_time', c.character_id, SUM(k.duration_s)"
        " FROM calls k JOIN conversations c ON c.id = k.conversation_id"
        " GROUP BY c.user_id, c.character_id"
        " UNION ALL SELECT c.user_id, 'gifts', c.character_id, COUNT(*)"
        " FROM conversation_events e JOIN conversations c ON c.id = e.conversation_id"
        " WHERE e.kind = 'gift' GROUP BY c.user_id, c.character_id"
    ))
    tiers: dict[tuple[str, str], int] = {}
    for user_id, character_id, tier in db.execute(
        "SELECT DISTINCT c.user_id, c.character_id, json_extract(m.affinity, '$.tier')"
        " FROM messages m JOIN conversations c ON c.id = m.conversation_id"
        " WHERE m.affinity IS NOT NULL"
        " UNION SELECT c.user_id, c.character_id, json_extract(e.affinity, '$.tier')"
        " FROM conversation_events e JOIN conversations c ON c.id = e.conversation_id"
        " WHERE e.kind = 'gift' AND e.affinity IS NOT NULL"
        # Recovering only ever brings it back to normal: as kept, it is in the tier it is.
        " UNION SELECT user_id, character_id, value FROM affinities WHERE value >= ?",
        (dict(TIERS)[TIERS_REACHED[0]],),
    ):
        if isinstance(tier, float):
            tier = tier_of(tier)
        if tier in TIERS_REACHED:
            rank = TIERS_REACHED.index(tier) + 1
            tiers[user_id, character_id] = max(tiers.get((user_id, character_id), 0), rank)
    progress += [(u, "tier", c, rank) for (u, c), rank in tiers.items()]
    db.create_function(
        "day_of", 1, lambda at: day_of(datetime.fromisoformat(at)).isoformat(),
        deterministic=True,
    )
    days = db.execute(
        "SELECT user_id, ?, COUNT(DISTINCT day), MAX(day) FROM ("
        " SELECT c.user_id, day_of(m.created_at) AS day FROM messages m"
        " JOIN conversations c ON c.id = m.conversation_id WHERE m.role = 'user'"
        " UNION ALL SELECT c.user_id, day_of(e.created_at) FROM conversation_events e"
        " JOIN conversations c ON c.id = e.conversation_id WHERE e.kind = 'gift'"
        ") GROUP BY user_id",
        (_ALL,),
    ).fetchall()
    db.executemany(
        "INSERT INTO achievement_progress"
        " (user_id, achievement, character_id, count, last_day) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (user_id, achievement, character_id) DO UPDATE"
        " SET count = MAX(count, excluded.count),"
        " last_day = MAX(last_day, excluded.last_day)",
        [(*p, None) for p in progress] + [(u, "days", c, n, last) for u, c, n, last in days],
    )
    due = []
    for user_id, character_id in sorted({(u, c) for u, _, c, _ in progress}):
        due += [(user_id, d) for d in _unlock(db, user_id, character_id, now)]
    return due


def hers(db: sqlite3.Connection, user_id: str, character_id: str) -> Hers:
    """How far the user has come with the character's own achievements."""
    counts = _counts(db, user_id)
    unlocked = _unlocked(db, user_id, character_id)
    achievements = [
        Progress(a, counts.get((a.id, character_id), 0), unlocked.get(a.id, {}))
        for a in PER_CHARACTER
    ]
    completed_at = None
    if _complete(unlocked):
        completed_at = max(
            (
                p.unlocked[n] for p in achievements
                for n in range(1, len(p.achievement.stages) + 1)
            ),
            default=None,
        )
    given = counts.get((_COMPLETE, character_id)) is not None
    return Hers(achievements, completed_at, 0 if given else COMPLETE_RESET_CARDS)


def of_all(db: sqlite3.Connection, user_id: str) -> list[Progress]:
    """How far the user has come with the achievements of all the characters together."""
    counts = _counts(db, user_id)
    unlocked = _unlocked(db, user_id, _ALL)
    return [
        Progress(a, _of_all(counts, a.id), unlocked.get(a.id, {})) for a in ALL_CHARACTERS
    ]


@dataclass(frozen=True)
class Tally:
    """How many stages the user has unlocked in all, whatever the goals are now, and how many
    characters are complete, as the stats show them (GLOSSARY.md「统计」)."""

    stages: int
    complete: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return {"stages": self.stages, "complete": self.complete}


def tally(db: sqlite3.Connection, user_id: str) -> Tally:
    """How far the user has come with the achievements, all told."""
    ((stages,),) = db.execute(
        "SELECT COUNT(*) FROM achievement_stages WHERE user_id = ?", (user_id,)
    )
    characters = db.execute(
        "SELECT DISTINCT character_id FROM achievement_stages"
        " WHERE user_id = ? AND character_id != ?",
        (user_id, _ALL),
    ).fetchall()
    return Tally(stages, sum(_complete(_unlocked(db, user_id, c)) for (c,) in characters))


def _unlock(
    db: sqlite3.Connection, user_id: str, character_id: str | None, now: datetime
) -> list[tuple[Reward, Unlocked | Completed]]:
    """Unlock the stages reached of the character's own achievements, if any character, and
    of those of all together; the rewards due for each stage reached, and for her being
    complete if she is."""
    counts = _counts(db, user_id)
    reached = [
        (a, character_id, counts.get((a.id, character_id), 0))
        for a in PER_CHARACTER if character_id is not None
    ] + [(a, _ALL, _of_all(counts, a.id)) for a in ALL_CHARACTERS]
    due: list[tuple[Reward, Unlocked | Completed]] = []
    for achievement, character, progress in reached:
        for n, stage in enumerate(achievement.stages, 1):
            if progress < stage.goal:
                continue
            db.execute(
                "INSERT INTO achievement_stages"
                " (user_id, achievement, character_id, stage, unlocked_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (user_id, achievement.id, character, n, now.isoformat()),
            )
            due.append((
                Reward(f"achievement:{achievement.id}:{character}:{n}", stage.draws),
                Unlocked(achievement.id, character or None, n, stage.draws),
            ))
    if character_id is not None and _complete(_unlocked(db, user_id, character_id)):
        db.execute(
            "INSERT INTO achievement_progress (user_id, achievement, character_id, count)"
            " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (user_id, _COMPLETE, character_id, COMPLETE_RESET_CARDS),
        )
        due.append((
            Reward(f"achievement_complete:{character_id}", 0, COMPLETE_RESET_CARDS),
            Completed(character_id, COMPLETE_RESET_CARDS),
        ))
    return due


def _counts(db: sqlite3.Connection, user_id: str) -> dict[tuple[str, str], float]:
    """Everything kept towards the user's achievements, by achievement and character."""
    rows: Iterable[tuple[str, str, float]] = db.execute(
        "SELECT achievement, character_id, count FROM achievement_progress WHERE user_id = ?",
        (user_id,),
    )
    return {(a, c): count for a, c, count in rows}


def _of_all(counts: dict[tuple[str, str], float], achievement: str) -> float:
    """How far the user has come with one of the achievements of all the characters
    together, worked out from each character's own where it is."""
    if achievement == "characters":
        return sum(1 for (a, c), n in counts.items() if a == "turns" and c != _ALL and n >= 1)
    if achievement == "intimate_characters":
        intimate = TIERS_REACHED.index("intimate") + 1
        return sum(1 for (a, c), n in counts.items() if a == "tier" and c != _ALL and n >= intimate)
    return counts.get((achievement, _ALL), 0)


def _unlocked(
    db: sqlite3.Connection, user_id: str, character_id: str
) -> dict[str, dict[int, datetime]]:
    """When each stage the user unlocked for the character (`_ALL` for those of all
    together) unlocked, by achievement and number."""
    unlocked: dict[str, dict[int, datetime]] = {}
    for achievement, stage, at in db.execute(
        "SELECT achievement, stage, unlocked_at FROM achievement_stages"
        " WHERE user_id = ? AND character_id = ?",
        (user_id, character_id),
    ):
        unlocked.setdefault(achievement, {})[stage] = datetime.fromisoformat(at)
    return unlocked


def _complete(unlocked: dict[str, dict[int, datetime]]) -> bool:
    """Whether every stage of a character's own achievements there is now is unlocked."""
    return all(
        n in unlocked.get(a.id, {}) for a in PER_CHARACTER for n in range(1, len(a.stages) + 1)
    )


def _tier(rank: int) -> Tier | None:
    """The tier reached of `rank`; None for none."""
    return TIERS_REACHED[rank - 1] if rank else None
