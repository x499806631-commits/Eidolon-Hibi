"""Sessions: a signed-in browser holds its session in an httpOnly cookie, which lasts 30
days after it was last used; signing out ends it, and requests that change something
must come from the site itself. Development signs in by name, which a public deployment
refuses to start with."""

import asyncio
import sqlite3
import threading
from dataclasses import replace

import httpx
import pytest

from eidolon.config import Settings
from eidolon.sessions import SESSION_COOKIE
from tests.conftest import COOKIE_DOMAIN, ClientFactory, agree_to_terms
from tests.fakes import FakeClock, FakeLLM
from tests.test_service_status import backend_of
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]


async def mano_conversations(client: httpx.AsyncClient) -> httpx.Response:
    return await client.get("/api/conversations", params={"character_id": "mano"})


async def dev_login(client: httpx.AsyncClient, name: str) -> httpx.Response:
    return await client.post("/api/auth/dev-login", json={"name": name})


@pytest.mark.parametrize("cookie", [None, "not-a-session"], ids=["missing", "invalid"])
async def test_requests_without_a_valid_session_are_refused(
    make_client: ClientFactory, cookie: str | None
):
    client = make_client(FakeLLM(), user=None)
    if cookie is not None:
        client.cookies.set(SESSION_COOKIE, cookie, domain=COOKIE_DOMAIN)

    for res in [
        await mano_conversations(client),
        await client.post("/api/conversations", json={"character_id": "mano"}),
        await client.get("/api/conversations/any/messages"),
        await client.post("/api/conversations/any/turns", json={"text": "在吗"}),
    ]:
        assert res.status_code == 401
        assert res.json()["detail"] == "请先登录"


async def test_health_check_needs_no_session(make_client: ClientFactory):
    assert (await make_client(FakeLLM(), user=None).get("/api/health")).status_code == 200


async def test_dev_login_sets_an_http_only_session_cookie_for_the_user_with_that_name(
    make_client: ClientFactory,
):
    desktop = make_client(FakeLLM(REPLY))
    conversation_id = await start_conversation(desktop)
    await send_turn(desktop, conversation_id, "在吗")
    phone = make_client(FakeLLM(), user=None)

    res = await dev_login(phone, "producer")

    assert res.status_code == 204
    cookie = res.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" not in cookie
    assert "Max-Age=2592000" in cookie  # 30 days
    assert [c["id"] for c in (await mano_conversations(phone)).json()] == [conversation_id]


async def test_dev_login_makes_a_user_the_first_time_a_name_is_used(make_client: ClientFactory):
    mine = make_client(FakeLLM(REPLY))
    await send_turn(mine, await start_conversation(mine), "在吗")
    newcomer = make_client(FakeLLM(), user=None)

    assert (await dev_login(newcomer, "newcomer")).status_code == 204

    await agree_to_terms(newcomer)
    assert (await mano_conversations(newcomer)).json() == []


async def test_without_the_dev_login_switch_there_is_no_dev_login(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), user=None, settings=replace(settings, dev_login=False))

    assert (await dev_login(client, "producer")).status_code == 404


