"""Daily tasks (GLOSSARY.md「每日任务」): the same three each day, Beijing time, wherever the
user is: a turn with any character, a gift given, ten turns with them all. Each done gives a
draw chance, once, at once, over and above the most kept of those given each day; at
midnight in Beijing they begin again, what was not done lost. Only what happens here while
affinity and gifts are open counts: a turn recorded, typed or in a call, a gift given; not
one refused or failed, nor what a data package brings. Driven through the backend's external
API, with the clock moved."""

import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from eidolon.config import Settings
from eidolon.llm import LLMError
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM, FakeSpeech
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import AFFINITY, keep
from tests.browser import Browser
from tests.test_call_stats import call
from tests.test_conversation_events import hang_up
from tests.test_data_package import import_package
from tests.test_data_package_conversations import conversations_package, packaged_conversation
from tests.test_delete_account import DELETE
from tests.test_gift_giving import CAKE, catalog, draw_them, given, holding  # noqa: F401
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True)


async def state(client: httpx.AsyncClient) -> dict:
    res = await client.get("/api/gifts")
    assert res.status_code == 200, res.text
    return res.json()


def task(id: str, progress: int, goal: int) -> dict:
    return {"id": id, "done": progress >= goal, "progress": progress, "goal": goal, "draws": 1}


def tasks(turns: int = 0, gifts: int = 0) -> list[dict]:
    """The three, as far as `turns` turns and `gifts` gifts take them."""
    return [
        task("one_turn", min(turns, 1), 1),
        task("one_gift", min(gifts, 1), 1),
        task("ten_turns", min(turns, 10), 10),
    ]


async def turns(client: httpx.AsyncClient, conversation: str, count: int) -> None:
    for i in range(count):
        events = await send_turn(client, conversation, f"第{i}句")
        assert events[-1]["type"] == "done", events


async def test_none_is_done_to_begin_with_and_they_begin_again_at_the_next_midnight_in_beijing(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), clock=FakeClock())

    assert (await state(client))["daily_tasks"] == {
        "tasks": tasks(),
        "renews_at": "2026-09-25T16:00:00+00:00",
    }


async def test_a_turn_does_the_first_and_gives_a_draw(make_client: ClientFactory):
    client = make_client(FakeLLM(REPLY), clock=FakeClock())
    await state(client)  # the day's one
    conversation = await start_conversation(client)

    await turns(client, conversation, 1)
    got = await state(client)

    assert got["daily_tasks"]["tasks"] == tasks(turns=1)
    assert got["draws"] == 2


async def test_a_turn_in_a_call_counts_as_one_typed_does(
    make_client: ClientFactory, speech: FakeSpeech
):
    client = make_client(FakeLLM(REPLY), clock=FakeClock())
    await state(client)

    await call(client, await start_conversation(client), speech)
    got = await state(client)

    assert got["daily_tasks"]["tasks"] == tasks(turns=1)
    assert got["draws"] == 2


async def test_ten_turns_with_them_all_do_the_third_and_more_give_nothing_more(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(*[REPLY] * 12), clock=FakeClock())
    await state(client)
    mano, kogane = await start_conversation(client), await start_conversation(client, "kogane")

    await turns(client, mano, 4)
    await turns(client, kogane, 5)
    nine = await state(client)
    await turns(client, mano, 1)
    ten = await state(client)
    await turns(client, kogane, 2)

    assert nine["daily_tasks"]["tasks"] == tasks(turns=9)
    assert nine["draws"] == 2
    assert ten["daily_tasks"]["tasks"] == tasks(turns=10)
    assert ten["draws"] == 3
    assert (await state(client))["draws"] == 3


async def test_a_gift_given_does_the_second_once_in_a_cooldown_too(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, CAKE)
    await draw_them(client, clock, 2)  # leaving none
    keep(settings.database_path, "producer", 0, clock.now, cooling=True)

    await given(client, "cake")
    once = await state(client)
    await given(client, "cake")

    assert once["daily_tasks"]["tasks"] == tasks(gifts=1)
    assert once["draws"] == 2  # and the first gift's achievement stage
    assert (await state(client))["draws"] == 2


