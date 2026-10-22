"""Photos in data packages (GLOSSARY.md「数据包」「照片」): the photos she sent go with the
conversations they were sent in, never without them. Each one sent is a plain WebP in the
package's `photos/`, to open unzipped; one still being generated as the package is written
goes as not sent, for taking too long, as on a restart. Imported, each is shown in her reply
as it was, and read only by the user whose conversation it is in. Left out, her replies carry
none. A package whose photos are not as its data says is refused whole. Driven through the
API: what a package holds by unzipping it, what an import did by the read endpoints; the
image service faked at its transport."""

import asyncio
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeImageService, FakeLLM
from tests.test_conversation_events import record
from tests.test_data_package import (
    MANIFEST,
    NOTHING_ELSE,
    export,
    import_package,
    memories_package,
    packaged_memory,
    unzipped,
)
from tests.test_data_package_conversations import packaged_conversation, replied
from tests.test_past_conversations import hers
from tests.test_photos import IMAGE, REPLY, asking, photo_events, settled
from tests.test_text_turn import send_turn, start_conversation


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the deployer's image generation and the judge model; affinity off."""
    return replace(settings, image=IMAGE, judge=FAKE_JUDGE, affinity_and_gifts=False)


def deployment(make_client: ClientFactory, settings: Settings, tmp_path: Path, name: str,
               *replies: list[str], user: str = "producer", **services: object
               ) -> httpx.AsyncClient:
    """A client of a deployment of its own: the same characters, a database of its own."""
    return make_client(
        FakeLLM(*replies), user,
        settings=replace(settings, database_path=tmp_path / f"{name}.db"), **services,
    )


async def photos_in(client: httpx.AsyncClient) -> list[dict | None]:
    """The photo of each reply in Mano's conversations, oldest first: its status and reason,
    and its picture's size if sent; None for a reply with none."""
    photos: list[dict | None] = []
    for conversation in reversed(await hers(client)):
        for entry in await record(client, conversation["id"]):
            if entry.get("role") != "character":
                continue
            photo = entry.get("photo")
            if photo is None:
                photos.append(None)
                continue
            read = await client.get(f"/api/photos/{photo['id']}")
            assert read.status_code == 200, read.text
            assert read.json() == {"status": photo["status"], "reason": photo["reason"]}
            seen = {"status": photo["status"], "reason": photo["reason"]}
            if photo["status"] == "ready":
                picture = await client.get(f"/api/photos/{photo['id']}/image")
                with Image.open(io.BytesIO(picture.content)) as webp:
                    seen["size"] = webp.size
            photos.append(seen)
    return photos


def webp(size: tuple[int, int] = (64, 96)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (200, 120, 160)).save(out, "WEBP")
    return out.getvalue()


