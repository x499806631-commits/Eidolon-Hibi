"""What a photo is generated from (GLOSSARY.md「照片」): the photo tag she wrote, read field by
field, and the prompts the image service is sent for it.

She writes how the photo is taken (`shot`: a selfie, or someone else taking it), how much of
her it shows (`framing`), and three fields of English tags: where and when (`place`), what
she wears (`outfit`), what she does and how she looks (`action`); and a note in Chinese for
herself alone, never sent. What she writes is cleaned before it goes anywhere: the prompt
syntax NovelAI reads (emphasis, weights, alternation, comments, quotes, `Text:`) is
stripped, the tags put in lower case with spaces for underscores, and those the model
does not know as tags put as it does, by the local tag table (`photo_tags.json`, made by
`scripts/photo_tags.py` from NovelAI's tag suggestions); one it has no word on is kept as it
is, and logged. A tag crossing the line (ADR-0023: nudity, underwear, see-through, bathing,
a sexual pose or look) is stripped, as is one of her looks, an art style or an artist, or
anyone else in the picture: only that tag, the photo taken all the same of the rest
(`PhotoTag.stripped` says which part of the line each stripped crossed, for the activity
records, never the tag). Each is written once, and each field cut to its most; a field left
empty gets its default. Swimsuits, pajamas, lying on a bed, a pool and the beach are not
crossing it. Neither the deployer nor any user changes the table.

The positive prompt is fixed around them: who she is (her character tag and the work's
tag), the deployment's art style, written in as the deployer wrote it (weights, artists
and all; none if empty), her looks, the tags for how the photo is taken, `rating:general` and V5's standard
quality tags, written out rather than left to the service's switch. The negative prompt is
fixed altogether: `nsfw`, the Human Focus preset, and what no photo of hers shows
(ADR-0023). Neither the deployer nor any user changes them.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from eidolon.characters import Character

log = logging.getLogger(__name__)

WORK_TAG = "idolmaster shiny colors"
QUALITY_TAGS = ("rating:general", "very aesthetic", "masterpiece", "no text")
"""What every positive prompt ends with: all ages, then V5's standard quality tags."""

NEGATIVE_PROMPT = ", ".join((
    "nsfw",
    # The Human Focus preset: the Heavy one, with what goes wrong with faces.
    "lowres", "artistic error", "film grain", "scan artifacts", "worst quality", "bad quality",
    "jpeg artifacts", "very displeasing", "chromatic aberration", "dithering", "halftone",
    "screentone", "multiple views", "logo", "too many watermarks", "negative space",
    "blank page", "@_@", "mismatched pupils", "glowing eyes", "bad anatomy",
    # All ages (ADR-0023).
    "nude", "nipples", "underwear", "bra", "panties", "bra strap", "strap slip", "see-through",
    "pantyshot", "cleavage",
))

Shot = Literal["selfie", "other"]
Framing = Literal["portrait", "upper body", "cowboy shot", "full body"]
FRAMINGS: tuple[Framing, ...] = get_args(Framing)
SELFIE_FRAMINGS: tuple[Framing, ...] = ("portrait", "upper body")
"""How much of her a selfie can show: no more than an arm's length takes in."""

FIELD_TAGS = 4
"""The most tags each of her three fields keeps: twelve at most in all."""
DEFAULTS = {"place": "indoors", "outfit": "casual", "action": "smile"}
"""What a field she left empty, or that comes to nothing once cleaned, is taken to say."""

LOOKING_AT_VIEWER = "looking at viewer"
_GAZES = ("looking", "eyes closed", "closed eyes")
"""What in her action says where she looks; without any, a photo someone else takes has her
looking at the camera."""

Crossing = Literal[
    "nudity", "underwear", "exposure", "bathing", "pose", "expression", "appearance", "style",
    "people",
]
"""Which part of the line a tag stripped from her photo crosses (ADR-0023): nudity or half of
it, underwear and costumes made to be sexy, see-through clothes and what shows by accident,
bathing, a sexual angle or pose, a sexual look; or what is never hers to write: her looks
(her hair, eyes and body), an art style or an artist, anyone else in the picture."""
CROSSINGS: tuple[Crossing, ...] = get_args(Crossing)

_WEIGHT = re.compile(r"-?\d+(\.\d+)?::|::|:-?\d+(\.\d+)?\b")
_TEXT = re.compile(r"\btext:", re.IGNORECASE)
_SYNTAX = re.compile(r"[{}\[\]|#\"'“”‘’`]")