async def test_a_session_expires_30_days_after_it_was_last_used(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    clock.advance(days=29, hours=23)
    assert (await mano_conversations(client)).status_code == 200  # renews it

    clock.advance(days=29, hours=23)
    assert (await mano_conversations(client)).status_code == 200
    clock.advance(days=30)
    res = await mano_conversations(client)

    assert res.status_code == 401


async def test_renewing_a_session_renews_its_cookie(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    assert "set-cookie" not in (await mano_conversations(client)).headers  # just made

    clock.advance(days=1)
    res = await mano_conversations(client)

    assert res.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=")
    assert "Max-Age=2592000" in res.headers["set-cookie"]


async def test_two_requests_at_once_renew_a_session_once(make_client: ClientFactory):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    clock.advance(days=1)
    writing, finish = threading.Event(), threading.Event()

    def held(_: sqlite3.Connection) -> None:
        writing.set()
        finish.wait(10)

    # Every write waits behind this one, so that both requests read the session as it was.
    held_up = asyncio.create_task(backend_of(client).database.write(held))
    await asyncio.to_thread(writing.wait, 10)
    both = asyncio.gather(mano_conversations(client), mano_conversations(client))
    await asyncio.sleep(0.3)
    finish.set()
    await held_up

    renewed = ["set-cookie" in res.headers for res in await both]
    assert sorted(renewed) == [False, True]


async def test_signing_out_ends_the_session_on_this_device_only(make_client: ClientFactory):
    desktop = make_client(FakeLLM())
    phone = make_client(FakeLLM())
    session = desktop.cookies[SESSION_COOKIE]

    res = await desktop.post("/api/auth/logout")

    assert res.status_code == 204
    assert f'{SESSION_COOKIE}=""' in res.headers["set-cookie"]
    desktop.cookies.set(SESSION_COOKIE, session, domain=COOKIE_DOMAIN)  # a copy of the cookie kept elsewhere
    assert (await mano_conversations(desktop)).status_code == 401
    assert (await mano_conversations(phone)).status_code == 200


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://localhost.evil.example:3000", "null"]
)
async def test_requests_that_change_something_from_other_sites_are_refused(
    make_client: ClientFactory, origin: str
):
    client = make_client(FakeLLM())

    res = await client.post(
        "/api/conversations", json={"character_id": "mano"}, headers={"Origin": origin}
    )

    assert res.status_code == 403
    assert (await mano_conversations(client)).json() == []
    reading = await client.get(
        "/api/conversations", params={"character_id": "mano"}, headers={"Origin": origin}
    )
    assert reading.status_code == 200


async def test_a_public_deployment_accepts_only_its_own_origin_and_secure_cookies(
    make_client: ClientFactory, settings: Settings
):
    public = replace(settings, dev_login=False, domain="eidolon.example.com")
    client = make_client(FakeLLM(), settings=public)

    local = await client.post(
        "/api/conversations", json={"character_id": "mano"},
        headers={"Origin": "http://localhost:3000"},
    )
    own = await client.post(
        "/api/conversations", json={"character_id": "mano"},
        headers={"Origin": "https://eidolon.example.com"},
    )
    signed_out = await client.post(
        "/api/auth/logout", headers={"Origin": "https://eidolon.example.com"}
    )

    assert local.status_code == 403
    assert own.status_code == 201
    assert "Secure" in signed_out.headers["set-cookie"]


def test_dev_login_and_a_public_deployment_exclude_each_other(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
):
    with pytest.raises(RuntimeError, match="EIDOLON_DEV_LOGIN"):
        replace(settings, domain="eidolon.example.com")  # dev_login is on in the tests

    for name, value in {
        "CHAT_LLM_BASE_URL": "http://llm.invalid/v1", "CHAT_LLM_API_KEY": "k",
        "CHAT_LLM_MODEL": "m", "EIDOLON_DEV_LOGIN": "1",
    }.items():
        monkeypatch.setenv(name, value)
    assert Settings.from_env().dev_login
    monkeypatch.setenv("EIDOLON_DOMAIN", "eidolon.example.com")
    with pytest.raises(RuntimeError, match="EIDOLON_DOMAIN"):
        Settings.from_env()


def test_the_server_refuses_to_start_with_dev_login_in_a_public_deployment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from eidolon.__main__ import main

    for name, value in {
        "CHAT_LLM_BASE_URL": "http://llm.invalid/v1", "CHAT_LLM_API_KEY": "k",
        "CHAT_LLM_MODEL": "m", "EIDOLON_DEV_LOGIN": "1", "EIDOLON_DOMAIN": "eidolon.example.com",
    }.items():
        monkeypatch.setenv(name, value)

    assert main(["serve"]) == 1
    assert "EIDOLON_DEV_LOGIN" in capsys.readouterr().err



async def test_a_signed_in_request_that_does_not_say_where_it_comes_from_is_refused(
    make_client: ClientFactory,
):
    client = make_client(FakeLLM())
    client.headers.pop("Origin")

    res = await client.post("/api/conversations", json={"character_id": "mano"})

    assert res.status_code == 403
    assert (await mano_conversations(client)).json() == []


async def test_a_request_without_a_session_needs_no_origin(make_client: ClientFactory):
    client = make_client(FakeLLM(), user=None)
    client.headers.pop("Origin")

    res = await client.post("/api/conversations", json={"character_id": "mano"})

    assert res.status_code == 401
