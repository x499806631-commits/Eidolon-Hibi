"""Deleting all of a user's data: once they confirm, their memories, conversations,
producer profile, sign-in methods, sessions and usage are gone for good, from every table,
and they are signed out everywhere. Signing in again with the same identity makes a new
user; nobody else's data is touched."""

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

import eidolon.data_access
from eidolon.config import Settings
from tests.browser import Browser
from tests.conftest import (
    ClientFactory,
    agree_to_terms,
    github_identity,
    sign_in_with,
)
from tests.fakes import (
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeIdentityProvider,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeSpeech,
)
from tests.test_concurrent_calls import another_user
from tests.test_data_access import emptied, log_of, reading
from tests.test_shared_memory import CAT, memorized
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]
DELETE = {"confirm": True}


def memory_client(make_client: ClientFactory, user: str | None = "producer") -> httpx.AsyncClient:
    """A client whose turns write down that the user has a cat."""
    return make_client(
        FakeLLM(REPLY, REPLY), user, retrieval_judge=FakeJudge(), embedder=FakeEmbedder(),
        reranker=FakeReranker("制作人"), background_llm=FakeBackgroundLLM(memorized(CAT)),
    )


async def use_everything(client: httpx.AsyncClient) -> None:
    """Leave data of every kind: a conversation, a memory, a profile and usage."""
    await client.put("/api/profile", json={"form_of_address": "小林"})
    await send_turn(client, await start_conversation(client), "我家的猫叫小白哦")
    assert len((await client.get("/api/memories")).json()) == 1


def rows_mentioning(database: Path, value: str) -> dict[str, int]:
    """How many rows of each table have a column equal to `value`."""
    found = {}
    with sqlite3.connect(database) as db:
        tables = [t for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        for table in tables:
            columns = [c[1] for c in db.execute(f"PRAGMA table_info({table})")]
            where = " OR ".join(f'"{c}" = ?' for c in columns)
            count = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}", [value] * len(columns)
            ).fetchone()[0]
            if count:
                found[table] = count
    return found


def user_id(database: Path, name: str) -> str:
    with sqlite3.connect(database) as db:
        [(found,)] = db.execute("SELECT id FROM users WHERE name = ?", (name,)).fetchall()
    return found


def files_holding(database: Path, text: str) -> list[Path]:
    """Which of the database file and its write-ahead log hold `text`."""
    return [
        path
        for path in (database, database.with_name(database.name + "-wal"))
        if path.exists() and text.encode() in path.read_bytes()
    ]


async def test_deleting_leaves_nothing_of_the_user_in_any_table(
    make_client: ClientFactory, settings: Settings
):
    client = memory_client(make_client)
    await use_everything(client)
    producer = user_id(settings.database_path, "producer")
    conversation_id = (await client.get(
        "/api/conversations", params={"character_id": "mano"}
    )).json()[0]["id"]
    assert rows_mentioning(settings.database_path, producer)

    res = await client.post("/api/account/delete", json=DELETE)

    assert res.status_code == 204
    assert rows_mentioning(settings.database_path, producer) == {}
    assert rows_mentioning(settings.database_path, conversation_id) == {}  # its messages too
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM memories WHERE text = ?", (CAT,)).fetchone() == (0,)


async def test_deleting_signs_the_user_out_everywhere(make_client: ClientFactory):
    desktop = make_client(FakeLLM())
    phone = make_client(FakeLLM())

    res = await desktop.post("/api/account/delete", json=DELETE)

    assert 'eidolon_session=""' in res.headers["set-cookie"]
    for client in (desktop, phone):
        assert (await client.get("/api/account")).status_code == 401


