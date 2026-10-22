"""The calendar (日历) beside the character configuration: each turn on an idol's birthday
or a holiday, by the producer's own date, tells her what day it is, and nothing on an
ordinary day; the calendar page reads a year's birthdays and holidays; a calendar that is
broken keeps the backend from starting, and the repository's own calendar loads against
the repository's characters."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from eidolon.characters import load_characters
from eidolon.special_days import CALENDAR_FILE, SpecialDaysError, load_special_days

from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import FakeLLM
from tests.test_producer_profile import REPLY, system_prompt

REPO_CHARACTERS = Path(__file__).parents[2] / "characters" / "characters.toml"

CALENDAR = """
[[birthdays]]
character = "mano"
date = "04-25"

[[birthdays]]
character = "kogane"
date = "02-25"

[[holidays]]
name = "情人节"
date = "02-14"
note = "女生会送巧克力。"
icon = "🍫"

[[holidays]]
name = "樱花季"
date = "03-25"
end = "04-10"

[[holidays]]
name = "春节"
dates = { 2026 = "02-17" }
"""


def noon_in_shanghai(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 4, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "told", "not_told"),
    [
        (date(2026, 4, 25), ["今天是你自己的生日。"], ["樱花季"]),
        (date(2026, 2, 25), ["今天是月岡恋鐘的生日。"], ["你自己的生日"]),
        (date(2026, 2, 14), ["今天是情人节。女生会送巧克力。"], []),
        (date(2026, 4, 1), ["这段时间是樱花季。"], []),
        (date(2026, 2, 17), ["今天是春节。"], []),
        (date(2027, 2, 17), [], ["春节", "这些是今天的日子"]),
    ],
    ids=["her-birthday", "another-idols-birthday", "holiday", "season", "lunar-listed", "lunar-unlisted-year"],
)
async def test_a_turn_on_a_special_day_tells_her_what_day_it_is(
    make_client: ClientFactory, characters: CharacterConfig,
    day: date, told: list[str], not_told: list[str],
):
    characters.write(CALENDAR_FILE, CALENDAR)
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: noon_in_shanghai(day))

    prompt = await system_prompt(client, llm)  # with Mano

    for line in told:
        assert line in prompt, line
    if told:
        assert "不要每句都提" in prompt
    for words in not_told:
        assert words not in prompt, words


async def test_the_calendar_page_reads_a_years_birthdays_and_holidays(
    make_client: ClientFactory, characters: CharacterConfig,
):
    without = make_client(FakeLLM())
    assert (await without.get("/api/calendar", params={"year": 2026})).json() == {
        "birthdays": [], "holidays": [], "anniversaries": [], "promises": [],
    }
    characters.write(CALENDAR_FILE, CALENDAR)
    client = make_client(FakeLLM())

    this_year = (await client.get("/api/calendar", params={"year": 2026})).json()
    next_year = (await client.get("/api/calendar", params={"year": 2027})).json()

    assert this_year["birthdays"] == [
        {"character_id": "mano", "month": 4, "day": 25},
        {"character_id": "kogane", "month": 2, "day": 25},
    ]
    assert this_year["holidays"] == [
        {"name": "情人节", "note": "女生会送巧克力。", "icon": "🍫",
         "start": {"month": 2, "day": 14}, "end": None},
        {"name": "樱花季", "note": "", "icon": "",
         "start": {"month": 3, "day": 25}, "end": {"month": 4, "day": 10}},
        {"name": "春节", "note": "", "icon": "", "start": {"month": 2, "day": 17}, "end": None},
    ]
    assert [h["name"] for h in next_year["holidays"]] == ["情人节", "樱花季"]


@pytest.mark.parametrize(
    ("calendar", "complaint"),
    [
        ('[[birthdays]]\ncharacter = "nobody"\ndate = "01-01"\n', "不存在的角色"),
        ('[[birthdays]]\ncharacter = "mano"\ndate = "4月25日"\n', "MM-DD"),
        ('[[holidays]]\nname = "七夕"\ndate = "07-07"\ndates = { 2026 = "08-19" }\n', "只写一项"),
        ('[[holidays]]\nname = "跨年季"\ndate = "12-25"\nend = "01-03"\n', "不能跨年"),
    ],
    ids=["unknown-character", "bad-date", "date-and-dates", "across-the-year"],
)
def test_a_broken_calendar_is_refused(
    characters: CharacterConfig, calendar: str, complaint: str
):
    characters.write(CALENDAR_FILE, calendar)

    with pytest.raises(SpecialDaysError, match=complaint):
        load_special_days(
            characters.directory / CALENDAR_FILE, load_characters(characters.path).values()
        )


def test_the_repositorys_calendar_has_every_idols_birthday():
    repo = load_characters(REPO_CHARACTERS)

    days = load_special_days(REPO_CHARACTERS.parent / CALENDAR_FILE, repo.values())

    assert days is not None
    assert set(days.birthdays) == set(repo)