@dataclass(frozen=True)
class PhotoTag:
    """A photo tag as read: how the photo is taken and how much of her it shows, and her
    three fields of tags, cleaned."""

    shot: Shot
    framing: Framing
    place: tuple[str, ...]
    outfit: tuple[str, ...]
    action: tuple[str, ...]
    stripped: tuple[Crossing, ...] = ()
    """Which part of the line each tag stripped crossed, one for each."""


@dataclass(frozen=True)
class ImagePrompt:
    positive: str
    negative: str
    size: tuple[int, int] | None = None
    """The picture's width and height; None for a photo's, portrait."""


def read_tag(text: str) -> PhotoTag:
    """The photo tag `text` says, one field a line (`shot: selfie`), whatever it leaves out or
    gets wrong taken as most likely meant: someone else taking it, from the waist up."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, colon, value = line.partition(":")
        if colon:
            fields.setdefault(name.strip().lower(), value.strip())
    shot: Shot = "selfie" if fields.get("shot", "").strip().lower() == "selfie" else "other"
    written = _cleaned(fields.get("framing", ""))
    framing: Framing = "upper body"
    for known in FRAMINGS:
        if known == written:
            framing = known
    if shot == "selfie" and framing not in SELFIE_FRAMINGS:
        framing = "upper body"
    seen = {*_FIXED, *_shot_tags(shot), framing}
    stripped: list[Crossing] = []
    place, outfit, action = (
        _field(fields.get(name, ""), name, seen, stripped) for name in DEFAULTS
    )
    return PhotoTag(shot, framing, place, outfit, action, tuple(stripped))


def image_prompt(character: Character, tag: PhotoTag, style: str = "") -> ImagePrompt:
    """The prompts a photo of `character` (who has her photo tags) is generated from, as
    `tag` says it is taken, in the art style `style`."""
    assert character.photo_tag is not None and character.photo_appearance is not None
    action = tag.action
    if tag.shot == "other" and not any(g in t for t in action for g in _GAZES):
        action = (*action, LOOKING_AT_VIEWER)
    positive = (
        "1girl", "solo", character.photo_tag, WORK_TAG, *_styled(style),
        character.photo_appearance, *_shot_tags(tag.shot), tag.framing, *tag.place,
        *tag.outfit, *action, *QUALITY_TAGS,
    )
    return ImagePrompt(", ".join(positive), NEGATIVE_PROMPT)


KEEPSAKE_SIZE = (1024, 1024)
"""A keepsake's picture: square (GLOSSARY.md「纪念品」)."""

KEEPSAKE_TAGS = 6
"""The most tags a keepsake's picture keeps from what was written for it."""

KEEPSAKE_NEGATIVE = ", ".join((
    NEGATIVE_PROMPT, "1girl", "1boy", "solo focus", "person", "hands", "text", "watermark",
))
"""A keepsake's picture shows no one: the thing alone."""


def keepsake_prompt(tags: str, style: str = "") -> tuple[ImagePrompt, tuple[Crossing, ...]]:
    """The prompts a keepsake's picture (GLOSSARY.md「纪念品」) is generated from: the thing
    alone, no one in it, square, in the art style `style`, from the English tags written for
    it (`tags`, comma-separated), cleaned and stripped as a photo's are, `KEEPSAKE_TAGS` at
    most; and which part of the line each stripped crossed.

    It starts with `background dataset`, NovelAI's tag for a scene with no one in it. One
    entry of `tags` in double quotes is the words written on the thing (`keepsake_words`):
    kept in its quotes, which is how V5 is asked to draw text, after the tags, with `text`
    no longer kept out of the picture."""
    seen = {*_FIXED, *_KEEPSAKE_START}
    stripped: list[Crossing] = []
    kept: list[str] = []
    words = ""
    for raw in re.split(r"[,，、]", tags):
        if quoted := _QUOTED.fullmatch(raw.strip()):
            words = words or keepsake_words(quoted.group(1))
            continue
        written = _cleaned(raw)
        tag = _TABLE.rewrites.get(written, written)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        if (crossing := _TABLE.crossing(tag)) is not None:
            stripped.append(crossing)
            continue
        kept.append(tag)
    quality = QUALITY_TAGS if not words else tuple(t for t in QUALITY_TAGS if t != "no text")
    positive = (
        *_KEEPSAKE_START, *_styled(style), *(kept[:KEEPSAKE_TAGS] or ["gift box"]),
        *((f'"{words}"',) if words else ()), *quality,
    )
    negative = KEEPSAKE_NEGATIVE if not words else KEEPSAKE_NEGATIVE.replace(", text,", ",")
    return ImagePrompt(", ".join(positive), negative, KEEPSAKE_SIZE), tuple(stripped)


