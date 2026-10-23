"""The producer's home (GLOSSARY.md「制作人的家」): a room of the user's own, drawn in the
browser, whose things lead to what the app holds (the mailbox to the letters, the diary on
the desk to the diary, the shelf to the keepsakes, the phone to the conversations, and so
on). What is kept of it here is only what the user chose: which room it is (`Room`), and
which of the photos she sent hangs framed on the wall, if any. Each user's, deleted with
them; in no data package, as it is only how they set out their room.

The album is the photos sent to the user, newest first: those in their conversations,
answered or sent unasked, that came out (`ready`)."""

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Literal, get_args

from eidolon.activity import ActivityLog, image_origin, timed
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.image_generation import TIME_LIMIT, ImageFailed
from eidolon.live_config import LiveConfig
from eidolon.photo_prompt import home_prompt

log = logging.getLogger(__name__)

Room = Literal["apartment", "mansion"]
"""Which room it is: a one-room apartment, or a big house."""
ROOMS: tuple[Room, ...] = get_args(Room)

THINGS = ("mailbox", "diary", "shelf", "phone", "calendar", "drawer", "frame", "window")
"""What the user can set out in a room (the browser's `HomeThing`)."""

ALBUM_SIZE = 200
"""The most photos the album shows, newest first."""

_SCHEMA = """
-- The producer's home (GLOSSARY.md「制作人的家」): which room the user chose and the photo
-- hanging framed in it, if any.
CREATE TABLE IF NOT EXISTS homes (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    room TEXT NOT NULL CHECK (room IN ('apartment', 'mansion')),
    framed_photo TEXT
);
-- Each room's picture as drawn for the user, and how many times it has been (for the
-- browser to ask anew).
CREATE TABLE IF NOT EXISTS home_backgrounds (
    user_id TEXT NOT NULL REFERENCES users(id),
    room TEXT NOT NULL,
    image BLOB NOT NULL,
    drawn INTEGER NOT NULL,
    PRIMARY KEY (user_id, room)
);
"""

Layout = dict[str, tuple[float, float]]
"""Where each thing stands in a room: across and down, as fractions of the picture."""


@dataclass(frozen=True)
class HomeState:
    room: Room = "apartment"
    framed_photo: str | None = None
    layouts: dict[str, Layout] = field(default_factory=dict)
    """Each room's layout, by room; a room never set out has none."""


@dataclass(frozen=True)
class AlbumPhoto:
    id: str
    character_id: str
    sent_at: str


class NoSuchPhoto(Exception):
    """No photo of that id was sent the user."""


