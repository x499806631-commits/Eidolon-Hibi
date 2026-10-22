"""She sends a photo in a typed turn (GLOSSARY.md「照片」): where the deployer has image
generation, she has her photo tags and there is the judge model, it is asked whether the
user asks for a photo, in the same request as whatever else it asks or apart; asked, she is
told she can send one, or in the coldest tier that she does not want to, and the photo tag
at the front of her reply begins the photo. `photo` tells its id before her first words;
neither the tag nor anything of it is shown or kept as her words. Her reply is recorded with
the photo, being generated, until the picture is kept, as WebP without the metadata the
image service put in it; the record tells it, and only the user whose conversation it is
reads it. Her later turns carry the tag before her reply; the photo goes with the
conversation and with the user.

Driven through the backend's external API, the judge model faked turn by turn and the image
service faked at its transport, where every request it is sent is seen."""

import asyncio
import io
from dataclasses import replace

import httpx
import pytest
from PIL import Image

from eidolon.config import ImageSettings, Settings
from eidolon.characters import Character
from eidolon.photo_prompt import image_prompt, read_tag
from eidolon.photos import CAN_SEND, CANNOT_SEND, NOT_NOW
from tests.conftest import MANO_PHOTO, ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeClock,
    FakeEmbedder,
    FakeImageService,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeServiceClients,
    context_of,
    judge_key,
)
from tests.fakes import level
from tests.test_affinity import keep
from tests.test_conversation_events import hang_up
from tests.test_delete_account import DELETE, rows_mentioning
from tests.test_text_turn import send_turn, start_conversation
from tests.test_turns import Turns

IMAGE = ImageSettings("nai-key", "https://image.novelai.test")

SELFIE = """
shot: selfie
framing: upper body
place: cafe, indoors, afternoon, sunlight
outfit: white shirt, cardigan
action: peace sign, smile
note: 在咖啡店给他拍的自拍
"""
WORDS = "<ja>はい、どうぞ</ja><zh>给，你看</zh>"
REPLY = [f"<photo>{SELFIE}</photo>", WORDS]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the deployer's image generation and the judge model; affinity off."""
    return replace(settings, image=IMAGE, judge=FAKE_JUDGE, affinity_and_gifts=False)


def asking(photo: bool = True) -> FakeServiceClients:
    """The deployer's models, the judge model finding the user asks for a photo or not."""
    clients = FakeServiceClients()
    clients.judge.photo = 1.0 if photo else 0.0
    return clients


async def settled(client: httpx.AsyncClient, photo_id: str) -> dict:
    """How the photo came out, once it is no longer being generated."""
    for _ in range(200):
        res = await client.get(f"/api/photos/{photo_id}")
        assert res.status_code == 200, res.text
        if res.json()["status"] != "pending":
            return res.json()
        await asyncio.sleep(0.01)
    raise AssertionError(f"photo {photo_id} is still being generated")


def photo_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "photo"]


async def test_asked_she_sends_one_before_her_words_and_it_is_kept_with_her_reply(
    make_client: ClientFactory,
):
    image = FakeImageService()
    image.gate = asyncio.Event()
    llm = FakeLLM(REPLY)
    client = make_client(llm, clients=asking(), image_transport=image.transport())
    conversation = await start_conversation(client)

    events = await send_turn(client, conversation, "发张自拍给我看看")
    [photo] = photo_events(events)
    pending = await client.get(f"/api/photos/{photo['id']}")
    image.gate.set()
    ready = await settled(client, photo["id"])
    picture = await client.get(f"/api/photos/{photo['id']}/image")
    record = (await client.get(f"/api/conversations/{conversation}/messages")).json()

    assert CAN_SEND in context_of(llm.prompts[0])
    assert [e["type"] for e in events][:3] == ["user", "photo", "delta"]
    assert photo == {"type": "photo", "id": photo["id"], "status": "pending"}
    shown = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(shown) == "はい、どうぞ给，你看"
    done = events[-1]["message"]
    assert (done["ja"], done["zh"]) == ("はい、どうぞ", "给，你看")
    assert done["photo"] == {"id": photo["id"], "status": "pending", "reason": None}
    assert pending.json() == {"status": "pending", "reason": None}
    assert ready == {"status": "ready", "reason": None}
    assert picture.status_code == 200
    assert picture.headers["content-type"] == "image/webp"
    assert picture.headers["cache-control"].startswith("private")
    assert FakeImageService.METADATA.encode() not in picture.content
    with Image.open(io.BytesIO(picture.content)) as webp:
        assert (webp.format, webp.size) == ("WEBP", (832, 1216))
        assert not {"exif", "xmp", "icc_profile"} & set(webp.info)
    [said, reply] = record
    assert "photo" not in said
    assert reply["photo"] == {"id": photo["id"], "status": "ready", "reason": None}
    assert (reply["ja"], reply["zh"]) == ("はい、どうぞ", "给，你看")