async def test_turns_refused_or_failed_count_for_nothing(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = make_client(FakeLLM(LLMError("模型返回 503: overloaded")), clock=clock)
    conversation = await start_conversation(client)
    await state(client)

    failed = await send_turn(client, conversation, "你好")
    keep(settings.database_path, "producer", 0, clock.now, cooling=True)
    refused = await client.post(f"/api/conversations/{conversation}/turns", json={"text": "你好"})
    got = await state(client)

    assert failed[-1]["type"] == "error"
    assert refused.status_code == 409
    assert got["daily_tasks"]["tasks"] == tasks()
    assert got["draws"] == 1


async def test_at_midnight_in_beijing_they_begin_again_and_what_was_not_done_is_lost(
    make_client: ClientFactory,
):
    clock = FakeClock()  # 20:00 in Beijing
    client = make_client(FakeLLM(*[REPLY] * 12), clock=clock)
    await state(client)
    conversation = await start_conversation(client)

    clock.advance(hours=3, minutes=59)  # 23:59
    await turns(client, conversation, 9)
    late = await state(client)
    clock.advance(minutes=2)  # 00:01 the next day
    await turns(client, conversation, 1)
    next_day = await state(client)

    assert late["daily_tasks"]["tasks"] == tasks(turns=9)
    assert next_day["daily_tasks"] == {
        "tasks": tasks(turns=1), "renews_at": "2026-09-26T16:00:00+00:00"
    }
    # The day's one, and the first task each day; and the tenth turn's achievement stage.
    assert [late["draws"], next_day["draws"]] == [2, 5]


async def test_what_they_give_goes_over_seven_and_the_next_day_keeps_it(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)
    await state(client)
    clock.advance(days=8)
    full = await state(client)
    conversation = await start_conversation(client)

    await turns(client, conversation, 1)
    over = await state(client)
    clock.advance(days=1)

    assert [full["draws"], over["draws"], (await state(client))["draws"]] == [7, 8, 8]


async def test_off_nothing_counts_and_on_again_what_was_done_stays(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(*[REPLY] * 3), clock=FakeClock())
    await state(client)
    conversation = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        await turns(client, conversation, 1)
        off = await state(client)
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200
        opened = await state(client)
        await turns(client, conversation, 1)
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200
    again = await state(client)

    assert off["daily_tasks"] is None
    assert opened["daily_tasks"]["tasks"] == tasks()
    assert again["daily_tasks"]["tasks"] == tasks(turns=1)
    assert again["draws"] == 2


async def test_without_the_judge_model_there_are_none_and_turns_count_for_nothing(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    without = replace(settings, judge=None)
    client = make_client(FakeLLM(REPLY), clock=clock, settings=without)
    await turns(client, await start_conversation(client), 1)

    closed = await state(client)
    opened = await state(make_client(FakeLLM(), clock=clock))

    assert closed["daily_tasks"] is None
    assert opened["daily_tasks"]["tasks"] == tasks()


async def test_deleting_the_conversation_it_was_done_in_takes_nothing_back(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(*[REPLY] * 2), clock=FakeClock())
    await state(client)
    past = await start_conversation(client)
    await turns(client, past, 1)
    hang_up(settings.database_path, past)
    await turns(client, await start_conversation(client), 1)

    assert (await client.delete(f"/api/conversations/{past}")).status_code == 204
    got = await state(client)

    assert got["daily_tasks"]["tasks"] == tasks(turns=2)
    assert got["draws"] == 2


async def test_a_data_package_brings_nothing_towards_them(make_client: ClientFactory):
    client = make_client(FakeLLM(), clock=FakeClock())
    await state(client)

    res = await import_package(client, conversations_package(
        *(packaged_conversation(f"c{i}", f"2026-09-{16 + i}") for i in range(10))
    ), "conversations")
    got = await state(client)

    assert res.json()["conversations"] == 10
    assert got["daily_tasks"]["tasks"] == tasks()
    assert got["draws"] == 1


async def test_deleting_the_user_leaves_none_of_it(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(REPLY), clock=FakeClock())
    await turns(client, await start_conversation(client), 1)
    before = rows_of(settings.database_path)

    assert (await client.post("/api/account/delete", json=DELETE)).status_code == 204

    assert before == [1, 1]
    assert rows_of(settings.database_path) == [0, 0]


def rows_of(database: Path) -> list[int]:
    """How many rows the daily tasks' progress and the rewards given have."""
    with sqlite3.connect(database) as db:
        return [
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("daily_task_progress", "gift_rewards")
        ]


def rewarded(*daily_tasks: str, at: datetime, stage: tuple[str, int] | None = None) -> dict:
    """What something that did `daily_tasks` at `at`, and unlocked the first `stage` of an
    achievement of hers (by id, with her by turns or gifts), says it was rewarded with."""
    return {
        "draws": len(daily_tasks) + (stage is not None),
        "reset_cards": 0,
        "daily_tasks": list(daily_tasks),
        "achievements": [
            {"achievement": a, "character_id": "mano", "stage": n, "draws": n}
            for a, n in ([stage] if stage else [])
        ],
        "completed": [],
        "rewarded_at": at.isoformat(),
    }


async def test_a_turn_says_what_it_was_rewarded_with_and_none_if_nothing(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 10), clock=clock)
    conversation = await start_conversation(client)

    began = clock.now
    first = await send_turn(client, conversation, "你好")
    second = await send_turn(client, conversation, "第二句")
    for i in range(7):
        await turns(client, conversation, 1)
    clock.advance(minutes=1)
    tenth = await send_turn(client, conversation, "第十句")

    assert first[-1]["rewarded"] == rewarded("one_turn", at=began)
    assert second[-1]["type"] == "done"
    assert second[-1]["rewarded"] is None
    assert tenth[-1]["rewarded"] == rewarded("ten_turns", at=clock.now, stage=("turns", 1))


async def test_a_turn_in_a_call_says_what_it_was_rewarded_with(
    make_client: ClientFactory, speech: FakeSpeech, browser: Browser
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)

    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()
    await speech.stts[-1].hear("你好")
    done = (await browser.turn())[-1]
    await browser.hang_up()

    assert done["type"] == "done"
    assert done["rewarded"] == rewarded("one_turn", at=clock.now)


async def test_a_gift_given_says_what_it_was_rewarded_with_once(make_client: ClientFactory):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, CAKE)
    await draw_them(client, clock, 2)

    first = await given(client, "cake")
    second = await given(client, "cake")

    assert first["rewarded"] == rewarded("one_gift", at=clock.now, stage=("gifts", 1))
    assert second["rewarded"] is None


async def test_when_the_user_was_last_rewarded_is_none_until_they_are_then_the_latest(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY, REPLY), clock=clock)
    conversation = await start_conversation(client)
    never = await state(client)

    began = clock.now
    await turns(client, conversation, 1)
    once = await state(client)
    clock.advance(hours=1)
    await turns(client, conversation, 1)  # rewarded with nothing
    unchanged = await state(client)

    assert never["rewarded_at"] is None
    assert once["rewarded_at"] == unchanged["rewarded_at"] == began.isoformat()
