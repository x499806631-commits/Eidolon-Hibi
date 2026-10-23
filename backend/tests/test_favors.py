"""Favors (GLOSSARY.md「拜托」): asked of one of them with a favor ticket and gifts from the
backpack, both given back should asking fail; she answers at once, and what was asked comes
about: how she calls the producer, the mornings she wakes them, the two of them sworn sisters,
peace made by another, a party the days before and that evening, and after it."""

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest

from eidolon.config import Settings
from eidolon.favors import (
    HELPER_TWIST,
    MATERIALS,
    SURPRISE_TWIST,
    WANTS_TO_MAKE_UP,
    WANTS_TO_REPORT,
    WANTS_TO_THANK,
    WANTS_TO_WAKE,
    Slot,
    match,
)
from eidolon.gift_catalog import Category, Gift, Rarity
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeImageService, FakeLLM, FakeRandom, system_of
from tests.test_delete_account import user_id
from tests.test_photos import IMAGE
from tests.test_reach_outs import backend_of, reach_outs, record
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
OK = ["<ja>うん、いいよ</ja><zh>嗯，好呀</zh>"]
NOON = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
TAG = (
    "shot: selfie\nframing: upper body\nplace: office, indoors\noutfit: dress\naction: smile\n"
    "group: yes\nothers: party hat\nscene: They hold a cake\nnote: 聚会的合照"
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return replace(
        settings, judge=FAKE_JUDGE, affinity_and_gifts=True, schedules=True, keepsakes=True,
        image=IMAGE,
    )


async def talked(
    make_client: ClientFactory, *replies, numbers: tuple[int, ...] = (),
    clock: FakeClock | None = None,
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    client = make_client(
        llm, clock=clock or FakeClock(NOON), random=FakeRandom(*numbers),
        image_transport=FakeImageService().transport(),
    )
    await client.put("/api/profile", json={"timezone": "Etc/GMT", "gender": "female"})
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "今天也加油")
    return client, llm, conversation


async def holding(client: httpx.AsyncClient, settings: Settings, *gift_ids: str) -> None:
    """Give the user a favor ticket and the gifts."""
    backend = backend_of(client)
    user = user_id(settings.database_path, "producer")
    await backend.gifts.put_back_ticket(user, "favor")
    await backend.gifts.put_back_gifts(user, gift_ids)


def tier(settings: Settings, character_id: str, value: float, cooling: bool = False) -> None:
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO affinities (user_id, character_id, value, updated_at, cooling)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (user_id, character_id) DO UPDATE SET"
            " value = excluded.value, updated_at = excluded.updated_at, cooling = excluded.cooling",
            (user_id(settings.database_path, "producer"), character_id, value, NOON.isoformat(),
             int(cooling)),
        )


async def gifts_state(client: httpx.AsyncClient) -> dict:
    return (await client.get("/api/gifts")).json()


def test_gifts_fill_a_favors_slots_one_each_in_any_order():
    def gift(i: str, c: Category, r: Rarity = "common") -> Gift:
        return Gift(i, i, c, r)

    party = MATERIALS["party"]

    assert match(party, [gift("c", "drink"), gift("a", "food"), gift("b", "food"), gift("d", "trinket")])
    assert not match(party, [gift("a", "food"), gift("b", "food"), gift("c", "drink")])
    assert not match(party, [gift(x, "food") for x in "abcd"])
    assert match((Slot(rarity="rare"),), [gift("p", "trinket", "rare")])


async def test_asked_to_call_the_producer_otherwise_she_answers_and_does_from_then_on(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, OK, ["<ja>ねえ</ja><zh>呐</zh>"])
    await holding(client, settings, "black-tea")

    asked = await client.post("/api/favors/address", json={
        "character_id": "mano", "address": "小茶", "gift_ids": ["black-tea"],
    })
    await send_turn(client, conversation, "早上好")

    assert asked.status_code == 200, asked.text
    assert asked.json() == {"conversation_id": conversation, "accepted": True, "left": 0}
    assert "以后叫对方「小茶」" in str(llm.prompts[1]) and "<answer>yes</answer>" in str(llm.prompts[1])
    assert "以后叫对方「小茶」，这是只属于你们俩的叫法" in system_of(llm.prompts[2])
    assert [r["segments"][0]["zh"] for r in reach_outs(await record(client, conversation))] == ["嗯，好呀"]
    assert (await gifts_state(client))["backpack"] == []