async def test_signing_in_again_after_deleting_is_a_new_user(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = memory_client(make_client, user=None)
    await sign_in_with(client, github, github_identity())
    await agree_to_terms(client)
    await use_everything(client)
    await client.post("/api/account/delete", json=DELETE)

    await sign_in_with(client, github, github_identity())

    assert (await client.get("/api/account")).status_code == 428  # agrees afresh
    await agree_to_terms(client)
    assert (await client.get("/api/conversations", params={"character_id": "mano"})).json() == []
    assert (await client.get("/api/memories")).json() == []
    assert (await client.get("/api/profile")).json()["form_of_address"] is None


async def test_other_users_keep_their_data(make_client: ClientFactory, settings: Settings):
    friend = memory_client(make_client, "friend")
    await use_everything(friend)
    before = rows_mentioning(settings.database_path, user_id(settings.database_path, "friend"))
    mine = memory_client(make_client)
    await use_everything(mine)

    await mine.post("/api/account/delete", json=DELETE)

    after = rows_mentioning(settings.database_path, user_id(settings.database_path, "friend"))
    assert after == before
    assert len((await friend.get("/api/memories")).json()) == 1


@pytest.mark.parametrize("body", [{}, {"confirm": False}], ids=["unconfirmed", "declined"])
async def test_deleting_needs_confirming(
    make_client: ClientFactory, settings: Settings, body: dict
):
    client = make_client(FakeLLM())

    res = await client.post("/api/account/delete", json=body)

    assert res.status_code == 422
    assert (await client.get("/api/account")).status_code == 200


async def test_a_user_who_has_not_agreed_to_the_terms_can_still_delete(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(), user=None)
    await sign_in_with(client, github, github_identity())

    res = await client.post("/api/account/delete", json=DELETE)

    assert res.status_code == 204
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM sign_in_methods").fetchone() == (0,)


async def test_deleting_hangs_up_the_users_call(
    make_client: ClientFactory, speech: FakeSpeech, browser: Browser
):
    client = make_client(FakeLLM(REPLY))
    await browser.call(client, conversation_id=await start_conversation(client))
    await browser.connect()

    await client.post("/api/account/delete", json=DELETE)

    await browser.hung_up()


async def test_deleted_data_is_gone_from_the_database_files_too(
    make_client: ClientFactory, settings: Settings
):
    client = memory_client(make_client)
    await use_everything(client)

    await client.post("/api/account/delete", json=DELETE)

    database = settings.database_path
    await emptied(database)  # soon after
    for path in (database, database.with_name(database.name + "-wal")):
        if path.exists():
            assert CAT.encode() not in path.read_bytes(), path


async def test_a_reader_meanwhile_holds_up_no_one_elses_turn_and_the_log_is_emptied_after_it(
    make_client: ClientFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
):
    """As when a backup is being copied: deleting waits for it no longer than a moment,
    and the pages as they were leave the write-ahead log once it is done."""
    monkeypatch.setattr(eidolon.data_access, "CHECKPOINT_RETRY", 0.1)
    client = make_client(FakeLLM(REPLY, REPLY))
    friend = another_user(client, "friend")
    friends = await start_conversation(friend)
    await send_turn(client, await start_conversation(client), "早上好")
    database = settings.database_path
    reader = reading(database)
    try:
        async with asyncio.timeout(5):
            assert (await client.post("/api/account/delete", json=DELETE)).status_code == 204
            await send_turn(friend, friends, "在吗")
        assert log_of(database)  # held up by the reader
    finally:
        reader.close()

    await emptied(database)
    assert len((await friend.get(f"/api/conversations/{friends}/messages")).json()) == 2


async def test_memories_cleared_before_deleting_are_gone_from_the_database_files_too(
    make_client: ClientFactory, settings: Settings
):
    client = memory_client(make_client)
    await use_everything(client)
    assert (await client.delete("/api/memories")).status_code == 204

    await client.post("/api/account/delete", json=DELETE)

    await emptied(settings.database_path)  # soon after
    assert files_holding(settings.database_path, CAT) == []


async def test_a_memory_deleted_before_deleting_is_gone_from_the_database_files_too(
    make_client: ClientFactory, settings: Settings
):
    client = memory_client(make_client)
    await use_everything(client)
    [cat] = (await client.get("/api/memories")).json()
    assert (await client.delete(f"/api/memories/{cat['id']}")).status_code == 204

    await client.post("/api/account/delete", json=DELETE)

    await emptied(settings.database_path)  # soon after
    assert files_holding(settings.database_path, CAT) == []
