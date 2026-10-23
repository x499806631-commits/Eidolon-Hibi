"""The service status (GLOSSARY.md「服务状态」): whether each of the deployer's services works
now, as users are shown it. Judged by the share of its calls on the deployer's keys that
failed over the latest minutes; a service without any is tried with the least of requests,
recorded nowhere; admins choose which services are shown and over how long. Driven through
the backend's external API with a clock the tests control; the probes' ticks are taken by
hand."""

from dataclasses import replace
from datetime import timedelta

import httpx
import pytest

from eidolon.activity import Origin, ServiceCall
from eidolon.assembly import Backend
from eidolon.billing import Billing
from eidolon.config import EmbeddingSettings, RerankSettings, ServiceName, Settings
from eidolon.live_config import TRIAL_MAX_TOKENS
from tests.conftest import ClientFactory
from tests.fakes import (
    DEPLOYER_FISH_KEY,
    FAKE_JUDGE,
    FakeClock,
    FakeEmbedder,
    FakeIdentityProvider,
    FakeLLM,
    FakeReranker,
    FakeServiceClients,
    FakeSpeech,
)
from tests.test_live_config import REPLY, admin_client

STATUS = "/api/service-status"
SERVICE_STATUS = "/api/admin/config/service_status"
SHOWN = {"window_minutes": 30} | dict.fromkeys(
    ("chat_llm", "memory_llm", "tts", "stt", "judge", "embedding", "rerank", "image"), True
)


def backend_of(client: httpx.AsyncClient) -> Backend:
    return client._transport.app.state.backend  # pyright: ignore[reportAttributeAccessIssue]


async def made(
    client: httpx.AsyncClient,
    service: ServiceName,
    calls: int = 1,
    *,
    failed: int = 0,
    own_key: bool = False,
    ago: timedelta = timedelta(minutes=1),
) -> None:
    """Record `calls` calls to `service` made `ago`, the first `failed` of them failing."""
    backend = backend_of(client)
    for i in range(calls):
        await backend.activity.record(
            ServiceCall(
                Origin(service, "provider.example", "model", own_key=own_key),
                backend.clock() - ago, "timeout" if i < failed else None, 100.0, Billing(),
            ),
            None,
        )


