"""Her schedule (GLOSSARY.md「行程」): what an idol is roughly up to each day, written anew
every day, so that asked what she is doing she has something to say, and it is not the same
every week.

A day's schedule is a few slots (`Slot`), kept for each user and character apart, in the
producer's own time zone as every "now" of theirs is; written by `schedule_writer` on the
deployer's chat model, with a twist or two drawn for the day (`TWISTS`) so that it is never
learnt by heart. Kept `KEPT_DAYS`, then swept. What the model writes is checked here
(`parse_schedule`): a slot out of order, overlapping another, out of the day's hours, of a kind
not known, or with someone not one of the characters, is dropped.

Reading it never writes: `lines` is what she is told of it in a turn, a reach-out or her
diary, the whole day in a sentence and the slot she is in now. It is only roughly so: she is
told she need not keep to it.

A night she goes to bed, she has a bath before it (GLOSSARY.md「洗澡」), drawn as the day is
written (`draw_bath`): `BATH_BEFORE` before she goes to bed, as long as her way of bathing
takes (`BATH_MINUTES`).
"""

import json
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Literal, cast, get_args

from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.hearts import BathKind
from eidolon.randomness import Random

SlotKind = Literal["work", "lesson", "school", "live", "rest", "private"]
"""Work (a shoot, a job), a lesson or practice, school, a live, resting at home, her own plans."""

KEPT_DAYS = 14
"""How many days a schedule is kept."""

MAX_SLOTS = 8
MAX_WHAT = 30
MAX_WHERE = 30
DAY_STARTS = time(6)
"""The earliest a slot may begin; the latest it may end is midnight."""

TWISTS = (
    "临时加练，结束得比预想晚",
    "被同组合的伙伴拉去逛街",
    "下雨了，原本在外面的安排改到了室内",
    "早上睡过头了，赶得有点急",
    "收到了粉丝的信，开心了一整天",
    "事务所临时开了个短会",
    "有一项工作提前结束，多出了一段空闲",
    "路上发现了一家想进去看看的小店",
    "被拜托帮别人一个小忙",
    "突然很想吃某样东西，绕路去买了",
)
"""The day's twists one or two are drawn from, for the model to work into the day."""

TRIP_PLACES = (
    "大阪", "京都", "北海道", "福冈", "冲绳", "横滨", "名古屋", "仙台", "神户", "金泽", "广岛", "长野",
)
"""Where a trip away from Tokyo a day may have takes her (GLOSSARY.md「纪念品」)."""

TRIP_ODDS = 8
"""One day in so many has a trip, drawn as its twists are."""

TRIP_TWIST = "要去{place}出外景，当天来回"
"""The twist a trip is to the model writing the day."""

LIVE_CITIES = (
    "横浜", "大阪", "名古屋", "福岡", "札幌", "仙台", "神戸", "京都", "広島", "静岡", "金沢", "新潟",
)
"""The cities a live's venue away from Tokyo names, as the world setting writes them."""

_SCHEMA = """
-- Her schedule for a day of the producer's (GLOSSARY.md「行程」): `slots` and the day's
-- `twists` as JSON; slots '[]' for a day it could not be written, which is not tried again.
CREATE TABLE IF NOT EXISTS schedules (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    slots TEXT NOT NULL,
    twists TEXT NOT NULL,
    created_at TEXT NOT NULL,
    wake TEXT,
    sleep TEXT,
    bath_at TEXT,
    bath_minutes INTEGER,
    trip TEXT,
    PRIMARY KEY (user_id, character_id, day)
);
"""

Rest = Literal["asleep", "bath", "bedtime"]
"""Asleep, in the bath (GLOSSARY.md「洗澡」), or her bedtime near (`BEDTIME`)."""

BATH_BEFORE = (timedelta(minutes=60), timedelta(minutes=120))
"""How long before she goes to bed her bath begins: between the two, by five minutes."""

BATH_MINUTES: dict[BathKind, tuple[int, int]] = {"tub": (30, 50), "shower": (15, 25)}
"""How long her bath takes, in the tub or a shower: between the two, by five minutes."""