async def test_a_photo_out_before_her_reply_is_recorded_is_kept_as_it_is(
    make_client: ClientFactory,
):
    image = FakeImageService()
    client = make_client(
        FakeLLM(REPLY, delay=0.2), clients=asking(), image_transport=image.transport()
    )
    conversation = await start_conversation(client)

    events = await send_turn(client, conversation, "发张自拍给我看看")

    [photo] = photo_events(events)
    assert (await client.get(f"/api/photos/{photo['id']}")).json()["status"] == "ready"


async def test_the_image_service_is_sent_her_tag_cleaned_within_fixed_prompts(
    make_client: ClientFactory,
):
    selfie = """
shot: Selfie
framing: full body
place: Cafe, {indoors}, 1.2::sunlight::, afternoon, window
outfit: White_Shirt, white shirt, "cardigan"
action: peace sign, [smile], looking at viewer
note: 自拍
"""
    other = """
shot: other
framing: cowboy shot
action: sitting, smile
"""
    image = FakeImageService()
    client = make_client(
        FakeLLM([f"<photo>{selfie}</photo>{WORDS}"], [f"<photo>{other}</photo>{WORDS}"]),
        clients=asking(),
        image_transport=image.transport(),
    )
    conversation = await start_conversation(client)

    for asked in ("自拍一张", "让我看看你今天的衣服"):
        [photo] = photo_events(await send_turn(client, conversation, asked))
        await settled(client, photo["id"])

    head = "1girl, solo, sakuragi mano, idolmaster shiny colors, brown hair, medium hair, brown eyes"
    tail = "high complexity, rating:general, very aesthetic, masterpiece, no text"
    negative = (
        "nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, bad quality,"
        " jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone,"
        " screentone, multiple views, logo, too many watermarks, negative space, blank page,"
        " @_@, mismatched pupils, glowing eyes, bad anatomy, nude, nipples, underwear, bra,"
        " panties, bra strap, strap slip, see-through, pantyshot, cleavage"
    )
    [first, second] = image.requests
    assert first["body"]["input"] == (
        f"{head}, selfie, looking at viewer, upper body, cafe, indoors, sunlight, day,"
        f" white shirt, cardigan, peace sign, smile, {tail}"
    )
    assert second["body"]["input"] == (
        f"{head}, cowboy shot, indoors, casual, sitting, smile, looking at viewer, {tail}"
    )
    for request in image.requests:
        body = request["body"]
        parameters = body["parameters"]
        assert request["url"] == "https://image.novelai.test/ai/generate-image"
        assert request["key"] == "nai-key"
        assert (body["model"], body["action"]) == ("nai-diffusion-5-curated", "generate")
        assert parameters["negative_prompt"] == negative
        assert parameters["v4_prompt"]["caption"]["base_caption"] == body["input"]
        assert parameters["v4_negative_prompt"]["caption"]["base_caption"] == negative
        assert {k: parameters[k] for k in ("width", "height", "steps", "scale", "sampler",
                                           "n_samples")} == {
            "width": 832, "height": 1216, "steps": 23, "scale": 7,
            "sampler": "k_euler_ancestral", "n_samples": 1,
        }
        assert isinstance(parameters["seed"], int)


