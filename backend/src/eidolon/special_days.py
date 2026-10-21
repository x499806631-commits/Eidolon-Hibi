"""Special days (日历): the idols' birthdays and the holidays, loaded from the calendar
beside the character configuration, and what a turn on one tells her about it.

Like the gift catalog it is configuration, not data: kept in the repository beside the
characters, changed by neither `.env` nor the admin console. Without the file there are
no special days; anything wrong in it keeps the backend from starting
(`SpecialDaysError`).

The day is the producer's, in their own time zone (`producer.local_date`), as for their
birthday. On it she is told whose birthday it is, her own or another idol's, and which
holiday or season it is, with a line on what people do then, to bring up when it fits.
"""

import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from eidolon.characters import Character
from eidolon.producer import Birthday, is_birthday, is_day_of_the_year

CALENDAR_FILE = "calendar.toml"
"""The file's name, in the directory of the character configuration."""

_DAY = re.compile(r"(\d{2})-(\d{2})")

_OWN_BIRTHDAY = "今天是你自己的生日。"
_MENTION = "这些是今天的日子：聊到合适的时候可以自然地提起，先回应制作人说的话，不要每句都提。"


class SpecialDaysError(RuntimeError):
    """The calendar is broken."""


MonthDay = tuple[int, int]


@dataclass(frozen=True)
class Holiday:
    name: str
    note: str = ""
    icon: str = ""
    """A little picture of it on the calendar page, an emoji; none if empty."""
    day: MonthDay | None = None
    """Its day every year; None when it moves from year to year (`by_year`)."""
    by_year: Mapping[int, MonthDay] = field(default_factory=dict)
    """Its first day in each year listed, for a holiday on a lunar date and the like."""
    end: MonthDay | None = None
    """The last day of a holiday that lasts, such as the cherry blossom season."""

    def first_day(self, year: int) -> MonthDay | None:
        return self.by_year.get(year) if self.day is None else self.day

    def on(self, today: date) -> bool:
        start = self.first_day(today.year)
        if start is None:
            return False
        if self.end is None:
            return (today.month, today.day) == start
        return start <= (today.month, today.day) <= self.end


@dataclass(frozen=True)
class SpecialDays:
    birthdays: Mapping[str, Birthday]
    """Each idol's birthday, by character identifier."""
    holidays: Sequence[Holiday]

    def birthdays_on(self, today: date) -> list[str]:
        """The characters whose birthday `today` is, in calendar order."""
        return [id for id, birthday in self.birthdays.items() if is_birthday(birthday, today)]

    def holidays_on(self, today: date) -> list[Holiday]:
        return [h for h in self.holidays if h.on(today)]

    def greets(self, character_id: str, today: date) -> bool:
        """Whether `today` is a day the character comes to say something first for
        (`reach_outs`): her own birthday, or a holiday of one day; a season is none."""
        birthday = self.birthdays.get(character_id)
        if birthday is not None and is_birthday(birthday, today):
            return True
        return any(h.end is None and h.on(today) for h in self.holidays)

    def described(self, year: int) -> dict:
        """As the calendar page reads it for `year`: every idol's birthday, and the holidays
        that year has, each with its first day and, if it lasts, its last."""
        holidays = []
        for h in self.holidays:
            if (start := h.first_day(year)) is None:
                continue
            holidays.append({
                "name": h.name, "note": h.note, "icon": h.icon, "start": _described_day(start),
                "end": _described_day(h.end) if h.end else None,
            })
        return {
            "birthdays": [
                {"character_id": id, "month": b.month, "day": b.day} for id, b in self.birthdays.items()
            ],
            "holidays": holidays,
        }

    def prompt_lines(self, character_id: str, names: Mapping[str, str], today: date) -> list[str]:
        """What a turn on `today` tells the character `character_id` about the day: whose
        birthday it is, by `names` (character identifier to name), and which holidays;
        nothing on an ordinary day."""
        lines = []
        for id in self.birthdays_on(today):
            if id == character_id:
                lines.append(_OWN_BIRTHDAY)
            elif id in names:
                lines.append(f"今天是{names[id]}的生日。")
        for holiday in self.holidays_on(today):
            lead = f"这段时间是{holiday.name}。" if holiday.end else f"今天是{holiday.name}。"
            lines.append(lead + holiday.note)
        if lines:
            lines.append(_MENTION)
        return lines