BEDTIME = timedelta(minutes=30)
"""How long before she goes to bed it is her bedtime."""

WAKE_HOURS = (time(4), time(12))
"""When she may get up: from the first to the second."""

SLEEP_HOURS = (time(20), time(4))
"""When she may go to bed: from the first, through midnight, to the second."""


@dataclass(frozen=True)
class Slot:
    start: time
    end: time
    what: str
    where: str
    kind: SlotKind
    with_: tuple[str, ...] = ()
    """The characters with her, by identifier."""

    def shown(self) -> dict:
        return {
            "start": self.start.strftime("%H:%M"),
            "end": self.end.strftime("%H:%M"),
            "what": self.what,
            "where": self.where,
            "kind": self.kind,
            "with": list(self.with_),
        }


@dataclass(frozen=True)
class Bath:
    """Her bath that night (GLOSSARY.md「洗澡」): when it begins, past midnight if before noon,
    and how many minutes it takes."""

    at: time
    minutes: int

    def starts(self, day: date, zone: tzinfo | None) -> datetime:
        """When it begins, on the night of `day`."""
        night = day + timedelta(days=1) if self.at < time(12) else day
        return datetime.combine(night, self.at, zone)


def draw_bath(sleep: time, kind: BathKind, random: Random) -> Bath:
    """Her bath before she goes to bed at `sleep`, drawn on `random` (`BATH_BEFORE`,
    `BATH_MINUTES`)."""
    low, high = (int(b.total_seconds() // 300) for b in BATH_BEFORE)
    before = (low + random(high - low + 1)) * 5
    shortest, longest = BATH_MINUTES[kind]
    minutes = shortest + random((longest - shortest) // 5 + 1) * 5
    at = (_minutes(sleep) - before) % (24 * 60)
    return Bath(time(at // 60, at % 60), minutes)


@dataclass(frozen=True)
class Schedule:
    day: date
    slots: tuple[Slot, ...]
    twists: tuple[str, ...]
    wake: time | None = None
    """When she gets up that day; None if it was not written."""
    sleep: time | None = None
    """When she goes to bed that night, past midnight if before noon; None if not written."""
    bath: Bath | None = None
    """Her bath that night; None without a time she goes to bed."""
    trip: str | None = None
    """Where she went that day, away from Tokyo, if she did (GLOSSARY.md「纪念品」)."""

    def shown(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "slots": [s.shown() for s in self.slots],
            "twists": list(self.twists),
            "wake": _clock(self.wake),
            "sleep": _clock(self.sleep),
            "bath": None if self.bath is None else {
                "at": _clock(self.bath.at), "minutes": self.bath.minutes,
            },
            "trip": self.trip,
        }

    @property
    def sleeps_after_midnight(self) -> bool:
        return self.sleep is not None and self.sleep < time(12)

    def now(self, at: time) -> Slot | None:
        """The slot she is in at `at`; None between them."""
        return next((s for s in self.slots if s.start <= at < s.end), None)


def parse_schedule(answer: str, characters: Mapping[str, object]) -> list[Slot]:
    """The slots in the model's answer (`{"slots": [...]}`), checked: those that cannot be read,
    are of a kind not known, end before they begin, fall outside the day's hours or overlap one
    kept before them are dropped, and so is anyone with her who is not one of `characters`.
    In order of their start, `MAX_SLOTS` at most; none where the answer is not the JSON asked."""
    start_at, end_at = answer.find("{"), answer.rfind("}")
    if start_at < 0:
        return []
    try:
        written = json.loads(answer[start_at : end_at + 1])
    except ValueError:
        return []
    entries = written.get("slots") if isinstance(written, dict) else None
    if not isinstance(entries, list):
        return []
    read: list[Slot] = []
    for entry in entries:
        slot = _slot(entry, characters)
        if slot is not None:
            read.append(slot)
    read.sort(key=lambda s: s.start)
    kept: list[Slot] = []
    for slot in read:
        if kept and slot.start < kept[-1].end:
            continue
        kept.append(slot)
    return kept[:MAX_SLOTS]


def _slot(entry: object, characters: Mapping[str, object]) -> Slot | None:
    if not isinstance(entry, dict):
        return None
    start, end = _time(entry.get("start")), _time(entry.get("end"))
    what, where, kind = entry.get("what"), entry.get("where", ""), entry.get("kind")
    if start is None or end is None or not isinstance(what, str) or not what.strip():
        return None
    if kind not in get_args(SlotKind) or not isinstance(where, str):
        return None
    midnight = end == time(0)
    if start < DAY_STARTS or (not midnight and end <= start):
        return None
    people = entry.get("with") or []
    with_ = tuple(p for p in people if isinstance(p, str) and p in characters) if isinstance(
        people, list
    ) else ()
    return Slot(
        start, time(23, 59) if midnight else end, what.strip()[:MAX_WHAT],
        where.strip()[:MAX_WHERE], cast(SlotKind, kind), with_,
    )


def parse_rest(answer: str) -> tuple[time | None, time | None]:
    """When she gets up and goes to bed, as the model's answer says (`"wake"`, `"sleep"`);
    either None where it cannot be read or falls outside its hours (`WAKE_HOURS`,
    `SLEEP_HOURS`)."""
    start_at, end_at = answer.find("{"), answer.rfind("}")
    try:
        written = json.loads(answer[start_at : end_at + 1]) if start_at >= 0 else None
    except ValueError:
        written = None
    if not isinstance(written, dict):
        return None, None
    wake, sleep = _time(written.get("wake")), _time(written.get("sleep"))
    if wake is not None and not WAKE_HOURS[0] <= wake <= WAKE_HOURS[1]:
        wake = None
    if sleep is not None and SLEEP_HOURS[1] < sleep < SLEEP_HOURS[0]:
        sleep = None
    return wake, sleep


def bath_began(today: Schedule | None, yesterday: Schedule | None, at: datetime) -> datetime | None:
    """When the bath she is in at `at` (in the producer's time zone) began, by her schedules
    of the day and the day before; None if she is in none."""
    for schedule in (today, yesterday):
        if schedule is None or schedule.bath is None:
            continue
        began = schedule.bath.starts(schedule.day, at.tzinfo)
        if began <= at < began + timedelta(minutes=schedule.bath.minutes):
            return began
    return None


def rest(today: Schedule | None, yesterday: Schedule | None, at: datetime) -> Rest | None:
    """Whether she is asleep at `at` (in the producer's time zone), in the bath, or it is her
    bedtime, by her schedules of the day and the day before; None awake, or not known. Before
    today's is written she gets up as she did yesterday."""
    if bath_began(today, yesterday, at) is not None:
        return "bath"
    now = at.time()
    if yesterday is not None and yesterday.sleeps_after_midnight:
        assert yesterday.sleep is not None
        if now < yesterday.sleep:
            return "bedtime" if _within(now, yesterday.sleep) else None
    known = today or yesterday
    wake = known.wake if known is not None else None
    if wake is not None and now < wake:
        return "asleep"
    if today is not None and today.sleep is not None:
        if today.sleeps_after_midnight:
            return "bedtime" if _within(now, today.sleep) else None
        if now >= today.sleep:
            return "asleep"
        if _within(now, today.sleep):
            return "bedtime"
    return None


def _within(now: time, sleep: time) -> bool:
    """Whether `now` is within `BEDTIME` before `sleep`, midnight between them or not."""
    ahead = (_minutes(sleep) - _minutes(now)) % (24 * 60)
    return 0 < ahead <= BEDTIME.total_seconds() // 60


def _minutes(at: time) -> int:
    return at.hour * 60 + at.minute


def _clock(at: time | None) -> str | None:
    return at.strftime("%H:%M") if at is not None else None


def _time(text: object) -> time | None:
    if not isinstance(text, str) or not re.fullmatch(r"\d{1,2}:\d{2}", text.strip()):
        return None
    hour, minute = (int(x) for x in text.strip().split(":"))
    if hour == 24 and minute == 0:
        return time(0)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return time(hour, minute)


class Schedules:
    """Every character's schedules with every user, in the database at `path` beside the users
    they belong to."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._db = connect(path)
        self._lock = threading.Lock()
        self._clock = clock
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            columns = {row[1] for row in self._db.execute("PRAGMA table_info(schedules)")}
            for column, kind in (
                ("wake", "TEXT"), ("sleep", "TEXT"), ("bath_at", "TEXT"), ("bath_minutes", "INTEGER"),
                ("trip", "TEXT"),
            ):
                if column not in columns:
                    self._db.execute(f"ALTER TABLE schedules ADD COLUMN {column} {kind}")

    def of(self, user_id: str, character_id: str, day: date) -> Schedule | None:
        """Her schedule for `day` with the user; None if none was written, or it could not be."""
        with self._lock:
            row = self._db.execute(
                "SELECT slots, twists, wake, sleep, bath_at, bath_minutes, trip FROM schedules"
                " WHERE user_id = ? AND character_id = ?"
                " AND day = ?",
                (user_id, character_id, day.isoformat()),
            ).fetchone()
        if row is None:
            return None
        slots = tuple(_stored_slot(s) for s in json.loads(row[0]))
        if not slots:
            return None
        return Schedule(
            day, slots, tuple(json.loads(row[1])),
            time.fromisoformat(row[2]) if row[2] else None,
            time.fromisoformat(row[3]) if row[3] else None,
            Bath(time.fromisoformat(row[4]), row[5]) if row[4] and row[5] else None,
            row[6],
        )

    def tried(self, user_id: str, character_id: str, day: date) -> bool:
        """Whether her schedule for `day` was written, or tried and could not be."""
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM schedules WHERE user_id = ? AND character_id = ? AND day = ?",
                (user_id, character_id, day.isoformat()),
            ).fetchone() is not None

    def keep(
        self, user_id: str, character_id: str, day: date, slots: Sequence[Slot],
        twists: Sequence[str], wake: time | None = None, sleep: time | None = None,
        bath: Bath | None = None, trip: str | None = None,
    ) -> None:
        """Keep her schedule for `day`; none (`slots` empty) keeps the day from being tried
        again. One kept already is left as it is."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO schedules"
                " (user_id, character_id, day, slots, twists, created_at, wake, sleep, bath_at,"
                " bath_minutes, trip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, character_id, day.isoformat(),
                    json.dumps([s.shown() for s in slots], ensure_ascii=False),
                    json.dumps(list(twists), ensure_ascii=False),
                    self._clock().isoformat(), _clock(wake), _clock(sleep),
                    _clock(bath.at) if bath else None, bath.minutes if bath else None, trip,
                ),
            )

    def forget(self, user_id: str, character_id: str, day: date) -> None:
        """Forget her schedule for `day`, to write it again."""
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM schedules WHERE user_id = ? AND character_id = ? AND day = ?",
                (user_id, character_id, day.isoformat()),
            )

    def sweep(self, today: date) -> None:
        """Forget the schedules of days more than `KEPT_DAYS` ago."""
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM schedules WHERE day < ?",
                ((today - timedelta(days=KEPT_DAYS)).isoformat(),),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()


WOKEN = (
    "现在（{clock}）你本来已经睡着了，是被制作人的消息吵醒的：照你的性格迷迷糊糊地回，"
    "可以小小抱怨一句或者撒个娇；聊着聊着可以慢慢清醒，困了也可以自然地道晚安。"
)
STILL_UP = (
    "现在（{clock}）本来是你睡觉的时间，你们已经聊了一会儿，你还有点困；"
    "不用再提被吵醒的事，困了可以自然地道晚安。"
)
WOKEN_BY_CALL = (
    "现在（{clock}）本来是你睡觉的时间，你是被制作人的电话吵醒的，声音还有点迷糊；"
    "不用每句都提被吵醒，困了可以自然地道晚安。"
)
BEDTIME_NEAR = "快到你睡觉的时间了，可以带一点困意，也可以提醒制作人早点休息。"
BATH_TOLD = "说到洗澡，只说「在泡澡」「刚洗完」「头发还没干」这种程度，不描写洗澡的过程和身体。"
JUST_OUT = (
    "现在（{clock}）你本来在洗澡，制作人的消息是你洗完出来才看到的：可以先说一句刚洗完，"
    "再自然地接着聊。" + BATH_TOLD
)
IN_THE_TUB = (
    "现在（{clock}）你正在泡澡，一边泡一边和制作人打电话，声音在浴室里有点回响。"
    "就是平常地聊天；泡久了可以说要出来了，自然地道别，或者说出来再聊。" + BATH_TOLD
)


def lines(
    schedule: Schedule | None,
    at: datetime,
    names: Mapping[str, str],
    resting: Rest | None = None,
    woken: bool = True,
    in_call: bool = False,
) -> list[str]:
    """What she is told of her schedule at `at` (in the producer's time zone): the day in a
    sentence and where she is now, and that it is only roughly so; asleep (`resting`, by
    `rest`), that she was woken, by a message (`woken` by this one, or awake a while) or a
    call; in the bath (GLOSSARY.md「洗澡」), that she is talking from the tub in a call, or
    else saw the message as she came out; near her bedtime, that it is. Nothing without a
    schedule, unless she is asleep or in the bath."""
    clock = at.strftime("%H:%M")
    if resting == "asleep":
        woke = WOKEN_BY_CALL if in_call else WOKEN if woken else STILL_UP
        return [woke.format(clock=clock)]
    if resting == "bath":
        bath = (IN_THE_TUB if in_call else JUST_OUT).format(clock=clock)
        day = said(schedule, names)
        return [f"你今天大概的安排：{day}", bath] if day else [bath]
    if schedule is None:
        return []
    day = "；".join(_said(s, names) for s in schedule.slots)
    if schedule.sleep is not None:
        day += f"；打算 {schedule.sleep.strftime('%H:%M')} 睡觉"
    current = schedule.now(at.time())
    if current is not None:
        now = f"现在（{clock}）你大概在：{_said(current, names, with_time=False)}。"
    elif at.time() < schedule.slots[0].start:
        now = f"现在（{clock}）今天的安排还没开始。"
    else:
        now = f"现在（{clock}）你没什么安排。"
    told = [
        f"你今天大概的安排：{day}。",
        now,
        "这是大概的安排，可以有出入。被问到在干嘛时自然地说，不要像念行程表一样一条条背出来。",
    ]
    if resting == "bedtime":
        told.append(BEDTIME_NEAR)
    return told


def said(schedule: Schedule | None, names: Mapping[str, str]) -> str | None:
    """Her schedule for a day in a sentence; None without one."""
    if schedule is None:
        return None
    return "；".join(_said(s, names) for s in schedule.slots) + "。"


def summary(schedule: Schedule | None, names: Mapping[str, str]) -> str | None:
    """Her schedule for a day in a sentence, for her diary of it; None without one."""
    day = said(schedule, names)
    return day and f"你今天原本的安排（只是安排，不一定都照着做了）：{day}"


def _said(slot: Slot, names: Mapping[str, str], with_time: bool = True) -> str:
    said = slot.what
    if slot.with_:
        said += "（和" + "、".join(names.get(c, c) for c in slot.with_) + "）"
    if slot.where:
        said += f"，在{slot.where}"
    if with_time:
        said = f"{slot.start.strftime('%H:%M')}–{slot.end.strftime('%H:%M')} {said}"
    return said


def _stored_slot(stored: dict) -> Slot:
    return Slot(
        time.fromisoformat(stored["start"]), time.fromisoformat(stored["end"]), stored["what"],
        stored["where"], stored["kind"], tuple(stored.get("with", [])),
    )
