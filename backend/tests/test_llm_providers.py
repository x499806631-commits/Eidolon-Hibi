"""The providers and models an LLM may be picked from (ADR-0027): models.dev's, fetched
with the price catalogs, each model with where and over which protocol its provider
serves it, for the settings page and the admin console to fill in the address, protocol
and model with. Driven through the backend's external API, the catalogs served by a fake
over HTTP."""

import sqlite3
from dataclasses import replace

import httpx
import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeCatalog, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_price_catalog import REFRESH, admin_on, settled

PROVIDERS = "/api/account/keys/llm/providers"

TOOLS = {"tool_call": True, "modalities": {"input": ["text"], "output": ["text"]}}
"""A model that calls tools and answers in text, as models.dev writes one."""

MODELS_DEV = {
    "deepseek": {
        "id": "deepseek", "name": "DeepSeek", "npm": "@ai-sdk/openai-compatible",
        "api": "https://api.deepseek.com",
        "models": {"deepseek-v4-pro": {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", **TOOLS}},
    },
    "openai": {
        "id": "openai", "name": "OpenAI", "npm": "@ai-sdk/openai",
        "models": {
            "gpt-6-sol": {"id": "gpt-6-sol", "name": "GPT-6 Sol", **TOOLS},
            "gpt-image-3": {
                "id": "gpt-image-3", "name": "GPT Image 3", "tool_call": False,
                "modalities": {"input": ["text"], "output": ["image"]},
            },
            "gpt-3.5-turbo": {
                "id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "status": "deprecated", **TOOLS,
            },
        },
    },
    "google": {
        "id": "google", "name": "Google", "npm": "@ai-sdk/google",
        "models": {"gemini-3.5-flash": {"id": "gemini-3.5-flash", "name": "Gemini 3.5 Flash", **TOOLS}},
    },
    "commandcode": {
        "id": "commandcode", "name": "CommandCode", "npm": "@ai-sdk/openai-compatible",
        "api": "https://api.commandcode.ai/provider/v1",
        "models": {
            "qwen-4-max": {"id": "qwen-4-max", "name": "Qwen 4 Max", **TOOLS},
            "claude-sonnet-5": {
                "id": "claude-sonnet-5", "name": "Claude Sonnet 5", **TOOLS,
                "provider": {"npm": "@ai-sdk/anthropic"},
            },
            "gemini-relayed": {
                "id": "gemini-relayed", "name": "Gemini Relayed", **TOOLS,
                "provider": {"npm": "@ai-sdk/google", "api": "https://api.commandcode.ai/google/v1beta"},
            },
        },
    },
    "azure": {
        "id": "azure", "name": "Azure", "npm": "@ai-sdk/azure",
        "api": "https://${AZURE_RESOURCE_NAME}.openai.azure.com/openai/v1",
        "models": {"gpt-6-sol": {"id": "gpt-6-sol", "name": "GPT-6 Sol", **TOOLS}},
    },
    "vertex": {
        "id": "vertex", "name": "Vertex", "npm": "@ai-sdk/google-vertex",
        "models": {
            "claude-opus-5-5": {
                "id": "claude-opus-5-5", "name": "Claude Opus 5.5", **TOOLS,
                "provider": {"npm": "@ai-sdk/anthropic", "api": "https://vertex.example/anthropic/v1"},
            },
        },
    },
    "sakana": {
        "id": "sakana", "name": "Sakana", "npm": "@ai-sdk/openai-compatible",
        "api": "https://api.sakana.example/v1",
        "models": {
            "fugu": {"id": "fugu", "name": "Fugu", **TOOLS, "provider": {"shape": "responses"}},
        },
    },
    "loopback": {
        "id": "loopback", "name": "Loopback", "npm": "@ai-sdk/openai-compatible",
        "api": "https://llm.localhost/v1",
        "models": {"qwen-4-8b": {"id": "qwen-4-8b", "name": "Qwen 4 8B", **TOOLS}},
    },
    "lmstudio": {
        "id": "lmstudio", "name": "LMStudio", "npm": "@ai-sdk/openai-compatible",
        "api": "http://127.0.0.1:1234/v1",
        "models": {"qwen-4-8b": {"id": "qwen-4-8b", "name": "Qwen 4 8B", **TOOLS}},
    },
    "openrouter": {
        "id": "openrouter", "name": "OpenRouter", "npm": "@openrouter/ai-sdk-provider",
        "api": "https://openrouter.ai/api/v1",
        "models": {"z-ai/glm-5": {"id": "z-ai/glm-5", "name": "GLM-5"}},
    },
}
"""A few providers as models.dev lists them: at an address of their own or one it does
not write down, models served over another protocol than their provider's (by another
SDK, or another of OpenAI's), and providers reached only on this machine or at a
placeholder."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The tests' settings, where the GitHub user 1001 is an admin."""
    return replace(settings, admins=frozenset({("github", "1001")}))


async def providers(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(PROVIDERS)
    assert response.status_code == 200, response.text
    return response.json()["providers"]


async def models(client: httpx.AsyncClient, provider: str) -> list[dict]:
    response = await client.get(f"{PROVIDERS}/{provider}/models")
    assert response.status_code == 200, response.text
    return response.json()["models"]


async def fetched(
    make_client: ClientFactory, github: FakeIdentityProvider, catalog: FakeCatalog
) -> httpx.AsyncClient:
    """An admin, the catalogs fetched."""
    admin = await admin_on(make_client, github, FakeClock(), catalog)
    await admin.post(REFRESH)
    return admin


async def test_the_providers_each_with_where_and_over_which_protocol_by_name(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    admin = await fetched(make_client, github, FakeCatalog(MODELS_DEV))

    # Only one reached at a placeholder (Azure's) or on this machine (LM Studio's, and
    # one at a name for it) is left out; one whose models.dev entry does not say where is
    # where we know it is, and Google over its OpenAI-compatible endpoint, not its own
    # protocol. Each with every address its models are at, to tell one saved by.
    assert await providers(admin) == [
        {"id": "commandcode", "name": "CommandCode", "protocol": "chat-completions",
         "base_url": "https://api.commandcode.ai/provider/v1",
         "base_urls": ["https://api.commandcode.ai/provider/v1"]},
        {"id": "deepseek", "name": "DeepSeek", "protocol": "chat-completions",
         "base_url": "https://api.deepseek.com", "base_urls": ["https://api.deepseek.com"]},
        {"id": "google", "name": "Google", "protocol": "chat-completions",
         "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
         "base_urls": ["https://generativelanguage.googleapis.com/v1beta/openai"]},
        {"id": "openai", "name": "OpenAI", "protocol": "responses",
         "base_url": "https://api.openai.com/v1", "base_urls": ["https://api.openai.com/v1"]},
        {"id": "openrouter", "name": "OpenRouter", "protocol": "chat-completions",
         "base_url": "https://openrouter.ai/api/v1",
         "base_urls": ["https://openrouter.ai/api/v1"]},
        {"id": "sakana", "name": "Sakana", "protocol": "chat-completions",
         "base_url": "https://api.sakana.example/v1",
         "base_urls": ["https://api.sakana.example/v1"]},
        # Not at an address of its own over one of ours, but one of its models is.
        {"id": "vertex", "name": "Vertex", "protocol": None, "base_url": None,
         "base_urls": ["https://vertex.example/anthropic/v1"]},
    ]


async def test_a_providers_models_each_over_its_own_protocol_where_it_says(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    admin = await fetched(make_client, github, FakeCatalog(MODELS_DEV))

    # Claude at CommandCode over Anthropic's protocol at the same address; one relayed
    # over Google's own protocol is left out.
    assert await models(admin, "commandcode") == [
        {"model": "claude-sonnet-5", "name": "Claude Sonnet 5", "protocol": "anthropic",
         "base_url": "https://api.commandcode.ai/provider/v1"},
        {"model": "qwen-4-max", "name": "Qwen 4 Max", "protocol": "chat-completions",
         "base_url": "https://api.commandcode.ai/provider/v1"},
    ]
    # Neither one that makes pictures nor one that is deprecated; one models.dev says
    # nothing of calling tools is listed.
    assert [m["model"] for m in await models(admin, "openai")] == ["gpt-6-sol"]
    assert [m["model"] for m in await models(admin, "openrouter")] == ["z-ai/glm-5"]
    # One models.dev says is spoken to over OpenAI's Responses, not its provider's.
    assert [m["protocol"] for m in await models(admin, "sakana")] == ["responses"]
    assert await models(admin, "vertex") == [
        {"model": "claude-opus-5-5", "name": "Claude Opus 5.5", "protocol": "anthropic",
         "base_url": "https://vertex.example/anthropic/v1"},
    ]
    assert await models(admin, "azure") == []
    assert await models(admin, "nowhere") == []


async def test_any_user_signed_in_may_ask_and_no_one_else(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    catalog = FakeCatalog(MODELS_DEV)
    await fetched(make_client, github, catalog)
    user = make_client(FakeLLM(), catalog_transport=catalog.transport())
    stranger = make_client(FakeLLM(), user=None, catalog_transport=catalog.transport())

    listed = await providers(user)
    refused = [
        await stranger.get(PROVIDERS),
        await stranger.get(f"{PROVIDERS}/deepseek/models"),
    ]

    assert [p["id"] for p in listed][:2] == ["commandcode", "deepseek"]
    assert [r.status_code for r in refused] == [401, 401]


async def test_before_the_catalogs_are_fetched_there_are_none_and_asking_fetches_them(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    catalog = FakeCatalog(MODELS_DEV)
    admin = await admin_on(make_client, github, FakeClock(), catalog)

    assert await providers(admin) == []
    await settled(admin)

    assert catalog.fetches == 1
    assert [p["id"] for p in await providers(admin)][:1] == ["commandcode"]


async def test_catalogs_fetched_before_there_were_providers_are_fetched_again_soon(
    make_client: ClientFactory, github: FakeIdentityProvider, settings: Settings
):
    catalog = FakeCatalog(MODELS_DEV)
    clock = FakeClock()
    admin = await admin_on(make_client, github, clock, catalog)
    await admin.post(REFRESH)
    # As an older version fetched them, without the providers.
    with sqlite3.connect(settings.database_path) as db:
        db.execute("DELETE FROM price_catalog_providers")
        db.execute("DELETE FROM price_catalog_offers")
    db.close()
    clock.advance(minutes=11)
    deployed = await admin_on(make_client, github, clock, catalog)  # as after a deploy

    assert await providers(deployed) == []
    await settled(deployed)

    assert catalog.fetches == 2
    assert [p["id"] for p in await providers(deployed)][:1] == ["commandcode"]
