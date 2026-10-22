"""The live configuration of the services besides the chat model (ADR-0013): the memory
model, speech synthesis and recognition, the judge model with its thresholds, and the
reranker's thresholds. Each change is tried before it is kept (the thresholds are not),
then holds for the next turn or call, while calls already connected keep what they
connected with. What cannot be changed in the admin console is only shown. The clients
are made by a fake `ServiceClients`, the speech services' keys tried on `FakeSpeech`."""

import asyncio
import logging
import sqlite3
from dataclasses import replace

import httpx
import pytest

from eidolon.assembly import Backend
from eidolon.config import (
    DEFAULT_FISH_AUDIO_MODEL,
    BailianJudgeSettings,
    CloudflareJudgeSettings,
    JevSettings,
    LLMSettings,
    RerankSettings,
    Settings,
    SttKey,
)
from tests.browser import Browser, open_browser, until_gone
from tests.conftest import ClientFactory
from tests.fakes import (
    DEPLOYER_FISH_KEY,
    DEPLOYER_KEY,
    DEPLOYER_SONIOX_KEY,
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeIdentityProvider,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeServiceClients,
    FakeSpeech,
    context_of,
)
from tests.test_live_config import (
    CONFIG,
    NEW,
    NEW_SETTINGS,
    NEW_SHOWN,
    REPLY,
    admin_client,
    recorded,
    start,
    until,
)
from tests.test_text_turn import send_turn, start_conversation

MEMORY_LLM = "/api/admin/config/memory_llm"
MEMORY_KEY = "sk-memory-key-4321"
NEW_MEMORY = NEW | {"api_key": MEMORY_KEY, "model": "memory-model"}
NEW_MEMORY_SETTINGS = LLMSettings(
    "https://llm.example/v1", MEMORY_KEY, "memory-model", "anthropic", 1024, "high", {"top_k": 5}
)
ENV_SETTINGS = LLMSettings("http://llm.invalid/v1", DEPLOYER_KEY, "test-model")
CAT = '{"memories": ["制作人养了一只猫"]}'


@pytest.fixture
def admins(settings: Settings) -> Settings:
    return replace(settings, admins=frozenset({("github", "1001")}))


def with_memory(
    make_client: ClientFactory,
    background: FakeBackgroundLLM | None = None,
    judge: FakeJudge | None = None,
):
    """A client factory for backends with memory on, every memory model writing down from
    `background`'s script, if given; turns need what `judge` says (nothing, by default)."""

    def factory(*args, **kwargs) -> httpx.AsyncClient:
        if background is not None:
            kwargs["background_llm"] = background
        return make_client(
            *args, retrieval_judge=judge or FakeJudge("nothing"), embedder=FakeEmbedder(),
            reranker=FakeReranker(), **kwargs,
        )

    return factory


async def settle(client: httpx.AsyncClient) -> list[dict]:
    """The user's memories, once what their turns are writing down is written."""
    res = await client.get("/api/memories")
    assert res.status_code == 200, res.text
    return res.json()


async def test_a_saved_memory_model_writes_down_the_next_turns_memories_until_restored(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY))
    client = await admin_client(with_memory(make_client), admins, github, clients)
    conversation_id = await start_conversation(client)
    before = (await client.get(CONFIG)).json()["memory_llm"]
    await send_turn(client, conversation_id, "早上好")
    await settle(client)

    saved = await client.put(MEMORY_LLM, json=NEW_MEMORY)
    assert saved.status_code == 200, saved.text
    await send_turn(client, conversation_id, "你好")
    await settle(client)
    restored = await client.delete(MEMORY_LLM)
    assert restored.status_code == 200, restored.text
    await send_turn(client, conversation_id, "再见")
    await settle(client)
    await client.get("/api/conversations", params={"character_id": "mano"})  # named, too

    assert before["status"] == "env" and before["follows_chat"] is True
    shown = saved.json()["memory_llm"]
    assert shown["status"] == "overridden" and shown["follows_chat"] is False
    assert shown["effective"]["model"] == "memory-model"
    assert shown["effective"]["api_key_last4"] == "4321"
    assert MEMORY_KEY not in saved.text
    assert restored.json()["memory_llm"] == before
    # The third turn's model names the conversation after it, as well as writing down.
    assert clients.memory_keys == [DEPLOYER_KEY, MEMORY_KEY, DEPLOYER_KEY, DEPLOYER_KEY]
    assert clients.llm.keys == [DEPLOYER_KEY] * 3  # the chat model is untouched
    assert clients.memory_opened == [ENV_SETTINGS, NEW_MEMORY_SETTINGS, ENV_SETTINGS]


