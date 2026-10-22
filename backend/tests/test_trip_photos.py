"""Trip photos (GLOSSARY.md「外景照片」): a day her schedule has her away on a trip, she sends the
producer a photo from there once, unasked, as a reach-out with a photo; with others of her
unit on it, it may be of them all, each with a character prompt of her own. Her unit's free
members go on a trip with her now and then, and their days have them on it. Off with the
user's switch, with image generation's, or with her schedule's."""

from dataclasses import replace
from datetime import UTC, datetime, time

import httpx
import pytest

from eidolon.characters import Character
from eidolon.config import Settings
from eidolon.engine import WANTS_TO_SEND_TRIP_PHOTO
from eidolon.features import Features
from eidolon.image_generation import generation_request, openai_request
from eidolon.photo_prompt import GROUP_SIZE, group_prompt, read_tag
from eidolon.schedules import TRIP_WITH_TWIST, Schedules, Slot
from tests.conftest import AVATAR, KOGANE_SHEET, MANO_PHOTO, MANO_SHEET, CharacterConfig, ClientFactory
from tests.fakes import FakeClock, FakeImageService, FakeLLM, FakeRandom
from tests.test_delete_account import user_id
from tests.test_photos import IMAGE
from tests.test_reach_outs import backend_of, reach_outs, record
from tests.test_schedules import PLAN
from tests.test_text_turn import send_turn, start_conversation

KOGANE_PHOTO = ("tsukioka kogane", "blonde hair, long hair, blue eyes")
REPLY = ["<ja>はい</ja><zh>好的</zh>"]
TAG = (
    "shot: selfie\nframing: upper body\nplace: kyoto, temple, autumn leaves, outdoors\n"
    "outfit: coat, scarf\naction: peace sign, smile\n{group}"
    "scene: She holds up a maple leaf with the temple gate behind her.\n"
    "words: 清水寺\nwords_on: the stone pillar behind her\nnote: 在清水寺拍的"
)
SENDS = [f"<photo>{TAG.format(group='')}</photo><ja>見て、紅葉です</ja><zh>你看，是红叶</zh>"]
TOGETHER = "group: yes\nothers: smile, waving\n"
SENDS_ALL = [
    f"<photo>{TAG.format(group=TOGETHER)}</photo><ja>みんなで撮りました</ja><zh>大家一起拍的</zh>"
]
TODAY = datetime.now(UTC).date()


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(TODAY, time(hour, minute), UTC)


@pytest.fixture
def characters(tmp_path) -> CharacterConfig:
    """Mano and Kogane, both with their photo tags."""
    return (
        CharacterConfig(tmp_path / "characters")
        .add("mano", "櫻木真乃", MANO_SHEET, avatar=AVATAR, voice_id="mano-voice", photo=MANO_PHOTO)
        .add("kogane", "月岡恋鐘", KOGANE_SHEET, voice_id="kogane-voice", photo=KOGANE_PHOTO)
    )


async def talked(
    make_client: ClientFactory, settings: Settings, *replies, image: FakeImageService | None = None,
    clock: FakeClock | None = None, numbers: tuple[int, ...] = (0,),
) -> tuple[httpx.AsyncClient, FakeLLM, str]:
    llm = FakeLLM(REPLY, *replies)
    image = image or FakeImageService()
    client = make_client(
        llm, clock=clock or FakeClock(at(10)), random=FakeRandom(*numbers),
        settings=replace(settings, schedules=True, image=IMAGE),
        image_transport=image.transport(),
    )
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "今天也加油")
    return client, llm, conversation


def on_a_trip(client: httpx.AsyncClient, settings: Settings, trip_with: tuple[str, ...] = ()) -> None:
    schedules: Schedules = backend_of(client).schedules
    me = user_id(settings.database_path, "producer")
    slot = Slot(time(9), time(18), "拍外景", "京都", "work", ())
    schedules.keep(me, "mano", TODAY, [slot], [], trip="京都", trip_with=trip_with)


