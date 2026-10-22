"""Her scene at a door (GLOSSARY.md「重要的一幕」): her affinity first coming to close's or
intimate's is held just below it, and her scene there begins; the turns after, at the door, are
of it, she told which and what to do, and the last of `SCENE_TURNS` opens the door, a moment
of her diary. There is no failing it: turns off from the door wait for it. Driven through the
backend's external API, the judge model scripted."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eidolon.affinity import (
    DOOR_GAP,
    SCENE_INVITATION,
    SCENE_TURNS,
    Scene,
    scene_prompt,
)
from eidolon.config import Settings
from eidolon.hearts import Heart
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeClock,
    FakeLLM,
    FakeServiceClients,
    Judging,
    context_of,
    level,
)
from tests.test_affinity import keep
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
SCENE = "# 重要的一幕"


def today_at(hour: int) -> datetime:
    return datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model, affinity on, and her diary."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True, diary=True)


class Talk:
    def __init__(
        self, make_client: ClientFactory, settings: Settings, judgings: list[Judging]
    ) -> None:
        self.llm = FakeLLM(*[REPLY] * (len(judgings) + 2))
        clients = FakeServiceClients()
        clients.judge.judgings = list(judgings)
        self.clock = FakeClock(today_at(10))
        self.client = make_client(self.llm, clients=clients, clock=self.clock, settings=settings)
        self.database = settings.database_path

    async def start(self, value: float) -> None:
        self.conversation = await start_conversation(self.client)
        keep(self.database, "producer", value, self.clock.now)

    async def turn(self) -> dict:
        events = await send_turn(self.client, self.conversation, "嗯")
        return next(e for e in events if e["type"] == "done")

    def told(self) -> str:
        return context_of(self.llm.prompts[-1])


def value(database: Path) -> float:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT value FROM affinities").fetchone()[0]


def scenes(database: Path) -> list[tuple[str, int, bool]]:
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT tier, turns, done_at FROM affinity_scenes").fetchall()
    return [(tier, turns, done is not None) for tier, turns, done in rows]


async def test_first_coming_to_a_door_she_is_held_below_it_and_her_scene_begins(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(4)])
    await talk.start(64)

    done = await talk.turn()

    assert done["affinity"]["tier"] == "trust"
    assert done["message"]["affinity"]["tier_changed"] is False
    assert value(talk.database) == pytest.approx(65 - DOOR_GAP)
    assert scenes(talk.database) == [("close", 0, False)]
    assert SCENE not in talk.told()  # this turn is the last before it


async def test_her_scene_is_played_turn_by_turn_and_the_last_opens_the_door(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(4), *[level(2)] * SCENE_TURNS, level(2)])
    await talk.start(64)
    await talk.turn()

    told = []
    changes = []
    for _ in range(SCENE_TURNS):
        changes.append((await talk.turn())["message"]["affinity"])
        told.append(talk.told())
    await talk.turn()

    assert "第 1 轮" in told[0] and "把心里那件事说出来" in told[0]
    assert "第 3 轮" in told[2] and "接着这件事聊下去" in told[2]
    assert f"第 {SCENE_TURNS} 轮" in told[-1] and "最后一轮" in told[-1]
    assert changes[:-1] == [None] * (SCENE_TURNS - 1)
    assert changes[-1] == {
        "up": True, "tier_changed": True, "tier": "close", "cooldown": False, "thawed": False,
    }
    assert SCENE not in talk.told()  # played once, for good
    assert scenes(talk.database) == [("close", SCENE_TURNS, True)]
    with sqlite3.connect(talk.database) as db:
        kinds = [k for (k,) in db.execute("SELECT kind FROM diary_moments")]
    assert kinds == ["scene"]  # her affinity reaching close with it needs no other


async def test_turns_off_from_the_door_wait_for_it(make_client: ClientFactory, settings: Settings):
    talk = Talk(make_client, settings, [level(4), level(2), level(0), level(2), level(2)])
    await talk.start(64)
    await talk.turn()
    await talk.turn()  # the first of it
    await talk.turn()  # the second, though it hurt her: further from the door than it is played at

    await talk.turn()
    off = talk.told()
    keep(talk.database, "producer", 64.5, talk.clock.now)
    await talk.turn()

    assert SCENE not in off
    assert "第 3 轮" in talk.told()  # taken up again where it was


async def test_she_asks_the_producer_for_a_moment_as_she_reaches_out(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(4)])
    await talk.start(64)
    await talk.turn()
    backend = backend_of(talk.client)
    conversation = backend.store.get_conversation(talk.conversation)
    assert conversation is not None

    await backend.engine.reach_out(conversation, talk.llm)

    assert SCENE_INVITATION in context_of(talk.llm.prompts[-1])


async def test_a_gift_at_the_door_is_held_too(make_client: ClientFactory, settings: Settings):
    talk = Talk(make_client, settings, [])
    await talk.start(64)
    me = user_id(settings.database_path, "producer")
    backend = backend_of(talk.client)

    _, status = backend.affinities.receive_gift(me, "mano", "rare", True, lambda change: change)

    assert status.tier == "trust"
    assert scenes(talk.database) == [("close", 0, False)]


ADULT = Heart(
    age=19,
    romance={"intimate_female": "承认喜欢。", "intimate_male": "承认喜欢他。", "close_female": "心动。"},
    scenes={"close": "嘴硬地开口。", "intimate": "小声告白。"},
)
MINOR = Heart(age=17, scenes={"intimate": "说制作人很重要。"})


def test_at_intimates_door_an_adult_may_tell_her_love_a_minor_never():
    adult = scene_prompt(Scene("intimate", 0), ADULT, female_producer=True)
    minor = scene_prompt(Scene("intimate", 0), MINOR, female_producer=True)
    unknown = scene_prompt(Scene("intimate", 0), None, female_producer=False)
    adult_close = scene_prompt(Scene("close", 2), ADULT, female_producer=True)

    assert "想把这份心意告诉制作人" in adult and "承认喜欢。" in adult and "小声告白。" in adult
    assert "不是恋爱" not in adult
    assert "可以完全信赖的人" in minor and "不是恋爱" in minor and "说制作人很重要。" in minor
    assert "不是恋爱" in unknown  # without her age, never romance
    assert "可以说真心话" in adult_close and "第 3 轮" in adult_close and "嘴硬地开口。" in adult_close
    assert "不是恋爱" not in adult_close  # as her tier says


async def test_a_door_already_behind_her_holds_nothing(
    make_client: ClientFactory, settings: Settings
):
    talk = Talk(make_client, settings, [level(4)])
    await talk.start(70)

    done = await talk.turn()

    assert done["affinity"]["tier"] == "close"
    assert scenes(talk.database) == []
