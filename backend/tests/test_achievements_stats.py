"""Stats (GLOSSARY.md「统计」) of achievements (GLOSSARY.md「成就」): how many stages the user
has unlocked in all, and how many characters are complete, as the achievements keep them,
whatever the range; a past conversation deleted takes nothing from them. None while
affinity and gifts are closed. Driven through the backend's external API, the goals lowered
as a later deployment might."""

from dataclasses import replace

import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM
from tests.test_achievements import REPLY, goals, turns, with_goals
from tests.test_conversation_events import hang_up
from tests.test_gift_giving import CAKE, catalog, draw_them, given, holding  # noqa: F401
from tests.test_stats import stats
from tests.test_text_turn import start_conversation


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


@pytest.fixture
def low_goals(monkeypatch: pytest.MonkeyPatch) -> None:
    """A turn and a gift complete her; the first character had a turn with, then the
    second, each a stage of those of all together."""
    with_goals(monkeypatch, "PER_CHARACTER", turns=goals(1), gifts=goals(1))
    with_goals(monkeypatch, "ALL_CHARACTERS", characters=goals(1, 2))


@pytest.mark.usefixtures("low_goals")
async def test_stats_count_the_stages_unlocked_and_the_characters_complete_whatever_the_range(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, llm=FakeLLM(*[REPLY] * 2))
    await draw_them(client, clock, 1)
    await turns(client, await start_conversation(client), 1)
    await given(client, "cake")
    await turns(client, await start_conversation(client, "kogane"), 1)

    # Hers, turns and gifts, kogane's turns, and both stages of the characters had a turn with.
    assert (await stats(client))["achievements"] == {"stages": 5, "complete": 1}
    assert (await stats(client, range="30d"))["achievements"] == {"stages": 5, "complete": 1}


@pytest.mark.usefixtures("low_goals")
async def test_a_past_conversation_deleted_takes_nothing_from_them(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(REPLY), clock=FakeClock())
    past = await start_conversation(client)
    await turns(client, past, 1)
    hang_up(settings.database_path, past)
    await start_conversation(client)
    before = (await stats(client))["achievements"]

    assert (await client.delete(f"/api/conversations/{past}")).status_code == 204

    assert before == {"stages": 2, "complete": 0}
    assert (await stats(client))["achievements"] == before


async def test_none_while_affinity_and_gifts_are_closed(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), settings=replace(settings, affinity_and_gifts=False))

    assert (await stats(client))["achievements"] is None
