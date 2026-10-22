"""What a photo is generated from (GLOSSARY.md「照片」): the photo tag she wrote, read field by
field, and the prompts the image service is sent for it.

She writes how the photo is taken (`shot`: a selfie, or someone else taking it), how much of
her it shows (`framing`), and three fields of English tags: where and when (`place`), what
she wears (`outfit`), what she does and how she looks (`action`); a short English sentence
on how the picture is laid out (`scene`), which V5 reads better than tags for where things
are; words that are written somewhere in the picture, Japanese or English (`words`), and
where (`words_on`); and a note in Chinese for herself alone, never sent. The tags give the
picture its bones, the sentence its layout, as NovelAI's guide has it. What she writes is cleaned before it goes anywhere: the prompt
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
and all; none if empty), her looks, the tags for how the photo is taken, her sentence and the words in quotes (V5
draws text in double quotes, in Japanese as well as English, put where a sentence says),
`high complexity` (V5's tag for a detailed, finished picture, left out where the art style
says a complexity of its own), `rating:general` and V5's standard quality tags, written out
rather than left to the service's switch, `no text` among them only where no words are. The negative prompt is
fixed altogether: `nsfw`, the Human Focus preset, and what no photo of hers shows
(ADR-0023). Neither the deployer nor any user changes them.
"""

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
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
    # What goes wrong with faces and hands in her photos, as drawing them has shown.
    "white patch on nose", "square highlight on face", "large hands", "thick fingers",
    "signature",
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


COMPLEXITY = "high complexity"
"""V5's tag for a detailed, finished picture, before the quality tags."""

SCENE_CHARS = 200
"""The most characters of her sentence on how the picture is laid out."""
WORDS_CHARS = 12
"""The most characters of the words written in a picture."""
_NOT_IN_WORDS = re.compile(r"[\"“”「」『』{}\[\]|#:`\\<>\n\r\t]")


@dataclass(frozen=True)
class PhotoTag:
    """A photo tag as read: how the photo is taken and how much of her it shows, and her
    three fields of tags, cleaned."""

    shot: Shot
    framing: Framing
    place: tuple[str, ...]
    outfit: tuple[str, ...]
    action: tuple[str, ...]
    others: tuple[str, ...] = ()
    """In a group photo, what the others with her wear and do (`others`), cleaned; none if
    she wrote none."""
    scene: str = ""
    """Her sentence on how it is laid out, cleaned; empty if none, or it crossed the line."""
    writing: str = ""
    """The words written in it and where, as the prompt has them (`writing`); empty if none."""
    stripped: tuple[Crossing, ...] = ()
    """Which part of the line each tag stripped crossed, one for each."""


@dataclass(frozen=True)
class ImagePrompt:
    positive: str
    negative: str
    size: tuple[int, int] | None = None
    """The picture's width and height; None for a photo's, portrait."""
    characters: tuple[str, ...] = ()
    """For a picture with several in it, each one's own prompt, in order (V5's character
    prompts); none for one with one or none, all said in `positive`."""


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
    others = _field(fields.get("others", ""), "others", set(_FIXED), stripped, default=())
    scene = _sentence(fields.get("scene", ""), stripped)
    words = writing(fields.get("words", ""), fields.get("words_on", ""), stripped)
    return PhotoTag(shot, framing, place, outfit, action, others, scene, words, tuple(stripped))


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
        *tag.outfit, *action, *_said(tag.scene, tag.writing), *_finish(style, tag.writing),
    )
    return ImagePrompt(", ".join(positive), _negative(NEGATIVE_PROMPT, style, tag.writing))


GROUP_SIZE = (1216, 832)
"""A group photo's width and height: landscape, to have room for them all."""
MAX_IN_GROUP = 5
"""The most in a group photo, her among them."""
SIDE_BY_SIDE = 3
"""The most in a group photo standing side by side, in their prompts' order, left to right."""
WRITTEN_KEPT_OUT = ("outlined text", "sticker")
"""What words written in a picture are kept from looking like: drawn on, not stuck on."""