def _described_day(day: MonthDay) -> dict:
    return {"month": day[0], "day": day[1]}


def special_days_path(characters_path: Path) -> Path:
    """Where the calendar is: beside the character configuration."""
    return characters_path.parent / CALENDAR_FILE


def load_special_days(path: Path, characters: Iterable[Character]) -> SpecialDays | None:
    """The calendar at `path`, its birthdays only of `characters`; None if there is none.
    Raises SpecialDaysError if it is broken."""
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SpecialDaysError(f"日历 {path} 格式有误：{e}") from None
    known = {c.id for c in characters}
    birthdays: dict[str, Birthday] = {}
    for entry in _entries(config, "birthdays", path):
        id = entry.get("character")
        if id not in known:
            raise SpecialDaysError(f"日历 {path} 里的生日属于不存在的角色 {id!r}")
        if id in birthdays:
            raise SpecialDaysError(f"日历 {path} 里角色 {id} 的生日写了两次")
        month, day = _month_day(entry.get("date"), f"角色 {id} 的生日", path)
        birthdays[id] = Birthday(month, day)
    holidays = [_holiday(entry, path) for entry in _entries(config, "holidays", path)]
    return SpecialDays(birthdays, holidays)


def _entries(config: dict, key: str, path: Path) -> list[dict]:
    entries = config.get(key, [])
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise SpecialDaysError(f"日历 {path} 里的 {key} 应当是 [[{key}]] 段")
    return entries


def _month_day(value: object, what: str, path: Path) -> MonthDay:
    match = _DAY.fullmatch(value) if isinstance(value, str) else None
    if match is None or not is_day_of_the_year(int(match[1]), int(match[2])):
        raise SpecialDaysError(f"日历 {path} 里{what}的日期 {value!r} 应当写成 \"MM-DD\"")
    return int(match[1]), int(match[2])


def _holiday(entry: dict, path: Path) -> Holiday:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SpecialDaysError(f"日历 {path} 里有节日没写 name")
    note = entry.get("note", "")
    if not isinstance(note, str):
        raise SpecialDaysError(f"日历 {path} 里节日 {name} 的 note 应当是字符串")
    icon = entry.get("icon", "")
    if not isinstance(icon, str):
        raise SpecialDaysError(f"日历 {path} 里节日 {name} 的 icon 应当是字符串")
    what = f"节日 {name}"
    if ("date" in entry) == ("dates" in entry):
        raise SpecialDaysError(f"日历 {path} 里{what}要在 date 和 dates 里写且只写一项")
    day = _month_day(entry["date"], what, path) if "date" in entry else None
    by_year: dict[int, MonthDay] = {}
    if "dates" in entry:
        dates = entry["dates"]
        if not isinstance(dates, dict) or not dates:
            raise SpecialDaysError(f"日历 {path} 里{what}的 dates 应当按年份列出日期")
        for year, value in dates.items():
            if not year.isdigit():
                raise SpecialDaysError(f"日历 {path} 里{what}的 dates 里 {year!r} 不是年份")
            by_year[int(year)] = _month_day(value, f"{what} {year} 年", path)
    end = _month_day(entry["end"], f"{what}的结束", path) if "end" in entry else None
    if end is not None and any(start > end for start in [day, *by_year.values()] if start):
        raise SpecialDaysError(f"日历 {path} 里{what}的结束早于开始（不能跨年）")
    return Holiday(name, note, icon, day, by_year, end)
