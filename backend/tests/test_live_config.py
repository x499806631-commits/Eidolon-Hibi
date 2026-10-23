"""The live configuration: admins change the deployer's chat model in the admin console
(ADR-0013). A change is tried before it is kept, then plays the next turn and the next
call, while calls already connected keep the model they connected with; it overrides
`.env` until restored, and one that no longer works when the server starts falls back to
`.env`, marked as such. The clients are made by a fake `ServiceClients`."""

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from eidolon.assembly import Backend
from eidolon.config import LLMSettings, Settings
from tests.browser import Browser, open_browser, until_gone
from tests.conftest import ClientFactory, agree_to_terms, github_identity, sign_in_with
from tests.fakes import (
    DEPLOYER_KEY,
    FakeIdentityProvider,
    FakeLLM,
    FakeServiceClients,
    FakeSpeech,
)
from tests.test_text_turn import send_turn, start_conversation

CONFIG = "/api/admin/config"
CHAT_LLM = "/api/admin/config/chat_llm"
REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]

NEW_KEY = "sk-new-key-5678"
NEW = {
    "protocol": "anthropic",
    "base_url": "https://llm.example/v1",
    "model": "new-model",
    "max_tokens": 1024,
    "reasoning_effort": "high",
    "extra_body": {"top_k": 5},
    "call_reasoning_effort": "low",
    "api_key": NEW_KEY,
}
NEW_SETTINGS = LLMSettings(
    "https://llm.example/v1", NEW_KEY, "new-model", "anthropic", 1024, "high", {"top_k": 5}
)
NEW_FOLLOWED = LLMSettings("https://llm.example/v1", NEW_KEY, "new-model", "anthropic")
"""The memory model following the chat model NEW: where it is and which alone."""
NEW_SHOWN = {k: v for k, v in NEW.items() if k != "api_key"} | {"api_key_last4": "5678"}
ENV_SETTINGS = LLMSettings("http://llm.invalid/v1", DEPLOYER_KEY, "test-model")
ENV_SHOWN = {
    "protocol": "chat-completions",
    "base_url": "http://llm.invalid/v1",
    "model": "test-model",
    "max_tokens": None,
    "reasoning_effort": None,
    "extra_body": None,
    "call_reasoning_effort": None,
    "api_key_last4": DEPLOYER_KEY[-4:],
}


@pytest.fixture
def admins(settings: Settings) -> Settings:
    return replace(settings, admins=frozenset({("github", "1001")}))


async def admin_client(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    clients: FakeServiceClients,
) -> httpx.AsyncClient:
    """A client signed in as the admin, on a backend whose chat models `clients` makes."""
    client = make_client(clients.llm, user=None, settings=settings, clients=clients)
    await sign_in_with(client, github, github_identity("1001"))
    await agree_to_terms(client)
    return client


def recorded(client: httpx.AsyncClient, service: str) -> list[tuple[str, str, bool]]:
    """The service calls to `service` recorded so far, by provider, model and whether on
    the user's own key, sorted."""
    backend: Backend = client._transport.app.state.backend  # pyright: ignore[reportAttributeAccessIssue]
    with closing(sqlite3.connect(backend.settings.database_path)) as db:
        calls = db.execute(
            "SELECT provider, model, own_key FROM service_calls WHERE service = ? AND at > ?",
            (service, (backend.clock() - timedelta(days=1)).timestamp()),
        ).fetchall()
    return sorted((provider, model, bool(own)) for provider, model, own in calls)


async def until(done: Callable[[], object]) -> None:
    """Wait, a while at most, for `done` to come true, as what a call let go of closes."""
    for _ in range(100):
        if done():
            return
        await asyncio.sleep(0.02)


async def start(client: httpx.AsyncClient) -> None:
    """What the server does as it starts, before serving anyone."""
    backend: Backend = client._transport.app.state.backend  # pyright: ignore[reportAttributeAccessIssue]
    await backend.start()


