"""How many calls there may be at once: each user one at a time, a new one, let through,
hanging up the one they had, and all users together as many as the limits say
(`UsageLimits.concurrent_calls`). A call is under way from being placed until it is over;
one refused is refused before anything is asked of it, both as the browser asks what it
needs to call and as it places the call."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from eidolon.assembly import Backend
from eidolon.config import Settings, UsageLimits
from tests.browser import Browser, open_browser, until_gone
from tests.conftest import SITE, ClientFactory, sign_in
from tests.fakes import FakeLLM
from tests.test_text_turn import start_conversation

BUSY = "现在打电话的人太多，稍后再试"


@pytest.fixture
def one_call_at_once(settings: Settings) -> Settings:
    return replace(settings, usage_limits=UsageLimits(concurrent_calls=1))


def another_user(client: httpx.AsyncClient, name: str) -> httpx.AsyncClient:
    """A client on the same backend as `client`, signed in as the user named `name`."""
    backend: Backend = client._transport.app.state.backend  # pyright: ignore[reportAttributeAccessIssue]
    return httpx.AsyncClient(
        transport=client._transport,
        base_url="http://test",
        headers=SITE,
        cookies=sign_in(backend.store, name, backend.clock()),
    )


async def test_a_users_new_call_hangs_up_the_one_they_had(
    make_client: ClientFactory, one_call_at_once: Settings, browser: Browser
):
    """Theirs is not counted against the calls at once: it is hung up as the new one is
    let through."""
    client = make_client(FakeLLM(), settings=one_call_at_once)
    conversation_id = await start_conversation(client)
    await browser.call(client, conversation_id=conversation_id)
    await browser.connect()

    async with open_browser() as again:
        asked = await client.get("/api/ice-servers", params={"conversation": conversation_id})
        placed = await again.call(client, conversation_id=conversation_id)
        await browser.hung_up()
        await again.connect()

    assert asked.status_code == 200
    assert placed.status_code == 200


async def test_a_call_placed_while_the_users_last_is_still_being_placed_is_refused(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM())
    conversation_id = await start_conversation(client)
    async with open_browser() as a, open_browser() as b:
        placed = await asyncio.gather(
            a.call(client, conversation_id=conversation_id),
            b.call(client, conversation_id=conversation_id),
        )

    assert sorted(res.status_code for res in placed) == [200, 409]
    assert "还在拨" in next(res for res in placed if res.status_code == 409).json()["detail"]


async def test_calls_at_once_are_limited_across_users(
    make_client: ClientFactory, one_call_at_once: Settings, browser: Browser
):
    client = make_client(FakeLLM(), settings=one_call_at_once)
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()
    assert browser.pc_id is not None

    async with another_user(client, "hazuki") as other:
        theirs = await start_conversation(other)
        asked = await other.get("/api/ice-servers", params={"conversation": theirs})
        async with open_browser() as second:
            refused = await second.call(other, conversation_id=theirs)
        await browser.hang_up()
        await until_gone(client, browser.pc_id)
        async with open_browser() as again:
            after = await again.call(other, conversation_id=theirs)

    assert asked.status_code == 503
    assert asked.json() == {"detail": BUSY, "code": "calls_busy"}
    assert refused.status_code == 503
    assert refused.json() == {"detail": BUSY, "code": "calls_busy"}
    assert after.status_code == 200


async def test_a_call_counts_from_being_placed(
    make_client: ClientFactory, one_call_at_once: Settings
):
    """Two placed at the same moment: the one placed first holds the place while it is let
    through and answered, before it is served."""
    client = make_client(FakeLLM(), settings=one_call_at_once)
    mine = await start_conversation(client)
    async with another_user(client, "hazuki") as other, open_browser() as a, open_browser() as b:
        theirs = await start_conversation(other)
        placed = await asyncio.gather(
            a.call(client, conversation_id=mine), b.call(other, conversation_id=theirs)
        )

    assert sorted(res.status_code for res in placed) == [200, 503]