async def statuses(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.get(STATUS)
    assert res.status_code == 200, res.text
    return {s["service"]: s["status"] for s in res.json()["services"]}


async def test_each_service_goes_by_the_share_of_its_calls_on_the_deployers_keys_that_failed(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM(), clock=FakeClock())
    await made(client, "chat_llm", 20)
    await made(client, "chat_llm", 10, failed=10, own_key=True)  # a user's own key says nothing
    await made(client, "memory_llm", 20, failed=1)
    await made(client, "tts", 2, failed=1)
    await made(client, "stt", 3, failed=3, ago=timedelta(minutes=31))  # before the window

    res = await client.get(STATUS)

    assert res.json() == {
        "window_minutes": 30,
        "services": [  # of those the deployment has: no judge model, retrieval or images
            {"service": "chat_llm", "status": "ok"},
            {"service": "memory_llm", "status": "unsteady"},
            {"service": "tts", "status": "down"},
            {"service": "stt", "status": "idle"},  # not tried yet
            {"service": "web_search", "status": "idle"},  # 没试过，也没调用过
        ],
    }
    assert (await make_client(FakeLLM(), None).get(STATUS)).status_code == 401


async def test_a_service_without_calls_is_tried_twice_before_it_is_down_and_once_a_window(
    make_client: ClientFactory, settings: Settings, speech: FakeSpeech
):
    clock = FakeClock()
    llm = FakeLLM(REPLY)
    clients = FakeServiceClients(llm)
    embedder, reranker = FakeEmbedder(), FakeReranker()
    retrieving = replace(
        settings,
        judge=FAKE_JUDGE,
        embedding=EmbeddingSettings("siliconflow", "sk-embed", "embed", "https://embed.example"),
        rerank=RerankSettings("sk-rerank"),
    )
    client = make_client(
        llm, settings=retrieving, clock=clock, clients=clients, embedder=embedder,
        reranker=reranker,
    )
    backend = backend_of(client)
    await made(client, "memory_llm", 3)
    speech.rejects[DEPLOYER_FISH_KEY] = "Fish Audio 返回 503"
    reranker.down = True

    await backend.service_status.probe_idle()
    first = await statuses(client)
    await backend.service_status.probe_idle()
    clock.advance(minutes=31)
    await backend.service_status.probe_idle()
    again = await statuses(client)

    working = {"judge": "ok", "embedding": "ok", "rerank": "down", "web_search": "idle"}
    assert first == {"chat_llm": "ok", "memory_llm": "ok", "tts": "down", "stt": "ok"} | working
    # The memory model had calls, and was not tried; once they were past, it was.
    assert again == {"chat_llm": "down", "memory_llm": "ok", "tts": "down", "stt": "ok"} | working
    assert len(llm.prompts) == 3  # one probe, then two the script had nothing for
    # Tried without thinking, writing next to nothing.
    trial = replace(settings.chat_llm, max_tokens=TRIAL_MAX_TOKENS, reasoning_effort=None)
    assert clients.opened.count(trial) == 3
    assert clients.memory_opened.count(trial) == 1  # the memory model is the chat model
    assert len(speech.voiced) == 4
    assert [s for s, _ in speech.checked if s == "soniox"] == ["soniox", "soniox"]
    assert [kind for _, kind in clients.judge.asked] == ["check", "check"]
    assert len(embedder.queries) == 2
    assert len(reranker.calls) == 4
    # Nothing tried is recorded: neither the dashboard's usage nor its costs count it.
    _, calls = await backend.activity.since(clock() - timedelta(hours=1))
    assert [c.service for c in calls] == ["memory_llm"] * 3


@pytest.fixture
def admins(settings: Settings) -> Settings:
    return replace(settings, admins=frozenset({("github", "1001")}))


async def test_admins_choose_which_services_users_are_shown_and_over_how_long(
    make_client: ClientFactory, admins: Settings, github: FakeIdentityProvider, speech: FakeSpeech
):
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    client = await admin_client(
        lambda *a, **k: make_client(*a, clock=FakeClock(), **k), admins, github, clients
    )
    speech.rejects[DEPLOYER_FISH_KEY] = "Fish Audio 返回 503"
    await backend_of(client).service_status.probe_idle()
    before = await client.get(STATUS)

    saved = await client.put(
        SERVICE_STATUS, json=SHOWN | {"window_minutes": 10, "chat_llm": False, "web_search": False}
    )
    after = await client.get(STATUS)
    refused = await client.put(SERVICE_STATUS, json=SHOWN | {"window_minutes": 15})
    hidden = await client.put(
        SERVICE_STATUS, json=SHOWN | {"window_minutes": 10} | dict.fromkeys(
            ("chat_llm", "memory_llm", "tts", "stt", "web_search"), False
        )
    )
    none = await client.get(STATUS)

    assert {s["service"]: s["status"] for s in before.json()["services"]}["tts"] == "down"
    assert saved.status_code == 200, saved.text
    assert saved.json()["service_status"]["unconfigured"] == ["judge", "embedding", "rerank", "image"]
    # At once, though users were shown it a moment ago; what the probes found, maybe of a
    # key since changed, is forgotten until they try again.
    assert after.json() == {
        "window_minutes": 10,
        "services": [
            {"service": "memory_llm", "status": "idle"},
            {"service": "tts", "status": "idle"},
            {"service": "stt", "status": "idle"},
        ],
    }
    assert refused.status_code == 422
    assert hidden.status_code == 200, hidden.text
    assert none.json() == {"window_minutes": 10, "services": []}
