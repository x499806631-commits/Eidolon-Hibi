"""When letters come (GLOSSARY.md「来信」): planned once for each day of the producer's (here
UTC, `Etc/GMT`) for each current conversation with a turn, drawn on a fixed random source
(`FakeRandom`); written ahead and kept, but not there till they come. Now and then in the
day's hours; for the producer's birthday, written the evening before and coming at midnight;
for a holiday of the calendar, coming in the evening, telling how she spent it. None planned
once the hours are over, none at all where the deployer has them off.

The clock is set to times of today's date in UTC, as the store writes when turns were
recorded at the time it is, so that the last contact falls on the same day."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx

from eidolon.config import Settings
from eidolon.engine import letter_rules
from eidolon.letters import CHANCES, PLACES
from eidolon.special_days import CALENDAR_FILE
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import FakeClock, FakeLLM, FakeRandom, context_of
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
LETTER = ["<ja>プロデューサーさんへ</ja><zh>致制作人</zh><ja>真乃より</ja><zh>真乃</zh>"]
MIDDLE = PLACES // 2
MISS = 1
"""A chance of an ordinary letter missed."""


def today_at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    now = datetime.now(UTC) + timedelta(days=days)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


async def talked(
    make_client: ClientFactory, settings: Settings, at: datetime, *numbers: int,
    profile: dict | None = None,
) -> tuple[httpx.AsyncClient, FakeLLM, FakeClock]:
    llm = FakeLLM(REPLY, LETTER)
    clock = FakeClock(at)
    client = make_client(llm, clock=clock, random=FakeRandom(*numbers), settings=settings)
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT", **(profile or {})})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    return client, llm, clock


async def letters(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    return [(x["character_id"], x["occasion"]) for x in (await client.get("/api/letters")).json()]


async def test_now_and_then_she_writes_ahead_of_a_time_drawn_in_the_days_hours(
    make_client: ClientFactory, settings: Settings
):
    # A chance hit (0), then halfway through 09:00–21:00: coming at 15:00, written at 14:30.
    client, llm, clock = await talked(
        make_client, replace(settings, letters=True), today_at(8), 0, MIDDLE
    )
    run = backend_of(client).letters.run_due

    await run()
    before = len(llm.prompts)
    clock.now = today_at(14, 29)
    await run()
    early = len(llm.prompts)
    clock.now = today_at(14, 30)
    await run()
    written = len(llm.prompts)
    not_yet = await letters(client)
    clock.now = today_at(15)
    await run()

    assert (early - before, written - before, not_yet) == (0, 1, [])
    assert await letters(client) == [("mano", "ordinary")]
    assert letter_rules("ordinary") in context_of(llm.prompts[-1])
    assert len(llm.prompts) == written  # written once


async def test_a_chance_missed_is_no_letter_that_day_and_not_drawn_again(
    make_client: ClientFactory, settings: Settings
):
    assert CHANCES["trust"] > MISS  # affinity is off here: she writes as in trust
    client, _, clock = await talked(
        make_client, replace(settings, letters=True), today_at(8), MISS
    )
    run = backend_of(client).letters.run_due

    await run()
    clock.now = today_at(20)
    await run()  # nothing more drawn: the fake source has no numbers left

    assert await letters(client) == []


async def test_his_birthday_letter_is_written_the_evening_before_and_comes_at_midnight(
    make_client: ClientFactory, settings: Settings
):
    tomorrow = today_at(0, days=1)
    client, llm, clock = await talked(
        make_client, replace(settings, letters=True), today_at(12), MISS,
        profile={"birthday": {"month": tomorrow.month, "day": tomorrow.day}},
    )
    run = backend_of(client).letters.run_due

    await run()
    before = len(llm.prompts)
    clock.now = today_at(20, 59)
    await run()
    early = len(llm.prompts)
    clock.now = today_at(21)
    await run()
    written = len(llm.prompts)
    not_yet = await letters(client)
    clock.now = tomorrow
    await run()

    assert (early - before, written - before, not_yet) == (0, 1, [])
    assert await letters(client) == [("mano", "producer_birthday")]
    assert letter_rules("producer_birthday") in context_of(llm.prompts[-1])


async def test_on_a_holiday_she_writes_in_the_evening_how_she_spent_it(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    day = today_at(0)
    characters.write(
        CALENDAR_FILE,
        f'[[holidays]]\nname = "七夕"\ndate = "{day:%m-%d}"\nnote = "牛郎织女相会的日子。"\n',
    )
    # Halfway through 19:00–22:00: coming at 20:30, written at 19:30.
    client, llm, clock = await talked(
        make_client, replace(settings, letters=True), today_at(8), MIDDLE
    )
    run = backend_of(client).letters.run_due

    await run()
    clock.now = today_at(19, 30)
    await run()
    clock.now = today_at(20, 30)

    assert await letters(client) == [("mano", "holiday")]
    told = context_of(llm.prompts[-1])
    assert "今天是七夕。牛郎织女相会的日子。现在是晚上" in told
    assert "现在是" in told and "20:30" in told  # written as of when it comes


async def test_none_is_planned_once_the_days_hours_are_over(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, replace(settings, letters=True), today_at(22), 0)

    await backend_of(client).letters.run_due()

    assert await letters(client) == []


async def test_none_where_the_deployer_has_them_off(make_client: ClientFactory, settings: Settings):
    client, _, clock = await talked(make_client, settings, today_at(8))

    await backend_of(client).letters.run_due()
    clock.now = today_at(20)
    await backend_of(client).letters.run_due()

    assert await letters(client) == []