@pytest.mark.parametrize("deployment", ["no judge model", "no image generation", "no photo tags"])
async def test_a_turn_that_could_have_no_photo_tells_her_she_cannot_send_one(
    make_client: ClientFactory, settings: Settings, deployment: str
):
    character = "kogane" if deployment == "no photo tags" else "mano"
    if deployment == "no judge model":
        settings = replace(settings, judge=None)
    if deployment == "no image generation":
        settings = replace(settings, image=None)
    image = FakeImageService()
    clients = asking()
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, clients=clients, settings=settings, image_transport=image.transport()
    )
    conversation = await start_conversation(client, character)

    events = await send_turn(client, conversation, "发张自拍给我看看")

    assert clients.judge.photo_asked == []
    assert CANNOT_SEND in context_of(llm.prompts[0])
    assert photo_events(events) == []
    assert image.requests == []
    assert events[-1]["message"]["ja"] == "はい、どうぞ"


@pytest.mark.parametrize("judged", ["no", "judge model down", "coldest tier"])
async def test_her_tag_is_dropped_unless_asked_and_willing(
    make_client: ClientFactory, settings: Settings, judged: str
):
    told = NOT_NOW if judged == "coldest tier" else None
    image = FakeImageService()
    clients = asking(photo=judged != "no")
    if judged == "judge model down":
        clients.rejects[judge_key(FAKE_JUDGE)] = "fake judge model is down"
    if judged == "coldest tier":
        settings = replace(settings, affinity_and_gifts=True)
    llm = FakeLLM(REPLY)
    clock = FakeClock()
    client = make_client(
        llm, clients=clients, settings=settings, clock=clock, image_transport=image.transport()
    )
    conversation = await start_conversation(client)
    if judged == "coldest tier":
        keep(settings.database_path, "producer", 10, clock())

    events = await send_turn(client, conversation, "发张自拍给我看看")
    record = (await client.get(f"/api/conversations/{conversation}/messages")).json()

    context = context_of(llm.prompts[0])
    assert (told in context) if told else ("# 照片" not in context)
    assert photo_events(events) == []
    assert image.requests == []
    assert "photo" not in record[-1]
    assert record[-1]["ja"] == "はい、どうぞ"


async def test_asked_apart_a_photo_answer_not_read_still_leaves_her_affinity_judged(
    make_client: ClientFactory, settings: Settings
):
    clients = asking()
    clients.judge.photo = "not a probability"  # pyright: ignore[reportAttributeAccessIssue]
    clients.judge.judgings = [level(4)]
    image = FakeImageService()
    client = make_client(
        FakeLLM(REPLY), clients=clients, settings=replace(settings, affinity_and_gifts=True),
        clock=FakeClock(), image_transport=image.transport(),
    )
    conversation = await start_conversation(client)

    events = await send_turn(client, conversation, "发张自拍给我看看")

    assert [k for _, k in clients.judge.asked] == ["affinity"]
    assert photo_events(events) == []
    assert image.requests == []
    assert events[-1]["message"]["affinity"]["up"] is True


@pytest.mark.parametrize("asked_with", ["nothing else", "affinity", "retrieval"])
async def test_whether_she_is_asked_for_one_is_judged_with_whatever_else_is(
    make_client: ClientFactory, settings: Settings, asked_with: str
):
    clients = asking()
    services: dict = {}
    if asked_with == "affinity":
        settings = replace(settings, affinity_and_gifts=True)
    if asked_with == "retrieval":
        clients.judge.needs = FakeJudge("nothing")
        services = {"embedder": FakeEmbedder(), "reranker": FakeReranker()}
    image = FakeImageService()
    client = make_client(
        FakeLLM(REPLY), clients=clients, settings=settings, image_transport=image.transport(),
        **services,
    )
    conversation = await start_conversation(client)

    events = await send_turn(client, conversation, "发张自拍给我看看")

    kind = {"nothing else": "photo", "affinity": "affinity", "retrieval": "turn"}[asked_with]
    assert [k for _, k in clients.judge.asked] == [kind]
    assert clients.judge.photo_asked == ["发张自拍给我看看"]
    assert len(photo_events(events)) == 1