async def test_a_name_she_will_not_take_up_she_calls_them_once_and_the_ticket_is_used_all_the_same(
    make_client: ClientFactory, settings: Settings
):
    no = ["<answer>no</answer>\n<ja>今回だけですよ、ご主人様</ja><zh>就这一次哦，主人</zh>"]
    client, llm, conversation = await talked(make_client, no, ["<ja>はい</ja><zh>好的</zh>"])
    await holding(client, settings, "cake")

    asked = await client.post("/api/favors/address", json={
        "character_id": "mano", "address": "主人", "gift_ids": ["cake"],
    })
    await send_turn(client, conversation, "早上好")
    kept = await gifts_state(client)
    [said] = reach_outs(await record(client, conversation))

    assert asked.json() == {"conversation_id": conversation, "accepted": False, "left": 0}
    assert said["segments"] == [{"ja": "今回だけですよ、ご主人様", "zh": "就这一次哦，主人"}]
    assert "你没有答应，只在那一次这样叫过" in system_of(llm.prompts[2])
    assert "这是只属于你们俩的叫法" not in system_of(llm.prompts[2])
    assert (await client.get("/api/favors")).json()["ties"] == []
    assert kept["tickets"] == [] and kept["backpack"] == []


async def test_a_favor_asked_with_the_wrong_gifts_keeps_the_ticket_and_the_gifts(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client)
    await holding(client, settings, "cake")

    asked = await client.post("/api/favors/wake", json={
        "character_id": "mano", "at": "07:00", "days": 1, "gift_ids": ["cake"],
    })
    kept = await gifts_state(client)

    assert asked.status_code == 409
    assert kept["tickets"] == [{"kind": "favor", "count": 1}]
    assert kept["backpack"] == [{"gift_id": "cake", "count": 1}]