def group_prompt(
    characters: Sequence[Character], tag: PhotoTag, style: str = ""
) -> ImagePrompt:
    """The prompts a photo of `characters` together (the first the one who sends it, all with
    their photo tags, `MAX_IN_GROUP` at most) is generated from, as `tag` says it is taken,
    in the art style `style`: what all of them share in the base prompt (how many girls,
    the work, the style, how it is taken, as close as `group_framing` lets it be, where, her
    sentence and the words), and each a
    character prompt of her own (V5's), `girl` and who she is, her looks, then what she
    wears and does: hers from `tag`'s outfit and action, the others' from its `others`, or
    hers where it says nothing. With one, it is `image_prompt`'s."""
    characters = list(characters)[:MAX_IN_GROUP]
    if len(characters) == 1:
        return image_prompt(characters[0], tag, style)
    for c in characters:
        assert c.photo_tag is not None and c.photo_appearance is not None
    action = tag.action
    if tag.shot == "other" and not any(g in t for t in action for g in _GAZES):
        action = (*action, LOOKING_AT_VIEWER)
    n = len(characters)
    base = (
        f"{n}girls", WORK_TAG, *_styled(style), *_shot_tags(tag.shot), group_framing(tag.framing, n),
        *(("side-by-side",) if n <= SIDE_BY_SIDE else ()), *tag.place,
        f"exactly {n} girls in the whole picture",  # each one drawn once, by her own prompt
        *_said(tag.scene, tag.writing), *_finish(style, tag.writing),
    )
    others = tag.others or (*tag.outfit, *tag.action)
    each = [
        ", ".join(("girl", c.photo_tag or "", c.photo_appearance or "", *(
            (*tag.outfit, *action) if i == 0 else others
        )))
        for i, c in enumerate(characters)
    ]
    return ImagePrompt(
        ", ".join(base), _negative(NEGATIVE_PROMPT, style, tag.writing), GROUP_SIZE,
        tuple(each),
    )


def group_framing(framing: Framing, n: int) -> Framing:
    """How much of them a photo of `n` together shows: never so far off that their faces are
    too small to be drawn (a picture `GROUP_SIZE` across): two no further than `cowboy shot`,
    three or more `upper body`."""
    if n >= 3:
        return "upper body" if framing != "portrait" else framing
    return "cowboy shot" if framing == "full body" else framing


WORN_TAGS = 8
"""The most tags an outfit given her (`worn`) keeps."""


def worn(tag: PhotoTag, outfit: str) -> PhotoTag:
    """`tag` with her outfit as `outfit` says (comma-separated tags, as `outfit_tags` reads
    one from her outfit list), cleaned and stripped as hers are, `WORN_TAGS` at most; as it
    was if none are left."""
    stripped = list(tag.stripped)
    seen = {*_FIXED, *_shot_tags(tag.shot), tag.framing, *tag.place, *tag.action}
    tags: list[str] = []
    for raw in re.split(r"[,，、]", outfit):
        written = _cleaned(raw)
        found = _TABLE.rewrites.get(written, written)
        if not found or found in seen:
            continue
        seen.add(found)
        if (crossing := _TABLE.crossing(found)) is not None:
            stripped.append(crossing)
            continue
        tags.append(found)
    if not tags:
        return tag
    return replace(tag, outfit=tuple(tags[:WORN_TAGS]), stripped=tuple(stripped))


def writing(words: str, on: str, stripped: list[Crossing] | None = None) -> str:
    """The words `words` written on `on` (where in the picture, in English), as a prompt has
    them: `the text "…" is written on …`, the words in double quotes, which is how V5 is
    asked to draw text, Japanese or English. The words are cut to `WORDS_CHARS`, without
    quotes, brackets or prompt syntax; where they are, to a short phrase. Empty if there are
    no words, or they or where cross the line (which part added to `stripped`)."""
    words = " ".join(_NOT_IN_WORDS.sub(" ", words).split())[:WORDS_CHARS].strip()
    if not words:
        return ""
    where = _sentence(on, stripped).rstrip(".")[:60].strip() or "it"
    if (crossing := _TABLE.crossing(words.lower())) is not None:
        if stripped is not None:
            stripped.append(crossing)
        return ""
    return f'the text "{words}" is written on {where}'


def _sentence(text: str, stripped: list[Crossing] | None) -> str:
    """`text` as a plain sentence in a prompt: without NovelAI's prompt syntax or commas (which
    would cut it into tags), spaces made single, cut to `SCENE_CHARS`; empty if it crosses
    the line (which part added to `stripped`)."""
    text = _WEIGHT.sub("", _TEXT.sub("", text))
    text = re.sub(r"[{}\[\]|#\"“”`<>]", "", text).replace(",", " ").replace("，", " ")
    text = " ".join(text.split())[:SCENE_CHARS].strip()
    if not text:
        return ""
    if (crossing := _TABLE.crossing(text.lower())) is not None:
        if stripped is not None:
            stripped.append(crossing)
        return ""
    return text.rstrip(".!?。 ")  # a sentence among tags, ended by the comma after it


def _said(scene: str, words: str) -> tuple[str, ...]:
    """Her sentence and the words written, as they come after the tags."""
    return tuple(part for part in (words, scene) if part)


