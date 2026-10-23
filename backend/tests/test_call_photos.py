"""She sends a photo in a call (GLOSSARY.md「照片」), as in a typed turn: the judge model is
asked whether the user asks for one, and her tag at the front of her reply begins it, never
voiced nor shown. `photo` tells the browser it is being generated, with the number of the
reply; while the call is on, a second `photo` tells it how it came out. One that comes out
after the call is over is told of no more, and is kept with her reply all the same. The
user's photos this hour, text turns' and calls' alike, used up, `photos-used-up` tells the
browser; a call bringing the user's own image generation has her photos generated on it,
counting towards nothing. In a call that could have no photo she is told she cannot send
one, as in a typed turn.

The user's own image generation, brought with the offer, through the backend's external
API, with a browser calling in over a real WebRTC connection; the rest, its timing held, at
the Pipecat stage that wraps the conversation engine, as `test_call_turns.py` drives it. The judge model finds every turn asks for a photo, and the
image service is faked at its transport."""

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from functools import partial

import httpx
import pytest
from pipecat.frames.frames import Frame
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test

from eidolon.call import SONIOX_FOLLOW_ON_TIMEOUT, Call, CallTurns, SegmentTextFrame
from eidolon.call_outcome import CallLimits, CallOutcome
from eidolon.config import Settings, UsageLimits
from eidolon.own_keys import BroughtKeys
from eidolon.photos import CANNOT_SEND
from tests.browser import open_browser
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeImageService,
    FakeLLM,
    FakeSpeech,
    build_faked,
    context_of,
)
from tests.test_call_turns import NOW, one_by_one, spoken
from tests.test_own_image import MINE, MY_KEY, keys_of, photo_uses
from tests.test_photos import IMAGE, REPLY, asking
from tests.test_text_turn import start_conversation

ASKED = "拍一张给我看"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the deployer's image generation and the judge model; affinity off."""
    return replace(settings, image=IMAGE, judge=FAKE_JUDGE, affinity_and_gifts=False)


@pytest.fixture
async def conversation(make_client: ClientFactory) -> tuple[httpx.AsyncClient, str]:
    client = make_client(FakeLLM())
    return client, await start_conversation(client, "mano")


async def call(
    settings: Settings,
    conversation_id: str,
    llm: FakeLLM,
    image: FakeImageService,
    frames: Sequence[Frame],
    *,
    after: asyncio.Event | None = None,
) -> tuple[list[str], list[dict]]:
    """Run the frames through a call into the conversation, connected; the text sent to
    TTS and the messages sent to the browser. Given `after`, it is set once the call is
    over, and the backend stays up till whatever it set off is settled."""
    backend = build_faked(
        settings, llm, clients=asking(), clock=lambda: NOW, image_transport=image.transport()
    )
    try:
        conversation = await backend.store.get_conversation(conversation_id)
        assert conversation is not None
        admission = await backend.admissions.call(conversation.user_id, "mano", BroughtKeys())
        mano = backend.characters["mano"]
        on = Call(conversation, mano, admission.llms, False, "standard")
        outcome = CallOutcome(
            admission, CallLimits(), backend.clock,
            partial(backend.happenings.call_ended, conversation.id, conversation.user_id, "mano"),
        )
        turns = CallTurns(backend.engine, backend.store, on, outcome, SONIOX_FOLLOW_ON_TIMEOUT)
        turns.connected()
        down, _ = await run_test(turns, frames_to_send=frames)
        await outcome.settle()
        if after is not None:
            after.set()
            await photos_settled(settings, conversation_id)
    finally:
        await backend.aclose()
    spoken_text = [f.text for f in down if isinstance(f, SegmentTextFrame)]
    return spoken_text, [f.data for f in down if isinstance(f, RTVIServerMessageFrame)]


async def photos_settled(settings: Settings, conversation_id: str) -> None:
    """Wait till none of the conversation's photos is being generated."""
    for _ in range(200):
        with sqlite3.connect(settings.database_path) as db:
            [(pending,)] = db.execute(
                "SELECT COUNT(*) FROM photos WHERE conversation_id = ? AND status = 'pending'",
                (conversation_id,),
            ).fetchall()
        if not pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("a photo is still being generated")


def photo_messages(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["type"] == "photo"]


async def record_of(client: httpx.AsyncClient, conversation: str) -> list[dict]:
    return (await client.get(f"/api/conversations/{conversation}/messages")).json()


