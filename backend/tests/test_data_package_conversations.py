"""Conversations in data packages (GLOSSARY.md「数据包」「对话」): each conversation with each
character, its messages as kept, delivery cues, affinity changes and all, its conversation
events, its title and the calls kept in it, go into a package and come out of it into another
deployment in the order they happened, merged with what is there: the newest conversation is
her current one, wherever it began. One already there is passed over whole; one with a
character not there is skipped, and the user told. Driven through the API: what a package
holds by unzipping it, what an import did by the read endpoints; her hanging up, ending one,
a gift and a call put in the database as recorded elsewhere."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from eidolon import data_package
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeBackgroundLLM,
    FakeLLM,
    FakeServiceClients,
    level,
)
from tests.test_conversation_events import hang_up, record
from tests.test_conversation_titles import TITLE, answering
from tests.test_data_package import (
    EMPTY_PROFILE,
    MANIFEST,
    NOTHING_ELSE,
    PACKAGED_PROFILE,
    export,
    import_package,
    package_of,
    unzipped,
)
from tests.test_memory_management import memories
from tests.test_past_conversations import hers
from tests.test_she_ends_conversation import end
from tests.test_stats import registered_at, said_at, stats
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>[小声で]はい</ja><zh>好的</zh>"]
"""Her reply, with a delivery cue that the record shows without and the package keeps."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Affinity and gifts on, judged by the judge model."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


def deployment(make_client: ClientFactory, settings: Settings, tmp_path: Path, name: str,
               *replies: list[str], user: str = "producer", **services: object
               ) -> httpx.AsyncClient:
    """A client of a deployment of its own: the same characters, a database of its own."""
    return make_client(
        FakeLLM(*replies), user,
        settings=replace(settings, database_path=tmp_path / f"{name}.db"), **services,
    )


def database(tmp_path: Path, name: str) -> Path:
    return tmp_path / f"{name}.db"


def give_pudding(database: Path, conversation: str) -> None:
    """Record the user's giving her a pudding she liked, now, raising her affinity, as giving
    would."""
    affinity = {"up": True, "tier_changed": False, "tier": "trust", "cooldown": False,
                "thawed": False}
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO conversation_events"
            " (conversation_id, kind, created_at, affinity, gift_id, liked)"
            " VALUES (?, 'gift', ?, ?, 'pudding', 1)",
            (conversation, datetime.now(UTC).isoformat(), json.dumps(affinity)),
        )


def keep_call(database: Path, conversation: str, seconds: float) -> None:
    """Keep a call that connected in the conversation, now, and lasted `seconds`."""
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO calls (conversation_id, connected_at, duration_s) VALUES (?, ?, ?)",
            (conversation, datetime.now(UTC).isoformat(), seconds),
        )


def without_ids(entries: list[dict]) -> list[dict]:
    return [{k: v for k, v in e.items() if k != "id"} for e in entries]


async def listed(client: httpx.AsyncClient, character: str = "mano") -> list[tuple]:
    """Her conversations, newest first: each one's title, whether she ended it, and the last
    thing in it."""
    return [(c["title"], c["ended"], c["last_message"]) for c in await hers(client, character)]


async def records(client: httpx.AsyncClient, character: str = "mano") -> list[list[dict]]:
    """The records of her conversations, newest first, without their ids."""
    return [without_ids(await record(client, c["id"])) for c in await hers(client, character)]


def packaged_conversations(package: bytes) -> list[dict]:
    """The package's conversations, without their ids."""
    return without_ids(unzipped(package)[2]["conversations"])


