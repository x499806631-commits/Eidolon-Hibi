"""The anniversaries of knowing each other (纪念日): whether a day is the hundredth day or a
whole year since the producer and an idol first spoke, what a turn tells her of it, and what
the calendar lists for a year; never before the day they first spoke."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sqlite3

from eidolon import anniversaries
from eidolon.config import Settings
from eidolon.special_days import MENTION
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_delete_account import user_id
from tests.test_producer_profile import REPLY, system_prompt

FIRST = date(2026, 1, 1)
"""They first spoke that day; the hundredth is `HUNDRED - 1` days after it, 4 月 10 日."""


def seed_first_contact(database: Path, user: str, day: date) -> None:
    """A message from `day` between `user` and Mano, as the first thing kept between them."""
    when = f"{day}T00:00:00+00:00"
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO conversations (id, user_id, character_id, created_at) VALUES (?, ?, ?, ?)",
            ("seeded", user, "mano", when),
        )
        db.execute(
            "INSERT INTO messages (conversation_id, role, text, created_at) VALUES (?, ?, ?, ?)",
            ("seeded", "user", "在吗", when),
        )


def days_known(start: date, day: date) -> int:
    return (day - start).days + 1


def test_the_hundredth_day_and_the_whole_years():
    hundredth = FIRST + timedelta(days=anniversaries.HUNDRED - 1)

    assert anniversaries.of(FIRST, FIRST) is None  # 第一天不是纪念日
    assert anniversaries.of(FIRST, hundredth) == anniversaries.Occurrence(100)
    assert anniversaries.of(FIRST, FIRST + timedelta(days=1)) is None
    assert anniversaries.of(FIRST, hundredth + timedelta(days=1)) is None
    assert anniversaries.of(FIRST, FIRST + timedelta(days=anniversaries.HUNDRED * 2 - 1)) == (
        anniversaries.Occurrence(200)
    )

    # 周年也算：既是第 N 天，又是整年
    for year, made in ((2027, 1), (2028, 2)):
        day = date(year, 1, 1)
        assert anniversaries.of(FIRST, day) == anniversaries.Occurrence(days_known(FIRST, day), made)


def test_a_year_that_starts_on_february_29_falls_on_february_28_in_a_common_year():
    start = date(2024, 2, 29)
    leap_day = date(2025, 2, 28)
    assert anniversaries.of(start, leap_day) == anniversaries.Occurrence(
        days_known(start, leap_day), 1
    )
    assert anniversaries.of(start, date(2025, 3, 1)) is None  # 只有平年的 2 月 28 日算
    fourth = date(2028, 2, 29)
    assert anniversaries.of(start, fourth) == anniversaries.Occurrence(days_known(start, fourth), 4)


def test_nothing_before_the_day_they_first_spoke():
    assert anniversaries.of(FIRST, date(2025, 12, 31)) is None


def test_what_she_is_told_on_the_day():
    hundredth = FIRST + timedelta(days=anniversaries.HUNDRED - 1)

    assert anniversaries.lines(FIRST, hundredth) == [
        f"今天是你和制作人认识的第 {anniversaries.HUNDRED} 天。",
        MENTION,
    ]
    assert anniversaries.lines(FIRST, date(2027, 1, 1)) == ["今天是你和制作人认识的第 1 周年。", MENTION]
    assert anniversaries.lines(FIRST, FIRST + timedelta(days=3)) == []  # 平常的日子什么都不加
    assert anniversaries.lines(None, FIRST) == []  # 还没说过话


def test_the_calendar_lists_this_years_anniversaries_by_the_days():
    firsts = {"mano": datetime(2026, 1, 1, 3, tzinfo=UTC), "hiori": datetime(2026, 3, 1, 3, tzinfo=UTC)}

    this_year = anniversaries.described(firsts, 2026, "Asia/Shanghai")

    assert {"character_id": "mano", "month": 4, "day": 10, "label": "认识第 100 天"} in this_year
    assert {"character_id": "hiori", "month": 6, "day": 8, "label": "认识第 100 天"} in this_year
    assert not any(a["label"].endswith("周年") for a in this_year)  # 第一年没有周年
    assert this_year == sorted(this_year, key=lambda a: (a["month"], a["day"], a["character_id"]))

    next_year = anniversaries.described(firsts, 2027, "Asia/Shanghai")

    assert {"character_id": "mano", "month": 1, "day": 1, "label": "认识 1 周年"} in next_year
    assert {"character_id": "hiori", "month": 3, "day": 1, "label": "认识 1 周年"} in next_year
    five_hundredth = FIRST + timedelta(days=500 - 1)
    assert {
        "character_id": "mano",
        "month": five_hundredth.month,
        "day": five_hundredth.day,
        "label": "认识第 500 天",
    } in next_year


def test_the_calendar_takes_the_producers_own_day():
    # 2026-01-01 的 UTC 01:30 在上海已是 1 月 1 日，在檀香山还是 2025 年 12 月 31 日
    moment = datetime(2026, 1, 1, 1, 30, tzinfo=UTC)
    assert anniversaries.day_of(moment, "Asia/Shanghai") == date(2026, 1, 1)
    assert anniversaries.day_of(moment, "Pacific/Honolulu") == date(2025, 12, 31)

    # 起步日差一天，第一百天就差一天，日历上也就落在不同的月份
    firsts = {"mano": moment}
    shanghai = anniversaries.described(firsts, 2026, "Asia/Shanghai")
    honolulu = anniversaries.described(firsts, 2026, "Pacific/Honolulu")
    assert (shanghai[0]["month"], shanghai[0]["day"]) != (honolulu[0]["month"], honolulu[0]["day"])


async def test_a_turn_on_an_anniversary_tells_her(make_client: ClientFactory, settings: Settings):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: datetime(2026, 4, 10, 4, 30, tzinfo=UTC))
    # 消息的 created_at 由数据库按真实时间写，注入的时钟管不到，所以直接种一条在 1 月 1 日：
    # 那天算第一天，4 月 10 日就是第 100 天。
    seed_first_contact(settings.database_path, user_id(settings.database_path, "producer"), FIRST)

    prompt = await system_prompt(client, llm)

    assert "今天是你和制作人认识的第 100 天。" in prompt
    assert MENTION in prompt