async def test_a_call_bringing_the_users_own_image_generation_has_her_photo_on_it(
    make_client: ClientFactory, speech: FakeSpeech, settings: Settings
):
    image = FakeImageService()
    client = make_client(FakeLLM(REPLY), clients=asking(), image_transport=image.transport())
    conversation_id = await start_conversation(client, "mano")

    async with open_browser() as browser:
        await browser.call(client, conversation_id=conversation_id, own_keys={"image": MINE})
        await browser.connect()
        await speech.stts[-1].hear(ASKED)
        turn = await browser.turn()
        ready = await browser.next_server_message("photo")
        await browser.hang_up()

    [pending] = photo_messages(turn)
    assert pending["status"] == "pending"
    assert ready == {"type": "photo", "reply": 1, "id": pending["id"], "status": "ready", "reason": None}
    assert keys_of(image) == [MY_KEY]
    assert photo_uses(settings.database_path) == [(1,)]


async def test_asked_in_a_call_she_sends_one_told_as_it_is_generated_and_as_it_comes_out(
    settings: Settings, conversation: tuple[httpx.AsyncClient, str]
):
    client, conversation_id = conversation
    image = FakeImageService()
    spoken_text, messages = await call(
        settings, conversation_id, FakeLLM(REPLY), image,
        one_by_one(spoken(ASKED), SleepFrame(0.5)),
    )
    record = await record_of(client, conversation_id)

    [pending, ready] = photo_messages(messages)
    assert pending == {"type": "photo", "reply": 1, "id": pending["id"], "status": "pending"}
    assert ready == {
        "type": "photo", "reply": 1, "id": pending["id"], "status": "ready", "reason": None
    }
    kinds = [m["type"] for m in messages if m["type"] != "emotion"]
    assert kinds.index("photo") < kinds.index("delta")  # before her first words
    assert "".join(spoken_text) == "はい、どうぞ"  # her tag never voiced
    shown = "".join(m["text"] for m in messages if m["type"] == "delta")
    assert "photo" not in shown and "selfie" not in shown
    assert len(image.requests) == 1
    assert record[-1]["photo"] == {"id": pending["id"], "status": "ready", "reason": None}


async def test_one_not_sent_is_told_why_while_the_call_is_on(
    settings: Settings, conversation: tuple[httpx.AsyncClient, str]
):
    _, conversation_id = conversation
    image = FakeImageService()
    image.status = 500
    _, messages = await call(
        settings, conversation_id, FakeLLM(REPLY), image,
        one_by_one(spoken(ASKED), SleepFrame(0.5)),
    )

    [_, failed] = photo_messages(messages)
    assert (failed["status"], failed["reason"]) == ("failed", "service_error")


async def test_one_out_after_the_call_is_over_is_told_no_more_and_kept_with_her_reply(
    settings: Settings, conversation: tuple[httpx.AsyncClient, str]
):
    client, conversation_id = conversation
    image = FakeImageService()
    image.gate = asyncio.Event()
    _, messages = await call(
        settings, conversation_id, FakeLLM(REPLY), image,
        one_by_one(spoken(ASKED), SleepFrame(0.3)), after=image.gate,
    )
    record = await record_of(client, conversation_id)

    assert [m["status"] for m in photo_messages(messages)] == ["pending"]
    [said, reply] = [m for m in record if m["role"] in ("user", "character")]
    assert said["text"] == ASKED
    assert reply["photo"]["status"] == "ready"


async def test_with_the_users_photos_used_up_the_call_tells_the_browser_before_done(
    settings: Settings, conversation: tuple[httpx.AsyncClient, str]
):
    _, conversation_id = conversation
    image = FakeImageService()
    limited = replace(settings, usage_limits=UsageLimits(photos_per_hour=1))
    await call(
        limited, conversation_id, FakeLLM(REPLY), image,
        one_by_one(spoken(ASKED), SleepFrame(0.3)), after=asyncio.Event(),
    )
    _, messages = await call(
        limited, conversation_id, FakeLLM(REPLY), image,
        one_by_one(spoken(ASKED), SleepFrame(0.3)),
    )

    assert photo_messages(messages) == []
    kinds = [m["type"] for m in messages if m["type"] != "emotion"]
    assert kinds[-2:] == ["photos-used-up", "done"]
    assert len(image.requests) == 1


async def test_in_a_call_without_image_generation_she_is_told_she_cannot_send_one(
    settings: Settings, conversation: tuple[httpx.AsyncClient, str]
):
    _, conversation_id = conversation
    image = FakeImageService()
    llm = FakeLLM(REPLY)
    spoken_text, messages = await call(
        replace(settings, image=None), conversation_id, llm, image,
        one_by_one(spoken(ASKED), SleepFrame(0.3)),
    )

    assert CANNOT_SEND in context_of(llm.prompts[0])
    assert photo_messages(messages) == []
    assert "".join(spoken_text) == "はい、どうぞ"
    assert image.requests == []