async def test_unless_set_apart_the_memory_model_follows_the_chat_models_connection_saved(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    client = await admin_client(with_memory(make_client, FakeBackgroundLLM(CAT)), admins, github, clients)
    conversation_id = await start_conversation(client)

    saved = await client.put("/api/admin/config/chat_llm", json=NEW)
    await send_turn(client, conversation_id, "我养了一只猫")
    memories = await settle(client)

    memory = saved.json()["memory_llm"]
    assert memory["status"] == "env" and memory["follows_chat"] is True
    # Where the model is and which, not how hard it thinks, how long it writes or what
    # else its requests carry: those stay the memory model's own.
    connection = {"protocol", "base_url", "model", "api_key_last4"}
    assert memory["effective"] == {k: v for k, v in NEW_SHOWN.items() if k in connection} | {
        "max_tokens": None, "reasoning_effort": None, "extra_body": None,
    }
    assert [m["text"] for m in memories] == ["制作人养了一只猫"]
    assert clients.llm.keys == [NEW["api_key"]]
    assert clients.memory_keys == [NEW["api_key"]]
    assert clients.memory_opened[-1] == LLMSettings(
        "https://llm.example/v1", NEW["api_key"], "new-model", "anthropic"
    )


async def test_a_chat_models_effort_set_in_env_alone_is_not_the_memory_models(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    chat = replace(ENV_SETTINGS, max_tokens=512, reasoning_effort="none", extra_body={"a": 1})
    env = replace(admins, chat_llm=chat, background_llm=ENV_SETTINGS)
    client = await admin_client(with_memory(make_client), env, github, clients)

    saved = await client.put("/api/admin/config/chat_llm", json=NEW)

    memory = saved.json()["memory_llm"]
    assert memory["follows_chat"] is True
    assert memory["effective"]["model"] == "new-model"
    assert memory["effective"]["reasoning_effort"] is None
    assert clients.memory_opened == [ENV_SETTINGS, replace(
        ENV_SETTINGS, base_url="https://llm.example/v1", api_key=NEW["api_key"],
        model="new-model", protocol="anthropic",
    )]


async def test_a_chat_model_the_memory_models_own_effort_cannot_be_written_for_is_refused(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients()
    env = replace(admins, background_llm=replace(ENV_SETTINGS, reasoning_effort="minimal"))
    client = await admin_client(with_memory(make_client), env, github, clients)

    res = await client.put("/api/admin/config/chat_llm", json=NEW)  # on Anthropic

    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "记忆模型" in detail and "Anthropic 接口没有「极低」" in detail
    assert (await client.get(CONFIG)).json()["chat_llm"]["status"] == "env"
    assert clients.memory_opened == [replace(ENV_SETTINGS, reasoning_effort="minimal")]


async def test_a_memory_model_following_is_tried_at_its_own_effort_on_the_chat_model_saved(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients()
    clients.refused_efforts["xhigh"] = "400 'xhigh' is not supported with this model"
    memory_env = replace(ENV_SETTINGS, reasoning_effort="xhigh")
    env = replace(admins, background_llm=memory_env)
    client = await admin_client(with_memory(make_client), env, github, clients)

    refused = await client.put("/api/admin/config/chat_llm", json=NEW)
    del clients.refused_efforts["xhigh"]
    saved = await client.put("/api/admin/config/chat_llm", json=NEW)

    assert refused.status_code == 422
    assert "记忆模型" in refused.json()["detail"]
    assert "思考强度『极高』被服务拒绝：400 'xhigh'" in refused.json()["detail"]
    assert saved.status_code == 200, saved.text
    followed = LLMSettings(
        "https://llm.example/v1", NEW["api_key"], "new-model", "anthropic", reasoning_effort="xhigh"
    )
    # Tried each time, kept as it was tried the second.
    assert clients.memory_opened == [memory_env, followed, followed]
    assert clients.closed == [followed, NEW_SETTINGS, ENV_SETTINGS, memory_env]


async def test_a_memory_model_following_is_untouched_by_a_change_of_the_chat_models_efforts(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    client = await admin_client(with_memory(make_client), admins, github, clients)
    shown_env = (await client.get(CONFIG)).json()["chat_llm"]["env"]
    change = {k: v for k, v in shown_env.items() if k != "api_key_last4"}

    saved = await client.put(
        "/api/admin/config/chat_llm",
        json=change | {"reasoning_effort": "high", "call_reasoning_effort": "none"},
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["memory_llm"]["follows_chat"] is True
    assert clients.memory_opened == [ENV_SETTINGS]  # the same model, as it was


async def test_a_memory_model_set_apart_in_env_does_not_follow_the_chat_model(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    apart = replace(admins, background_llm=replace(ENV_SETTINGS, model="background-model"))
    client = await admin_client(with_memory(make_client), apart, github, clients)
    conversation_id = await start_conversation(client)

    saved = await client.put("/api/admin/config/chat_llm", json=NEW)
    await send_turn(client, conversation_id, "我养了一只猫")
    await settle(client)

    memory = saved.json()["memory_llm"]
    assert memory["follows_chat"] is False
    assert memory["effective"]["model"] == "background-model"
    assert clients.memory_keys == [DEPLOYER_KEY]
    assert clients.memory_opened == [replace(ENV_SETTINGS, model="background-model")]
    assert clients.llm.keys == [NEW["api_key"]]


async def test_a_memory_model_replaced_while_writing_down_is_closed_once_written(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    gate = asyncio.Event()
    clients.background = FakeBackgroundLLM(CAT, gate=gate)
    client = await admin_client(with_memory(make_client), admins, github, clients)
    conversation_id = await start_conversation(client)
    assert (await client.put(MEMORY_LLM, json=NEW_MEMORY)).status_code == 200
    await send_turn(client, conversation_id, "我养了一只猫")  # written down after the turn

    other = NEW_MEMORY | {"api_key": "sk-other-key-9999"}
    assert (await client.put(MEMORY_LLM, json=other)).status_code == 200
    closed_while_writing = list(clients.closed)
    gate.set()
    memories = await settle(client)

    assert NEW_MEMORY_SETTINGS not in closed_while_writing
    assert [m["text"] for m in memories] == ["制作人养了一只猫"]
    assert clients.closed == [ENV_SETTINGS, NEW_MEMORY_SETTINGS]


# The memory model's reasoning effort and extra request body, saved and tried as the rest.

async def test_a_memory_models_effort_and_extra_body_are_saved_and_tried_with(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY))
    client = await admin_client(with_memory(make_client), admins, github, clients)
    change = NEW_MEMORY | {
        "protocol": "chat-completions",
        "reasoning_effort": "minimal",
        "extra_body": {"enable_thinking": False, "reasoning": {"summary": "auto"}},
    }

    saved = await client.put(MEMORY_LLM, json=change)

    assert saved.status_code == 200, saved.text
    shown = saved.json()["memory_llm"]
    assert shown["effective"]["reasoning_effort"] == "minimal"
    assert shown["override"]["extra_body"] == change["extra_body"]
    assert clients.memory_opened[-1] == replace(
        NEW_MEMORY_SETTINGS,
        protocol="chat-completions",
        reasoning_effort="minimal",
        extra_body=change["extra_body"],
    )


async def test_a_memory_models_effort_left_as_env_has_it_follows_env(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    env = replace(ENV_SETTINGS, model="background-model", reasoning_effort="low")
    apart = replace(admins, background_llm=env)
    client = await admin_client(with_memory(make_client), apart, github, FakeServiceClients())
    shown_env = (await client.get(CONFIG)).json()["memory_llm"]["env"]
    change = {k: v for k, v in shown_env.items() if k != "api_key_last4"} | {"model": "other"}

    saved = await client.put(MEMORY_LLM, json=change)

    assert saved.status_code == 200, saved.text
    shown = saved.json()["memory_llm"]
    assert shown["override"] == {"model": "other"}
    assert shown["effective"]["reasoning_effort"] == "low"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"extra_body": {"model": "x"}}, "「model」"),
        ({"reasoning_effort": "minimal"}, "Anthropic 接口没有「极低」"),
    ],
)
async def test_a_memory_model_with_an_effort_or_extra_body_it_cannot_take_is_refused(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    change: dict,
    reason: str,
):
    clients = FakeServiceClients()
    client = await admin_client(with_memory(make_client), admins, github, clients)

    res = await client.put(MEMORY_LLM, json=NEW_MEMORY | change)

    assert res.status_code == 422
    assert reason in res.json()["detail"]
    assert (await client.get(CONFIG)).json()["memory_llm"]["status"] == "env"
    assert clients.memory_opened == [ENV_SETTINGS]  # not even tried


async def test_a_memory_model_whose_effort_the_service_refuses_is_not_saved_saying_so(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients()
    said = "400 Unsupported value: 'high' is not supported with this model. " + "x" * 400
    clients.rejects[MEMORY_KEY] = said
    client = await admin_client(with_memory(make_client), admins, github, clients)

    res = await client.put(MEMORY_LLM, json=NEW_MEMORY)

    assert res.status_code == 422
    detail = res.json()["detail"]
    # Cut short, what the service said is cut at its end, never its start.
    assert "思考强度『高』被服务拒绝：400 Unsupported value: 'high' is not supported" in detail
    assert len(detail) < len(said)
    assert (await client.get(CONFIG)).json()["memory_llm"]["status"] == "env"


def backend_of(client: httpx.AsyncClient) -> Backend:
    return client._transport.app.state.backend  # pyright: ignore[reportAttributeAccessIssue]


TTS = "/api/admin/config/tts"
STT = "/api/admin/config/stt"
NEW_FISH = "new-fish-key-0001"
NEW_DASHSCOPE = "new-dashscope-key-0002"


async def test_speech_saved_during_a_call_holds_from_the_next_call(
    make_client: ClientFactory,
    admins: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    speech.regions[NEW_DASHSCOPE] = "singapore"
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    client = await admin_client(make_client, admins, github, clients)
    conversation_id = await start_conversation(client)
    await browser.call(client, conversation_id=conversation_id)
    await browser.connect()
    assert browser.pc_id is not None

    tts = await client.put(TTS, json={"api_key": NEW_FISH, "model": "drama-3"})
    stt = await client.put(STT, json={"provider": "bailian", "api_key": NEW_DASHSCOPE})
    assert tts.status_code == 200, tts.text
    assert stt.status_code == 200, stt.text
    await speech.stts[-1].hear("早上好")
    await browser.turn()
    await browser.close()
    await until_gone(client, browser.pc_id)
    async with open_browser() as again:
        await again.call(client, conversation_id=conversation_id)
        await again.connect()

    assert [(t.api_key, t.model) for t in speech.ttss] == [
        (DEPLOYER_FISH_KEY, DEFAULT_FISH_AUDIO_MODEL), (NEW_FISH, "drama-3")
    ]
    assert [s.key for s in speech.stts] == [
        SttKey("soniox", DEPLOYER_SONIOX_KEY), SttKey("bailian", NEW_DASHSCOPE, "singapore")
    ]
    assert speech.checked == [("tts", NEW_FISH), ("bailian", NEW_DASHSCOPE)]
    assert speech.tts_models == ["drama-3"]  # tried with the model it is to speak with
    shown = stt.json()
    assert shown["tts"]["effective"] == {
        "api_key_last4": "0001", "engine": "fish", "model": "drama-3", "read_engine": "same",
        "read_model": "same", "gpt_sovits_url": "http://127.0.0.1:9880",
    }
    assert shown["stt"]["effective"] == {
        "provider": "bailian", "soniox_api_key_last4": DEPLOYER_SONIOX_KEY[-4:],
        "dashscope_api_key_last4": "0002", "region": "singapore",
    }
    assert NEW_FISH not in stt.text and NEW_DASHSCOPE not in stt.text


async def test_a_speech_override_kept_before_there_was_a_list_of_models_falls_back_to_env(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    first = await admin_client(make_client, admins, github, FakeServiceClients())
    assert (await first.put(TTS, json={"api_key": NEW_FISH, "model": "drama-3"})).status_code == 200
    with sqlite3.connect(admins.database_path) as db:  # as an admin once typed it in
        db.execute("UPDATE config_overrides SET value = ? WHERE name = 'tts.model'", ('"speech-1.6"',))

    restarted = await admin_client(make_client, admins, github, FakeServiceClients())
    await start(restarted)
    lease = backend_of(restarted).config.lease()
    lease.release()

    shown = (await restarted.get(CONFIG)).json()["tts"]
    assert shown["status"] == "failed" and "speech-1.6" in shown["failure"]
    assert (lease.keys.tts, lease.keys.tts_model) == (DEPLOYER_FISH_KEY, DEFAULT_FISH_AUDIO_MODEL)


async def test_a_speech_key_that_fails_its_try_is_not_saved(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    speech.rejects[NEW_FISH] = "401 invalid fish key"
    speech.rejects[NEW_DASHSCOPE] = "InvalidApiKey"
    client = await admin_client(make_client, admins, github, FakeServiceClients())

    tts = await client.put(TTS, json={"api_key": NEW_FISH, "model": "s1"})
    stt = await client.put(STT, json={"provider": "bailian", "api_key": NEW_DASHSCOPE})
    no_key = await client.put(STT, json={"provider": "bailian"})

    for res, said in ((tts, "401 invalid fish key"), (stt, "InvalidApiKey"), (no_key, "key")):
        assert res.status_code == 422, said
        assert said in res.json()["detail"], said
    shown = (await client.get(CONFIG)).json()
    assert shown["tts"]["status"] == shown["stt"]["status"] == "env"


async def test_a_speech_override_that_fails_as_the_server_starts_falls_back_to_env(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    first = await admin_client(make_client, admins, github, FakeServiceClients())
    assert (await first.put(TTS, json={"api_key": NEW_FISH, "model": "s1"})).status_code == 200

    speech.rejects[NEW_FISH] = "401 key revoked"
    restarted = await admin_client(make_client, admins, github, FakeServiceClients())
    await start(restarted)
    lease = backend_of(restarted).config.lease()
    lease.release()

    shown = (await restarted.get(CONFIG)).json()["tts"]
    assert shown["status"] == "failed" and "401 key revoked" in shown["failure"]
    assert (lease.keys.tts, lease.keys.tts_model) == (DEPLOYER_FISH_KEY, DEFAULT_FISH_AUDIO_MODEL)


async def test_a_speech_recognition_override_that_fails_as_the_server_starts_falls_back_to_env(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    first = await admin_client(make_client, admins, github, FakeServiceClients())
    saved = await first.put(STT, json={"provider": "bailian", "api_key": NEW_DASHSCOPE})
    assert saved.status_code == 200, saved.text

    speech.rejects[NEW_DASHSCOPE] = "InvalidApiKey"
    restarted = await admin_client(make_client, admins, github, FakeServiceClients())
    await start(restarted)
    lease = backend_of(restarted).config.lease()
    lease.release()

    shown = (await restarted.get(CONFIG)).json()["stt"]
    assert shown["status"] == "failed" and "InvalidApiKey" in shown["failure"]
    assert lease.keys.stt == SttKey("soniox", DEPLOYER_SONIOX_KEY)


JUDGE = "/api/admin/config/judge"
JUDGE_THRESHOLDS = "/api/admin/config/judge_thresholds"
JEV_AFFINITY = {"out_of_line": 0.7, "manipulation": 0.4, "photo": 0.5}
"""Jev's thresholds for affinity's two questions and the photo's, saved alongside as they are."""
JEV_KEY = "env-jev-key-1111"
CF_TOKEN = "cf-token-2222"
CLOUDFLARE = {
    "provider": "cloudflare", "api_key": CF_TOKEN, "model": "clef-flash",
    "account_id": "account-1", "emotion_timeout": 1.5,
}


@pytest.fixture
def judged(admins: Settings) -> Settings:
    """Retrieval and emotions judged by Jev, as `.env` has it."""
    return replace(admins, judge=JevSettings(JEV_KEY))


def with_retrieval(make_client: ClientFactory):
    """A client factory for backends that retrieve, with the judge model `.env` has."""

    def factory(*args, **kwargs) -> httpx.AsyncClient:
        return make_client(*args, embedder=FakeEmbedder(), reranker=FakeReranker(), **kwargs)

    return factory


async def test_a_saved_judge_model_judges_the_next_turn_while_a_call_keeps_its_own(
    make_client: ClientFactory,
    judged: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    browser: Browser,
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY))
    # Recalled by Jev's 0.5, not by Clef-flash's 0.7; happy by Clef-flash's 0.4, not Jev's.
    clients.judge.retrieval = {"needs_retrieval": 0.1, "recall": 0.6, "about_producer": 0.1}
    clients.judge.emotion = {"开心": 0.45, "平静": 0.3}
    client = await admin_client(with_retrieval(make_client), judged, github, clients)
    conversation_id = await start_conversation(client)
    await browser.call(client, conversation_id=conversation_id)
    await browser.connect()
    assert browser.pc_id is not None

    saved = await client.put(JUDGE, json=CLOUDFLARE)
    assert saved.status_code == 200, saved.text
    await speech.stts[-1].hear("早上好")
    await browser.turn()
    emotion = await browser.next_server_message("emotion")
    await send_turn(client, conversation_id, "你好")
    asked = list(clients.judge.asked)
    closed_during_call = list(clients.judges_closed)
    await browser.close()
    await until_gone(client, browser.pc_id)
    await until(lambda: clients.judges_closed)

    assert asked == [
        (CF_TOKEN, "check"), (CF_TOKEN, "turn"), (JEV_KEY, "emotion"), (CF_TOKEN, "turn")
    ]
    assert clients.judges_opened == [
        JevSettings(JEV_KEY), CloudflareJudgeSettings(CF_TOKEN, "account-1", "clef-flash")
    ]
    assert closed_during_call == []
    assert clients.judges_closed == [JevSettings(JEV_KEY)]  # once the call was over
    # The call judges her emotion on the model it connected with, by its threshold; each
    # turn's question goes to the model in effect, by its own, recorded as the one it is.
    assert emotion["emotion"] == "calm"
    assert [[t.name for t in tools] for tools in clients.llm.tools] == [
        ["recall_story"], ["recall_story"]
    ]
    await until(lambda: len(recorded(client, "judge")) == 3)  # as the call is written
    assert recorded(client, "judge") == [
        ("api.cloudflare.com", "clef-flash", False),
        ("api.cloudflare.com", "clef-flash", False),
        ("openrouter.ai", "typesafe/jev-1.13", False),
    ]
    shown = saved.json()["judge"]
    assert shown["status"] == "overridden"
    assert shown["effective"] == {k: v for k, v in CLOUDFLARE.items() if k != "api_key"} | {
        "api_key_last4": "2222", "workspace_id": None, "region": None
    }


async def test_a_judge_model_that_fails_its_try_is_not_saved(
    make_client: ClientFactory, judged: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients()
    clients.rejects[CF_TOKEN] = "Cloudflare Workers AI 返回 401"
    client = await admin_client(with_retrieval(make_client), judged, github, clients)

    rejected = await client.put(JUDGE, json=CLOUDFLARE)
    keyless = await client.put(JUDGE, json={k: v for k, v in CLOUDFLARE.items() if k != "api_key"})

    assert rejected.status_code == 422 and "返回 401" in rejected.json()["detail"]
    assert keyless.status_code == 422 and "key" in keyless.json()["detail"]
    assert (await client.get(CONFIG)).json()["judge"]["status"] == "env"
    assert clients.judges_closed == [CloudflareJudgeSettings(CF_TOKEN, "account-1", "clef-flash")]


BL_KEY = "sk-bl-3333"
BAILIAN = {
    "provider": "bailian", "api_key": BL_KEY, "model": "decision-model-preview",
    "workspace_id": "llm-ws1", "emotion_timeout": 1.5,
}


async def test_bailian_saved_judges_the_next_turn_at_its_workspace_in_beijing_unless_told(
    make_client: ClientFactory, judged: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    client = await admin_client(with_retrieval(make_client), judged, github, clients)
    conversation_id = await start_conversation(client)

    workspaceless = await client.put(JUDGE, json=BAILIAN | {"workspace_id": None})
    saved = await client.put(JUDGE, json=BAILIAN)
    await send_turn(client, conversation_id, "你好")
    singapore = await client.put(JUDGE, json=BAILIAN | {"region": "singapore"})

    assert workspaceless.status_code == 422 and "业务空间" in workspaceless.json()["detail"]
    assert saved.status_code == 200, saved.text
    assert singapore.status_code == 200, singapore.text
    assert clients.judges_opened[1:] == [
        BailianJudgeSettings(BL_KEY, "llm-ws1"),
        BailianJudgeSettings(BL_KEY, "llm-ws1", "singapore"),
    ]
    assert (BL_KEY, "turn") in clients.judge.asked
    await until(lambda: recorded(client, "judge"))
    assert recorded(client, "judge") == [
        ("cn-beijing.maas.aliyuncs.com", "decision-model-preview", False)
    ]
    shown = saved.json()["judge"]
    assert shown["effective"] == {k: v for k, v in BAILIAN.items() if k != "api_key"} | {
        "api_key_last4": "3333", "account_id": None, "region": "beijing"
    }


async def test_bailian_in_env_is_shown_as_it_is(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    env = replace(admins, judge=BailianJudgeSettings(BL_KEY, "llm-ws1", "singapore"))
    client = await admin_client(with_retrieval(make_client), env, github, FakeServiceClients())

    shown = (await client.get(CONFIG)).json()["judge"]["env"]

    assert shown == {
        "provider": "bailian", "api_key_last4": "3333", "model": "decision-model-preview",
        "account_id": None, "workspace_id": "llm-ws1", "region": "singapore",
        "emotion_timeout": shown["emotion_timeout"],
    }


async def test_the_recall_threshold_saved_judges_the_next_turn_until_the_family_changes(
    make_client: ClientFactory, judged: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY, REPLY))
    clients.judge.retrieval = {"needs_retrieval": 0.1, "recall": 0.75, "about_producer": 0.1}
    client = await admin_client(with_retrieval(make_client), judged, github, clients)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "那次比赛怎么样")  # Jev's 0.5: recalled

    saved = await client.put(JUDGE_THRESHOLDS, json={"recall": 0.9, "emotion": 0.5, **JEV_AFFINITY})
    assert saved.status_code == 200, saved.text
    await send_turn(client, conversation_id, "那次比赛怎么样")  # 0.9: not recalled
    upgraded = await client.put(JUDGE, json={
        "provider": "jev", "model": "typesafe/jev-1.14", "emotion_timeout": 1.5
    })
    assert upgraded.status_code == 200, upgraded.text
    await send_turn(client, conversation_id, "那次比赛怎么样")  # still 0.9
    changed = await client.put(JUDGE, json=CLOUDFLARE)
    assert changed.status_code == 200, changed.text
    await send_turn(client, conversation_id, "那次比赛怎么样")  # Clef-flash's 0.7: recalled

    # A turn that recalled forbids the tool its model is offered all the same.
    assert clients.llm.forbidden == [True, False, False, True]
    thresholds = saved.json()["judge_thresholds"]
    assert thresholds["status"] == "overridden"
    assert thresholds["env"] == {"recall": 0.5, "emotion": 0.5} | JEV_AFFINITY
    assert thresholds["effective"] == {"recall": 0.9, "emotion": 0.5} | JEV_AFFINITY
    kept = upgraded.json()["judge_thresholds"]
    assert kept["status"] == "overridden" and kept["model"] == "typesafe/jev-1.14"
    assert kept["effective"] == {"recall": 0.9, "emotion": 0.5} | JEV_AFFINITY
    cleared = changed.json()["judge_thresholds"]
    assert cleared["status"] == "env" and cleared["override"] is None
    assert cleared["effective"] == {
        "recall": 0.7, "emotion": 0.4, "out_of_line": 0.6, "manipulation": 0.6, "photo": 0.8
    }
    assert cleared["model"] == "clef-flash"


def judging_by(client: httpx.AsyncClient) -> tuple[float, float]:
    """The recall and emotion thresholds the next turn and call judge by."""
    config = backend_of(client).config
    lease = config.lease()
    lease.release()
    assert lease.keys.emotions is not None
    return config.judge_thresholds.recall, lease.keys.emotions.min_probability


async def test_judge_thresholds_saved_hold_for_the_models_family_whatever_env_calls_it(
    make_client: ClientFactory, judged: Settings, github: FakeIdentityProvider
):
    factory = with_retrieval(make_client)
    first = await admin_client(factory, judged, github, FakeServiceClients())
    saved = await first.put(JUDGE_THRESHOLDS, json={"recall": 0.9, "emotion": 0.6, **JEV_AFFINITY})
    assert saved.status_code == 200, saved.text

    renamed = replace(judged, judge=JevSettings(JEV_KEY, "jev-1.14"))  # and upgraded
    restarted = await admin_client(factory, renamed, github, FakeServiceClients())
    await start(restarted)

    shown = (await restarted.get(CONFIG)).json()["judge_thresholds"]
    assert shown["status"] == "overridden"
    assert shown["effective"] == {"recall": 0.9, "emotion": 0.6} | JEV_AFFINITY
    assert judging_by(restarted) == (0.9, 0.6)


async def test_judge_thresholds_saved_do_not_hold_once_env_has_another_family(
    make_client: ClientFactory, judged: Settings, github: FakeIdentityProvider
):
    factory = with_retrieval(make_client)
    first = await admin_client(factory, judged, github, FakeServiceClients())
    saved = await first.put(JUDGE_THRESHOLDS, json={"recall": 0.9, "emotion": 0.6, **JEV_AFFINITY})
    assert saved.status_code == 200, saved.text

    clef = replace(judged, judge=CloudflareJudgeSettings(CF_TOKEN, "account-1", "clef"))
    restarted = await admin_client(factory, clef, github, FakeServiceClients())
    await start(restarted)

    shown = (await restarted.get(CONFIG)).json()["judge_thresholds"]
    assert shown["status"] == "failed"
    assert "typesafe/jev-1.13" in shown["failure"]
    assert shown["effective"] == {
        "recall": 0.8, "emotion": 0.4, "out_of_line": 0.5, "manipulation": 0.5, "photo": 0.8
    }
    assert judging_by(restarted) == (0.8, 0.4)


async def test_judge_thresholds_saved_for_a_model_of_no_family_go_with_its_name(
    make_client: ClientFactory, judged: Settings, github: FakeIdentityProvider
):
    unknown = replace(judged, judge=JevSettings(JEV_KEY, "someone/new-judge"))
    client = await admin_client(with_retrieval(make_client), unknown, github, FakeServiceClients())
    saved = await client.put(JUDGE_THRESHOLDS, json={"recall": 0.9, "emotion": 0.6, **JEV_AFFINITY})
    assert saved.status_code == 200, saved.text

    changed = await client.put(JUDGE, json={
        "provider": "jev", "model": "someone/newer-judge", "emotion_timeout": 1.5
    })

    assert changed.status_code == 200, changed.text
    assert changed.json()["judge_thresholds"]["status"] == "env"
    assert judging_by(client) == (0.5, 0.5)


async def test_a_judge_model_not_calibrated_is_warned_of_once_as_it_takes_effect(
    make_client: ClientFactory,
    judged: Settings,
    github: FakeIdentityProvider,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    unknown = replace(judged, judge=JevSettings(JEV_KEY, "someone/new-judge"))
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY))
    client = await admin_client(with_retrieval(make_client), unknown, github, clients)
    await start(client)
    conversation_id = await start_conversation(client)
    for said in ("早上好", "你好", "再见"):
        await send_turn(client, conversation_id, said)
    judging_by(client)

    def warned(model: str) -> int:
        return sum(model in r.getMessage() for r in caplog.records)

    assert warned("someone/new-judge") == 1
    changed = await client.put(JUDGE, json={
        "provider": "jev", "model": "someone/other-judge", "emotion_timeout": 1.5
    })
    assert changed.status_code == 200, changed.text
    judging_by(client)
    assert warned("someone/other-judge") == 1
    assert warned("someone/new-judge") == 1


RERANK = "/api/admin/config/rerank"


async def test_the_memory_threshold_saved_leaves_out_what_the_next_turn_finds(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY, REPLY))
    reranked = replace(admins, rerank=RerankSettings("sk-rerank"))
    factory = with_memory(make_client, FakeBackgroundLLM(CAT), FakeJudge("memories"))
    client = await admin_client(factory, reranked, github, clients)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "我养了一只猫")
    await settle(client)

    await send_turn(client, conversation_id, "猫怎么样")
    saved = await client.put(RERANK, json={"references": 0.3, "memories": 0.95})
    assert saved.status_code == 200, saved.text
    await send_turn(client, conversation_id, "猫怎么样")

    found = ["制作人养了一只猫" in context_of(prompt) for prompt in clients.llm.prompts]
    assert found == [False, True, False]
    rerank = saved.json()["rerank"]
    assert rerank["model"] == "Qwen/Qwen3-Reranker-0.6B"
    assert rerank["env"] == {"references": 0.3, "memories": 0.01}
    assert rerank["effective"] == {"references": 0.3, "memories": 0.95}


async def test_what_cannot_be_changed_here_is_only_shown_saying_why(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider
):
    client = await admin_client(make_client, admins, github, FakeServiceClients())

    shown = await client.get(CONFIG)
    refused = await client.put("/api/admin/config/secret_key", json={"value": "new"})
    unconfigured = await client.put(JUDGE, json=CLOUDFLARE)

    read_only = {item["name"]: item for item in shown.json()["read_only"]}
    assert set(read_only) == {
        "admins", "secret_key", "oauth", "domain", "webrtc", "backup", "paths", "embedding"
    }
    assert all(item["why"] for item in read_only.values())
    assert read_only["admins"]["value"] == ["github:1001"]
    assert read_only["secret_key"]["value"] is True
    assert admins.secret_key and admins.secret_key not in shown.text
    assert refused.status_code == 403 and "服务器" in refused.json()["detail"]
    assert unconfigured.status_code == 409
    assert "unavailable" in shown.json()["judge"]

