"""Letters over HTTP (GLOSSARY.md「来信」): the user's letters, newest first and without
delivery cues, how many are unread, and reading one; nobody else's."""

from contextlib import closing
from dataclasses import replace

import httpx

from eidolon.config import Settings
from eidolon.reply_format import Segment
from eidolon.store import Store
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM
from tests.test_text_turn import start_conversation

LETTER = [
    Segment("[softly] 拝啓、プロデューサーさん。", "敬启，制作人。"),
    Segment("<whisper>いつもありがとう</whisper>ございます。真乃", "一直以来谢谢你。真乃"),
]


async def producer_id(client: httpx.AsyncClient, settings: Settings) -> str:
    """The signed-in user's id, through a conversation they start."""
    conversation_id = await start_conversation(client, "mano")
    with closing(Store(settings.database_path)) as store:
        conversation = store.get_conversation(conversation_id)
    assert conversation is not None
    return conversation.user_id


async def test_letters_come_newest_first_without_delivery_cues_and_are_read_once(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM())
    user_id = await producer_id(client, settings)
    with closing(Store(settings.database_path)) as store:
        first = store.add_letter(user_id, "mano", "ordinary", LETTER)
        second = store.add_letter(user_id, "kogane", "holiday", [Segment("メリクリ！", "圣诞快乐！")])

    assert (await client.get("/api/letters/unread")).json() == {"unread": 2}
    letters = (await client.get("/api/letters")).json()
    assert [(x["id"], x["character_id"], x["occasion"]) for x in letters] == [
        (second.id, "kogane", "holiday"), (first.id, "mano", "ordinary"),
    ]
    mano = letters[1]
    assert mano["ja"] == "拝啓、プロデューサーさん。いつもありがとうございます。真乃"
    assert mano["segments"][0] == {"ja": "拝啓、プロデューサーさん。", "zh": "敬启，制作人。"}
    assert mano["read_at"] is None

    read = (await client.post(f"/api/letters/{first.id}/read")).json()
    again = (await client.post(f"/api/letters/{first.id}/read")).json()

    assert read["read_at"] is not None and again["read_at"] == read["read_at"]
    assert (await client.get("/api/letters/unread")).json() == {"unread": 1}
    only_mano = (await client.get("/api/letters", params={"character": "mano"})).json()
    assert [x["id"] for x in only_mano] == [first.id]


async def test_nobody_reads_another_users_letters(make_client: ClientFactory, settings: Settings):
    producer = make_client(FakeLLM())
    user_id = await producer_id(producer, settings)
    with closing(Store(settings.database_path)) as store:
        letter = store.add_letter(user_id, "mano", "ordinary", LETTER)
    other = make_client(FakeLLM(), "someone else")

    assert (await other.get("/api/letters")).json() == []
    assert (await other.get("/api/letters/unread")).json() == {"unread": 0}
    assert (await other.post(f"/api/letters/{letter.id}/read")).status_code == 404
    assert (await producer.post("/api/letters/999/read")).status_code == 404


async def test_letters_go_with_the_user(make_client: ClientFactory, settings: Settings):
    client = make_client(FakeLLM())
    user_id = await producer_id(client, settings)
    with closing(Store(settings.database_path)) as store:
        store.add_letter(user_id, "mano", "ordinary", LETTER)
        store.delete_user(user_id)
        assert store.letters(user_id) == []


async def test_under_the_development_sign_in_she_writes_one_at_once(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(["<ja>拝啓</ja><zh>敬启</zh><ja>真乃</ja><zh>真乃</zh>"])
    client = make_client(llm, settings=replace(settings, dev_login=True))
    await start_conversation(client, "mano")
    without = make_client(FakeLLM(), settings=replace(settings, dev_login=False))

    res = await client.post("/api/letters/write-now", json={"character_id": "mano"})
    none_yet = await client.post("/api/letters/write-now", json={"character_id": "kogane"})

    assert res.status_code == 201 and none_yet.status_code == 404
    [letter] = (await client.get("/api/letters")).json()
    assert (letter["character_id"], letter["occasion"], letter["zh"]) == ("mano", "ordinary", "敬启真乃")
    assert (await without.post("/api/letters/write-now", json={"character_id": "mano"})).status_code in (
        404, 405,
    )
