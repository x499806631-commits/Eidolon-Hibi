"""Keepsakes (GLOSSARY.md「纪念品」): what she gives the producer, and the cabinet. Turned on in
`.env`, switched off by a user for themselves; put down as due for her scene, an anniversary, a
birthday, a gift she liked, a promise kept, a trip, a holiday's letter; given at a tick as an
event of her current conversation, or with a letter; chosen on the chat model with what she
says giving it; its picture drawn square, the thing alone, where there is image generation,
and drawn later otherwise, all the missing at one go."""

import io
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from PIL import Image

from eidolon.config import Settings
from eidolon.engine import EmptyReply, WANTS_TO_GIVE, read_keepsake
from eidolon.keepsakes import PER_DAY
from eidolon.photo_prompt import keepsake_prompt
from eidolon.promises import Promise
from eidolon.schedules import Schedules, Slot
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeImageService, FakeLLM, FakeRandom, context_of
from tests.test_delete_account import user_id
from tests.test_photos import IMAGE
from tests.test_reach_outs import backend_of, last_contact, record
from tests.test_schedules import PLAN, schedule
from tests.test_schedules import talked as schedule_talked
from tests.test_schedules import today_at as schedule_today_at
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
GIVES = [
    "<keepsake>\nname: 手作的四叶草书签\nlook: 压干的四叶草封在透明书签里，系着粉色丝带。\n"
    "emoji: 🍀\ntags: bookmark, four-leaf clover, pressed flower, pink ribbon, nude\n</keepsake>"
    "<ja>[softly] これ、プロデューサーさんに</ja><zh>这个，送给制作人</zh>"
]
TODAY = datetime.now(UTC).date()


