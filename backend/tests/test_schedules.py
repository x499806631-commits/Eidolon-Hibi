"""Her schedule (GLOSSARY.md「行程」): written anew each day for the idols the producer has been
in touch with lately, from the early morning of the producer's day, with a twist or two drawn
for it; what the model writes checked, a day it fails kept as none; read in her turns as the
day in a sentence and where she is now, the prompt as it was without one; and hers to read over
HTTP. The producer's time zone is UTC (`Etc/GMT`), the clock set to times of today's date, as
the store writes when turns were recorded at the time it is."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame
from pipecat.tests.utils import SleepFrame

from eidolon.config import Settings
from eidolon.engine import ANSWERING, SCHEDULE_RULES, WANTS_TO_PLAN
from eidolon.llm import LLMError
from eidolon.schedules import (
    TWISTS,
    Schedule,
    Schedules,
    Slot,
    lines,
    parse_rest,
    parse_schedule,
    rest,
)
from tests.conftest import CharacterConfig, ClientFactory, database_in
from tests.fakes import FakeClock, FakeLLM, FakeRandom, context_of
from tests.test_call_turns import CallUnderTest
from tests.test_call_turns import mano_call  # noqa: F401  # the fixture
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation
from tests.test_world_setting import write_world

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
CHARACTERS = {"mano": object(), "kogane": object()}
PLAN = (
    '{"slots": ['
    '{"start": "09:00", "end": "12:00", "what": "舞蹈课", "where": "事务所", "kind": "lesson", "with": []},'
    '{"start": "14:00", "end": "17:00", "what": "逛文具店", "where": "车站前", "kind": "private",'
    ' "with": ["kogane", "nobody"]}'
    "]}"
)


def today_at(hour: int, minute: int = 0) -> datetime:
    return datetime.now(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_what_the_model_writes_is_checked_slot_by_slot():
    good = {"start": "09:00", "end": "10:00", "what": "练习", "where": "", "kind": "lesson"}
    answer = {"slots": [
        {**good, "start": "13:00", "end": "15:00", "what": "拍摄", "kind": "work"},
        good,
        {**good, "start": "09:30", "end": "11:00"},  # overlaps the one before it
        {**good, "start": "05:00", "end": "06:30"},  # before the day's hours
        {**good, "start": "16:00", "end": "15:00"},  # ends before it begins
        {**good, "start": "17:00", "end": "18:00", "kind": "dating"},  # no such kind
        {**good, "start": "22:00", "end": "24:00", "kind": "rest", "with": ["kogane", "x"]},
        "not a slot",
    ]}

    slots = parse_schedule(json.dumps(answer), CHARACTERS)

    assert [(s.start, s.what) for s in slots] == [
        (time(9), "练习"), (time(13), "拍摄"), (time(22), "练习"),
    ]
    assert slots[-1].end == time(23, 59) and slots[-1].with_ == ("kogane",)
    assert parse_schedule("没有 JSON", CHARACTERS) == []
    assert parse_schedule('{"slots": "x"}', CHARACTERS) == []


def test_she_is_told_the_day_in_a_sentence_and_where_she_is_now():
    schedule = Schedule(date(2026, 10, 22), (
        Slot(time(9), time(12), "舞蹈课", "事务所", "lesson"),
        Slot(time(14), time(17), "逛文具店", "车站前", "private", ("kogane",)),
    ), ())
    names = {"kogane": "月岡恋鐘"}
    on = date(2026, 10, 22)

    during = lines(schedule, datetime.combine(on, time(15, 5)), names)
    between = lines(schedule, datetime.combine(on, time(12, 30)), names)
    early = lines(schedule, datetime.combine(on, time(7)), names)

    assert during[0] == (
        "你今天大概的安排：09:00–12:00 舞蹈课，在事务所；14:00–17:00 逛文具店（和月岡恋鐘），在车站前。"
    )
    assert during[1] == "现在（15:05）你大概在：逛文具店（和月岡恋鐘），在车站前。"
    assert between[1] == "现在（12:30）你没什么安排。"
    assert early[1] == "现在（07:00）今天的安排还没开始。"
    assert "不要像念行程表" in during[2]
    assert lines(None, datetime.combine(on, time(9)), names) == []


async def talked(
    make_client: ClientFactory, settings: Settings, *replies, numbers=(1, 0, 1),
) -> tuple[httpx.AsyncClient, FakeLLM, FakeClock]:
    llm = FakeLLM(REPLY, *replies, REPLY)
    clock = FakeClock(today_at(4))
    client = make_client(
        llm, clock=clock, random=FakeRandom(*numbers), settings=replace(settings, schedules=True)
    )
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    client.conversation_id = conversation  # type: ignore[attr-defined]
    return client, llm, clock


async def schedule(client: httpx.AsyncClient, character: str = "mano") -> dict | None:
    res = await client.get(f"/api/characters/{character}/schedule")
    assert res.status_code == 200, res.text
    return res.json()


async def test_hers_for_today_is_written_once_from_the_early_morning(
    make_client: ClientFactory, settings: Settings
):
    # One twist (1), the first of them (0); no trip (1).
    client, llm, clock = await talked(make_client, settings, [PLAN])
    writer = backend_of(client).schedule_writer

    await writer.run_due()
    too_early = len(llm.prompts)
    clock.now = today_at(6)
    await writer.run_due()
    await writer.run_due()

    assert too_early == 1  # the turn's only
    assert len(llm.prompts) == 2  # written once
    prompt = llm.prompts[-1]
    told = "\n".join(m.content for m in prompt if m.role == "system")
    assert SCHEDULE_RULES in told and TWISTS[0] in told
    assert f"星期{'一二三四五六日'[today_at(6).weekday()]}" in told
    assert prompt[-1].content == WANTS_TO_PLAN
    written = await schedule(client)
    assert written is not None
    assert [(s["what"], s["with"]) for s in written["slots"]] == [
        ("舞蹈课", []), ("逛文具店", ["kogane"]),
    ]
    assert written["twists"] == [TWISTS[0]]


async def test_a_day_the_model_fails_is_none_and_not_tried_again(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock = await talked(make_client, settings, LLMError("挂了"), numbers=(0, 1))
    writer = backend_of(client).schedule_writer
    clock.now = today_at(6)

    await writer.run_due()
    await writer.run_due()

    assert len(llm.prompts) == 2
    assert await schedule(client) is None


async def test_off_none_is_written(make_client: ClientFactory, settings: Settings):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=FakeClock(today_at(6)), settings=settings)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")

    await backend_of(client).schedule_writer.run_due()

    assert len(llm.prompts) == 1


async def test_her_turn_tells_where_she_is_now_and_without_one_is_as_it_was(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock = await talked(make_client, settings, [PLAN])
    before = context_of(llm.prompts[-1])
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()
    clock.now = today_at(15)
    await send_turn(client, client.conversation_id, "在干嘛")  # type: ignore[attr-defined]

    told = context_of(llm.prompts[-1])
    assert "现在（15:00）你大概在：逛文具店" in told
    assert "大概的安排" not in before


async def test_a_live_of_hers_that_day_is_to_be_in_it(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    write_world(characters)
    llm = FakeLLM(REPLY, [PLAN])
    client = make_client(llm, settings=replace(settings, schedules=True))
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "嗯")
    backend = backend_of(client)
    conversation = await backend.store.get_conversation(conversation_id)
    assert conversation is not None

    await backend.engine.write_schedule(conversation, llm, date(2026, 11, 7), [], "排练。")

    told = "\n".join(m.content for m in llm.prompts[-1] if m.role == "system")
    assert "今天你有 live：「THE IDOLM@STER SHINY COLORS 8thLIVE」，在Kアリーナ横浜。必须排进去。" in told
    assert "你昨天的安排：排练。" in told


async def test_hers_is_hers_to_read(make_client: ClientFactory, settings: Settings):
    client, _, clock = await talked(make_client, settings, [PLAN])
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()
    other = make_client(FakeLLM(), "other", settings=replace(settings, schedules=True))

    assert await schedule(other) is None
    assert (await client.get("/api/characters/nobody/schedule")).status_code == 404
    yesterday = (today_at(6) - timedelta(days=1)).date().isoformat()
    res = await client.get("/api/characters/mano/schedule", params={"day": yesterday})
    assert res.json() is None


async def test_what_she_is_up_to_now_is_told_for_the_line_under_her_name(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings, [PLAN])
    none_yet = (await client.get("/api/characters/mano/schedule/now")).json()
    clock.now = today_at(6)
    await backend_of(client).schedule_writer.run_due()

    clock.now = today_at(10)
    lesson = (await client.get("/api/characters/mano/schedule/now")).json()
    clock.now = today_at(13)
    free = (await client.get("/api/characters/mano/schedule/now")).json()

    assert none_yet is None
    assert lesson == {"what": "舞蹈课", "where": "事务所", "kind": "lesson", "until": "12:00"}
    assert free == {"free": True}


def rested(wake: str | None, sleep: str | None, day: date) -> Schedule:
    return Schedule(
        day, (Slot(time(10), time(12), "练习", "", "lesson"),), (),
        time.fromisoformat(wake) if wake else None, time.fromisoformat(sleep) if sleep else None,
    )


def test_she_is_asleep_from_bedtime_to_waking_and_her_bedtime_comes_first():
    day = date(2026, 10, 22)
    early = rested("07:00", "23:00", day)
    late = rested("09:00", "01:30", day)
    before = rested("07:30", "23:00", day - timedelta(days=1))
    night_owl = rested("09:00", "02:00", day - timedelta(days=1))

    def at(hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute))

    assert rest(early, before, at(6, 59)) == "asleep"
    assert rest(early, before, at(7)) is None
    assert rest(early, before, at(22, 40)) == "bedtime"
    assert rest(early, before, at(23, 5)) == "asleep"
    assert rest(late, before, at(23, 50)) is None
    assert rest(late, before, at(1, 10)) == "asleep"  # yesterday's bedtime, before today's waking
    assert rest(late, night_owl, at(1, 40)) == "bedtime"  # still up from last night
    assert rest(early, night_owl, at(2, 30)) == "asleep"
    assert rest(None, before, at(5)) == "asleep"  # today's not written: up as yesterday
    assert rest(None, None, at(3)) is None


def test_when_she_gets_up_and_goes_to_bed_is_read_within_their_hours():
    assert parse_rest('{"wake": "07:30", "sleep": "00:30", "slots": []}') == (time(7, 30), time(0, 30))
    assert parse_rest('{"wake": "03:00", "sleep": "18:00"}') == (None, None)
    assert parse_rest('{"wake": "08:00", "sleep": "23:00"}') == (time(8), time(23))
    assert parse_rest("没有 JSON") == (None, None)


async def keep_night(settings: Settings, wake: time, sleep: time, day: date) -> None:
    async with database_in(settings.database_path) as db:
        await Schedules(db, lambda: datetime.now(UTC)).keep(
            user_id(settings.database_path, "producer"), "mano", day,
            [Slot(time(10), time(12), "练习", "", "lesson")], [], wake, sleep,
        )


async def test_a_message_while_she_sleeps_wakes_her_and_after_that_she_is_up(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, REPLY, REPLY)
    clock = FakeClock(today_at(1))
    client = make_client(llm, clock=clock)
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await keep_night(settings, time(7), time(23), today_at(1).date())

    await send_turn(client, conversation, "还醒着吗")
    woken = context_of(llm.prompts[-1])
    status = (await client.get("/api/characters/mano/schedule/now")).json()
    clock.advance(minutes=5)
    await send_turn(client, conversation, "抱歉吵醒你了")
    up = context_of(llm.prompts[-1])

    assert "是被制作人的消息吵醒的" in woken
    assert "你们已经聊了一会儿" in up and "被制作人的消息吵醒" not in up
    assert status == {"asleep": True, "woken": True}


async def test_woken_by_a_call_she_answers_it_first(
    mano_call: CallUnderTest, settings: Settings
):
    await keep_night(settings, time(7), time(20), date(2026, 9, 25))  # Shanghai's 20:30: asleep
    llm = FakeLLM(["<ja>……もしもし……？</ja><zh>……喂……？</zh>"])

    spoken, messages = await mano_call.run(llm, [answered(), SleepFrame(0.5)])

    assert spoken == ["……もしもし……？"]
    assert messages[0]["type"] == "user" and messages[0]["text"] == ""
    assert llm.prompts[0][-1].content == ANSWERING
    assert "是被制作人的电话吵醒的" in context_of(llm.prompts[0])


async def test_awake_she_waits_for_the_user_to_speak(mano_call: CallUnderTest, settings: Settings):
    await keep_night(settings, time(7), time(23, 30), date(2026, 9, 25))  # Shanghai's 20:30: awake
    llm = FakeLLM()

    spoken, _ = await mano_call.run(llm, [answered(), SleepFrame(0.3)])

    assert spoken == [] and llm.prompts == []


def answered() -> RTVIClientMessageFrame:
    return RTVIClientMessageFrame(msg_id="a1", type="answered", data={})
