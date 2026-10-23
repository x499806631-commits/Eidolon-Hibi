"""Tickets (GLOSSARY.md「体验券」) and the draws that give them: one in twenty draws, of a kind
the user can use; a rare gift at the latest every twentieth draw (「保底」); each kind used
once, given back should using it fail: a gift she likes, a letter from her, a diary page
opened, a photo in an outfit, a day out together that she answers, has in her schedule and
tells the producer about after, with a photo."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest

from eidolon.config import Settings
from eidolon.engine import (
    WANTS_TO_ANSWER_DATE, WANTS_TO_SEND_ASKED_PHOTO, WANTS_TO_TALK_AFTER_DATE, WITH_YOU,
)
from eidolon.outfit_tags import OutfitTags, VisionSettings, cleaned
from eidolon.schedules import DATE_TWIST
from tests.conftest import ClientFactory, database_in
from tests.fakes import FAKE_JUDGE, FakeClock, FakeImageService, FakeLLM, FakeRandom
from tests.test_delete_account import user_id
from tests.test_photos import IMAGE
from tests.test_reach_outs import backend_of, reach_outs, record
from tests.test_schedules import PLAN
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
TICKET, COMMON, RARE = 95, 1, 90
TAG = (
    "shot: selfie\nframing: upper body\nplace: cafe, indoors\noutfit: coat\naction: smile\n"
    "scene: She holds a cup\nnote: 拍给制作人的"
)
PHOTO = [f"<photo>{TAG}</photo><ja>どうかな</ja><zh>怎么样</zh>"]
NOON = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(
        settings, judge=FAKE_JUDGE, affinity_and_gifts=True, letters=True, diary=True,
        schedules=True, image=IMAGE,
    )


async def talked(
    make_client: ClientFactory, *replies, numbers: tuple[int, ...] = (),
    image: FakeImageService | None = None, clock: FakeClock | None = None,
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    client = make_client(
        llm, clock=clock or FakeClock(NOON), random=FakeRandom(*numbers),
        image_transport=(image or FakeImageService()).transport(),
    )
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "今天也加油")
    return client, llm, conversation


async def give(client: httpx.AsyncClient, settings: Settings, kind: str) -> None:
    await backend_of(client).gifts.put_back_ticket(user_id(settings.database_path, "producer"), kind)


async def state(client: httpx.AsyncClient) -> dict:
    return (await client.get("/api/gifts")).json()


async def test_a_ticket_is_drawn_of_the_kinds_the_user_can_use_and_kept(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, numbers=(TICKET, 3))

    drawn = (await client.post("/api/gifts/draws", json={})).json()
    kept = await state(client)

    assert drawn["drawn"] == [{"kind": "ticket", "ticket": "photo"}]  # the fourth kind
    assert kept["tickets"] == [{"kind": "photo", "count": 1}]
    assert kept["to_rare"] == 19


async def test_the_twentieth_draw_without_a_rare_gift_is_one(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, numbers=(*[COMMON, 0] * 19, 0))
    database = settings.database_path
    import sqlite3

    await state(client)  # given the day's
    with sqlite3.connect(database) as db:
        db.execute("UPDATE gift_balances SET draws = 20")
    drawn = (await client.post("/api/gifts/draws", json={"all": True})).json()["drawn"]

    assert drawn[:19] == [{"kind": "gift", "gift_id": "cake"}] * 19
    assert drawn[19]["kind"] == "gift" and drawn[19]["gift_id"] in {"plush", "jewelry", "preserved-flower"}
    assert (await state(client))["to_rare"] == 20


async def test_a_liked_gift_ticket_puts_one_she_likes_in_the_backpack_once(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, numbers=(0,))
    await give(client, settings, "liked_gift")

    used = await client.post("/api/tickets/liked_gift", json={"character_id": "mano"})
    again = await client.post("/api/tickets/liked_gift", json={"character_id": "mano"})

    assert used.json() == {"gift_id": "cake", "left": 0}
    assert (await state(client))["backpack"] == [{"gift_id": "cake", "count": 1}]
    assert again.status_code == 409


async def test_a_letter_ticket_has_her_write_now(make_client: ClientFactory, settings: Settings):
    letter = ["<ja>プロデューサーさんへ</ja><zh>致制作人</zh>", "<ja>真乃より</ja><zh>真乃</zh>"]
    client, _, _ = await talked(make_client, letter)
    await give(client, settings, "letter")

    used = await client.post("/api/tickets/letter", json={"character_id": "mano"})

    assert used.status_code == 200, used.text
    assert len((await client.get("/api/letters")).json()) == 1


async def test_a_diary_key_opens_all_of_a_page_and_is_given_back_if_there_was_nothing_more(
    make_client: ClientFactory, settings: Settings
):
    diary = [
        "<ja>今日は雨。</ja><zh>今天下雨。</zh>", "<ja>話せてよかった。</ja><zh>聊得很开心。</zh>",
        "<ja>※ずっと一緒に。</ja><zh>※想一直在一起。</zh>",
    ]
    client, _, _ = await talked(make_client, diary)
    entry_id = (await client.post(
        "/api/diary/write-now", json={"character_id": "mano", "what": "聊得很开心"}
    )).json()["id"]
    await give(client, settings, "diary_key")
    await give(client, settings, "diary_key")

    [before] = (await client.get("/api/diary")).json()
    opened = await client.post("/api/tickets/diary_key", json={"entry_id": entry_id})
    again = await client.post("/api/tickets/diary_key", json={"entry_id": entry_id})

    assert before["shown"] == "opening" and before["unlockable"] is True
    assert opened.json()["entry"]["shown"] == "secret"
    assert opened.json()["entry"]["secret"] == [{"ja": "ずっと一緒に。", "zh": "想一直在一起。"}]
    assert again.status_code == 409  # nothing more to open: the key is kept
    assert (await state(client))["tickets"] == [{"kind": "diary_key", "count": 1}]


async def test_a_photo_ticket_has_her_send_one_in_the_outfit_asked_for(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, llm, conversation = await talked(make_client, PHOTO, image=image)
    await give(client, settings, "photo")

    used = await client.post("/api/tickets/photo", json={
        "character_id": "mano", "outfit_id": "stand/1030010010", "card": "ほわっとスマイル",
        "look": "stage",
    })
    tasks = backend_of(client).trip_photos._drawing  # pyright: ignore[reportPrivateUsage]
    for task in list(tasks):
        await task
    [sent] = reach_outs(await record(client, conversation))

    assert used.json() == {"conversation_id": conversation, "left": 0}
    assert llm.prompts[-1][-1].content == WANTS_TO_SEND_ASKED_PHOTO
    assert "穿「ほわっとスマイル」的演出服拍一张" in str(llm.prompts[-1])
    assert sent["photo"]["status"] == "ready" and image.requests


async def test_an_outfit_is_read_once_from_its_thumbnail_and_kept(tmp_path):
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith(".png"):
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        body = json.loads(request.content)
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "White_Blouse, pink ribbon,\n frilled skirt. "
        }}]})

    async with database_in(tmp_path / "db") as db:
        tags = OutfitTags(
            db, VisionSettings("https://vision.test/v1", "k", "m"),
            "https://assets.test", httpx.MockTransport(answer),
        )
        first = await tags.of("stand/1030010010")
        second = await tags.of("stand/1030010010")

        assert first == second == "white blouse, pink ribbon, frilled skirt"
        assert asked == ["/thumbnails/stand/1030010010.png", "/v1/chat/completions"]
        assert await tags.of("../etc/passwd") is None
    assert cleaned("a, a, b") == "a, b"


async def test_a_date_ticket_she_answers_has_in_her_schedule_and_tells_of_after_with_a_photo(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(NOON)
    after = [f"<photo>{TAG}</photo><ja>楽しかった！</ja><zh>好开心！</zh>"]
    client, llm, conversation = await talked(
        make_client, ["<ja>行きたい！</ja><zh>想去！</zh>"], [PLAN], after,
        numbers=(0, 1, 0, 1), clock=clock,
    )
    await give(client, settings, "date")
    tomorrow = date(2026, 10, 23)

    used = await client.post("/api/tickets/date", json={
        "character_id": "mano", "what": "去看电影", "day": tomorrow.isoformat(),
    })
    clock.now = datetime.combine(tomorrow, time(6), UTC)
    backend = backend_of(client)
    await backend.schedule_writer.run_due()
    clock.now = datetime.combine(tomorrow, time(17, 31), UTC)
    await backend.trip_photos.run_due()
    told = reach_outs(await record(client, conversation))

    assert used.status_code == 200, used.text
    assert llm.prompts[1][-1].content == WANTS_TO_ANSWER_DATE
    assert DATE_TWIST.format(what="去看电影") in str(llm.prompts[2])
    assert llm.prompts[3][-1].content.endswith(WANTS_TO_TALK_AFTER_DATE)
    assert [r["segments"][0]["zh"] for r in told] == ["想去！", "好开心！"]
    assert "photo" not in told[0] and told[1]["photo"]["status"] == "ready"


async def test_on_a_date_she_may_have_the_producer_in_her_photo_as_their_profile_has_them(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(NOON)
    image = FakeImageService()
    tag = TAG + "\nyou: yes, holding hands"
    after = [f"<photo>{tag}</photo><ja>楽しかった！</ja><zh>好开心！</zh>"]
    client, llm, _ = await talked(
        make_client, ["<ja>行きたい！</ja><zh>想去！</zh>"], [PLAN], after,
        numbers=(0, 1, 0, 1), clock=clock, image=image,
    )
    await client.put("/api/profile", json={"gender": "female"})
    await give(client, settings, "date")
    tomorrow = date(2026, 10, 23)

    await client.post("/api/tickets/date", json={
        "character_id": "mano", "what": "去看电影", "day": tomorrow.isoformat(),
    })
    clock.now = datetime.combine(tomorrow, time(6), UTC)
    backend = backend_of(client)
    await backend.schedule_writer.run_due()
    clock.now = datetime.combine(tomorrow, time(17, 31), UTC)
    await backend.trip_photos.run_due()

    assert any(WITH_YOU.strip() in m.content for m in llm.prompts[3])
    [request] = image.requests
    sent = json.dumps(request["body"], ensure_ascii=False)
    assert "p-head producer" in sent and "genderswap (mtf)" in sent and "holding hands" in sent
    assert "1girl, 1other" in sent


async def test_a_date_today_or_too_far_is_refused_and_the_ticket_kept(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client)
    await give(client, settings, "date")

    today = await client.post("/api/tickets/date", json={
        "character_id": "mano", "what": "散步", "day": NOON.date().isoformat(),
    })
    far = await client.post("/api/tickets/date", json={
        "character_id": "mano", "what": "散步", "day": (NOON.date() + timedelta(days=8)).isoformat(),
    })

    assert today.status_code == far.status_code == 409
    assert (await state(client))["tickets"] == [{"kind": "date", "count": 1}]


async def test_under_the_development_sign_in_tickets_and_gifts_are_given_at_once(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client)

    given = await client.post("/api/tickets/dev-grant", json={
        "tickets": {"favor": 2, "photo": 1}, "gifts": {"cake": 2, "coffee": 1},
    })
    unknown = await client.post("/api/tickets/dev-grant", json={"gifts": {"no-such": 1}})

    assert given.status_code == 200, given.text
    assert {t["kind"]: t["count"] for t in given.json()["tickets"]} == {"favor": 2, "photo": 1}
    assert {g["gift_id"]: g["count"] for g in given.json()["backpack"]} == {"cake": 2, "coffee": 1}
    assert unknown.status_code == 404