async def test_a_saved_chat_model_plays_the_next_turn_until_restored(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY))
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)
    before = (await client.get(CONFIG)).json()
    await send_turn(client, conversation_id, "早上好")

    saved = await client.put(CHAT_LLM, json=NEW)
    assert saved.status_code == 200, saved.text
    await send_turn(client, conversation_id, "你好")
    shown = (await client.get(CONFIG)).json()
    restored = await client.delete(CHAT_LLM)
    assert restored.status_code == 200, restored.text
    await send_turn(client, conversation_id, "再见")

    unchanged = {"status": "env", "env": ENV_SHOWN, "effective": ENV_SHOWN, "override": None,
                 "failure": None}
    assert before["keys_editable"] is True and before["chat_llm"] == unchanged
    assert shown == saved.json()
    assert shown["chat_llm"] == {"status": "overridden", "env": ENV_SHOWN, "effective": NEW_SHOWN,
                                 "override": NEW_SHOWN, "failure": None}
    assert NEW_KEY not in saved.text and DEPLOYER_KEY not in saved.text
    assert restored.json() == before
    assert clients.llm.keys == [DEPLOYER_KEY, NEW_KEY, DEPLOYER_KEY]
    assert clients.opened == [ENV_SETTINGS, NEW_SETTINGS, ENV_SETTINGS]
    # Each once no turn was on it, the memory model following the chat model's each time.
    assert clients.closed == [ENV_SETTINGS, ENV_SETTINGS, NEW_SETTINGS, NEW_FOLLOWED]


async def test_a_chat_model_that_fails_its_try_is_not_saved(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    clients.rejects[NEW_KEY] = "401 invalid api key"
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)

    res = await client.put(CHAT_LLM, json=NEW)
    await send_turn(client, conversation_id, "早上好")

    assert res.status_code == 422
    assert "401 invalid api key" in res.json()["detail"]
    assert (await client.get(CONFIG)).json()["chat_llm"]["status"] == "env"
    assert clients.llm.keys == [DEPLOYER_KEY]
    assert clients.closed == [NEW_SETTINGS]


async def test_a_call_keeps_the_chat_model_it_connected_with(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)
    assert (await client.put(CHAT_LLM, json=NEW)).status_code == 200
    await browser.call(client, conversation_id=conversation_id)
    await browser.connect()
    assert browser.pc_id is not None

    other = NEW | {"api_key": "sk-other-key-9999", "model": "other-model"}
    assert (await client.put(CHAT_LLM, json=other)).status_code == 200
    await speech.stts[-1].hear("早上好")
    await browser.turn()
    assert clients.llm.keys == [NEW_KEY]
    assert clients.closed == [ENV_SETTINGS, ENV_SETTINGS]  # the call is still on the new one

    await browser.close()
    await until_gone(client, browser.pc_id)
    async with open_browser() as again:
        await again.call(client, conversation_id=conversation_id)
        await again.connect()
        await speech.stts[-1].hear("你好")
        await again.turn()

    assert clients.llm.keys == [NEW_KEY, "sk-other-key-9999"]
    assert clients.closed == [ENV_SETTINGS, ENV_SETTINGS, NEW_SETTINGS, NEW_FOLLOWED]
    # Each call's turn is recorded as the model it connected with.
    await until(lambda: len(recorded(client, "chat_llm")) == 2)  # as the call is written
    assert recorded(client, "chat_llm") == [
        ("llm.example", "new-model", False), ("llm.example", "other-model", False)
    ]


# The chat model's two reasoning efforts: text turns go at one, calls' turns at the other.

async def call_turn(client: httpx.AsyncClient, speech: FakeSpeech, conversation_id: str) -> None:
    """One turn of a call placed and hung up on the conversation."""
    async with open_browser() as browser:
        await browser.call(client, conversation_id=conversation_id)
        await browser.connect()
        await speech.stts[-1].hear("早上好")
        await browser.turn()
        await browser.close()
        assert browser.pc_id is not None
        await until_gone(client, browser.pc_id)