async def test_on_a_trip_she_sends_one_photo_from_there_once_its_time_comes(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    clock = FakeClock(at(10))
    # Her time: 11:00 and 5 minutes on (1).
    client, llm, conversation = await talked(
        make_client, settings, SENDS, image=image, clock=clock, numbers=(1,)
    )
    on_a_trip(client, settings)
    trips = backend_of(client).trip_photos

    await trips.run_due()
    too_early = len(llm.prompts)
    clock.now = at(11, 5)
    await trips.run_due()
    await trips.run_due()
    [sent] = reach_outs(await record(client, conversation))
    picture = await client.get(f"/api/photos/{sent['photo']['id']}/image")

    assert too_early == 1 and len(llm.prompts) == 2  # once
    assert llm.prompts[-1][-1].content == WANTS_TO_SEND_TRIP_PHOTO
    assert "你今天在京都出外景" in str(llm.prompts[-1])
    assert sent["segments"] == [{"ja": "見て、紅葉です", "zh": "你看，是红叶"}]
    assert sent["photo"]["status"] == "ready" and picture.status_code == 200
    [request] = image.requests
    body = request["body"]["input"]
    assert body.startswith("1girl, solo, sakuragi mano") and "kyoto" in body
    assert 'the text "清水寺" is written on the stone pillar behind her.' in body
    assert request["body"]["parameters"]["v4_prompt"]["caption"]["char_captions"] == []


async def test_she_knows_she_sent_it_and_the_list_shows_a_photo(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, settings, SENDS, REPLY)
    sent = await client.post("/api/trip-photos/send-now", json={"character_id": "mano"})
    listed = (await client.get("/api/conversations/latest")).json()

    await send_turn(client, conversation, "好漂亮")

    assert sent.status_code == 201
    [mano] = [c for c in listed if c["character_id"] == "mano"]
    assert mano["last_message"]["photo"] is True and mano["last_message"]["reach_out"] is True
    assert "<photo>shot: selfie" in str(llm.prompts[-1])


async def test_with_her_unit_on_the_trip_it_may_be_of_them_all(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, llm, _ = await talked(make_client, settings, SENDS_ALL, image=image, clock=FakeClock(at(17)))
    on_a_trip(client, settings, trip_with=("kogane",))

    await backend_of(client).trip_photos.run_due()

    assert "和你一起来的有月岡恋鐘" in str(llm.prompts[-1])
    [request] = image.requests
    parameters = request["body"]["parameters"]
    base = request["body"]["input"]
    captions = parameters["v4_prompt"]["caption"]["char_captions"]
    assert base.startswith("2girls, idolmaster shiny colors, selfie")
    assert [c["char_caption"] for c in captions] == [
        "girl, sakuragi mano, brown hair, medium hair, brown eyes, coat, scarf, peace sign, smile",
        "girl, tsukioka kogane, blonde hair, long hair, blue eyes, smile, waving",
    ]
    assert len(parameters["v4_negative_prompt"]["caption"]["char_captions"]) == 2
    assert (parameters["width"], parameters["height"]) == GROUP_SIZE


async def test_none_with_image_generation_or_trip_photos_switched_off(
    make_client: ClientFactory, settings: Settings
):
    client, llm, conversation = await talked(make_client, settings, clock=FakeClock(at(17)))
    on_a_trip(client, settings)
    me = user_id(settings.database_path, "producer")
    switches = Features(settings.database_path)

    switches.switch(me, "images", False)
    await backend_of(client).trip_photos.run_due()
    switches.switch(me, "images", True)
    switches.switch(me, "trip_photos", False)
    await backend_of(client).trip_photos.run_due()

    assert len(llm.prompts) == 1
    assert reach_outs(await record(client, conversation)) == []


async def test_her_unit_mates_days_have_them_on_her_trip(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, [PLAN], REPLY)
    clock = FakeClock(datetime.combine(TODAY, time(6), UTC))
    client = make_client(
        llm, clock=clock, random=FakeRandom(1, 0, 1), settings=replace(settings, schedules=True)
    )
    await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "早")
    backend = backend_of(client)
    me = user_id(settings.database_path, "producer")
    slot = Slot(time(9), time(18), "拍外景", "京都", "work", ())
    backend.schedules.keep(me, "kogane", TODAY, [slot], [], trip="京都", trip_with=("mano",))

    await backend.schedule_writer.run_due()
    mine = backend.schedules.of(me, "mano", TODAY)

    assert mine is not None and (mine.trip, mine.trip_with) == ("京都", ("kogane",))
    assert TRIP_WITH_TWIST.format(names="月岡恋鐘", place="京都") in str(llm.prompts[-1])


def test_a_group_prompt_takes_each_one_her_own_and_openai_gets_them_after():
    mano = Character("mano", "櫻木真乃", "", "v", "", None, "#ffbad6",
                     photo_tag="sakuragi mano", photo_appearance="brown hair")
    kogane = Character("kogane", "月岡恋鐘", "", "v", "", None, "#f84cad",
                       photo_tag="tsukioka kogane", photo_appearance="blonde hair")
    tag = read_tag("shot: other\nplace: beach, 2girls\noutfit: swimsuit\naction: smile")

    prompt = group_prompt([mano, kogane], tag)
    alone = group_prompt([mano], tag)

    assert "2girls, 2girls" not in prompt.positive and "beach" in prompt.positive
    assert prompt.characters[1] == "girl, tsukioka kogane, blonde hair, swimsuit, smile"
    assert alone.characters == () and alone.positive.startswith("1girl, solo")
    assert openai_request("m", prompt)["prompt"].endswith("girl, tsukioka kogane, blonde hair, swimsuit, smile")
    assert len(generation_request("m", prompt, 1)["parameters"]["v4_prompt"]["caption"]["char_captions"]) == 2
    assert tag.stripped == ("people",)  # how many are in it is not hers to write
