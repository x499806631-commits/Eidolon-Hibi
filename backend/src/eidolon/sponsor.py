"""Sponsoring (GLOSSARY.md「赞赏」): the code the deployer sets in the admin console for users
to scan and sponsor the deployment with, kept in the database (`sponsor`) as photos are
(ADR-0024). Users are shown it only while it is switched on and a code is uploaded.

Its picture is kept as a PNG made anew from the upload's pixels, turned upright as the
camera said and on white where it is transparent, nothing else of it kept, and no larger
than `MAX_SIDE` on its longer side: lossless and opaque, so the code still scans on the dark
viewer."""

import asyncio
import io
import sqlite3
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from eidolon.clock import Clock
from eidolon.data_access import Database

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
"""The largest picture the admin may upload."""

MAX_SIDE = 1024
"""How many pixels its longer side has at most, kept; a larger one is scaled down."""

MAX_PIXELS = 40_000_000
"""The most pixels an upload may have, so that a small file cannot unpack into a huge one."""

_SCHEMA = """
-- The deployer's sponsor code: one row at most, `id` 1. `image` a PNG, NULL until uploaded;
-- `image_at` when it was, which tells browsers it is new.
CREATE TABLE IF NOT EXISTS sponsor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    image BLOB,
    image_at TEXT
);
"""


class NotAPicture(Exception):
    """The upload is not a picture PNG, JPEG or WebP, saying why."""


@dataclass(frozen=True)
class SponsorState:
    enabled: bool
    """Whether the admin switched it on."""
    image_at: str | None
    """When the code was uploaded, ISO 8601; None without one."""

    @property
    def version(self) -> str | None:
        """The version of the code users see, when it was uploaded; None while they see
        none (switched off, or no code)."""
        return self.image_at if self.enabled else None


def kept_as_png(upload: bytes) -> bytes:
    """`upload` as the code is kept: a PNG of its pixels alone, upright, on white, scaled
    down to `MAX_SIDE`. Raises NotAPicture unless it is a PNG, JPEG or WebP of at most
    `MAX_PIXELS`."""
    try:
        with Image.open(io.BytesIO(upload), formats=("PNG", "JPEG", "WEBP")) as opened:
            width, height = opened.size
            if width * height > MAX_PIXELS:
                raise NotAPicture(f"{width}×{height} 太大")
            upright = ImageOps.exif_transpose(opened).convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise NotAPicture(str(e)) from None
    pixels = Image.new("RGBA", upright.size, "white")
    pixels.alpha_composite(upright)
    # Made anew from its pixels alone, so nothing of what came with it is written.
    bare = Image.frombytes("RGB", pixels.size, pixels.convert("RGB").tobytes())
    bare.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    bare.save(out, "PNG", optimize=True)
    return out.getvalue()


class Sponsor:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        db.prepare(lambda conn: conn.executescript(_SCHEMA))
        self._clock = clock

    async def state(self) -> SponsorState:
        return await self._db.read(_state)

    async def switch(self, enabled: bool) -> SponsorState:
        """Switch it on or off; switched on without a code, users see nothing yet."""
        return await self._db.write(_switch, enabled)

    async def upload(self, upload: bytes) -> SponsorState:
        """Keep `upload` as the code, replacing the one there was; raises NotAPicture."""
        image = await asyncio.to_thread(kept_as_png, upload)
        return await self._db.write(_upload, image, self._clock().isoformat())

    async def image(self) -> tuple[bytes, str] | None:
        """The code, a PNG, and its version, when it was uploaded; None without one."""
        return await self._db.read(_image)

    async def clear(self) -> SponsorState:
        """Switch it off and delete the code."""
        return await self._db.write(_clear)


def _state(db: sqlite3.Connection) -> SponsorState:
    row = db.execute("SELECT enabled, image_at FROM sponsor WHERE id = 1").fetchone()
    if row is None:
        return SponsorState(False, None)
    return SponsorState(bool(row[0]), row[1])


def _switch(db: sqlite3.Connection, enabled: bool) -> SponsorState:
    db.execute(
        "INSERT INTO sponsor (id, enabled) VALUES (1, ?)"
        " ON CONFLICT (id) DO UPDATE SET enabled = excluded.enabled",
        (enabled,),
    )
    return _state(db)


def _upload(db: sqlite3.Connection, image: bytes, now: str) -> SponsorState:
    db.execute(
        "INSERT INTO sponsor (id, image, image_at) VALUES (1, ?, ?)"
        " ON CONFLICT (id) DO UPDATE SET image = excluded.image, image_at = excluded.image_at",
        (image, now),
    )
    return _state(db)


def _image(db: sqlite3.Connection) -> tuple[bytes, str] | None:
    row = db.execute(
        "SELECT image, image_at FROM sponsor WHERE id = 1 AND image IS NOT NULL"
    ).fetchone()
    return None if row is None else (row[0], row[1])


def _clear(db: sqlite3.Connection) -> SponsorState:
    db.execute("DELETE FROM sponsor")
    return _state(db)