def at(hour: int, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime.combine(day, time(hour, minute), UTC)


def test_what_she_gives_is_read_from_its_tag_and_what_she_says_after_it():
    draft = read_keepsake(GIVES[0])

    assert draft.name == "手作的四叶草书签" and draft.emoji == "🍀"
    assert draft.look.startswith("压干的四叶草")
    assert [(s.ja, s.zh) for s in draft.said] == [("これ、プロデューサーさんに", "这个，送给制作人")]
    with pytest.raises(EmptyReply):
        read_keepsake("<ja>あの</ja><zh>那个</zh>")  # names nothing
    assert read_keepsake("<keepsake>\nname: 布丁\n</keepsake><ja>はい</ja><zh>给</zh>").emoji == "🎁"


def test_its_picture_is_the_thing_alone_square_and_all_ages():
    prompt, stripped = keepsake_prompt("bookmark, four-leaf clover, nude, 1girl, pink ribbon", "flat color")

    assert prompt.size == (1024, 1024)
    assert prompt.positive.startswith("background dataset, no humans, still life, object focus, flat color, bookmark")
    assert "nude" not in prompt.positive and "1girl" not in prompt.positive
    assert stripped == ("nudity",)
    assert "1girl" in prompt.negative and "nsfw" in prompt.negative
    assert "no text" in prompt.positive and ", text," in prompt.negative


def test_words_written_on_it_are_drawn_in_quotes_and_text_is_let_in():
    draft = read_keepsake(
        "<keepsake>\nname: 生日卡\ntags: birthday card, cake\nwords: \"Happy, Birthday!\" {1.5::x::}\n"
        "</keepsake><ja>はい</ja><zh>给</zh>"
    )
    prompt, _ = keepsake_prompt(draft.tags)

    assert draft.tags == 'birthday card, cake, "Happy Birthday! 1.5x"'
    assert '"Happy Birthday! 1.5x", rating:general' in prompt.positive
    assert "no text" not in prompt.positive and ", text," not in prompt.negative
    assert "watermark" in prompt.negative


async def talked(
    make_client: ClientFactory, settings: Settings, *replies, clock: FakeClock | None = None,
    image: FakeImageService | None = None, random: FakeRandom | None = None, **switches,
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    client = make_client(
        llm, clock=clock or FakeClock(at(12)),
        settings=replace(
            settings, keepsakes=True, image=IMAGE if image else None, **switches
        ),
        image_transport=image.transport() if image else None,
        **({"random": random} if random else {}),
    )
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "今天也辛苦了")
    return client, llm, conversation


async def cabinet(client: httpx.AsyncClient) -> dict:
    res = await client.get("/api/keepsakes")
    assert res.status_code == 200, res.text
    return res.json()


async def test_given_now_it_comes_in_the_conversation_and_the_cabinet_and_she_knows_it(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, settings, GIVES, REPLY)

    res = await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    shown = await cabinet(client)
    unseen = (await client.get("/api/keepsakes/unseen")).json()
    events = [e for e in await record(client, conversation) if e.get("type") == "event"]
    await send_turn(client, conversation, "谢谢你的书签")

    assert res.status_code == 201
    assert llm.prompts[1][-1].content == WANTS_TO_GIVE
    assert "制作人前几天送了你一块布丁" in context_of(llm.prompts[1])
    [given] = shown["keepsakes"]
    assert (given["name"], given["emoji"], given["has_image"]) == ("手作的四叶草书签", "🍀", False)
    assert given["said"] == [{"ja": "これ、プロデューサーさんに", "zh": "这个，送给制作人"}]
    assert shown["missing"] == 1 and shown["can_draw"] is False
    assert unseen == {"unseen": 1}
    assert [(e["kind"], e["keepsake_id"]) for e in events] == [("keepsake", given["id"])]
    assert "（你送给了制作人「手作的四叶草书签」。）" in llm.prompts[-1][-1].content
    assert (await client.get(f"/api/keepsakes/{given['id']}/image")).status_code == 404
    await client.post("/api/keepsakes/seen")
    assert (await client.get("/api/keepsakes/unseen")).json() == {"unseen": 0}


async def test_with_image_generation_its_picture_is_drawn_square(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, _, _ = await talked(make_client, settings, GIVES, image=image)

    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    [given] = (await cabinet(client))["keepsakes"]
    picture = await client.get(f"/api/keepsakes/{given['id']}/image")

    assert given["has_image"] is True
    assert image.requests[0]["body"]["input"].startswith("background dataset, no humans, still life")
    with Image.open(io.BytesIO(picture.content)) as webp:
        assert (webp.format, webp.size) == ("WEBP", (1024, 1024))


async def test_the_pictures_missing_are_drawn_at_one_go_once_there_is_image_generation(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await talked(make_client, settings, GIVES)
    await client.post("/api/keepsakes/give-now", json={"character_id": "mano"})
    backend = backend_of(client)
    image = FakeImageService()
    drawn = make_client(
        FakeLLM(), settings=replace(settings, keepsakes=True, image=IMAGE),
        image_transport=image.transport(),
    )
    before = (await drawn.get("/api/keepsakes")).json()

    started = (await drawn.post("/api/keepsakes/fill-images")).json()
    task = backend_of(drawn).keepsakes._filling[user_id(settings.database_path, "producer")]  # pyright: ignore[reportPrivateUsage]
    await task
    after = (await drawn.get("/api/keepsakes")).json()

    assert backend is not None
    assert (before["missing"], before["can_draw"]) == (1, True)
    assert started["started"] is True
    assert after["missing"] == 0 and after["keepsakes"][0]["has_image"] is True


async def test_her_scene_played_through_has_her_give_one_soon_after(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12))
    client, llm, _ = await talked(make_client, settings, GIVES, clock=clock)
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")

    keepsakes.scene_played(me, "mano", "close", clock.now)
    await keepsakes.run_due()
    soon = len((await cabinet(client))["keepsakes"])
    clock.advance(minutes=11)
    await keepsakes.run_due()

    assert soon == 0
    assert len((await cabinet(client))["keepsakes"]) == 1
    assert "第一次走到了「亲近」" in context_of(llm.prompts[-1])


async def test_a_liked_gift_now_and_then_has_her_give_one_back_the_next_day(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12))
    client, _, _ = await talked(
        make_client, settings, GIVES, clock=clock, random=FakeRandom(0, 30, 1),
    )
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")

    keepsakes.gift_given(me, "mano", "布丁", True, None, clock.now)  # one in three: this one
    keepsakes.gift_given(me, "mano", "布丁", True, None, clock.now)  # not this
    keepsakes.gift_given(me, "mano", "布丁", False, None, clock.now)  # not liked
    await keepsakes.run_due()
    today = len((await cabinet(client))["keepsakes"])
    clock.now = at(9, 31, TODAY + timedelta(days=1))
    await keepsakes.run_due()

    assert today == 0
    assert len((await cabinet(client))["keepsakes"]) == 1


async def test_her_birthday_keepsake_comes_with_her_letter_and_not_in_the_conversation(
    make_client: ClientFactory, settings: Settings
):
    letter = ["<ja>お誕生日おめでとう</ja><zh>生日快乐</zh>"]
    client, _, conversation = await talked(make_client, settings, letter, GIVES, letters=True)
    keepsakes = backend_of(client).keepsakes

    await client.post(
        "/api/letters/write-now", json={"character_id": "mano", "occasion": "producer_birthday"}
    )
    await keepsakes.run_due()
    [written] = (await client.get("/api/letters")).json()
    events = [e for e in await record(client, conversation) if e.get("type") == "event"]

    assert written["keepsake"]["name"] == "手作的四叶草书签"
    assert written["keepsake"]["occasion"] == "birthday"
    assert events == []
    assert len((await cabinet(client))["keepsakes"]) == 1


async def test_no_more_than_a_few_a_day_the_rest_the_next_day(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(12))
    client, _, _ = await talked(
        make_client, settings, *[GIVES] * (PER_DAY + 1), clock=clock,
        random=FakeRandom(*[0] * 2 * (PER_DAY + 1)),  # one in four: each; 30 minutes after
    )
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")

    for n in range(PER_DAY + 1):
        keepsakes.promise_settled(
            Promise(n + 1, me, "mano", f"约定{n}", TODAY), "kept", clock.now - timedelta(hours=3)
        )
    await keepsakes.run_due()
    await keepsakes.run_due()

    assert len((await cabinet(client))["keepsakes"]) == PER_DAY


async def test_the_day_after_a_trip_she_brings_something_from_there(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock(at(10))
    client, llm, conversation = await talked(
        make_client, settings, GIVES, clock=clock, schedules=True,
    )
    last_contact(settings.database_path, conversation, at(9))
    me = user_id(settings.database_path, "producer")
    schedules = Schedules(settings.database_path, lambda: clock.now)
    schedules.keep(
        me, "mano", TODAY - timedelta(days=1), [Slot(time(10), time(12), "外景", "大阪", "work")],
        [], trip="大阪",
    )
    schedules.close()

    await backend_of(client).keepsakes.run_due()

    assert len((await cabinet(client))["keepsakes"]) == 1
    assert "你刚从大阪回来" in context_of(llm.prompts[-1])


async def test_switched_off_or_not_turned_on_she_gives_none(
    make_client: ClientFactory, settings: Settings
):
    client, llm, _ = await talked(make_client, settings, GIVES)
    keepsakes = backend_of(client).keepsakes
    me = user_id(settings.database_path, "producer")
    await client.put("/api/account/features/keepsakes", json={"on": False})

    keepsakes.scene_played(me, "mano", "close", at(11))
    await keepsakes.run_due()

    assert (await cabinet(client))["keepsakes"] == []
    assert len(llm.prompts) == 1


async def test_a_day_now_and_then_has_a_trip_away_from_tokyo(
    make_client: ClientFactory, settings: Settings
):
    # No twist (0), a trip (0), to the third place (2).
    client, _, clock = await schedule_talked(make_client, settings, [PLAN], numbers=(0, 0, 2))
    clock.now = schedule_today_at(6)

    await backend_of(client).schedule_writer.run_due()

    written = await schedule(client)
    assert written is not None and written["trip"] == "北海道"
    assert written["twists"] == ["要去北海道出外景，当天来回"]
