"""The dashboard's「每日任务和成就」section (GLOSSARY.md「看板」「成就」): over the dashboard's
range, how many achievements' stages were unlocked, the draw chances they gave (stage n gives
n), and the reset cards characters complete gave. Each stage unlocked and each character
complete has an activity record of its own, written once it is rewarded, counting as no turn,
no active user and nothing in the trend. The section shows where the daily tasks' does.

Driven through the backend's external API, with the clock moved and the goals lowered."""

import asyncio
from dataclasses import replace

import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_achievements import REPLY, goals, turns, with_goals
from tests.test_activity_records import dashboard
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import AFFINITY
from tests.test_gift_giving import CAKE, catalog, draw_them, given, holding  # noqa: F401
from tests.test_text_turn import send_turn, start_conversation


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True)


def unlocked(stages: int = 0, draws: int = 0, reset_cards: int = 0) -> dict:
    return {"stages": stages, "draws": draws, "reset_cards": reset_cards}


async def test_the_section_counts_stages_unlocked_their_draws_and_characters_complete(
    make_client: ClientFactory, github: FakeIdentityProvider, monkeypatch: pytest.MonkeyPatch
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, llm=FakeLLM(*[REPLY] * 3))
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)
    with_goals(monkeypatch, "PER_CHARACTER", turns=goals(1, 2), gifts=goals(1))

    async with admin_beside(client, github) as admin:
        await turns(client, conversation, 2)
        await given(client, "cake")
        clock.advance(days=2)
        with_goals(monkeypatch, "PER_CHARACTER", turns=goals(1, 2, 3), gifts=goals(1))
        await turns(client, conversation, 1)
        day = (await dashboard(admin, "24h"))["achievements"]
        week = (await dashboard(admin, "7d"))["achievements"]

    assert day == unlocked(stages=1, draws=3)
    # Turns' stages 1, 2 and 3, gifts' stage 1; complete once, not again with a stage more.
    assert week == unlocked(stages=4, draws=7, reset_cards=1)


async def test_two_turns_at_once_reaching_a_goal_count_its_stage_once(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    """Only the turn that is rewarded records the stage, not each that reached its goal."""
    client = make_client(FakeLLM(*[REPLY] * 11), clock=FakeClock())
    mano = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        await turns(client, mano, 9)
        await asyncio.gather(
            send_turn(client, mano, "一"), send_turn(client, await start_conversation(client), "二")
        )
        board = await dashboard(admin)

    assert board["achievements"] == unlocked(stages=1, draws=1)


async def test_the_stages_unlocked_count_as_no_turns_users_or_characters(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(*[REPLY] * 10), clock=FakeClock())
    conversation = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        await turns(client, conversation, 10)
        board = await dashboard(admin)

    assert board["achievements"] == unlocked(stages=1, draws=1)
    assert board["turns"]["total"] == 10
    assert board["calls"]["total"] == 0
    assert board["active_users"] == 1
    assert sum(s["turns"] for s in board["trend"]) == 10
    assert max(s["active_users"] for s in board["trend"]) == 1
    assert [(c["character_id"], c["turns"]) for c in board["characters"]] == [("mano", 10)]


async def test_off_there_is_no_section_unless_the_range_holds_some_unlocked(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 10), clock=clock)
    conversation = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        none_unlocked = (await dashboard(admin))["achievements"]
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        off = (await dashboard(admin))["achievements"]
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200
        await turns(client, conversation, 10)
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        some_unlocked = (await dashboard(admin))["achievements"]
        clock.advance(days=2)
        out_of_range = (await dashboard(admin))["achievements"]

    assert none_unlocked == unlocked()
    assert off is None
    assert some_unlocked == unlocked(stages=1, draws=1)
    assert out_of_range is None
