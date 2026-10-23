"""Achievements (GLOSSARY.md「成就」): what the user and the characters have done together,
kept as it happens, each achievement's stages unlocked as its goals are reached, stage n
giving n draw chances at once; and every stage of a character's own achievements unlocked,
a reset card, once ever for her. Only what happens here while affinity and gifts are open
counts: a turn recorded, typed or in a call, a gift given; not what a data package brings,
and deleting a past conversation takes nothing back. Driven through the backend's external
API, with the clock moved."""

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from eidolon import achievements
from eidolon.achievements import Achievement, Stage
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM, FakeSpeech
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import AFFINITY
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


async def hers(client: httpx.AsyncClient, character: str = "mano") -> dict | None:
    res = await client.get(f"/api/characters/{character}/achievements")
    assert res.status_code == 200, res.text
    return res.json()


def achievement(achieved: dict | None, id: str) -> dict:
    assert achieved is not None
    (found,) = [a for a in achieved["achievements"] if a["id"] == id]
    return found


def unlocked(achieved: dict | None, id: str) -> list[int]:
    """The stages of the achievement unlocked, by number."""
    stages = achievement(achieved, id)["stages"]
    return [n for n, s in enumerate(stages, 1) if s["unlocked_at"] is not None]


async def turns(client: httpx.AsyncClient, conversation: str, count: int) -> dict:
    """Take `count` turns: the last one's `done`."""
    events: list[dict] = [{}]
    for i in range(count):
        events = await send_turn(client, conversation, f"第{i}句")
        assert events[-1]["type"] == "done", events
    return events[-1]


async def test_ten_turns_with_her_unlock_the_first_stage_and_give_a_draw(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 10), clock=clock)
    await state(client)
    conversation = await start_conversation(client)

    await turns(client, conversation, 9)
    nine = await state(client)
    await turns(client, conversation, 1)
    ten = await state(client)
    got = await hers(client)

    assert achievement(got, "turns")["progress"] == 10
    assert unlocked(got, "turns") == [1]
    # The day's one, the first daily task, the ten turns daily task, then the stage's one.
    assert [nine["draws"], ten["draws"]] == [2, 4]


async def test_a_gift_given_her_unlocks_the_first_stage_once(make_client: ClientFactory):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, CAKE)
    await draw_them(client, clock, 2)

    await given(client, "cake")
    await given(client, "cake")
    got = await hers(client)

    assert achievement(got, "gifts")["progress"] == 2
    assert unlocked(got, "gifts") == [1]
    assert unlocked(await hers(client, "kogane"), "gifts") == []


async def test_a_turn_in_a_call_counts_towards_turns_with_her(
    make_client: ClientFactory, speech: FakeSpeech
):
    client = make_client(FakeLLM(REPLY), clock=FakeClock())

    await call(client, await start_conversation(client), speech)

    assert achievement(await hers(client), "turns")["progress"] == 1


async def test_her_achievements_show_each_stage_its_goal_and_reward_and_the_tier_by_name(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 10), clock=clock)
    await turns(client, await start_conversation(client), 10)

    got = await hers(client)

    assert got is not None
    assert [a["id"] for a in got["achievements"]] == ["turns", "call_time", "gifts", "tier"]
    assert achievement(got, "turns")["stages"] == [
        {"goal": 10, "draws": 1, "unlocked_at": clock.now.isoformat()},
        {"goal": 100, "draws": 2, "unlocked_at": None},
        {"goal": 300, "draws": 3, "unlocked_at": None},
        {"goal": 1000, "draws": 4, "unlocked_at": None},
        {"goal": 3000, "draws": 5, "unlocked_at": None},
    ]
    assert [s["goal"] for s in achievement(got, "call_time")["stages"]] == [
        600, 3600, 18000, 72000
    ]
    assert achievement(got, "tier") == {
        "id": "tier",
        "progress": None,
        "stages": [
            {"goal": "close", "draws": 1, "unlocked_at": None},
            {"goal": "intimate", "draws": 2, "unlocked_at": None},
        ],
    }
    assert got["complete"] == {"completed_at": None, "reset_cards": 1}


async def test_no_such_character_has_none(make_client: ClientFactory):
    client = make_client(FakeLLM(), clock=FakeClock())

    res = await client.get("/api/characters/nobody/achievements")

    assert res.status_code == 404


def goals(*goals: int) -> tuple[Stage, ...]:
    """Stages reaching `goals`, stage n giving n draw chances, as the real ones do."""
    return tuple(Stage(goal, n) for n, goal in enumerate(goals, 1))