class Home:
    def __init__(self, db: Database, config: LiveConfig, activity: ActivityLog) -> None:
        self._config = config
        self._activity = activity
        self._db = db
        db.prepare(_open)

    async def of(self, user_id: str) -> HomeState:
        """The user's home as they set it out; an apartment with nothing framed if never."""
        return await self._db.read(self._of_body, user_id)

    def _of_body(self, db: sqlite3.Connection, user_id: str) -> HomeState:
        row = by_name(db).execute(
            "SELECT room, framed_photo, layouts FROM homes WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return HomeState()
        framed = row["framed_photo"]
        if framed is not None and not self._sent_in(db, user_id, framed):
            framed = None  # its conversation deleted since
        layouts = {
            room: {thing: (float(at[0]), float(at[1])) for thing, at in layout.items()}
            for room, layout in json.loads(row["layouts"] or "{}").items()
        }
        return HomeState(row["room"], framed, layouts)

    async def set(
        self, user_id: str, room: Room | None = None, framed_photo: str | None = None,
        unframe: bool = False, layout: Layout | None = None,
    ) -> HomeState:
        """The user's home with `room` if given, `framed_photo` hanging if given, or none if
        `unframe`, and the room (as it is then) set out as `layout` if given: the things it
        names, each kept within the picture, those it does not where they were. Raises
        NoSuchPhoto for a photo not sent them."""
        return await self._db.write(self._set_body, user_id, room, framed_photo, unframe, layout)

    def _set_body(
        self,
        db: sqlite3.Connection, user_id: str, room: Room | None = None, framed_photo: str | None = None,
        unframe: bool = False, layout: Layout | None = None,
    ) -> HomeState:
        if framed_photo is not None and not self._sent_in(db, user_id, framed_photo):
            raise NoSuchPhoto()
        now = self._of_body(db, user_id)
        room = room or now.room
        framed = None if unframe else (framed_photo or now.framed_photo)
        layouts = dict(now.layouts)
        if layout is not None:
            set_out = dict(layouts.get(room, {}))
            for thing, (x, y) in layout.items():
                if thing in THINGS:
                    set_out[thing] = (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))
            layouts[room] = set_out
        by_name(db).execute(
            "INSERT INTO homes (user_id, room, framed_photo, layouts) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (user_id) DO UPDATE SET room = excluded.room,"
            " framed_photo = excluded.framed_photo, layouts = excluded.layouts",
            (user_id, room, framed, json.dumps(layouts)),
        )
        return HomeState(room, framed, layouts)

    def can_draw(self) -> bool:
        """Whether rooms' pictures are drawn: on the deployer's image generation."""
        return self._config.image_generator() is not None

    async def drawn(self, user_id: str, room: Room) -> int | None:
        """How many times the room's picture has been drawn for the user; None if never."""
        return await self._db.read(self._drawn_body, user_id, room)

    def _drawn_body(self, db: sqlite3.Connection, user_id: str, room: Room) -> int | None:
        row = by_name(db).execute(
            "SELECT drawn FROM home_backgrounds WHERE user_id = ? AND room = ?", (user_id, room)
        ).fetchone()
        return row["drawn"] if row else None

    async def background(self, user_id: str, room: Room) -> bytes | None:
        return await self._db.read(self._background_body, user_id, room)

    def _background_body(self, db: sqlite3.Connection, user_id: str, room: Room) -> bytes | None:
        row = by_name(db).execute(
            "SELECT image FROM home_backgrounds WHERE user_id = ? AND room = ?", (user_id, room)
        ).fetchone()
        return row["image"] if row else None

    async def draw(self, user_id: str, room: Room) -> bool:
        """Draw the room's picture for the user, in place of the one it has, on the deployer's
        image generation: False without one, or if it was not drawn (the old one kept)."""
        generator = self._config.image_generator()
        if generator is None:
            return False
        settings = generator.settings
        recording = self._activity.begin("home", user_id, "", own_llm=False, exempt=False)
        until = asyncio.get_running_loop().time() + TIME_LIMIT
        try:
            with recording.current():
                image = (await timed(
                    self._activity, image_origin(settings),
                    generator.generate(home_prompt(room, settings.style), until), (),
                )).webp
        except (ImageFailed, TimeoutError) as e:
            log.warning("a room's picture was not drawn: %s", e)
            await recording.finish("failed")
            return False
        await recording.finish("success")
        await self._db.write(_keep_background, user_id, room, image)
        return True

    async def album(self, user_id: str) -> list[AlbumPhoto]:
        """The photos sent the user that came out, newest first, `ALBUM_SIZE` at most."""
        return await self._db.read(self._album_body, user_id)

    def _album_body(self, db: sqlite3.Connection, user_id: str) -> list[AlbumPhoto]:
        rows = by_name(db).execute(
            "SELECT p.id, c.character_id, p.created_at FROM (SELECT id, status,"
            " conversation_id, created_at FROM photos UNION ALL SELECT id, status,"
            " conversation_id, created_at FROM reach_out_photos) p"
            " JOIN conversations c ON c.id = p.conversation_id"
            " WHERE c.user_id = ? AND p.status = 'ready'"
            " ORDER BY p.created_at DESC LIMIT ?",
            (user_id, ALBUM_SIZE),
        ).fetchall()
        return [AlbumPhoto(r["id"], r["character_id"], r["created_at"]) for r in rows]

    async def _sent(self, user_id: str, photo_id: str) -> bool:
        return await self._db.read(self._sent_in, user_id, photo_id)

    def _sent_in(self, db: sqlite3.Connection, user_id: str, photo_id: str) -> bool:
        return by_name(db).execute(
            "SELECT 1 FROM (SELECT id, status, conversation_id FROM photos UNION ALL"
            " SELECT id, status, conversation_id FROM reach_out_photos) p"
            " JOIN conversations c ON c.id = p.conversation_id"
            " WHERE p.id = ? AND c.user_id = ? AND p.status = 'ready'",
            (photo_id, user_id),
        ).fetchone() is not None



def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    if "layouts" not in {r[1] for r in db.execute("PRAGMA table_info(homes)")}:
        db.execute("ALTER TABLE homes ADD COLUMN layouts TEXT")


def _keep_background(db: sqlite3.Connection, user_id: str, room: str, image: bytes) -> None:
    db.execute(
        "INSERT INTO home_backgrounds (user_id, room, image, drawn) VALUES (?, ?, ?, 1)"
        " ON CONFLICT (user_id, room) DO UPDATE SET image = excluded.image,"
        " drawn = drawn + 1",
        (user_id, room, image),
    )