async def test_her_tag_is_never_voiced_nor_one_written_in_the_middle_of_her_words(
    settings: Settings,
):
    turns = Turns(settings)
    try:
        events = await turns.take(
            FakeLLM([f"<photo>{SELFIE}</photo><ja>はい<photo>shot: other</photo>、どうぞ</ja>",
                     "<zh>给，<photo>x</photo>你看</zh>"]),
            "发张自拍给我看看",
            in_house=True,
            clients=asking(),
            image_transport=FakeImageService().transport(),
        )
        record = turns.record()
    finally:
        turns.close()

    def said(kind: str, lang: str | None = None) -> str:
        return "".join(e["text"] for e in events if e["type"] == kind and e.get("lang") == lang)

    assert said("speech-delta") == "はい、どうぞ"
    assert said("delta", "ja") == "はい、どうぞ"
    assert said("delta", "zh") == "给，你看"
    assert len(photo_events(events)) == 1
    assert record[-1] == ("character", None, "はい、どうぞ")


async def test_only_a_first_tag_at_the_front_is_kept_and_any_other_left_out_of_her_words(
    make_client: ClientFactory,
):
    image = FakeImageService()
    client = make_client(
        FakeLLM(
            [WORDS, f"<photo>{SELFIE}</photo>"],
            [f"<photo>{SELFIE}</photo><photo>shot: other</photo>", WORDS],
        ),
        clients=asking(),
        image_transport=image.transport(),
    )
    conversation = await start_conversation(client)

    behind = await send_turn(client, conversation, "发张自拍给我看看")
    twice = await send_turn(client, conversation, "再来一张")
    [photo] = photo_events(twice)
    await settled(client, photo["id"])

    assert photo_events(behind) == []
    for events in (behind, twice):
        message = events[-1]["message"]
        assert (message["ja"], message["zh"]) == ("はい、どうぞ", "给，你看")
    assert len(image.requests) == 1
    assert "selfie" in image.requests[0]["body"]["input"]


async def test_her_later_turns_carry_her_tag_where_the_judge_model_never_reads_it(
    make_client: ClientFactory,
):
    image = FakeImageService()
    clients = asking()
    llm = FakeLLM(REPLY, [WORDS])
    client = make_client(llm, clients=clients, image_transport=image.transport())
    conversation = await start_conversation(client)

    [photo] = photo_events(await send_turn(client, conversation, "发张自拍给我看看"))
    await settled(client, photo["id"])
    await send_turn(client, conversation, "好可爱")

    replies = [m.content for m in llm.prompts[1] if m.role == "assistant"]
    assert replies == [f"<photo>{SELFIE}</photo>{WORDS}"]
    told = clients.judge.states[-1]["上文"]
    assert not any("<photo>" in line or "selfie" in line for line in told)


async def test_only_the_user_whose_conversation_it_is_reads_it(make_client: ClientFactory):
    image = FakeImageService()
    producer = make_client(FakeLLM(REPLY), clients=asking(), image_transport=image.transport())
    friend = make_client(FakeLLM(), user="friend")
    conversation = await start_conversation(producer)
    [photo] = photo_events(await send_turn(producer, conversation, "发张自拍给我看看"))
    await settled(producer, photo["id"])

    for path in (f"/api/photos/{photo['id']}", f"/api/photos/{photo['id']}/image"):
        assert (await friend.get(path)).status_code == 404, path
        assert (await producer.get(path)).status_code == 200, path