async def test_asked_to_wake_the_producer_she_does_each_morning(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(NOON)
    morning = ["<ja>起きて！</ja><zh>起床啦！</zh>"]
    client, llm, conversation = await talked(make_client, OK, morning, morning, clock=clock)
    await holding(client, settings, "coffee")

    asked = await client.post("/api/favors/wake", json={
        "character_id": "mano", "at": "07:30", "days": 2, "gift_ids": ["coffee"],
    })
    planned = (await client.get("/api/favors")).json()["wakes"]
    favors = backend_of(client).favors
    clock.now = datetime(2026, 10, 23, 7, 29, tzinfo=UTC)
    await favors.run_due()
    early = len(reach_outs(await record(client, conversation)))
    clock.now = datetime(2026, 10, 23, 7, 31, tzinfo=UTC)
    await favors.run_due()
    clock.now = datetime(2026, 10, 24, 7, 30, tzinfo=UTC)
    await favors.run_due()
    await favors.run_due()

    assert asked.status_code == 200, asked.text
    assert planned == [{"character_id": "mano", "at": "07:30", "left": 2}]
    assert early == 1
    assert llm.prompts[2][-1].content.endswith(WANTS_TO_WAKE)
    assert "第 2 天，一共 2 天" in str(llm.prompts[3])
    assert len(reach_outs(await record(client, conversation))) == 3
    assert (await client.get("/api/favors")).json()["wakes"] == []


async def test_sworn_sisters_she_knows_it_from_then_on_and_gives_a_keepsake(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, OK, ["<ja>お姉ちゃん</ja><zh>姐姐</zh>"])
    await holding(client, settings, "plush")
    tier(settings, "mano", 70)

    asked = await client.post("/api/favors/bond", json={
        "character_id": "mano", "elder": "producer", "gift_ids": ["plush"],
    })
    await send_turn(client, conversation, "妹妹早")
    ties = (await client.get("/api/favors")).json()["ties"]

    assert asked.status_code == 200, asked.text
    assert "结拜成义姐妹：你当妹妹，制作人当姐姐" in str(llm.prompts[1])
    assert "你和制作人结拜成了义姐妹，制作人是你的姐姐，你是妹妹" in system_of(llm.prompts[2])
    assert ties == [{"character_id": "mano", "address": None, "bond": "producer"}]
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT occasion FROM keepsake_due").fetchall() == [("bond",)]


async def test_sworn_sisters_only_once_close(make_client: ClientFactory, settings: Settings):
    client, _, _ = await talked(make_client)
    await holding(client, settings, "plush")

    asked = await client.post("/api/favors/bond", json={
        "character_id": "mano", "elder": "her", "gift_ids": ["plush"],
    })

    assert asked.status_code == 409
    assert (await gifts_state(client))["tickets"] == [{"kind": "favor", "count": 1}]


async def test_one_makes_peace_with_another_who_forgives_the_producer_and_says_so(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(NOON)
    client, llm, conversation = await talked(
        make_client, OK, ["<ja>許してあげる</ja><zh>原谅你了</zh>"],
        ["<ja>仲直りできたよ</ja><zh>和好啦</zh>"], ["<ja>…ふん</ja><zh>……哼</zh>"],
        numbers=(3,), clock=clock,
    )
    kogane = await start_conversation(client, "kogane")
    await holding(client, settings, "cake")
    tier(settings, "kogane", 10, cooling=True)

    asked = await client.post("/api/favors/make_up", json={
        "mediator_id": "mano", "target_id": "kogane", "gift_ids": ["cake"],
    })
    favors = backend_of(client).favors
    clock.now = NOON + timedelta(hours=2, minutes=10)
    await favors.run_due()
    still = (await client.get("/api/characters/kogane/affinity")).json()["tier"]
    clock.now = NOON + timedelta(hours=2, minutes=20)
    await favors.run_due()
    after = (await client.get("/api/characters/kogane/affinity")).json()
    await send_turn(client, kogane, "对不起")

    assert asked.status_code == 200, asked.text
    assert still == "cold"
    assert after["tier"] == "trust" and after["cooldown_until"] is None
    assert llm.prompts[2][-1].content.endswith(WANTS_TO_MAKE_UP.format(mediator="櫻木真乃"))
    assert llm.prompts[3][-1].content.endswith(WANTS_TO_REPORT)
    assert [r["segments"][0]["zh"] for r in reach_outs(await record(client, kogane))] == ["原谅你了"]
    assert "櫻木真乃替制作人来找你说情，你们已经和好了" in system_of(llm.prompts[4])


async def test_no_peace_to_make_with_one_not_fallen_out(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client)
    await start_conversation(client, "kogane")
    await holding(client, settings, "cake")

    asked = await client.post("/api/favors/make_up", json={
        "mediator_id": "mano", "target_id": "kogane", "gift_ids": ["cake"],
    })

    assert asked.status_code == 409


async def test_a_birthday_party_is_got_ready_held_told_of_and_thanked_for(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(NOON)
    after = [f"<photo>{TAG}</photo><ja>楽しかった！</ja><zh>好开心！</zh>"]
    thanks = ["<ja>ありがとう</ja><zh>谢谢你</zh>"]
    client, llm, conversation = await talked(
        make_client, OK, after, thanks, numbers=(0, 0), clock=clock,
    )
    kogane = await start_conversation(client, "kogane")
    await holding(client, settings, "cake", "pudding", "coffee", "bouquet")
    the_day = date(2026, 10, 24)

    asked = await client.post("/api/favors/party", json={
        "helper_id": "mano", "guest_ids": [], "honoree_id": "kogane", "what": "生日会",
        "day": the_day.isoformat(), "gift_ids": ["cake", "pudding", "coffee", "bouquet"],
    })
    favors = backend_of(client).favors
    user = user_id(settings.database_path, "producer")
    clock.now = datetime.combine(the_day, time(21, 31), UTC)
    await favors.run_due()

    assert asked.status_code == 200, asked.text
    assert "是给月岡恋鐘过生日的" in str(llm.prompts[1])
    assert await favors.twists_on(user, "mano", date(2026, 10, 23)) == ["这几天在帮制作人悄悄准备明天傍晚的生日会"]
    assert await favors.twists_on(user, "mano", the_day) == [HELPER_TWIST.format(others="月岡恋鐘", what="生日会")]
    assert await favors.twists_on(user, "kogane", the_day) == [SURPRISE_TWIST]
    assert await favors.twists_on(user, "kogane", date(2026, 10, 23)) == []
    [told] = reach_outs(await record(client, conversation))[1:]
    assert told["photo"]["status"] == "ready"
    assert llm.prompts[3][-1].content.endswith(WANTS_TO_THANK)
    assert [r["segments"][0]["zh"] for r in reach_outs(await record(client, kogane))] == ["谢谢你"]
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT character_id, occasion FROM keepsake_due").fetchall() == [
            ("kogane", "party")
        ]
    assert (await client.get("/api/favors")).json()["parties"] == []