async def test_text_turns_go_at_the_text_effort_and_calls_at_the_call_effort(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)

    saved = await client.put(CHAT_LLM, json=NEW | {"call_reasoning_effort": "none"})
    assert saved.status_code == 200, saved.text
    await send_turn(client, conversation_id, "你好")
    await call_turn(client, speech, conversation_id)

    shown = saved.json()["chat_llm"]
    assert shown["effective"]["reasoning_effort"] == "high"
    assert shown["effective"]["call_reasoning_effort"] == "none"
    assert clients.efforts == ["high", "none"]
    assert clients.llm.keys == [NEW_KEY, NEW_KEY]
    # One model, at two efforts, recorded as the one model it is.
    await until(lambda: len(recorded(client, "chat_llm")) == 2)
    assert recorded(client, "chat_llm") == [("llm.example", "new-model", False)] * 2


async def test_calls_go_at_no_effort_unless_set_whatever_the_text_effort(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    env = replace(admins, chat_llm=replace(ENV_SETTINGS, reasoning_effort="high"))
    client = await admin_client(make_client, env, github, clients)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "你好")
    await call_turn(client, speech, conversation_id)

    assert clients.efforts == ["high", None]


async def test_a_call_effort_set_in_env_holds_until_overridden_and_again_once_restored(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY))
    env = replace(admins, call_reasoning_effort="low")
    client = await admin_client(make_client, env, github, clients)
    conversation_id = await start_conversation(client)
    before = (await client.get(CONFIG)).json()["chat_llm"]
    shown_env = {k: v for k, v in before["env"].items() if k != "api_key_last4"}

    saved = await client.put(CHAT_LLM, json=shown_env | {"call_reasoning_effort": "none"})
    assert saved.status_code == 200, saved.text
    await call_turn(client, speech, conversation_id)
    restored = await client.delete(CHAT_LLM)
    await call_turn(client, speech, conversation_id)
    await send_turn(client, conversation_id, "你好")

    assert before["env"]["call_reasoning_effort"] == "low"
    assert saved.json()["chat_llm"]["override"] == {"call_reasoning_effort": "none"}
    assert restored.json()["chat_llm"] == before
    assert clients.efforts == ["none", "low", None]


@pytest.mark.parametrize(
    ("text", "call", "checked"),
    [("high", "none", ["high", "none"]), ("low", "low", ["low"])],
)
async def test_the_two_efforts_are_each_tried_once_or_once_for_both_if_the_same(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    text: str | None,
    call: str | None,
    checked: list[str | None],
):
    clients = FakeServiceClients()
    client = await admin_client(make_client, admins, github, clients)

    change = NEW | {"reasoning_effort": text, "call_reasoning_effort": call}
    res = await client.put(CHAT_LLM, json=change)

    assert res.status_code == 200, res.text
    assert clients.efforts_checked == checked


@pytest.mark.parametrize(
    ("refused", "call", "said"),
    [
        ("high", "none", "文字的思考强度『高』被服务拒绝："),
        ("none", "none", "通话的思考强度『关闭』被服务拒绝："),
        ("high", "high", "思考强度『高』被服务拒绝："),  # the same: said once, as neither
    ],
)
async def test_an_effort_the_service_refuses_is_not_saved_saying_whether_text_or_call(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    refused: str,
    call: str,
    said: str,
):
    clients = FakeServiceClients()
    clients.refused_efforts[refused] = f"400 '{refused}' is not supported with this model"
    client = await admin_client(make_client, admins, github, clients)

    res = await client.put(CHAT_LLM, json=NEW | {"call_reasoning_effort": call})

    assert res.status_code == 422
    assert res.json()["detail"].startswith(f"试调失败，没有保存：{said}400 '{refused}'")
    assert (await client.get(CONFIG)).json()["chat_llm"]["status"] == "env"


