"""The dashboard's「每日任务」section (GLOSSARY.md「看板」「每日任务」): over the dashboard's
range, how many times each daily task was done. Each done has an activity record of its
own, with no character and the task in its result, counting as no turn, no active user and
nothing in the trend. Where affinity and gifts are off there is no section, unless the range
holds some done while they were on.

Driven through the backend's external API, with the clock moved."""

from dataclasses import replace

import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_activity_records import dashboard
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import AFFINITY
from tests.test_daily_tasks import REPLY, turns
from tests.test_gift_giving import CAKE, catalog, draw_them, given, holding  # noqa: F401
from tests.test_text_turn import start_conversation


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True)


def done(one_turn: int = 0, one_gift: int = 0, ten_turns: int = 0) -> dict:
    return {"one_turn": one_turn, "one_gift": one_gift, "ten_turns": ten_turns}


async def test_the_section_counts_each_task_done_over_the_range(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, llm=FakeLLM(*[REPLY] * 11))
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        await turns(client, conversation, 10)
        await given(client, "cake")
        clock.advance(days=2)
        await turns(client, conversation, 1)
        day = (await dashboard(admin, "24h"))["daily_tasks"]
        week = (await dashboard(admin, "7d"))["daily_tasks"]

    assert day == done(one_turn=1)
    assert week == done(one_turn=2, one_gift=1, ten_turns=1)


async def test_the_tasks_done_count_as_no_turns_users_or_characters(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(*[REPLY] * 10), clock=FakeClock())
    conversation = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        await turns(client, conversation, 10)
        board = await dashboard(admin)

    assert board["turns"]["total"] == 10
    assert board["calls"]["total"] == 0
    assert board["active_users"] == 1
    assert sum(s["turns"] for s in board["trend"]) == 10
    assert max(s["active_users"] for s in board["trend"]) == 1
    assert [(c["character_id"], c["turns"]) for c in board["characters"]] == [("mano", 10)]


async def test_off_there_is_no_section_unless_the_range_holds_some_done(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(REPLY), clock=clock)
    conversation = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        none_done = (await dashboard(admin))["daily_tasks"]
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200
        await turns(client, conversation, 1)
        assert (await admin.put(AFFINITY, json={"enabled": False})).status_code == 200
        some_done = (await dashboard(admin))["daily_tasks"]
        clock.advance(days=2)
        out_of_range = (await dashboard(admin))["daily_tasks"]

    assert none_done is None
    assert some_done == done(one_turn=1)
    assert out_of_range is None
