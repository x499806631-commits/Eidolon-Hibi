"""Signing in with GitHub or Google: the first sign-in with an identity makes a new user,
later ones sign in as that user. GitHub accounts less than a year old, Google accounts
with an unverified email address and banned users are turned away with the reason. Driven
through the API with the providers faked (FakeIdentityProvider)."""

import sqlite3
from dataclasses import replace
from datetime import timedelta
from unittest.mock import ANY
from urllib.parse import parse_qs, urlsplit

import httpx

from eidolon.config import Settings
from eidolon.sessions import SESSION_COOKIE
from tests.conftest import (
    COOKIE_DOMAIN,
    ClientFactory,
    agree_to_terms,
    github_identity,
    google_identity,
    sign_in_with,
)
from tests.fakes import START, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]


async def conversations(client: httpx.AsyncClient) -> list[str]:
    """The user's conversations with Mano, once they have agreed to the terms."""
    await agree_to_terms(client)
    res = await client.get("/api/conversations", params={"character_id": "mano"})
    assert res.status_code == 200, res.text
    return [c["id"] for c in res.json()]


async def account_id(client: httpx.AsyncClient) -> str:
    """Who the user is, as their browser keeps their own keys apart by."""
    return (await client.get("/api/account")).json()["id"]


async def signed_in(client: httpx.AsyncClient) -> bool:
    res = await client.get("/api/conversations", params={"character_id": "mano"})
    return res.status_code != 401


async def test_the_first_sign_in_with_github_makes_a_user_and_signs_them_in(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(), user=None)

    res = await sign_in_with(client, github, github_identity())

    assert res.status_code == 302
    assert res.headers["location"] == "/conversations"
    assert SESSION_COOKIE in client.cookies
    assert await conversations(client) == []
    account = (await client.get("/api/account")).json()
    assert account == {
        "id": ANY,
        "name": "producer",
        "sign_in_methods": [
            {"provider": "github", "display_name": "producer", "email": "producer@example.com"}
        ],
        "is_admin": False,
        "exempting_keys": ["llm", "tts", "stt"],
        "reach_outs": None,
        "web_search": True,  # 测试的部署有聊天密钥，搜索就能用
        "sponsor_version": None,
    }


