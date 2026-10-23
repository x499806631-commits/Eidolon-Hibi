"""Asking a service which models it offers a key (ADR-0027): a user for their own LLM,
keeping nothing and asked no more often than a key is tried; an admin for the deployer's
models, on the key in effect now where none is given, at its own address only. What the
service lists is read in OpenAI's and Anthropic's formats, bounded. Driven through the
backend's external API, the services faked; the reading over a fake transport."""

import json
from dataclasses import replace

import httpx
import pytest

from eidolon.config import NOT_AN_ADDRESS, ChatModelSettings, LLMSettings, Settings
from eidolon.llm import LLMError
from eidolon.llm.listing import MAX_LIST_BYTES, list_models
from eidolon.own_keys import ProviderLLMService
from eidolon.throttle import PER_MINUTE
from tests.conftest import ClientFactory, agree_to_terms, github_identity, sign_in_with
from tests.fakes import DEPLOYER_KEY, FakeClock, FakeIdentityProvider, FakeLLM, FakeLLMService, FakeServiceClients

MINE = "sk-my-own-key-1234"
OWN = "/api/account/keys/llm/models"
ADMIN = "/api/admin/config/chat_llm/models"
LISTED = {"base_url": "https://llm.example.com/v1", "api_key": MINE, "protocol": "chat-completions"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The tests' settings, where the GitHub user 1001 is an admin."""
    return replace(settings, admins=frozenset({("github", "1001")}))


def serving(pages: list[dict | bytes], requests: list[httpx.Request], status: int = 200) -> httpx.AsyncClient:
    """A client on a service answering each request with the next of `pages`."""

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = pages.pop(0)
        return httpx.Response(status, content=page if isinstance(page, bytes) else json.dumps(page).encode())

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


# Reading the list


async def test_openai_lists_are_read_page_after_page_each_model_once():
    requests: list[httpx.Request] = []
    client = serving([
        {"data": [{"id": "gpt-6"}, {"id": "gpt-6-mini"}], "has_more": True, "last_id": "gpt-6-mini"},
        {"data": [{"id": "gpt-6"}, {"id": "o9"}, {"id": ""}, {"name": "no id"}], "has_more": False},
    ], requests)

    models = await list_models(client, "responses", " https://api.openai.com/v1/ ", "sk-x")

    assert models == ["gpt-6", "gpt-6-mini", "o9"]
    assert [str(r.url) for r in requests] == [
        "https://api.openai.com/v1/models", "https://api.openai.com/v1/models?after=gpt-6-mini",
    ]
    assert requests[0].headers["authorization"] == "Bearer sk-x"


async def test_anthropic_lists_are_asked_for_with_its_headers():
    requests: list[httpx.Request] = []
    client = serving([
        {"data": [{"id": "claude-opus-5-5"}], "has_more": True, "last_id": "claude-opus-5-5"},
        {"data": [{"id": "claude-haiku-5-5"}], "has_more": False, "last_id": "claude-haiku-5-5"},
    ], requests)

    models = await list_models(client, "anthropic", "https://api.anthropic.com/v1", "sk-ant")

    assert models == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert requests[0].headers["x-api-key"] == "sk-ant"
    assert "anthropic-version" in requests[0].headers
    assert "authorization" not in requests[0].headers
    assert requests[1].url.params["after_id"] == "claude-opus-5-5"


async def test_no_more_than_three_pages_are_asked_for():
    requests: list[httpx.Request] = []
    pages: list[dict | bytes] = [
        {"data": [{"id": f"m{i}"}], "has_more": True, "last_id": f"m{i}"} for i in range(5)
    ]

    models = await list_models(serving(pages, requests), "chat-completions", "https://x.example/v1", "k")

    assert models == ["m0", "m1", "m2"]
    assert len(requests) == 3


@pytest.mark.parametrize(
    ("page", "status", "why"),
    [
        (b'{"error": {"message": "Invalid key", "type": "auth"}}', 401, "^服务商返回 401：Invalid key$"),
        (b"<html>not found</html>", 200, "无法解析"),
        (b'{"models": []}', 200, "格式"),
        (b"[" + b'{"id": "m"},' * (MAX_LIST_BYTES // 12 + 10) + b"]", 200, "太大"),
    ],
    ids=["refused", "not-json", "not-a-list", "too-big"],
)
async def test_what_is_not_a_list_says_why(page: bytes, status: int, why: str):
    client = serving([page], [], status)

    with pytest.raises(LLMError, match=why):
        await list_models(client, "chat-completions", "https://x.example/v1", "k")


async def test_a_public_deployment_lists_only_at_addresses_on_the_public_internet():
    service = ProviderLLMService(public_only=True)
    try:
        with pytest.raises(LLMError, match="公网地址"):
            await service.list_models("chat-completions", "https://localhost/v1", MINE)
        with pytest.raises(LLMError, match="不是一个网址"):  # tried before, it failed unexplained
            await service.check(ChatModelSettings(LLMSettings("https://[api.example.com/v1", MINE, "m")))
    finally:
        await service.aclose()


# A user's own LLM


async def test_a_user_is_told_the_models_their_key_is_offered_and_nothing_is_kept(
    make_client: ClientFactory, own_llm: FakeLLMService
):
    client = make_client(FakeLLM())

    listed = await client.post(OWN, json=LISTED | {"base_url": " https://llm.example.com/v1 "})

    assert listed.status_code == 200
    assert listed.json() == {"models": ["model-a", "model-b"]}
    assert own_llm.listed == [("chat-completions", "https://llm.example.com/v1", MINE)]


async def test_a_list_refused_says_what_the_service_said_the_key_masked(
    make_client: ClientFactory, own_llm: FakeLLMService
):
    own_llm.rejects[MINE] = "401 Unauthorized"
    client = make_client(FakeLLM())

    refused = await client.post(OWN, json=LISTED)

    assert refused.status_code == 422
    assert refused.json()["detail"].startswith("获取模型失败：401 Unauthorized")
    assert MINE not in refused.text


async def test_an_address_that_is_not_one_is_said_so_before_anything_is_asked(
    make_client: ClientFactory, own_llm: FakeLLMService
):
    client = make_client(FakeLLM())
    pasted = "[https://llm.example.com/v1](https://llm.example.com/v1)"

    listed = await client.post(OWN, json=LISTED | {"base_url": pasted})
    tried = await client.post(
        "/api/account/keys/llm/check", json=LISTED | {"base_url": "https://[llm", "model": "m"}
    )

    for refused in (listed, tried):
        assert refused.status_code == 422
        assert NOT_AN_ADDRESS in refused.json()["detail"][0]["msg"]
    assert own_llm.listed == [] and own_llm.checked == []


async def test_no_one_signed_out_may_ask(make_client: ClientFactory):
    stranger = make_client(FakeLLM(), user=None)

    assert (await stranger.post(OWN, json=LISTED)).status_code == 401


async def test_a_user_asking_too_often_is_told_when_to_ask_again(
    make_client: ClientFactory, own_llm: FakeLLMService
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    other = make_client(FakeLLM(), user="someone-else", clock=clock)

    # Listing and trying keys are counted together.
    asked = [await client.post(OWN, json=LISTED) for _ in range(PER_MINUTE - 1)]
    asked.append(await client.post("/api/account/keys/llm/check", json=LISTED | {"model": "m"}))
    refused = await client.post(OWN, json=LISTED)
    theirs = await other.post(OWN, json=LISTED)
    clock.advance(minutes=1)
    again = await client.post(OWN, json=LISTED)

    assert [r.status_code for r in asked] == [200] * PER_MINUTE
    assert refused.status_code == 429
    assert refused.json()["detail"] == "试得太频繁了，请 60 秒后再试"
    assert refused.headers["retry-after"] == "60"
    assert theirs.status_code == 200
    assert again.status_code == 200
    assert len(own_llm.listed) == PER_MINUTE + 1


# The deployer's models


async def admin(make_client: ClientFactory, github: FakeIdentityProvider, clients: FakeServiceClients) -> httpx.AsyncClient:
    client = make_client(FakeLLM(), user=None, clients=clients)
    await sign_in_with(client, github, github_identity("1001", name="octo"))
    await agree_to_terms(client)
    return client


async def test_an_admin_is_told_the_models_on_the_key_in_effect_at_its_own_address_only(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clients = FakeServiceClients()
    client = await admin(make_client, github, clients)
    at = {"protocol": "chat-completions", "base_url": "http://llm.invalid/v1/"}

    kept = await client.post(ADMIN, json=at)
    elsewhere = await client.post(ADMIN, json=at | {"base_url": "https://other.example/v1"})
    given = await client.post(ADMIN, json=at | {"base_url": "https://other.example/v1", "api_key": "sk-other-key"})
    memory = await client.post("/api/admin/config/memory_llm/models", json=at)
    judge = await client.post("/api/admin/config/judge/models", json=at)

    assert kept.json() == {"models": ["model-a", "model-b"]}
    assert elsewhere.status_code == 422
    assert elsewhere.json()["detail"] == "获取模型失败：换了接口地址，要先填这家的 API key"
    assert given.status_code == 200
    assert memory.status_code == 200
    assert judge.status_code == 404
    assert [(s, url, key) for s, _, url, key in clients.listed] == [
        ("chat_llm", "http://llm.invalid/v1/", DEPLOYER_KEY),
        ("chat_llm", "https://other.example/v1", "sk-other-key"),
        ("memory_llm", "http://llm.invalid/v1/", DEPLOYER_KEY),
    ]


async def test_only_an_admin_may_ask_for_the_deployers(make_client: ClientFactory):
    user = make_client(FakeLLM())

    asked = await user.post(ADMIN, json={"protocol": "chat-completions", "base_url": "http://llm.invalid/v1"})

    assert asked.status_code == 404