async def test_a_call_effort_the_protocol_cannot_write_is_refused_untried(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients()
    client = await admin_client(make_client, admins, github, clients)

    res = await client.put(CHAT_LLM, json=NEW | {"call_reasoning_effort": "minimal"})

    assert res.status_code == 422
    assert "Anthropic 接口没有「极低」" in res.json()["detail"]
    assert clients.opened == [ENV_SETTINGS]  # not even tried


async def test_without_the_secret_key_the_chat_models_key_is_read_only(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    client = await admin_client(make_client, replace(admins, secret_key=None), github, clients)
    conversation_id = await start_conversation(client)

    with_key = await client.put(CHAT_LLM, json=NEW)
    without_key = await client.put(CHAT_LLM, json={k: v for k, v in NEW.items() if k != "api_key"})
    await send_turn(client, conversation_id, "早上好")

    assert with_key.status_code == 409
    assert "EIDOLON_SECRET_KEY" in with_key.json()["detail"]
    assert without_key.status_code == 200, without_key.text
    shown = without_key.json()
    assert shown["keys_editable"] is False
    assert shown["chat_llm"]["effective"] == NEW_SHOWN | {"api_key_last4": DEPLOYER_KEY[-4:]}
    assert clients.opened == [ENV_SETTINGS, replace(NEW_SETTINGS, api_key=DEPLOYER_KEY)]
    assert clients.llm.keys == [DEPLOYER_KEY]


async def test_an_override_that_fails_its_try_as_the_server_starts_falls_back_to_env(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    first = await admin_client(make_client, admins, github, FakeServiceClients())
    assert (await first.put(CHAT_LLM, json=NEW)).status_code == 200

    # Restarted, the override still works.
    working = FakeServiceClients(FakeLLM(REPLY))
    restarted = await admin_client(make_client, admins, github, working)
    await start(restarted)
    await send_turn(restarted, await start_conversation(restarted), "早上好")
    assert working.llm.keys == [NEW_KEY]
    assert "chat_llm" in caplog.text and "overrides in effect" in caplog.text

    # Restarted again after the key was revoked.
    caplog.clear()
    revoked = FakeServiceClients(FakeLLM(REPLY))
    revoked.rejects[NEW_KEY] = "401 key revoked"
    again = await admin_client(make_client, admins, github, revoked)
    await start(again)
    await send_turn(again, await start_conversation(again), "早上好")

    shown = (await again.get(CONFIG)).json()["chat_llm"]
    assert shown["status"] == "failed"
    assert "401 key revoked" in shown["failure"]
    assert shown["effective"] == ENV_SHOWN and shown["override"] == NEW_SHOWN
    assert revoked.llm.keys == [DEPLOYER_KEY]
    assert any(
        r.levelno == logging.WARNING and "401 key revoked" in r.getMessage()
        for r in caplog.records
    )


async def test_a_key_the_changed_secret_key_cannot_decrypt_falls_back_with_a_warning(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    caplog: pytest.LogCaptureFixture,
):
    first = await admin_client(make_client, admins, github, FakeServiceClients())
    assert (await first.put(CHAT_LLM, json=NEW)).status_code == 200

    caplog.set_level(logging.INFO)
    changed = replace(admins, secret_key="another-secret-key-" + "0" * 45)
    restarted = await admin_client(make_client, changed, github, FakeServiceClients(FakeLLM(REPLY)))
    await start(restarted)

    assert (await restarted.get(CONFIG)).json()["chat_llm"]["status"] == "failed"
    assert any(
        r.levelno == logging.WARNING and "chat_llm" in r.getMessage()
        and "EIDOLON_SECRET_KEY" in r.getMessage()
        for r in caplog.records
    )


async def test_no_one_but_admins_finds_the_configuration(
    make_client: ClientFactory, admins: Settings
):
    producer = make_client(FakeLLM(), settings=admins)
    nobody = make_client(FakeLLM(), user=None, settings=admins)

    for client in (producer, nobody):
        assert (await client.get(CONFIG)).status_code == 404
        assert (await client.put(CHAT_LLM, json=NEW)).status_code == 404
        assert (await client.delete(CHAT_LLM)).status_code == 404
