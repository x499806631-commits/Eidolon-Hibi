"""The limits in the live configuration (ADR-0013): turns and calls per hour, how long and
how many turns one call may last, and how many of the latest turns each prompt carries.
Admins change them in the admin console, without trying them first; each holds from the
next turn or call on, the limits per hour as each use is taken, a call's own limits as it
connects, the turns a prompt carries as the turn begins."""

from dataclasses import replace

import pytest

from eidolon.config import Settings, UsageLimits
from tests.browser import Browser, open_browser, until_gone
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeIdentityProvider, FakeLLM, FakeServiceClients, FakeSpeech
from tests.test_live_config import CONFIG, REPLY, admin_client
from tests.test_concurrent_calls import another_user
from tests.test_rate_limit import post_turn
from tests.test_text_turn import send_turn, start_conversation

LIMITS = "/api/admin/config/limits"
ENV_LIMITS = {
    "turns_per_hour": 60, "calls_per_hour": 5, "photos_per_hour": 5, "call_minutes": 10,
    "turns_per_call": 2, "concurrent_calls": 10,
    "history_turns": 20, "exempting_llm": True, "exempting_tts": True, "exempting_stt": True,
}


@pytest.fixture
def admins(settings: Settings) -> Settings:
    return replace(
        settings,
        admins=frozenset({("github", "1001")}),
        usage_limits=UsageLimits(turns_per_call=2),
    )


async def test_the_turns_per_hour_saved_hold_from_the_next_turn(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(*[REPLY] * 4))
    client = await admin_client(
        lambda *a, **k: make_client(*a, clock=FakeClock(), **k), admins, github, clients
    )
    conversation_id = await start_conversation(client)
    before = (await client.get(CONFIG)).json()["limits"]

    saved = await client.put(LIMITS, json=ENV_LIMITS | {"turns_per_hour": 2})
    assert saved.status_code == 200, saved.text
    allowed = [await post_turn(client, conversation_id) for _ in range(2)]
    refused = await post_turn(client, conversation_id)
    unlimited = await client.put(LIMITS, json=ENV_LIMITS | {"turns_per_hour": 0})
    again = await post_turn(client, conversation_id)
    restored = await client.delete(LIMITS)

    assert before == {
        "status": "env", "env": ENV_LIMITS, "effective": ENV_LIMITS, "override": None,
        "failure": None,
    }
    assert saved.json()["limits"]["override"] == {"turns_per_hour": 2}
    assert [res.status_code for res in allowed] == [200, 200]
    assert refused.status_code == 429
    assert "每小时 2 次" in refused.json()["detail"]
    assert unlimited.json()["limits"]["effective"]["turns_per_hour"] == 0
    assert again.status_code == 200
    assert restored.json()["limits"] == before


async def test_a_call_keeps_its_own_limits_from_when_it_connected(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    clients = FakeServiceClients(FakeLLM(*[REPLY] * 3))
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)
    await browser.call(client, conversation_id=conversation_id)
    await browser.connect()
    assert browser.pc_id is not None

    saved = await client.put(
        LIMITS, json=ENV_LIMITS | {"turns_per_call": 1, "call_minutes": 30}
    )
    assert saved.status_code == 200, saved.text
    for said in ("早上好", "你好"):
        await speech.stts[-1].hear(said)
        await browser.turn()
    ended_first = await browser.next_server_message("ended")
    await until_gone(client, browser.pc_id)
    ice = (await client.get("/api/ice-servers")).json()
    async with open_browser() as again:
        await again.call(client, conversation_id=conversation_id)
        await again.connect()
        await speech.stts[-1].hear("再见")
        await again.turn()
        ended_next = await again.next_server_message("ended")

    assert "2 轮" in ended_first["message"]
    assert "1 轮" in ended_next["message"]
    assert ice["maxCallSeconds"] == 30 * 60


async def test_the_calls_at_once_saved_hold_from_the_next_call(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, browser: Browser
):
    client = await admin_client(make_client, admins, github, FakeServiceClients(FakeLLM()))
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()

    saved = await client.put(LIMITS, json=ENV_LIMITS | {"concurrent_calls": 1})
    async with another_user(client, "hazuki") as other:
        theirs = await start_conversation(other)
        refused = await other.get("/api/ice-servers", params={"conversation": theirs})
        await client.put(LIMITS, json=ENV_LIMITS | {"concurrent_calls": 0})
        unlimited = await other.get("/api/ice-servers", params={"conversation": theirs})

    assert saved.status_code == 200, saved.text
    assert refused.status_code == 503
    assert unlimited.status_code == 200


async def test_the_history_saved_is_what_the_next_turn_carries(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(*[REPLY] * 4))
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)
    for said in ("一", "二", "三"):
        await send_turn(client, conversation_id, said)

    saved = await client.put(LIMITS, json=ENV_LIMITS | {"history_turns": 1})
    assert saved.status_code == 200, saved.text
    await send_turn(client, conversation_id, "四")

    said_before = [m.content for m in clients.llm.prompts[2] if m.role == "user"]
    said_after = [m.content for m in clients.llm.prompts[3] if m.role == "user"]
    assert said_before == ["一", "二", "三"]
    assert said_after == ["三", "四"]


async def test_a_limit_that_is_not_a_whole_number_from_0_up_is_refused_saying_why(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    client = await admin_client(make_client, admins, github, FakeServiceClients())

    for bad in (-1, 1.5):
        res = await client.put(LIMITS, json=ENV_LIMITS | {"calls_per_hour": bad})
        assert res.status_code == 422, bad
        assert "每小时通话次数" in res.json()["detail"] and "非负整数" in res.json()["detail"], bad
    assert (await client.get(CONFIG)).json()["limits"]["status"] == "env"
