"""The English tags an outfit of hers is drawn with (GLOSSARY.md「衣装」「体验券」): for a photo
the producer asks for in one of the outfits on her outfit list (`stand_outfits`).

An image model does not know a card by its name, so an outfit is told it in tags, as her
photo tag's `outfit` is. Where the deployer has a model that reads images (`VisionSettings`,
`.env`'s `VISION_LLM_*`, any service speaking OpenAI's chat completions with image input),
the outfit's thumbnail (`stand_outfits.thumbnail_path`, from the stand assets) is read once,
the first time anyone asks for it, and its tags kept for everyone after (`outfit_tags`, not
anyone's). Without such a model, or should reading it fail, there are none, and she writes
them from what she remembers of the card, told its name.
"""

import base64
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from eidolon.database import connect
from eidolon.stand_outfits import thumbnail_path

log = logging.getLogger(__name__)

PROMPT = (
    "This is an anime idol character's outfit. List only the clothes and accessories she is "
    "wearing as 4 to 8 Danbooru-style English tags, comma-separated, lowercase: garments, "
    "their colours, notable details. Do not describe hair, eyes, face, body, pose or "
    "background. Answer with the tags alone."
)
TIMEOUT = 60.0
MAX_TAGS = 8

_SCHEMA = """
-- The tags each outfit on the outfit lists is drawn with, read once from its thumbnail by the
-- deployer's model that reads images; no one's.
CREATE TABLE IF NOT EXISTS outfit_tags (
    outfit_id TEXT PRIMARY KEY,
    tags TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class VisionSettings:
    """A model that reads images, over OpenAI's chat completions: where, the key, which."""

    base_url: str
    api_key: str
    model: str


class OutfitTags:
    def __init__(
        self,
        path: Path,
        vision: VisionSettings | None,
        assets_url: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._vision = vision
        self._assets_url = assets_url
        self._transport = transport
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    @property
    def can_read(self) -> bool:
        """Whether outfits can be read from their thumbnails here."""
        return self._vision is not None and self._assets_url is not None

    async def of(self, outfit_id: str) -> str | None:
        """The outfit's tags, comma-separated: kept, or read now and kept; None if there is no
        model to read it with, or reading it failed."""
        with self._lock:
            row = self._db.execute(
                "SELECT tags FROM outfit_tags WHERE outfit_id = ?", (outfit_id,)
            ).fetchone()
        if row is not None:
            return row["tags"]
        if not self.can_read or not _well_formed(outfit_id):
            return None
        try:
            tags = await self._read(outfit_id)
        except Exception as e:
            log.warning("outfit %s could not be read: %s", outfit_id, type(e).__name__)
            return None
        if not tags:
            return None
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO outfit_tags (outfit_id, tags, created_at) VALUES (?, ?, ?)",
                (outfit_id, tags, datetime.now(UTC).isoformat()),
            )
        return tags

    async def _read(self, outfit_id: str) -> str:
        assert self._vision is not None and self._assets_url is not None
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
            picture = await client.get(f"{self._assets_url}/{thumbnail_path(outfit_id)}")
            picture.raise_for_status()
            image = base64.b64encode(picture.content).decode()
            kind = picture.headers.get("content-type", "image/png").split(";")[0]
            answer = await client.post(
                f"{self._vision.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._vision.api_key}"},
                json={
                    "model": self._vision.model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": PROMPT},
                            {"type": "image_url", "image_url": {"url": f"data:{kind};base64,{image}"}},
                        ],
                    }],
                    "max_tokens": 200,
                },
            )
            answer.raise_for_status()
            text = answer.json()["choices"][0]["message"]["content"] or ""
        return cleaned(text)

    def close(self) -> None:
        with self._lock:
            self._db.close()


def cleaned(text: str) -> str:
    """The tags in what the model answered: comma-separated, lowercase, each a short phrase,
    `MAX_TAGS` at most; nothing else it wrote."""
    tags = []
    for raw in text.replace("\n", ",").split(","):
        tag = " ".join(raw.strip(" .*-\"'`").replace("_", " ").lower().split())
        if tag and len(tag) <= 40 and tag not in tags:
            tags.append(tag)
    return ", ".join(tags[:MAX_TAGS])


def _well_formed(outfit_id: str) -> bool:
    """`stand/<enzaId>` or `stand_costume/<enzaId>`, as the outfit lists name them."""
    category, _, card = outfit_id.partition("/")
    return category in ("stand", "stand_costume") and card.isascii() and card.isdigit()
