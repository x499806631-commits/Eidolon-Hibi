"""Anniversaries (纪念日): the day the producer and an idol first spoke, and every hundred days
and every whole year since.

The day they first spoke is the first thing still kept in their conversations, a message, an
event or a call, in the producer's own time zone: the day the statistics page counts「认识了
多久」from (`Store.first_records`), so the calendar and the statistics agree on it.

That day is the first of the hundred, so the hundredth is `HUNDRED - 1` days after it. A year
falls on its same month and day; February 29 falls on February 28 in a year that has no
February 29. Nothing here reads or writes beyond what it is given.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from eidolon.special_days import MENTION

HUNDRED = 100
"""Every this many days of knowing each other is an anniversary."""


@dataclass(frozen=True)
class Occurrence:
    """How long they have known each other on a day: which day it is, the day they first spoke
    counting as the first, and the whole years it makes up (0 when it is no year)."""

    days: int
    years: int = 0


def day_of(created_at: datetime, timezone: str) -> date:
    """The producer's own day `created_at` fell on, as the calendar's days are."""
    return created_at.astimezone(ZoneInfo(timezone)).date()


def of(start: date, day: date) -> Occurrence | None:
    """`day` as an anniversary of `start`: the hundredth day, or a whole year, or both at
    once; None on any other day, and before they first spoke."""
    if day < start:
        return None
    days = (day - start).days + 1
    years = day.year - start.year if day == _year_day(start, day.year) and day.year > start.year else 0
    if days % HUNDRED != 0 and years == 0:
        return None
    return Occurrence(days, years)


def lines(start: date | None, today: date) -> list[str]:
    """What she is told of `today` being an anniversary of `start`, if it is: the sentence,
    and the calendar's line on when to bring it up; nothing on an ordinary day."""
    found = None if start is None else of(start, today)
    if found is None:
        return []
    said = []
    if found.days % HUNDRED == 0:
        said.append(f"今天是你和制作人认识的第 {found.days} 天。")
    if found.years:
        said.append(f"今天是你和制作人认识的第 {found.years} 周年。")
    return [*said, MENTION]


def described(firsts: Mapping[str, datetime], year: int, timezone: str) -> list[dict]:
    """How the calendar page reads the `year` of the anniversaries in `firsts` (each
    character's first kept thing): the character, the day, and what it is, by the days."""
    found = []
    for character_id, created_at in firsts.items():
        start = day_of(created_at, timezone)
        for day in _days_in(start, year):
            occurrence = of(start, day)
            if occurrence is None:
                continue
            found.append({
                "character_id": character_id,
                "month": day.month,
                "day": day.day,
                "label": _label(occurrence),
            })
    found.sort(key=lambda a: (a["month"], a["day"], a["character_id"]))
    return found


def _label(occurrence: Occurrence) -> str:
    """What the calendar calls it: 认识第 100 天, or 认识 1 周年, one of them if both fall."""
    if occurrence.days % HUNDRED == 0:
        return f"认识第 {occurrence.days} 天"
    return f"认识 {occurrence.years} 周年"


def _days_in(start: date, year: int) -> list[date]:
    """The days of `year` that are an anniversary of `start`, earliest first."""
    days = []
    if year > start.year:
        days.append(_year_day(start, year))
    first, last = date(year, 1, 1), date(year, 12, 31)
    low = max(HUNDRED, (first - start).days + 1)
    high = (last - start).days + 1
    for count in range(low, high + 1):
        if count % HUNDRED == 0:
            days.append(start + timedelta(days=count - 1))
    return sorted(days)


def _year_day(start: date, year: int) -> date:
    """The day in `year` its same month and day falls on: February 29 falls on February 28 in
    a year that has no February 29, as the producer's birthday does."""
    try:
        return date(year, start.month, start.day)
    except ValueError:
        return date(year, 2, 28)