def with_goals(monkeypatch: pytest.MonkeyPatch, which: str, **stages: tuple[Stage, ...]) -> None:
    """Change the goals of the achievements of `which` (`PER_CHARACTER` or
    `ALL_CHARACTERS`) by id, as a later deployment might; those given no stages are left
    out."""
    monkeypatch.setattr(
        achievements, which, tuple(Achievement(id, s) for id, s in stages.items())
    )


async def test_goals_lowered_unlock_every_stage_passed_at_the_next_turn_each_giving_its_draws(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 4), clock=clock)
    conversation = await start_conversation(client)
    await turns(client, conversation, 3)

    with_goals(
        monkeypatch, "PER_CHARACTER",
        turns=goals(2, 3, 4, 100), call_time=goals(600), gifts=goals(1), tier=goals(1, 2),
    )
    lowered = await hers(client)
    done = await turns(client, conversation, 1)

    assert unlocked(lowered, "turns") == []
    assert [(u["stage"], u["draws"]) for u in done["rewarded"]["achievements"]] == [
        (1, 1), (2, 2), (3, 3)
    ]
    assert done["rewarded"]["draws"] == 6
    assert unlocked(await hers(client), "turns") == [1, 2, 3]


async def test_goals_raised_take_no_stage_back(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 11), clock=clock)
    conversation = await start_conversation(client)
    await turns(client, conversation, 10)
    before = await state(client)

    with_goals(
        monkeypatch, "PER_CHARACTER",
        turns=goals(20, 100), call_time=goals(600), gifts=goals(1), tier=goals(1, 2),
    )
    done = await turns(client, conversation, 1)

    assert unlocked(await hers(client), "turns") == [1]
    assert done["rewarded"] is None
    assert (await state(client))["draws"] == before["draws"]


async def test_every_stage_of_hers_unlocked_completes_her_with_a_reset_card_once_ever(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, llm=FakeLLM(*[REPLY] * 2))
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)
    with_goals(monkeypatch, "PER_CHARACTER", turns=goals(1), gifts=goals(1))

    await turns(client, conversation, 1)
    nearly = await hers(client)
    gift = await given(client, "cake")
    complete = await hers(client)
    with_goals(monkeypatch, "PER_CHARACTER", turns=goals(1, 2), gifts=goals(1))
    clock.advance(minutes=1)
    again = await turns(client, conversation, 1)

    assert nearly is not None
    assert nearly["complete"] == {"completed_at": None, "reset_cards": 1}
    assert gift["rewarded"]["completed"] == [{"character_id": "mano", "reset_cards": 1}]
    assert gift["rewarded"]["reset_cards"] == 1
    assert complete is not None
    # Its card given, being complete gives none ever again.
    assert complete["complete"] == {
        "completed_at": gift["rewarded"]["rewarded_at"], "reset_cards": 0
    }
    # Complete again with a stage more, she gives no second card.
    assert again["rewarded"]["achievements"] == [
        {"achievement": "turns", "character_id": "mano", "stage": 2, "draws": 2}
    ]
    assert again["rewarded"]["completed"] == []
    assert (await state(client))["reset_cards"] == 1


async def test_days_come_count_once_a_day_in_beijing_and_need_not_follow_one_another(
    make_client: ClientFactory,
):
    clock = FakeClock()  # 20:00 in Beijing
    client = make_client(FakeLLM(*[REPLY] * 5), clock=clock)
    conversation = await start_conversation(client)

    await turns(client, conversation, 2)
    clock.advance(hours=3, minutes=59)  # 23:59
    await turns(client, conversation, 1)
    one = await state(client)
    clock.advance(minutes=2)  # 00:01 the next day
    await turns(client, conversation, 1)
    two = await state(client)
    clock.advance(days=5)
    await turns(client, conversation, 1)

    assert [achievement(one, "days")["progress"], achievement(two, "days")["progress"]] == [1, 2]
    assert achievement(await state(client), "days")["progress"] == 3


async def test_a_gift_given_makes_a_day_come_too(make_client: ClientFactory):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE)
    await draw_them(client, clock, 1)

    await given(client, "cake")

    assert achievement(await state(client), "days")["progress"] == 1


