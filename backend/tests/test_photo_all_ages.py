"""A photo is all ages whatever she writes in her tag (ADR-0023): what crosses the line is
stripped from it, tag by tag, and the photo generated from the rest, a field stripped
bare taking its default; swimsuits, pajamas and lying on a bed are not crossing it. Her
looks, an art style or an artist, and anyone else in the picture are stripped too. What
she writes the model does not know as a tag is put as the model knows it, by the local
tag table, or kept as it is. The dashboard says how often, and for which part of the
line, tags were stripped from her photos; never which tags.

Driven through the backend's external API, the image service faked at its transport,
where every request it is sent is seen."""

import logging
from dataclasses import replace

import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeImageService, FakeLLM
from tests.test_activity_records import admin_client, dashboard, health
from tests.test_photos import IMAGE, WORDS, asking, photo_events, settled
from tests.test_text_turn import send_turn, start_conversation

HEAD = "1girl, solo, sakuragi mano, idolmaster shiny colors, brown hair, medium hair, brown eyes"
TAIL = "high complexity, rating:general, very aesthetic, masterpiece, no text"

CROSSING = """
shot: other
framing: full body
place: Bathroom, onsen, hot spring, rotenburo, beach, poolside, sparkly sand
outfit: micro_bikini, {{see-through}}, see_through, nude, bathing suit, one-piece swimsuit, school swimsuit, Lingerie, pajamas
action: lying, on bed, ahegao, spread legs, from below, blonde hair, twintails, large breasts, artist:someone, 1boy, v sign, looking at camera, rating:explicit
"""
ALL_OF_IT = """
shot: other
framing: upper body
place: shower
outfit: lingerie, panties, bunny_girl
action: ahegao
"""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the deployer's image generation and the judge model, affinity off; the GitHub
    user 1001 is an admin."""
    return replace(
        settings, image=IMAGE, judge=FAKE_JUDGE, affinity_and_gifts=False,
        admins=frozenset({("github", "1001")}),
    )


async def test_what_crosses_the_line_is_stripped_and_counted_never_named(
    make_client: ClientFactory, github: FakeIdentityProvider, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="eidolon.photo_prompt")
    clock = FakeClock()
    image = FakeImageService()
    client = make_client(
        FakeLLM([f"<photo>{CROSSING}</photo>{WORDS}"], [f"<photo>{ALL_OF_IT}</photo>{WORDS}"]),
        clients=asking(),
        clock=clock,
        image_transport=image.transport(),
    )
    admin = await admin_client(make_client, github, clock)
    conversation = await start_conversation(client)

    for asked in ("发张照片", "再来一张"):
        [photo] = photo_events(await send_turn(client, conversation, asked))
        assert (await settled(client, photo["id"]))["status"] == "ready", asked
    board = await dashboard(admin)

    [crossing, all_of_it] = (r["body"]["input"] for r in image.requests)
    assert crossing == (
        f"{HEAD}, full body, beach, poolside, sparkly sand, swimsuit, one-piece swimsuit, pajamas,"
        f" lying, on bed, peace sign, looking at viewer, {TAIL}"
    )
    assert all_of_it == (
        f"{HEAD}, upper body, indoors, casual, smile, looking at viewer, {TAIL}"
    )
    unknown = [r.getMessage() for r in caplog.records if r.name == "eidolon.photo_prompt"]
    assert any("sparkly sand" in m for m in unknown), unknown
    image_row = health(board, "image")
    assert image_row is not None
    assert image_row["stripped_photos"] == 2
    assert image_row["stripped"] == {
        "nudity": 2, "underwear": 6, "exposure": 2, "bathing": 5, "pose": 2,
        "expression": 2, "appearance": 3, "style": 1, "people": 1,
    }
    for written in ("bathroom", "micro", "lingerie", "ahegao", "blonde", "someone", "bunny", "1boy"):
        assert written not in str(board), written