_KEEPSAKE_START = ("background dataset", "no humans", "still life", "object focus")
_QUOTED = re.compile(r'["“”「」](.*)["“”「」]')
_WORDS_KEPT = re.compile(r"[^A-Za-z0-9 !?'.&+♥♡-]")
KEEPSAKE_WORDS = 24
"""The most characters of words a keepsake's picture shows written on it."""


def keepsake_words(words: str) -> str:
    """`words` as they may be written on a keepsake's picture: short English, no commas,
    quotes or prompt syntax (V5 draws Latin letters best), `KEEPSAKE_WORDS` at most."""
    return " ".join(_WORDS_KEPT.sub("", words).split())[:KEEPSAKE_WORDS].strip()


def check_prompt(style: str = "") -> ImagePrompt:
    """The prompts a change to an image service is tried with: no one in particular, in the
    art style `style`, otherwise as any photo is."""
    positive = (
        "1girl", "solo", *_styled(style), "upper body", "indoors", "smile", LOOKING_AT_VIEWER,
        *QUALITY_TAGS,
    )
    return ImagePrompt(", ".join(positive), NEGATIVE_PROMPT)


def _styled(style: str) -> tuple[str, ...]:
    """The art style as it is written into a prompt: as it is, or not at all if empty."""
    return (style.strip(),) if style.strip() else ()


def _shot_tags(shot: Shot) -> tuple[str, ...]:
    return ("selfie", LOOKING_AT_VIEWER) if shot == "selfie" else ()


def _field(
    value: str, name: str, seen: set[str], stripped: list[Crossing]
) -> tuple[str, ...]:
    """The tags of one of her fields, cleaned and put as the model knows them, those already
    written elsewhere left out (and added to `seen`), those crossing the line stripped (and
    which part of it added to `stripped`), cut to `FIELD_TAGS`; its default if none are
    left."""
    tags: list[str] = []
    for raw in re.split(r"[,，、]", value):
        written = _cleaned(raw)
        tag = _TABLE.rewrites.get(written, written)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        if (crossing := _TABLE.crossing(tag)) is not None:
            stripped.append(crossing)
            continue
        if tag not in _TABLE.known and len(tags) < FIELD_TAGS:
            log.info("a photo tag the tag table does not know, kept: %r", tag)
        tags.append(tag)
    return tuple(tags[:FIELD_TAGS]) or (DEFAULTS[name],)


def _cleaned(tag: str) -> str:
    """`tag` without NovelAI's prompt syntax, in lower case, a space for each underscore."""
    tag = _TEXT.sub("", tag)
    tag = _WEIGHT.sub("", tag)
    tag = _SYNTAX.sub("", tag)
    return " ".join(tag.replace("_", " ").lower().split())


class _TagTable:
    """The local tag table: the tags the model knows, what she may write for one, what is
    never stripped, and what crosses the line, tag by tag (`tags`) or as a word anywhere in
    one (`words`)."""

    def __init__(self, path: Path) -> None:
        table = json.loads(path.read_text(encoding="utf-8"))
        self.known: frozenset[str] = frozenset(table["known"])
        self.rewrites: dict[str, str] = table["rewrites"]
        self._allowed = frozenset(table["allowed"])
        self._tags: dict[str, Crossing] = {}
        self._words: list[tuple[re.Pattern[str], Crossing]] = []
        for crossing in CROSSINGS:
            blocked = table["blocked"][crossing]
            self._tags.update(dict.fromkeys(blocked["tags"], crossing))
            if words := "|".join(re.escape(w) for w in blocked["words"]):
                pattern = re.compile(rf"(?<![a-z0-9])(?:{words})(?![a-z0-9])")
                self._words.append((pattern, crossing))

    def crossing(self, tag: str) -> Crossing | None:
        """Which part of the line `tag` crosses, if any."""
        if tag in self._allowed:
            return None
        if (crossing := self._tags.get(tag)) is not None:
            return crossing
        return next((c for words, c in self._words if words.search(tag)), None)


_TABLE = _TagTable(Path(__file__).with_name("photo_tags.json"))
_FIXED = frozenset({"1girl", "solo", WORK_TAG, *QUALITY_TAGS})
"""What every positive prompt has already, written by her or not."""