def _finish(style: str, words: str) -> tuple[str, ...]:
    """What a prompt ends with: `COMPLEXITY` unless the art style says one, then the quality
    tags, `no text` among them only if no words are written in the picture."""
    before, after, _ = split_style(style)
    complexity = () if "complexity" in f"{before} {after}".lower() else (COMPLEXITY,)
    quality = tuple(t for t in QUALITY_TAGS if not (words and t == "no text"))
    return (*complexity, *quality, *((after,) if after else ()))


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

    It starts with `background dataset`, NovelAI's tag for a scene with no one in it, then
    what the thing is (her first tag). Her sentence describing it, after `SCENE_MARK` in
    `tags`, follows the tags, as a photo's scene does: V5 draws what a sentence says the
    subject is more surely than it puts tags together. The words written on the thing, if
    any, are in `tags` as `keepsake_words` puts them, and go into the prompt as a photo's
    do (`writing`), with `text` no longer kept out of it."""
    seen = {*_FIXED, *_KEEPSAKE_START}
    stripped: list[Crossing] = []
    kept: list[str] = []
    words = ""
    tags, _, described = tags.partition(SCENE_MARK)
    scene = _sentence(described, stripped) if described else ""
    if found := _KEEPSAKE_WORDS.search(tags):
        words = writing(found.group(1), found.group(2) or "", stripped)
        tags = tags[: found.start()] + tags[found.end() :]
    for raw in re.split(r"[,，、]", tags):
        written = _cleaned(raw)
        tag = _TABLE.rewrites.get(written, written)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        if (crossing := _TABLE.crossing(tag)) is not None:
            stripped.append(crossing)
            continue
        kept.append(tag)
    positive = (
        *_KEEPSAKE_START, *_styled(style), *(kept[:KEEPSAKE_TAGS] or ["gift box"]),
        *_said(scene, words), *_finish(style, words),
    )
    negative = KEEPSAKE_NEGATIVE if not words else KEEPSAKE_NEGATIVE.replace(", text,", ",")
    negative = _negative(negative, style, words)
    return ImagePrompt(", ".join(positive), negative, KEEPSAKE_SIZE), tuple(stripped)


_KEEPSAKE_START = ("background dataset", "no humans", "still life", "object focus")
SCENE_MARK = " || "
"""Between a keepsake's tags and her sentence describing it, as they are kept together."""
_KEEPSAKE_WORDS = re.compile(r'"([^"]*)"(?:\s*@\s*([^,，、]*))?')


def keepsake_words(words: str, on: str) -> str:
    """The words written on a keepsake and where, as they are kept with its tags (`"…" @ …`),
    for `keepsake_prompt` to read back; empty if there are no words."""
    words = " ".join(_NOT_IN_WORDS.sub(" ", words).split())[:WORDS_CHARS].strip()
    where = " ".join(re.sub(r'[,，、"@]', " ", on).split())[:60]
    if not words:
        return ""
    return f'"{words}" @ {where}' if where else f'"{words}"'


def check_prompt(style: str = "") -> ImagePrompt:
    """The prompts a change to an image service is tried with: no one in particular, in the
    art style `style`, otherwise as any photo is."""
    positive = (
        "1girl", "solo", *_styled(style), "upper body", "indoors", "smile", LOOKING_AT_VIEWER,
        *_finish(style, ""),
    )
    return ImagePrompt(", ".join(positive), _negative(NEGATIVE_PROMPT, style))


def _styled(style: str) -> tuple[str, ...]:
    """What of the art style goes where it is written into a prompt: all of it, or what is
    before `{prompt}` (`split_style`); nothing if empty."""
    before, _, _ = split_style(style)
    return (before,) if before else ()


PROMPT_MARK = "{prompt}"
NEGATIVE_MARK = "{negative}"


def split_style(style: str) -> tuple[str, str, str]:
    """The art style as a template: what goes before the picture's own prompt, what after it,
    and what is added to the negative prompt. Without marks it all goes before. `{prompt}`
    marks where the picture's own prompt goes, what follows it going at the very end; what
    follows `{negative}` is added to the negative prompt, after what is always kept out
    (which it cannot take out). Each is written as it is, weights and all, but for the
    commas and spaces at its ends and line breaks made spaces."""
    style = " ".join(style.split())
    positive, _, negative = style.partition(NEGATIVE_MARK)
    before, _, after = positive.partition(PROMPT_MARK)
    return before.strip(" ,"), after.strip(" ,"), negative.strip(" ,")


def _negative(negative: str, style: str, words: str = "") -> str:
    """`negative`, with `WRITTEN_KEPT_OUT` where words are written in the picture, then what
    the art style adds to it."""
    _, _, added = split_style(style)
    kept_out = (negative, *(WRITTEN_KEPT_OUT if words else ()), *((added,) if added else ()))
    return ", ".join(kept_out)


def _shot_tags(shot: Shot) -> tuple[str, ...]:
    return ("selfie", LOOKING_AT_VIEWER) if shot == "selfie" else ()


def _field(
    value: str, name: str, seen: set[str], stripped: list[Crossing],
    default: tuple[str, ...] | None = None,
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
    return tuple(tags[:FIELD_TAGS]) or (default if default is not None else (DEFAULTS[name],))


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