async def test_signing_in_again_with_the_same_identity_is_the_same_user(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    desktop = make_client(FakeLLM(REPLY), user=None)
    await sign_in_with(desktop, github, github_identity())
    await agree_to_terms(desktop)
    conversation_id = await start_conversation(desktop)
    await send_turn(desktop, conversation_id, "在吗")
    phone = make_client(FakeLLM(), user=None)

    await sign_in_with(phone, github, github_identity(name="renamed on GitHub"))

    assert await conversations(phone) == [conversation_id]
    assert await account_id(phone) == await account_id(desktop)


async def test_two_identities_are_two_users(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    mine = make_client(FakeLLM(), user=None)
    await sign_in_with(mine, github, github_identity("1001"))
    await agree_to_terms(mine)
    await start_conversation(mine)
    theirs = make_client(FakeLLM(), user=None)

    await sign_in_with(theirs, github, github_identity("2002", "friend"))

    assert await conversations(theirs) == []
    assert await account_id(theirs) != await account_id(mine)


async def test_github_accounts_less_than_a_year_old_cannot_sign_in(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(FakeLLM(), user=None, clock=clock)

    young = github_identity(created_at=START - timedelta(days=364))
    res = await sign_in_with(client, github, young)

    assert res.headers["location"] == "/login?error=too_new"
    assert SESSION_COOKIE not in client.cookies
    assert not await signed_in(client)

    old_enough = github_identity(created_at=START - timedelta(days=365))
    await sign_in_with(client, github, old_enough)
    assert await signed_in(client)


async def test_a_callback_this_browser_did_not_start_is_refused(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    victim = make_client(FakeLLM(), user=None)
    attacker = make_client(FakeLLM(), user=None)
    start = await attacker.get("/api/auth/github/start")
    [state] = parse_qs(urlsplit(start.headers["location"]).query)["state"]
    code = github.will_identify(github_identity("666", "attacker"))

    res = await victim.get("/api/auth/github/callback", params={"code": code, "state": state})

    assert res.headers["location"] == "/login?error=state"
    assert not await signed_in(victim)


async def test_a_callback_with_the_wrong_state_is_refused(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(), user=None)
    await client.get("/api/auth/github/start")
    code = github.will_identify(github_identity())

    res = await client.get("/api/auth/github/callback", params={"code": code, "state": "guess"})

    assert res.headers["location"] == "/login?error=state"
    assert not await signed_in(client)
    assert github.callbacks == []  # the code was never traded


async def test_a_banned_users_identity_cannot_sign_in(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(), user=None)
    await sign_in_with(client, github, github_identity())
    with sqlite3.connect(settings.database_path) as db:
        db.execute("UPDATE users SET banned_at = '2026-09-25T00:00:00+00:00'")
    again = make_client(FakeLLM(), user=None)

    res = await sign_in_with(again, github, github_identity())

    assert res.headers["location"] == "/login?error=banned"
    assert not await signed_in(again)


async def test_turning_the_provider_down_or_it_failing_says_so(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(), user=None)
    start = await client.get("/api/auth/github/start")
    [state] = parse_qs(urlsplit(start.headers["location"]).query)["state"]

    denied = await client.get(
        "/api/auth/github/callback", params={"error": "access_denied", "state": state}
    )
    github.down = True
    failed = await sign_in_with(client, github, github_identity())

    assert denied.headers["location"] == "/login?error=cancelled"
    assert failed.headers["location"] == "/login?error=provider"
    assert not await signed_in(client)


async def test_the_provider_sends_the_browser_back_to_the_sites_own_callback(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    public = replace(settings, dev_login=False, domain="eidolon.example.com")
    client = make_client(FakeLLM(), user=None, settings=public)

    start = await client.get("/api/auth/github/start")

    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["redirect_uri"] == ["https://eidolon.example.com/api/auth/github/callback"]
    state_cookie = start.headers["set-cookie"]
    assert "HttpOnly" in state_cookie and "Secure" in state_cookie and "Path=/api/auth" in state_cookie


async def test_only_configured_providers_are_offered(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    both = make_client(FakeLLM(), user=None)
    github_only = make_client(FakeLLM(), user=None, identity_providers={"github": github})

    assert (await both.get("/api/auth/providers")).json() == {
        "providers": ["github", "google"], "dev_login": True, "contact": None,
    }
    assert (await github_only.get("/api/auth/providers")).json()["providers"] == ["github"]
    assert (await github_only.get("/api/auth/google/start")).status_code == 404


async def test_signing_in_replaces_the_session_the_browser_had(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM())  # signed in as the dev user "producer"
    old = client.cookies[SESSION_COOKIE]

    await sign_in_with(client, github, github_identity())

    assert client.cookies[SESSION_COOKIE] != old
    stale = make_client(FakeLLM(), user=None)
    stale.cookies.set(SESSION_COOKIE, old, domain=COOKIE_DOMAIN)
    assert not await signed_in(stale)


# Google (ticket 03)


async def test_google_accounts_with_an_unverified_email_cannot_sign_in(
    make_client: ClientFactory, google: FakeIdentityProvider
):
    client = make_client(FakeLLM(), user=None)

    res = await sign_in_with(client, google, google_identity(verified=False))

    assert res.headers["location"] == "/login?error=email_unverified"
    assert not await signed_in(client)


async def test_the_same_email_at_google_and_github_is_two_users(
    make_client: ClientFactory, github: FakeIdentityProvider, google: FakeIdentityProvider
):
    at_github = make_client(FakeLLM(), user=None)
    await sign_in_with(at_github, github, github_identity(email="same@example.com"))
    await agree_to_terms(at_github)
    await start_conversation(at_github)
    at_google = make_client(FakeLLM(), user=None)

    await sign_in_with(at_google, google, google_identity(email="same@example.com"))

    assert await signed_in(at_google)
    assert await conversations(at_google) == []