async def test_it_goes_with_a_deleted_past_conversation_and_with_the_user(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client = make_client(
        FakeLLM(REPLY, REPLY), clients=asking(), image_transport=image.transport()
    )
    photos = []
    for _ in range(2):
        conversation = await start_conversation(client)
        [photo] = photo_events(await send_turn(client, conversation, "发张自拍给我看看"))
        await settled(client, photo["id"])
        hang_up(settings.database_path, conversation)
        photos.append((conversation, photo["id"]))
    [(past, in_past), (_, in_current)] = photos

    assert (await client.delete(f"/api/conversations/{past}")).status_code == 204
    gone_with_it = rows_mentioning(settings.database_path, in_past)
    kept = rows_mentioning(settings.database_path, in_current)
    assert (await client.post("/api/account/delete", json=DELETE)).status_code == 204

    assert gone_with_it == {}
    assert kept == {"photos": 1}
    assert rows_mentioning(settings.database_path, in_current) == {}


def test_the_deployers_image_generation_is_novelai_with_its_key(monkeypatch: pytest.MonkeyPatch):
    for name in ("NOVELAI_API_KEY", "NOVELAI_BASE_URL", "NOVELAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAT_LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setenv("CHAT_LLM_API_KEY", "chat-key")
    monkeypatch.setenv("CHAT_LLM_MODEL", "chat-model")
    without = Settings.from_env().image

    monkeypatch.setenv("NOVELAI_API_KEY", "pst-key")
    official = Settings.from_env().image
    monkeypatch.setenv("NOVELAI_BASE_URL", "https://relay.example/")
    monkeypatch.setenv("NOVELAI_MODEL", "nai-diffusion-4-5-full")
    relayed = Settings.from_env().image

    assert without is None
    assert official == ImageSettings("pst-key", "https://image.novelai.net", "nai-diffusion-5-curated")
    assert relayed == ImageSettings("pst-key", "https://relay.example", "nai-diffusion-4-5-full")


MANO = Character(
    "mano", "櫻木真乃", "", "voice", "", None, "#ffbad6",
    photo_tag=MANO_PHOTO[0], photo_appearance=MANO_PHOTO[1],
)


def test_her_sentence_and_the_words_in_it_go_after_the_tags_and_text_is_let_in():
    tag = read_tag(
        "shot: other\nframing: upper body\nplace: cafe\nscene: She holds {a latte}, smiling,"
        " with the window behind her\nwords: 「本日のおすすめ」::\nwords_on: the chalkboard behind her"
    )

    assert tag.scene == "She holds a latte smiling with the window behind her."
    assert tag.writing == 'the text "本日のおすすめ" is written on the chalkboard behind her'
    assert image_prompt(MANO, tag).positive.endswith(
        'the text "本日のおすすめ" is written on the chalkboard behind her.,'
        " She holds a latte smiling with the window behind her., high complexity,"
        " rating:general, very aesthetic, masterpiece"
    )


def test_a_sentence_crossing_the_line_is_left_out_whole_and_counted():
    tag = read_tag("shot: other\nscene: She is nude in the bath\nwords: hi\nwords_on: her lingerie")

    assert (tag.scene, tag.writing) == ("", 'the text "hi" is written on it')
    assert len(tag.stripped) == 2
    assert "no text" in image_prompt(MANO, read_tag("shot: other")).positive


def test_an_art_style_can_go_around_the_prompt_and_add_to_what_is_kept_out():
    style = "0.6::artist:a::, ultra complexity,\n{prompt}\n2::messy lines::, {negative} flat color, chibi,"
    prompt = image_prompt(MANO, read_tag("shot: other"), style)

    assert prompt.positive.startswith(
        "1girl, solo, sakuragi mano, idolmaster shiny colors, 0.6::artist:a::, ultra complexity, brown hair"
    )
    assert prompt.positive.endswith("masterpiece, no text, 2::messy lines::")
    assert "high complexity" not in prompt.positive
    assert prompt.negative.startswith("nsfw, lowres") and prompt.negative.endswith("cleavage, flat color, chibi")
    plain = image_prompt(MANO, read_tag("shot: other"), "0.7::artist:b::")
    assert "shiny colors, 0.7::artist:b::, brown hair" in plain.positive and plain.negative.endswith("cleavage")