async def test_photos_move_with_their_conversations_and_are_hers_alone_there(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    image = FakeImageService()
    here = deployment(
        make_client, settings, tmp_path, "here", REPLY, REPLY, REPLY,
        clients=asking(), image_transport=image.transport(),
    )
    sent = await start_conversation(here)
    [ready] = photo_events(await send_turn(here, sent, "发张自拍给我看看"))
    await settled(here, ready["id"])
    image.status = 500
    [failed] = photo_events(await send_turn(here, sent, "再来一张"))
    await settled(here, failed["id"])
    image.status, image.gate = None, asyncio.Event()
    waiting = await start_conversation(here)
    [pending] = photo_events(await send_turn(here, waiting, "发张自拍给我看看"))
    there = deployment(make_client, settings, tmp_path, "there")
    friend = deployment(make_client, settings, tmp_path, "there", user="friend")

    package = await export(here, "conversations,photos")
    image.gate.set()
    entries, manifest, data = unzipped(package)
    assert manifest["categories"] == ["conversations", "photos"]
    assert manifest["counts"] == {"conversations": 2, "photos": 1}  # those sent alone
    assert [e.filename for e in entries] == [
        "manifest.json", f"photos/{ready['id']}.webp", "data.json",
    ]
    with zipfile.ZipFile(io.BytesIO(package)) as zipped:
        picture = zipped.read(f"photos/{ready['id']}.webp")
    assert picture == (await here.get(f"/api/photos/{ready['id']}/image")).content
    [first, second] = data["conversations"]
    photos = [m["photo"] for m in first["messages"] + second["messages"] if "photo" in m]
    assert [(p["id"], p["status"], p["reason"]) for p in photos] == [
        (ready["id"], "ready", None),
        (failed["id"], "failed", "service_error"),
        (pending["id"], "failed", "timeout"),
    ]
    assert all(p["tag"] and p["created_at"] and p["updated_at"] for p in photos)
    assert "photo" not in first["messages"][0]

    res = await import_package(there, package, "conversations,photos")

    assert res.status_code == 200, res.text
    assert res.json() == {**NOTHING_ELSE, "conversations": 2, "photos": 1, "profile": False}
    assert await photos_in(there) == [
        {"status": "ready", "reason": None, "size": (832, 1216)},
        {"status": "failed", "reason": "service_error"},
        {"status": "failed", "reason": "timeout"},
    ]
    [imported] = [
        e["photo"] for c in await hers(there) for e in await record(there, c["id"])
        if e.get("photo", {}).get("status") == "ready"
    ]
    assert imported["id"] != ready["id"]
    assert (await friend.get(f"/api/photos/{imported['id']}")).status_code == 404
    assert (await friend.get(f"/api/photos/{imported['id']}/image")).status_code == 404
    again = await import_package(there, package, "conversations,photos")
    assert again.json() == {**NOTHING_ELSE, "profile": False}


async def sent_one(make_client: ClientFactory, settings: Settings, tmp_path: Path,
                   user: str = "producer") -> httpx.AsyncClient:
    """A deployment where she has sent the user a photo."""
    image = FakeImageService()
    here = deployment(
        make_client, settings, tmp_path, "here", REPLY, clients=asking(),
        image_transport=image.transport(), user=user,
    )
    conversation = await start_conversation(here)
    [photo] = photo_events(await send_turn(here, conversation, "发张自拍给我看看"))
    await settled(here, photo["id"])
    return here


async def test_no_one_elses_conversations_memories_or_photos_are_in_a_users_package(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    friend = await sent_one(make_client, settings, tmp_path, user="friend")
    remembered = await import_package(friend, memories_package(packaged_memory(1, "我住在大阪")), "memories")
    assert remembered.json()["memories"] == 1
    me = deployment(make_client, settings, tmp_path, "here")

    entries, manifest, data = unzipped(await export(me, "conversations,photos,memories"))

    assert [e.filename for e in entries] == ["manifest.json", "data.json"]
    assert manifest["counts"] == {"conversations": 0, "photos": 0, "memories": 0}
    assert data == {
        "conversations": [], "memories": [], "promises": [], "experiences": [], "diary": [],
    }


async def test_without_photos_her_replies_carry_none(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    here = await sent_one(make_client, settings, tmp_path)
    there = deployment(make_client, settings, tmp_path, "there")
    elsewhere = deployment(make_client, settings, tmp_path, "elsewhere")

    without = await export(here, "conversations")
    entries, manifest, data = unzipped(without)
    assert [e.filename for e in entries] == ["manifest.json", "data.json"]
    assert manifest["categories"] == ["conversations"]
    assert "photos" not in manifest["counts"]
    assert all("photo" not in m for m in data["conversations"][0]["messages"])
    alone = await export(here, "photos,memories")
    assert unzipped(alone)[1]["categories"] == ["memories"]
    assert len(unzipped(alone)[0]) == 2

    res = await import_package(there, without, "conversations,photos")
    assert res.json()["photos"] == 0
    assert await photos_in(there) == [None]
    res = await import_package(
        elsewhere, await export(here, "conversations,photos"), "conversations"
    )
    assert res.json() == {**NOTHING_ELSE, "conversations": 1, "profile": False}
    assert await photos_in(elsewhere) == [None]


def photo(id: str, status: str = "ready", reason: str | None = None) -> dict:
    at = "2026-09-01T08:00:02.000000+00:00"
    return {"id": id, "tag": "shot: selfie", "status": status, "reason": reason,
            "created_at": at, "updated_at": at}


def with_photos(*photos: dict, pictures: dict[str, bytes], categories: list[str] | None = None
                ) -> bytes:
    """A package of one conversation, her replies in it carrying `photos`, and `pictures` by
    their names in it."""
    conversation = packaged_conversation("1", "2026-09-01")
    at = conversation["created_at"]
    conversation["messages"] = [
        m for p in photos for m in (
            conversation["messages"][0], replied("好的", at) | {"photo": p},
        )
    ]
    categories = categories or ["conversations", "photos"]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zipped:
        zipped.writestr("manifest.json", json.dumps({
            **MANIFEST, "categories": categories,
            "counts": {"conversations": 1, "photos": len(photos)},
        }), compress_type=zipfile.ZIP_STORED)
        zipped.writestr("data.json", json.dumps({"conversations": [conversation]}))
        for name, picture in pictures.items():
            zipped.writestr(name, picture)
    return out.getvalue()


async def test_a_package_made_elsewhere_brings_its_photos_of_any_size(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")

    res = await import_package(client, with_photos(
        photo("a"), photo("b", "failed", "used_up"),
        pictures={"photos/a.webp": webp((64, 96))},
    ), "conversations,photos")

    assert res.status_code == 200, res.text
    assert res.json()["photos"] == 1  # the one sent: the other is only that she did not
    assert await photos_in(client) == [
        {"status": "ready", "reason": None, "size": (64, 96)},
        {"status": "failed", "reason": "used_up"},
    ]


async def test_photos_not_as_the_package_says_are_refused_and_nothing_is_imported(
    make_client: ClientFactory, settings: Settings, tmp_path: Path
):
    client = deployment(make_client, settings, tmp_path, "here")
    broken = {
        "a picture that is not one": with_photos(
            photo("a"), pictures={"photos/a.webp": b"RIFF\x00\x00\x00\x00WEBPnot really"},
        ),
        "a photo sent without its picture": with_photos(photo("a"), pictures={}),
        "a picture of a photo not sent": with_photos(
            photo("a", "failed", "timeout"), pictures={"photos/a.webp": webp()},
        ),
        "photos not listed": with_photos(
            photo("a"), pictures={"photos/a.webp": webp()}, categories=["conversations"],
        ),
        "a photo not sent without why": with_photos(photo("a", "failed"), pictures={}),
        "two photos under one id": with_photos(
            photo("a"), photo("a"), pictures={"photos/a.webp": webp()},
        ),
    }

    for case, package in broken.items():
        res = await import_package(client, package, "conversations,photos")

        assert res.status_code == 422, case
        assert res.json()["code"] == "broken", case
    assert await hers(client) == []
