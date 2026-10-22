"""Each user's own switches for the fork's features (GLOSSARY.md「玩法开关」): all on by
default, listed with the account for the features the deployment has, switched off one by one,
for that user alone. Off, each is as though it were not there for them: no door holds her, no
diary is written, no schedule, no sleep, no bath, no anniversary, promise or weather told her.
Her sleep and her bath are part of her schedule, off with it."""

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from pipecat.tests.utils import SleepFrame

from eidolon.config import Settings, WeatherSettings
from eidolon.features import Features
from eidolon.promises import NewPromise
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeLLM, context_of, level
from tests.test_baths import at, in_the_bath, now, offer
from tests.test_call_turns import CallUnderTest
from tests.test_call_turns import mano_call  # noqa: F401  # the fixture
from tests.test_delete_account import user_id
from tests.test_diary import MOMENT, talked, today_at
from tests.test_reach_outs import backend_of
from tests.test_scenes import Talk, scenes, value
from tests.test_schedules import REPLY, answered, keep_night
from tests.test_text_turn import send_turn, start_conversation
from tests.test_weather import NOW, SAID, answering


async def switch(client: httpx.AsyncClient, feature: str, on: bool) -> httpx.Response:
    return await client.put(f"/api/account/features/{feature}", json={"on": on})


async def features(client: httpx.AsyncClient) -> dict[str, bool]:
    res = await client.get("/api/account")
    assert res.status_code == 200, res.text
    return res.json()["features"]


async def test_the_features_the_deployment_has_are_on_till_the_user_switches_one_off(
    make_client: ClientFactory, settings: Settings
):
    on = replace(settings, diary=True, schedules=True)
    client = make_client(FakeLLM(), settings=on)
    other = make_client(FakeLLM(), "other", settings=on)

    before = await features(client)
    off = await switch(client, "diary", False)
    unknown = await switch(client, "nothing", False)
    absent = await switch(client, "weather", False)  # no place for the weather here

    assert before == {
        "diary": True, "schedules": True, "sleep": True, "bath": True,
        "anniversaries": True, "promises": True,
    }
    assert off.status_code == 204
    assert (await features(client))["diary"] is False
    assert (await features(other))["diary"] is True  # theirs alone
    assert unknown.status_code == 404 and absent.status_code == 404
    assert (await switch(client, "diary", True)).status_code == 204
    assert (await features(client))["diary"] is True


def test_sleep_and_the_bath_are_off_with_her_schedule(
    make_client: ClientFactory, settings: Settings
):
    make_client(FakeLLM())  # signs the user up
    me = user_id(settings.database_path, "producer")
    switches = Features(settings.database_path)

    switches.switch(me, "schedules", False)

    assert not switches.on(me, "sleep") and not switches.on(me, "bath")
    assert switches.of(me)["sleep"] is True  # as the user set it
    switches.close()


async def test_with_scenes_off_no_door_holds_her(make_client: ClientFactory, settings: Settings):
    talk = Talk(
        make_client, replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True), [level(4)]
    )
    await talk.start(64)
    assert (await switch(talk.client, "scenes", False)).status_code == 204

    done = await talk.turn()

    assert done["affinity"]["tier"] == "close"
    assert value(talk.database) > 65
    assert scenes(talk.database) == []


async def test_with_her_diary_off_none_is_written(make_client: ClientFactory, settings: Settings):
    client, llm, clock = await talked(make_client, settings)
    await switch(client, "diary", False)
    backend = backend_of(client)
    backend.diary.moment(user_id(settings.database_path, "producer"), "mano", MOMENT, clock.now)
    clock.now = today_at(0, 30, days=1)

    await backend.diary.run_due()

    assert len(llm.prompts) == 1  # the turn's only


async def test_with_her_schedule_off_she_has_none_told_nor_shown(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await in_the_bath(make_client, settings, REPLY)
    await switch(client, "schedules", False)

    status = await now(client)
    await send_turn(client, conversation, "在干嘛")
    called = await offer(client, conversation)

    assert status is None
    assert "大概的安排" not in context_of(llm.prompts[-1])
    assert called.status_code != 409  # not in the bath for them: the call goes on to be set up


async def test_with_her_bath_off_she_is_not_in_it(make_client: ClientFactory, settings: Settings):
    client, llm, conversation = await in_the_bath(make_client, settings, REPLY)
    await switch(client, "bath", False)

    status = await now(client)
    await send_turn(client, conversation, "在吗")

    assert status == {"free": True}
    told = context_of(llm.prompts[-1])
    assert "洗完出来才看到" not in told
    assert "大概的安排" in told  # her schedule is still there


async def test_with_her_sleep_off_she_is_up_all_night(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: at(1), settings=replace(settings, schedules=True))
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    keep_night(settings, time(7), time(23), at(1).date())
    await switch(client, "sleep", False)

    status = await now(client)
    await send_turn(client, conversation, "还醒着吗")

    assert status == {"free": True}
    assert "吵醒" not in context_of(llm.prompts[-1])


async def test_with_her_sleep_off_a_call_at_night_waits_for_the_user(
    mano_call: CallUnderTest, settings: Settings
):
    keep_night(settings, time(7), time(20), date(2026, 9, 25))  # Shanghai's 20:30: asleep
    switches = Features(settings.database_path)
    switches.switch(user_id(settings.database_path, "producer"), "sleep", False)
    switches.close()
    llm = FakeLLM()

    spoken, _ = await mano_call.run(llm, [answered(), SleepFrame(0.3)])

    assert spoken == [] and llm.prompts == []


async def test_with_promises_off_none_is_kept(make_client: ClientFactory, settings: Settings):
    client = make_client(FakeLLM())
    me = user_id(settings.database_path, "producer")
    await switch(client, "promises", False)
    promises = backend_of(client).promises
    assert promises is not None

    promises.record(me, "mano", [NewPromise("一起去看海", at(10).date())], at(9))

    assert promises.open_for(me) == []


async def test_with_the_weather_off_she_is_not_told_it(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, clock=lambda: NOW,
        settings=replace(settings, weather=WeatherSettings(31.23, 121.47)),
        weather_transport=answering(),
    )
    await switch(client, "weather", False)
    conversation = await start_conversation(client)

    await send_turn(client, conversation, "你好")

    assert SAID not in context_of(llm.prompts[-1])


@pytest.mark.parametrize("on", [True, False])
async def test_the_anniversaries_she_brings_up_go_by_the_switch(
    make_client: ClientFactory, settings: Settings, on: bool
):
    llm = FakeLLM(REPLY)
    client = make_client(llm)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "初次见面")
    await switch(client, "anniversaries", on)
    backend = backend_of(client)
    hundredth = datetime.now(UTC).date() + timedelta(days=99)

    told = backend.engine._anniversary_lines(  # pyright: ignore[reportPrivateUsage]
        backend.characters["mano"], user_id(settings.database_path, "producer"), hundredth,
        "Etc/GMT",
    )

    assert bool(told) is on
