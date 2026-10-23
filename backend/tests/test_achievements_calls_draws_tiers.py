"""Achievements (GLOSSARY.md「成就」) counted by what is not a turn or a gift alone: the time
talked with her in calls that connected, as each is settled after hanging up, those that
never connected counting nothing; the times drawn, once or every chance alike, drawing
telling what it was rewarded with; and the highest tier her affinity has reached after a
turn or a gift, reaching intimate reaching close too, falling back taking nothing back. How
many characters have reached intimate is worked out from hers. A call or a draw is no day
come. Driven through the backend's external API, with a browser calling in over a real
WebRTC connection, the judge model faked turn by turn, and the time a call lasts once
connected moved by hand."""

import asyncio
from collections.abc import Callable

import httpx
import pytest

from eidolon import call_outcome
from eidolon.config import Settings
from tests.browser import TIMEOUT, Browser, open_browser
from tests.conftest import ClientFactory
from tests.fakes import (
    FakeClock,
    FakeLLM,
    FakeRandom,
    FakeServiceClients,
    FakeSpeech,
    level,
)
from tests.test_achievements import (  # noqa: F401
    REPLY,
    achievement,
    goals,
    hers,
    settings,
    state,
    turns,
    unlocked,
    with_goals,
)
from tests.test_affinity import keep
from tests.test_gift_backpack import draw
from tests.test_gift_giving import CAKE, catalog, draw_them, given, holding  # noqa: F401
from tests.test_she_hangs_up import OUT_OF_LINE
from tests.test_text_turn import start_conversation

MINUTES_10 = 600