async def test_conversations_move_to_another_deployment_as_they_were(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    clients = FakeServiceClients()
    clients.judge.judgings = [level(0), level(4), level(1), level(3), level(2), level(4)]
    here = deployment(
        make_client, settings, tmp_path, "here", *[REPLY] * 6, clients=clients,
        background_llm=FakeBackgroundLLM(answering(TITLE)),
    )
    at_here = database(tmp_path, "here")
    ended = await start_conversation(here)
    await send_turn(here, ended, "我住在大阪")
    said_at(at_here, "我住在大阪", datetime.now(UTC) - timedelta(days=9))
    registered_at(at_here, datetime.now(UTC) - timedelta(days=9))
    hang_up(at_here, ended)
    end(at_here, ended)
    given = await start_conversation(here)
    await send_turn(here, given, "给你布丁")
    give_pudding(at_here, given)
    keep_call(at_here, given, 75.5)
    titled = await start_conversation(here)
    for text in ("第一句", "第二句", "第三句"):
        await send_turn(here, titled, text)
    kogane = await start_conversation(here, "kogane")
    await send_turn(here, kogane, "你好")
    assert (await listed(here))[0][0] == TITLE
    there = deployment(make_client, settings, tmp_path, "there", REPLY, REPLY)
    friend = deployment(make_client, settings, tmp_path, "there", user="friend")

    package = await export(here, "conversations")
    _, manifest, data = unzipped(package)
    assert manifest["categories"] == ["conversations"]
    assert manifest["counts"] == {"conversations": 4}
    first = data["conversations"][0]
    assert first["id"] == ended
    assert (first["character_id"], first["character_name"]) == ("mano", "櫻木真乃")
    assert [m["role"] for m in first["messages"]] == ["user", "character"]
    assert first["messages"][1]["ja"] == "[小声で]はい"
    assert first["messages"][1]["segments"] == [{"ja": "[小声で]はい", "zh": "好的"}]
    assert first["messages"][0]["hurt"] > 0
    assert [e["kind"] for e in first["events"]] == ["hung_up", "ended"]
    assert data["conversations"][1]["calls"] == [
        {"connected_at": data["conversations"][1]["calls"][0]["connected_at"], "duration_s": 75.5}
    ]
    res = await import_package(there, package, "conversations")

    assert res.status_code == 200, res.text
    assert res.json() == {**NOTHING_ELSE, "conversations": 4, "profile": False}
    for character in ("mano", "kogane"):
        assert await listed(there, character) == await listed(here, character)
        assert await records(there, character) == await records(here, character)
    assert (await stats(there))["range"] == (await stats(here))["range"]
    # Known since they first met over there, not since signing up here.
    assert (await stats(there))["days_known"] == (await stats(here))["days_known"] > 1
    assert packaged_conversations(await export(there, "conversations")) == (
        packaged_conversations(package)
    )
    affinity = await there.get("/api/characters/mano/affinity")
    assert affinity.json()["tier"] == "trust"
    gifts, friends = (await there.get("/api/gifts")).json(), (await friend.get("/api/gifts")).json()
    assert (gifts["backpack"], gifts["reset_cards"]) == ([], 0)
    assert gifts["draws"] == friends["draws"]
    # The newest of each of hers is her current one, to go on in.
    for character in ("mano", "kogane"):
        current = (await hers(there, character))[0]["id"]
        assert (await send_turn(there, current, "我回来了"))[-1]["type"] == "done"


def said(text: str, at: str, *, hurt: float | None = 0.0) -> dict:
    return {
        "role": "user", "text": text, "ja": None, "zh": None, "segments": [], "created_at": at,
        "affinity": None, "hurt": hurt, "out_of_line": None if hurt is None else False,
    }


def replied(zh: str, at: str) -> dict:
    return {
        "role": "character", "text": None, "ja": "はい", "zh": zh,
        "segments": [{"ja": "はい", "zh": zh}], "created_at": at, "affinity": None,
        "hurt": None, "out_of_line": None,
    }


def packaged_conversation(
    id: str, begun: str, *, character: tuple[str, str | None] = ("mano", "櫻木真乃"),
    events: list[dict] | None = None, said_then: str | None = None,
) -> dict:
    """A conversation begun on the day `begun` (as `2026-09-01`), the user saying `said_then`
    (by default its id) and her answering."""
    at = f"{begun}T08:00:00.000000+00:00"
    return {
        "id": id, "character_id": character[0], "character_name": character[1],
        "created_at": at, "title": None,
        "messages": [said(said_then or id, at), replied("好的", at)],
        "events": events or [], "calls": [],
    }


def conversations_package(*conversations: dict, memories: list[dict] | None = None) -> bytes:
    categories = ["conversations"] + (["memories"] if memories is not None else [])
    counts = {"conversations": len(conversations)} | (
        {"memories": len(memories)} if memories is not None else {}
    )
    return package_of(
        {**MANIFEST, "categories": categories, "counts": counts},
        {"conversations": list(conversations)}
        | ({"memories": memories} if memories is not None else {}),
    )


async def said_in(client: httpx.AsyncClient, character: str = "mano") -> list[str]:
    """What the user said first in each of her conversations, newest first; None for one
    with nothing in it."""
    firsts = []
    for c in await hers(client, character):
        entries = await record(client, c["id"])
        firsts.append(next((e["text"] for e in entries if e.get("role") == "user"), None))
    return firsts


async def test_her_conversation_here_stays_current_and_one_imported_is_where_there_is_none(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here", REPLY, REPLY)
    mine = await start_conversation(client)
    await send_turn(client, mine, "这边的")

    res = await import_package(client, conversations_package(
        packaged_conversation("older", "2026-09-01"),
        packaged_conversation("newer", "2026-09-02"),
        packaged_conversation("hers", "2026-09-03", character=("kogane", "月岡恋鐘")),
    ), "conversations")

    assert res.json()["conversations"] == 3
    assert await said_in(client) == ["这边的", "newer", "older"]
    latest = {c["character_id"]: c for c in (await client.get("/api/conversations/latest")).json()}
    assert latest["mano"]["conversation_id"] == mine
    kogane = latest["kogane"]["conversation_id"]
    assert latest["kogane"]["last_message"]["zh"] == "好的"
    assert (await send_turn(client, kogane, "接着聊"))[-1]["type"] == "done"


async def test_a_conversation_already_here_is_passed_over_whole(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")
    first = conversations_package(
        packaged_conversation("a", "2026-09-01"), packaged_conversation("b", "2026-09-02"),
    )
    overlapping = conversations_package(
        packaged_conversation("b", "2026-09-02", said_then="b 后来又说了"),
        packaged_conversation("c", "2026-09-03"),
    )

    assert (await import_package(client, first, "conversations")).json()["conversations"] == 2
    again = await import_package(client, first, "conversations")
    assert again.json() == {**NOTHING_ELSE, "profile": False}
    assert await said_in(client) == ["b", "a"]
    res = await import_package(client, overlapping, "conversations")

    assert res.json()["conversations"] == 1
    assert await said_in(client) == ["c", "b", "a"]


HAZUKI = ("hazuki", "七草はづき")
"""Someone not configured here."""


def told(id: int, text: str, conversation: str | None) -> dict:
    return {
        "id": id, "source_character_id": "mano", "source_character_name": "櫻木真乃",
        "conversation": conversation, "text": text,
        "created_at": "2026-09-01T09:00:00.000000+00:00", "superseded_by": None,
    }


async def told_in(client: httpx.AsyncClient) -> dict[str, str | None]:
    """Each memory's text, and what the user first said in the conversation it was told in,
    by a package exported here; None for one told in none."""
    _, _, data = unzipped(await export(client, "conversations,memories"))
    first = {c["id"]: c["messages"][0]["text"] for c in data["conversations"]}
    return {m["text"]: m["conversation"] and first[m["conversation"]] for m in data["memories"]}


async def test_conversations_with_someone_not_here_are_skipped_and_the_user_told(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")

    res = await import_package(client, conversations_package(
        packaged_conversation("1", "2026-09-01", character=HAZUKI),
        packaged_conversation("2", "2026-09-02"),
        packaged_conversation("3", "2026-09-03", character=HAZUKI),
        packaged_conversation("4", "2026-09-04", character=("chiyuki", None)),
        memories=[told(1, "制作人住在大阪", "1"), told(2, "制作人养了猫", "2")],
    ), "conversations,memories")

    assert res.status_code == 200, res.text
    assert res.json() == {
        "conversations": 1, "photos": 0, "memories": 2, "promises": 0, "experiences": 0,
        "profile": False,
        "skipped": [
            {"character_name": "七草はづき", "conversations": 2},
            {"character_name": "chiyuki", "conversations": 1},
        ],
    }
    assert await said_in(client) == ["2"]
    assert await told_in(client) == {"制作人住在大阪": None, "制作人养了猫": "2"}


async def test_a_gift_not_in_the_catalog_here_is_left_out_and_the_conversation_goes_on(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here", REPLY)
    at = "2026-09-01T08:30:00.000000+00:00"
    gift = {"kind": "gift", "created_at": at, "affinity": None, "liked": True}

    res = await import_package(client, conversations_package(packaged_conversation(
        "1", "2026-09-01",
        events=[gift | {"gift_id": "pudding"}, gift | {"gift_id": "golden-crown"}],
    )), "conversations")

    assert res.json()["conversations"] == 1
    [conversation] = await hers(client)
    assert [e.get("gift_id") for e in await record(client, conversation["id"])] == [
        None, None, "pudding",
    ]
    assert (await send_turn(client, conversation["id"], "还在吗"))[-1]["type"] == "done"
    assert [e.get("kind") for e in await record(client, conversation["id"])][-2:] == [None, None]


async def test_a_memory_says_which_conversation_it_was_told_in_until_that_is_deleted(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")
    await import_package(client, conversations_package(
        packaged_conversation("past", "2026-09-01"), packaged_conversation("now", "2026-09-02"),
    ), "conversations")

    # A later package, with the past conversation again: the memory goes with the one here.
    res = await import_package(client, conversations_package(
        packaged_conversation("old", "2026-09-01"),
        memories=[told(1, "制作人住在大阪", "old")],
    ), "conversations,memories")
    assert res.json()["conversations"] == 0
    assert await told_in(client) == {"制作人住在大阪": "past"}
    _, _, without = unzipped(await export(client, "memories"))
    assert without["memories"][0]["conversation"] is None
    past = (await hers(client))[1]["id"]
    assert (await client.delete(f"/api/conversations/{past}")).status_code == 204

    assert await told_in(client) == {"制作人住在大阪": None}


async def test_a_memory_told_in_a_conversation_imported_without_them_is_told_in_none(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")

    res = await import_package(client, conversations_package(
        packaged_conversation("1", "2026-09-01"), memories=[told(1, "制作人住在大阪", "1")],
    ), "memories")

    assert res.json()["conversations"] == 0 and res.json()["memories"] == 1
    assert await hers(client) == []
    assert await told_in(client) == {"制作人住在大阪": None}


async def test_conversations_not_in_the_format_are_refused(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")
    good = packaged_conversation("1", "2026-09-01")
    broken = {
        "two under one id": [good, packaged_conversation("1", "2026-09-02")],
        "a role no one has": [good | {"messages": [said("hi", good["created_at"]) | {"role": "p"}]}],
        "an event no one has": [good | {"events": [
            {"kind": "hugged", "created_at": good["created_at"], "affinity": None,
             "gift_id": None, "liked": False},
        ]}],
        "a gift without which": [good | {"events": [
            {"kind": "gift", "created_at": good["created_at"], "affinity": None,
             "gift_id": None, "liked": False},
        ]}],
        "which gift for no gift": [good | {"events": [
            {"kind": "hung_up", "created_at": good["created_at"], "affinity": None,
             "gift_id": "pudding", "liked": False},
        ]}],
        "a call lasting less than nothing": [good | {"calls": [
            {"connected_at": good["created_at"], "duration_s": -1},
        ]}],
        "reach-outs in the format before them": [good | {"reach_outs": [
            {"ja": "はい", "zh": "好的", "segments": [{"ja": "はい", "zh": "好的"}],
             "created_at": good["created_at"]},
        ]}],
    }

    for case, conversations in broken.items():
        res = await import_package(client, conversations_package(*conversations), "conversations")

        assert res.status_code == 422, case
        assert res.json()["code"] == "broken", case
    res = await import_package(client, conversations_package(
        good, memories=[told(1, "制作人住在大阪", "9")],
    ), "conversations,memories")
    assert res.status_code == 422
    assert await hers(client) == []


async def test_an_import_failing_halfway_leaves_nothing_behind(
    make_client: ClientFactory, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = deployment(make_client, settings, tmp_path, "here")

    def failing(*_: object) -> int:
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(data_package, "add_carried", failing)  # after the conversations
    package = package_of(
        {**MANIFEST, "categories": ["conversations", "memories", "profile"],
         "counts": {"conversations": 1, "memories": 1, "profile": 1}},
        {"profile": PACKAGED_PROFILE,
         "conversations": [packaged_conversation("1", "2026-09-01")],
         "memories": [told(1, "制作人住在大阪", "1")]},
    )

    with pytest.raises(RuntimeError):
        await import_package(client, package, "conversations,memories,profile")

    assert await hers(client) == []
    assert (await client.get("/api/profile")).json() == EMPTY_PROFILE
    assert await memories(client) == []
    monkeypatch.undo()
    for _ in range(3):  # nor did it count as one of today's
        assert (await import_package(client, package, "conversations,memories,profile")
                ).status_code == 200
