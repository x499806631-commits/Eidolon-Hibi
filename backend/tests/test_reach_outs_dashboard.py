"""The dashboard's「搭话」section (GLOSSARY.md「看板」「搭话」): over the dashboard's range, how
many times she reached out, how many times it failed, and the share of those she reached out
that were answered. One is answered once the user has a turn in that conversation, or a call
in it connects, after it and before the next; a gift is no answer, nor a call that never
connected. Not by character; and where the deployer has reach-outs off and none were recorded
in the range, there is no section.

Driven through the backend's external API, with the reach-outs' ticks taken by hand, as in
`test_reach_outs`."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from eidolon.config import Settings
from eidolon.gift_catalog import GIFTS_FILE
from eidolon.llm import LLMError
from tests.browser import TIMEOUT, Browser, open_browser
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import FAKE_JUDGE, FakeIdentityProvider, FakeRandom, FakeSpeech
from tests.test_activity_records import dashboard
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_gift_backpack import COMMON, FIRST
from tests.test_gift_catalog import ONE_OF_EACH_RARITY
from tests.test_gift_giving import given
from tests.test_reach_outs import REACH_OUT, REACH_OUTS, REPLY, backend_of, last_contact, talked
from tests.test_text_turn import send_turn


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, and reach-outs on in `.env`."""
    return replace(settings, admins=frozenset({ADMIN}), reach_outs=True)


async def section(client: httpx.AsyncClient, github: FakeIdentityProvider) -> dict | None:
    async with admin_beside(client, github) as admin:
        return (await dashboard(admin))["reach_outs"]


async def test_it_counts_those_she_reached_out_those_that_failed_and_the_share_answered(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client, _, clock, conversation = await talked(
        make_client, LLMError("模型返回 503: overloaded"), REACH_OUT, REPLY,
        random=FakeRandom(0, 0),
    )
    database = settings.database_path
    last_contact(database, conversation, clock.now - timedelta(hours=20))
    reach_out = backend_of(client).reach_outs
    await reach_out.run_due()  # fails, put off by 20 hours
    clock.advance(hours=20)
    await reach_out.run_due()
    unanswered = await section(client, github)

    await send_turn(client, conversation, "我回来了")

    assert unanswered == {"succeeded": 1, "failed": 1, "answered_share": 0}
    assert await section(client, github) == {"succeeded": 1, "failed": 1, "answered_share": 1}


async def answered_share(
    client: httpx.AsyncClient, github: FakeIdentityProvider, share: float
) -> None:
    """Wait for the section's share answered to come to `share`: a call is kept, answering her,
    just after its record is written, as it is let go of."""
    async with admin_beside(client, github) as admin, asyncio.timeout(TIMEOUT):
        while (await dashboard(admin))["reach_outs"]["answered_share"] != share:
            await asyncio.sleep(0.05)


async def test_a_call_that_connects_answers_her_but_a_gift_or_a_call_that_never_did_does_not(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig,
    github: FakeIdentityProvider, speech: FakeSpeech, browser: Browser,
):
    characters.write(GIFTS_FILE, ONE_OF_EACH_RARITY)
    gifting = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT, settings=gifting, random=FakeRandom(COMMON, FIRST, 0),
    )
    assert (await client.get("/api/gifts")).status_code == 200
    # Once: the turn talked about did a daily task too, giving another chance.
    assert (await client.post("/api/gifts/draws", json={"all": False})).status_code == 200
    last_contact(gifting.database_path, conversation, clock.now - timedelta(hours=20))
    await backend_of(client).reach_outs.run_due()

    await given(client, "cake")
    await browser.call(client, conversation_id=conversation)
    await browser.connect()
    await speech.ttss[-1].break_down()
    assert (await browser.next_server_message("ended"))["reason"] == "not_connected"
    unanswered = await section(client, github)
    async with open_browser() as connected:
        await connected.call(client, conversation_id=conversation)
        await connected.connect()
        await connected.hang_up()

    assert unanswered == {"succeeded": 1, "failed": 0, "answered_share": 0}
    await answered_share(client, github, 1)


async def test_a_turn_answers_only_her_latest_reach_out_not_one_she_makes_after_it(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT, REPLY, REACH_OUT, REPLY, random=FakeRandom(0, 0),
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))
    reach_out = backend_of(client).reach_outs
    await reach_out.run_due()
    await send_turn(client, conversation, "我回来了")
    clock.advance(hours=21)  # her time after the turn, 20 hours on
    await reach_out.run_due()
    once_answered = await section(client, github)

    await send_turn(client, conversation, "又回来了")

    assert once_answered == {"succeeded": 2, "failed": 0, "answered_share": 0.5}
    assert await section(client, github) == {"succeeded": 2, "failed": 0, "answered_share": 1}


async def test_with_reach_outs_off_and_none_in_the_range_there_is_no_section(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client, _, clock, conversation = await talked(make_client, REACH_OUT, random=FakeRandom(0))
    on_and_none = await section(client, github)
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))
    await backend_of(client).reach_outs.run_due()

    async with admin_beside(client, github) as admin:
        res = await admin.put(REACH_OUTS, json={"enabled": False, "per_day": 3})
        assert res.status_code == 200, res.text
        off_with_one = (await dashboard(admin))["reach_outs"]
        clock.advance(hours=25)  # out of the last 24 hours
        off_and_none = (await dashboard(admin))["reach_outs"]

    assert on_and_none == {"succeeded": 0, "failed": 0, "answered_share": None}
    assert off_with_one == {"succeeded": 1, "failed": 0, "answered_share": 0}
    assert off_and_none is None

