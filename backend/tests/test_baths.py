"""Her bath (GLOSSARY.md「洗澡」): a night she goes to bed, her bath before it is drawn with her
schedule, in the tub unless her heart says she showers. A message in it she reads as she comes
out, after which she is out; the line under her name says she is in the bath. A call in it:
an adult who soaks answers first from the tub, the browser told so for the sound of the
bathroom; any other does not answer, which is kept in the conversation, and she comes back to
the producer once the bath is over. Her habits are told her in every turn. The producer's time zone is UTC (`Etc/GMT`)."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from pipecat.tests.utils import SleepFrame

from eidolon.config import Settings
from eidolon.engine import ANSWERING_IN_THE_TUB, CALLING_BACK
from eidolon.hearts import HeartsError, hearts_path, load_hearts
from eidolon.schedules import (
    Bath,
    Schedule,
    Schedules,
    Slot,
    bath_began,
    draw_bath,
    lines,
    rest,
)
from tests.conftest import CharacterConfig, ClientFactory, database_in
from tests.fakes import FakeClock, FakeLLM, FakeRandom, context_of
from tests.test_call_turns import CallUnderTest
from tests.test_call_turns import mano_call  # noqa: F401  # the fixture
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of, last_contact, reach_outs, record
from tests.test_retrieval_services import MANO
from tests.test_schedules import PLAN, REPLY, answered, schedule, talked
from tests.test_text_turn import send_turn, start_conversation

ADULT = 'age = 19\nbath_habit = "泡澡时会放入浴剂。"\n'
SHOWERS = 'age = 19\nbath = "淋浴"\n'
MINOR = "age = 17\n"
TODAY = datetime.now(UTC).date()


def at(hour: int, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime.combine(day, time(hour, minute), UTC)


def night(day: date, bath: Bath | None, sleep: time = time(23)) -> Schedule:
    return Schedule(day, (Slot(time(10), time(12), "练习", "", "lesson"),), (), time(7), sleep, bath)


async def keep_bath(
    settings: Settings, bath: Bath, day: date = TODAY, sleep: time = time(23)
) -> None:
    async with database_in(settings.database_path) as db:
        await Schedules(db, lambda: datetime.now(UTC)).keep(
            user_id(settings.database_path, "producer"), "mano", day,
            [Slot(time(10), time(12), "练习", "", "lesson")], [], time(7), sleep, bath,
        )


def test_her_bath_is_drawn_before_she_goes_to_bed_as_long_as_her_way_takes():
    # 60 + 5 × 3 minutes before bed; 30 + 5 × 2 in the tub, 15 + 5 × 1 in the shower.
    assert draw_bath(time(23), "tub", FakeRandom(3, 2)) == Bath(time(21, 45), 40)
    assert draw_bath(time(0, 30), "shower", FakeRandom(12, 1)) == Bath(time(22, 30), 20)


def test_she_is_in_the_bath_from_when_it_begins_till_it_is_over_midnight_or_not():
    day = TODAY
    evening = night(day, Bath(time(21), 40))
    late = night(day, Bath(time(23, 40), 40), sleep=time(1))
    after_midnight = night(day, Bath(time(0, 20), 30), sleep=time(2))
    tomorrow = day + timedelta(days=1)

    assert rest(evening, None, at(21, 10)) == "bath"
    assert bath_began(evening, None, at(21, 39)) == at(21)
    assert rest(evening, None, at(21, 40)) is None
    assert rest(late, None, at(23, 50)) == "bath"
    assert rest(None, late, at(0, 10, tomorrow)) == "bath"  # yesterday's, past midnight
    assert bath_began(after_midnight, None, at(0, 30)) is None  # not that morning's: the night's
    assert rest(None, after_midnight, at(0, 30, tomorrow)) == "bath"
    assert night(day, None).shown()["bath"] is None
    assert evening.shown()["bath"] == {"at": "21:00", "minutes": 40}


def test_in_the_bath_she_reads_a_message_as_she_comes_out_and_talks_from_the_tub_in_a_call():
    evening = night(TODAY, Bath(time(21), 40))

    typing = lines(evening, at(21, 10), {}, "bath")
    calling = lines(evening, at(21, 10), {}, "bath", in_call=True)

    assert typing[0].startswith("你今天大概的安排：10:00–12:00 练习")
    assert "洗完出来才看到" in typing[1] and "不描写洗澡的过程和身体" in typing[1]
    assert "正在泡澡" in calling[1] and "回响" in calling[1]


def test_how_she_bathes_is_written_in_her_heart(characters: CharacterConfig):
    characters.write("hearts/mano.toml", SHOWERS)
    showers = load_hearts(hearts_path(characters.path), [MANO])["mano"]
    characters.write("hearts/mano.toml", ADULT)
    soaks = load_hearts(hearts_path(characters.path), [MANO])["mano"]
    characters.write("hearts/mano.toml", 'age = 19\nbath = "桑拿"\n')

    assert showers.bath == "shower" and soaks.bath == "tub"
    assert soaks.bath_habit == "泡澡时会放入浴剂。"
    with pytest.raises(HeartsError, match="泡澡"):
        load_hearts(hearts_path(characters.path), [MANO])


async def test_her_bath_is_drawn_with_her_schedule_as_her_heart_says(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    characters.write("hearts/mano.toml", SHOWERS)
    plan = PLAN.replace('{"slots"', '{"wake": "07:00", "sleep": "23:30", "slots"')
    # No twist (0), no trip (1); her bath 60 + 5 × 2 minutes before bed, 15 + 5 × 1 long.
    client, _, clock = await talked(make_client, settings, [plan], numbers=(0, 1, 2, 1))
    clock.now = at(6)

    await backend_of(client).schedule_writer.run_due()

    written = await schedule(client)
    assert written is not None and written["bath"] == {"at": "22:20", "minutes": 20}


async def in_the_bath(
    make_client: ClientFactory, settings: Settings, *replies: list[str]
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    """A conversation with Mano, last in touch in the afternoon, her bath at 21:00 tonight
    and the clock at 21:10."""
    llm = FakeLLM(REPLY, *replies)
    client = make_client(llm, clock=FakeClock(at(21, 10)), settings=replace(settings, schedules=True))
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "下午好")
    last_contact(settings.database_path, conversation, at(15))
    await keep_bath(settings, Bath(time(21), 40))
    return client, llm, conversation


async def now(client: httpx.AsyncClient) -> dict | None:
    res = await client.get("/api/characters/mano/schedule/now")
    assert res.status_code == 200, res.text
    return res.json()


async def test_a_message_in_her_bath_she_reads_coming_out_and_after_it_she_is_out(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await in_the_bath(make_client, settings, REPLY, REPLY)

    before = await now(client)
    await send_turn(client, conversation, "在吗")
    first = context_of(llm.prompts[-1])
    last_contact(settings.database_path, conversation, at(21, 10))
    await send_turn(client, conversation, "刚刚在忙吗")
    second = context_of(llm.prompts[-1])

    assert before == {"bath": True}
    assert "洗完出来才看到" in first
    assert "洗完出来才看到" not in second
    assert await now(client) == {"free": True}


async def test_the_line_under_an_adults_name_says_she_is_in_the_bath(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig
):
    characters.write("hearts/mano.toml", ADULT)
    client, llm, conversation = await in_the_bath(make_client, settings, REPLY)

    status = await now(client)
    await send_turn(client, conversation, "在吗")

    assert status == {"bath": True}
    assert "你的生活习惯：泡澡时会放入浴剂。" in context_of(llm.prompts[-1])


def missed_at(settings: Settings, when: datetime) -> None:
    """Have the calls she did not answer have been at `when`, as the store writes the time as it is."""
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE conversation_events SET created_at = ?", (when.isoformat(),))


async def offer(client: httpx.AsyncClient, conversation: str) -> httpx.Response:
    return await client.post("/api/offer", json={
        "sdp": "v=0", "type": "offer", "requestData": {"conversation_id": conversation},
    })


@pytest.mark.parametrize("heart", [None, MINOR, SHOWERS], ids=["no heart", "a minor", "showers"])
async def test_in_the_bath_she_does_not_answer_and_it_is_kept(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig,
    heart: str | None,
):
    if heart is not None:
        characters.write("hearts/mano.toml", heart)
    client, llm, conversation = await in_the_bath(make_client, settings, REPLY)

    res = await offer(client, conversation)
    missed_at(settings, at(21, 10))
    await send_turn(client, conversation, "刚才怎么没接")

    assert res.status_code == 409 and res.json()["code"] == "no_answer"
    events = [e for e in await record(client, conversation) if e.get("type") == "event"]
    assert [e["kind"] for e in events] == ["missed_call"]
    assert "（制作人打来电话，你那会儿在洗澡，没接到。）" in llm.prompts[-1][-1].content


async def test_an_adult_who_soaks_answers_first_from_the_tub(
    mano_call: CallUnderTest, settings: Settings, characters: CharacterConfig
):
    characters.write("hearts/mano.toml", ADULT)
    # The call's clock is Shanghai's 20:30, 2026-09-25.
    await keep_bath(settings, Bath(time(20, 15), 40), date(2026, 9, 25))
    llm = FakeLLM(["<ja>もしもし、今お風呂なの</ja><zh>喂，我在泡澡呢</zh>"])

    spoken, messages = await mano_call.run(llm, [answered(), SleepFrame(0.5)])

    assert messages[0] == {"type": "bath"}
    assert spoken == ["もしもし、今お風呂なの"]
    assert llm.prompts[0][-1].content == ANSWERING_IN_THE_TUB
    told = context_of(llm.prompts[0])
    assert "正在泡澡" in told and "回响" in told


async def test_after_the_bath_she_comes_back_for_the_call_she_did_not_answer_night_or_not(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, ["<ja>さっきお風呂だったの</ja><zh>刚才在洗澡</zh>"])
    clock = FakeClock(at(22, 30))
    client = make_client(
        llm, clock=clock, settings=replace(settings, schedules=True, reach_outs=True)
    )
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "晚上好")
    last_contact(settings.database_path, conversation, at(20))
    await keep_bath(settings, Bath(time(22, 20), 40), sleep=time(23, 50))
    assert (await offer(client, conversation)).status_code == 409
    missed_at(settings, at(22, 30))
    reaching = backend_of(client).reach_outs

    clock.now = at(22, 55)  # still in the bath
    await reaching.run_due()
    in_it = reach_outs(await record(client, conversation))
    clock.now = at(23, 5)  # out of it, at night
    await reaching.run_due()

    assert in_it == []
    assert len(reach_outs(await record(client, conversation))) == 1
    assert CALLING_BACK in context_of(llm.prompts[-1])