class Ticker:
    """Stands in for `perf_counter` in settling calls: the seconds set by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def ticker(monkeypatch: pytest.MonkeyPatch) -> Ticker:
    ticker = Ticker()
    monkeypatch.setattr(call_outcome, "perf_counter", ticker)
    return ticker


async def call(
    client: httpx.AsyncClient, conversation: str, speech: FakeSpeech, ticker: Ticker,
    seconds: float,
) -> None:
    """A call in the conversation that takes a turn and lasts `seconds` once connected."""
    async with open_browser() as browser:
        await browser.call(client, conversation_id=conversation)
        await browser.connect()
        await speech.stts[-1].hear("你好")
        await browser.turn()
        ticker.now += seconds
        await browser.hang_up()


async def until(client: httpx.AsyncClient, ready: Callable[[dict | None], bool]) -> dict | None:
    """Her achievements once `ready`: a call is kept as it is settled, just after hanging up."""
    async with asyncio.timeout(TIMEOUT):
        while not ready(got := await hers(client)):
            await asyncio.sleep(0.05)
    return got


def talked(seconds: int) -> Callable[[dict | None], bool]:
    return lambda got: achievement(got, "call_time")["progress"] == seconds


async def test_calls_that_connected_add_up_their_time_unlocking_each_stage_passed(
    make_client: ClientFactory, speech: FakeSpeech, ticker: Ticker
):
    client = make_client(FakeLLM(*[REPLY] * 3), clock=FakeClock())
    conversation = await start_conversation(client)
    before = await state(client)

    await call(client, conversation, speech, ticker, MINUTES_10 - 1)
    short = await until(client, talked(MINUTES_10 - 1))
    await call(client, conversation, speech, ticker, 1)
    ten = await until(client, talked(MINUTES_10))
    await call(client, conversation, speech, ticker, 2 * 60 * 60)
    hours = await until(client, talked(MINUTES_10 + 2 * 60 * 60))
    after = await state(client)

    assert unlocked(short, "call_time") == []
    assert unlocked(ten, "call_time") == [1]
    assert unlocked(hours, "call_time") == [1, 2]
    # Her first daily task, then the stages' one and two; the calls are no days come.
    assert after["draws"] == before["draws"] + 1 + 1 + 2
    assert achievement(after, "days")["progress"] == 1


async def test_a_call_that_never_connected_counts_no_time(
    make_client: ClientFactory, browser: Browser, speech: FakeSpeech, ticker: Ticker
):
    client = make_client(FakeLLM(REPLY), clock=FakeClock())
    conversation = await start_conversation(client)
    await call(client, conversation, speech, ticker, 30)
    await until(client, talked(30))

    # Its speech synthesis failing before any turn, it never really connected.
    await browser.call(client, conversation_id=conversation)
    await browser.connect()
    ticker.now += MINUTES_10
    await speech.ttss[-1].break_down()
    assert (await browser.next_server_message("ended"))["reason"] == "not_connected"
    await asyncio.sleep(0.2)

    assert achievement(await hers(client), "call_time")["progress"] == 30


async def test_each_draw_counts_once_or_all_at_once_and_drawing_tells_the_stage_it_unlocked(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock, random=FakeRandom(*CAKE * 4))
    with_goals(
        monkeypatch, "ALL_CHARACTERS",
        characters=goals(3), intimate_characters=goals(1), days=goals(7), draws=goals(2, 4),
    )
    await state(client)

    once = await draw(client)
    clock.advance(days=2)
    both = await draw(client, all=True)
    one_more = await draw(client)
    after = await state(client)

    assert once["rewarded"] is None
    assert both["rewarded"]["achievements"] == [
        {"achievement": "draws", "character_id": None, "stage": 1, "draws": 1}
    ]
    assert both["rewarded"]["draws"] == 1
    assert both["draws"] == 1  # the stage's chance, left to draw
    assert one_more["rewarded"]["achievements"] == [
        {"achievement": "draws", "character_id": None, "stage": 2, "draws": 2}
    ]
    assert one_more["draws"] == after["draws"] == 2
    assert achievement(after, "draws")["progress"] == 4
    assert unlocked(after, "draws") == [1, 2]
    assert achievement(after, "days")["progress"] == 0  # drawing is no day come


def judged(*judgings) -> FakeServiceClients:
    clients = FakeServiceClients()
    clients.judge.judgings = list(judgings)
    return clients


async def without_scenes(client: httpx.AsyncClient) -> None:
    """The fork's scenes off, so that affinity goes through a tier's door as it would
    upstream, held at none (GLOSSARY.md「玩法开关」)."""
    response = await client.put("/api/account/features/scenes", json={"on": False})
    assert response.status_code == 204


def tier(got: dict | None) -> str | None:
    return achievement(got, "tier")["progress"]


async def test_reaching_close_unlocks_its_stage_and_falling_back_takes_nothing_back(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = make_client(
        FakeLLM(*[REPLY] * 3), clock=clock, clients=judged(level(4), level(4), OUT_OF_LINE)
    )
    conversation = await start_conversation(client)
    await without_scenes(client)
    await turns(client, conversation, 1)
    keep(settings.database_path, "producer", 63, clock.now)

    up = await turns(client, conversation, 1)
    close = await hers(client)
    clock.advance(minutes=1)
    await turns(client, conversation, 1)
    fallen = await hers(client)

    assert up["affinity"]["tier"] == "close"
    assert up["rewarded"]["achievements"] == [
        {"achievement": "tier", "character_id": "mano", "stage": 1, "draws": 1}
    ]
    assert (tier(close), unlocked(close, "tier")) == ("close", [1])
    assert (tier(fallen), unlocked(fallen, "tier")) == ("close", [1])


async def test_reaching_intimate_at_once_unlocks_both_and_counts_her_among_those_intimate(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = make_client(FakeLLM(*[REPLY] * 2), clock=clock, clients=judged(level(4), level(4)))
    conversation = await start_conversation(client)
    await without_scenes(client)
    await turns(client, conversation, 1)
    keep(settings.database_path, "producer", 84, clock.now)

    up = await turns(client, conversation, 1)
    got = await hers(client)
    all_together = await state(client)

    assert up["affinity"]["tier"] == "intimate"
    assert [(u["achievement"], u["stage"]) for u in up["rewarded"]["achievements"]] == [
        ("tier", 1), ("tier", 2), ("intimate_characters", 1)
    ]
    assert (tier(got), unlocked(got, "tier")) == ("intimate", [1, 2])
    assert achievement(all_together, "intimate_characters")["progress"] == 1
    assert unlocked(await hers(client, "kogane"), "tier") == []


async def test_a_gift_that_brings_her_close_unlocks_its_stage(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE)
    await draw_them(client, clock, 1)
    await without_scenes(client)
    keep(settings.database_path, "producer", 62, clock.now)

    gift = await given(client, "cake")

    assert gift["affinity"]["tier"] == "close"
    assert ("tier", 1) in [(u["achievement"], u["stage"]) for u in gift["rewarded"]["achievements"]]
    assert unlocked(await hers(client), "tier") == [1]


async def test_she_is_complete_only_once_all_four_of_hers_are(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch, settings: Settings,
    speech: FakeSpeech,
    ticker: Ticker,
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, llm=FakeLLM(*[REPLY] * 2))
    with_goals(
        monkeypatch, "PER_CHARACTER",
        turns=goals(1), call_time=goals(60), gifts=goals(1), tier=goals(1),
    )
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)
    await without_scenes(client)
    await turns(client, conversation, 1)
    keep(settings.database_path, "producer", 62, clock.now)
    gift = await given(client, "cake")  # close, and a gift: all but the call
    clock.advance(minutes=1)

    await call(client, conversation, speech, ticker, 60)
    got = await until(client, talked(60))

    assert gift["rewarded"]["completed"] == []
    assert got is not None
    assert got["complete"]["completed_at"] is not None
    assert got["complete"]["reset_cards"] == 0
    assert (await state(client))["reset_cards"] == 1