async def test_seven_days_come_unlock_the_first_stage_of_all_together(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 7), clock=clock)
    conversation = await start_conversation(client)

    for _ in range(6):
        await turns(client, conversation, 1)
        clock.advance(days=2)
    done = await turns(client, conversation, 1)

    assert done["rewarded"]["achievements"] == [
        {"achievement": "days", "character_id": None, "stage": 1, "draws": 1}
    ]
    assert unlocked(await state(client), "days") == [1]


async def test_characters_had_a_turn_with_are_worked_out_from_her_own(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 3), clock=clock)
    with_goals(
        monkeypatch, "ALL_CHARACTERS",
        characters=goals(2, 10), intimate_characters=goals(1), days=goals(7), draws=goals(10),
    )

    await turns(client, await start_conversation(client), 2)
    one = await state(client)
    done = await turns(client, await start_conversation(client, "kogane"), 1)
    two = await state(client)

    assert achievement(one, "characters")["progress"] == 1
    assert achievement(two, "characters")["progress"] == 2
    assert done["rewarded"]["achievements"] == [
        {"achievement": "characters", "character_id": None, "stage": 1, "draws": 1}
    ]
    assert achievement(two, "intimate_characters")["progress"] == 0


async def test_all_together_show_in_order_as_hers_do(make_client: ClientFactory):
    client = make_client(FakeLLM(), clock=FakeClock())

    got = await state(client)

    assert [a["id"] for a in got["achievements"]] == [
        "characters", "intimate_characters", "days", "draws"
    ]
    assert achievement(got, "characters") == {
        "id": "characters",
        "progress": 0,
        "stages": [
            {"goal": 3, "draws": 1, "unlocked_at": None},
            {"goal": 10, "draws": 2, "unlocked_at": None},
            {"goal": 29, "draws": 3, "unlocked_at": None},
        ],
    }


async def test_closed_nothing_counts_and_none_show_and_on_again_what_was_kept_stays(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(*[REPLY] * 2), clock=FakeClock())
    conversation = await start_conversation(client)
    await turns(client, conversation, 1)

    async with admin_beside(client, github) as admin:
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        await turns(client, conversation, 1)
        closed, closed_all = await hers(client), (await state(client))["achievements"]
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200

    assert closed is None
    assert closed_all is None
    assert achievement(await hers(client), "turns")["progress"] == 1


async def test_deleting_a_past_conversation_takes_nothing_back(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(*[REPLY] * 10), clock=FakeClock())
    past = await start_conversation(client)
    await turns(client, past, 10)
    hang_up(settings.database_path, past)
    await start_conversation(client)

    assert (await client.delete(f"/api/conversations/{past}")).status_code == 204
    got = await hers(client)

    assert achievement(got, "turns")["progress"] == 10
    assert unlocked(got, "turns") == [1]


async def test_a_data_package_brings_nothing_towards_them(make_client: ClientFactory):
    client = make_client(FakeLLM(), clock=FakeClock())

    res = await import_package(client, conversations_package(
        *(packaged_conversation(f"c{i}", f"2026-09-{10 + i}") for i in range(12))
    ), "conversations")

    assert res.json()["conversations"] == 12
    assert achievement(await hers(client), "turns")["progress"] == 0
    assert achievement(await state(client), "days")["progress"] == 0


async def test_two_turns_at_once_reaching_a_goal_unlock_it_once(make_client: ClientFactory):
    client = make_client(FakeLLM(*[REPLY] * 11), clock=FakeClock())
    mano = await start_conversation(client)
    await turns(client, mano, 9)
    before = await state(client)

    both = await asyncio.gather(
        send_turn(client, mano, "一"), send_turn(client, await start_conversation(client), "二")
    )
    after = await state(client)

    assert [events[-1]["type"] for events in both] == ["done", "done"]
    assert achievement(await hers(client), "turns")["progress"] == 11
    assert unlocked(await hers(client), "turns") == [1]
    # The stage's one, and the ten turns daily task's.
    assert after["draws"] == before["draws"] + 2


async def test_deleting_the_user_leaves_none_of_it(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(*[REPLY] * 10), clock=FakeClock())
    await turns(client, await start_conversation(client), 10)
    before = rows_of(settings.database_path)

    assert (await client.post("/api/account/delete", json=DELETE)).status_code == 204

    assert before == [2, 1]
    assert rows_of(settings.database_path) == [0, 0]


def rows_of(database: Path) -> list[int]:
    """How many rows the achievements' progress and stages unlocked have."""
    with sqlite3.connect(database) as db:
        return [
            db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("achievement_progress", "achievement_stages")
        ]
