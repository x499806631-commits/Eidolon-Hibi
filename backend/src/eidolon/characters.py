"""Characters available for conversation, loaded from the character configuration.

The configuration is a TOML file listing each character's identifier, display
name, introduction, voice, image colour and the location of her character sheet
(and, optionally, her character facts, avatars and call background), relative to
the file, plus the speaker names her lines carry in the story corpus. Adding a
character or replacing a voice is a configuration change. The sheets, facts,
avatars and backgrounds are committed with the configuration (ADR-0003).

Optionally, she also names her idol in the original (`idol_id`, as in the Spine
mirror's `spine_manifest.json`) and the outfit her stand wears unless the user picks
another (`default_outfit`, e.g. `stand/1030010010`). The stands themselves are not
committed: they are served from object storage (ADR-0003, revised 2026-09-30).

Optionally, she also has the other names the conversation list searches her by
(`name_zh`, `name_kana`, `name_romaji`): her name as Chinese readers know it, its reading
in hiragana and in romaji, given name after family name with a space between. Without
them she is found by her name alone.

Optionally, she also has a greeting for the landing page (GLOSSARY.md「招呼」): what she
says in Japanese, delivery cues and all (`greeting`), and its Chinese subtitle
(`greeting_subtitle`), the one with the other. The words are committed; the audio
`eidolon greeting upload` voices from them is not, and goes to the stands' bucket
(ADR-0003, revised 2026-10-10). Which of the readings in the readings file beside the
configuration (`readings`) voicing it uses is read along with it: they are part of where
its audio is.

Optionally too, she has a call opener (GLOSSARY.md「开场白」), what she says first once the
visitor answers her call in the landing page's demo: `call_opener` and
`call_opener_subtitle`, configured, voiced and put in the bucket as greetings are, at
addresses of their own. Only she who calls there needs one.

Optionally, she has what her photos are prompted with (GLOSSARY.md「照片」): her character tag
as Danbooru writes it (`photo_tag`, e.g. `sakuragi mano`), kept apart rather than made from
her romaji, which is not how Danbooru writes every name; and her looks as tags
(`photo_appearance`): hair colour, hairstyle, eye colour, no accessories nor clothes. She
sends no photos without both.

She likes one to three gifts in the gift catalog (`liked_gifts`, by gift identifier),
each with her sources behind it in her facts' sources file (「喜欢的礼物」). Whether they
are in the catalog is checked once both are loaded (`gift_catalog.check_liked_gifts`).

Character facts are a Markdown file; each section under a heading is one fact,
retrieved on its own when a turn calls for it.

A sheet drafted by `eidolon character draft-sheet` starts with `DRAFT_MARKER` until an
administrator has reviewed it and removed the line; the marker never reaches the prompt.
"""

import logging
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from eidolon.delivery_cues import voiced, without_cues
from eidolon.readings import Reading, load_readings, readings_path, used_in

if TYPE_CHECKING:  # her heart is read beside the configuration, once she is (`hearts`)
    from eidolon.hearts import Heart

log = logging.getLogger(__name__)

_VALID_ID = re.compile(r"[a-z][a-z0-9_-]*")
_REQUIRED = ("id", "name", "intro", "voice_id", "sheet", "color")
_IMAGE_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_OUTFIT = re.compile(r"(stand|stand_costume)/[0-9]{10}")
"""An outfit: her everyday clothes on a card (`stand`) or its stage costume
(`stand_costume`), and the card's ten-digit enzaId."""

DRAFT_MARKER = "<!-- 人设草稿：由后台模型根据剧情生成，尚未校对。校对完成后删除这一行。 -->"


class CharacterConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fact:
    """One entry of a character's facts: the section under a heading."""

    title: str | None
    """The headings above it, e.g. `人物关系 / 風野灯織`; None for text before any heading."""
    text: str
    """The entry as it is retrieved, headed by its title."""


@dataclass(frozen=True)
class ScriptedLine:
    """A line she says on the landing page: written beforehand and reviewed, voiced once in
    her voice; never part of any turn."""

    text: str
    """In Japanese as it is voiced, delivery cues and all (`delivery_cues`)."""
    subtitle: str
    """Its Chinese subtitle."""
    readings: tuple[Reading, ...] = ()
    """The readings Fish Audio uses voicing it (`readings.used_in`), from the readings file
    beside the configuration."""

    @property
    def ja(self) -> str:
        """In Japanese as people see it, without its delivery cues."""
        return without_cues(self.text)


@dataclass(frozen=True)
class Greeting(ScriptedLine):
    """What she says to a producer she meets for the first time, on the landing page."""


@dataclass(frozen=True)
class CallOpener(ScriptedLine):
    """What she says first once the visitor answers her call, in the landing page's demo: to
    a producer she already knows, calling him."""


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    intro: str
    voice_id: str
    sheet: str
    """Empty when the sheet file does not exist yet (only when loaded without requiring it)."""
    avatar: Path | None
    color: str
    """Her image colour, the official one from the original, as lower-case `#rrggbb`."""
    background: Path | None = None
    """The picture behind her call screen; None shows a gradient of her image colour."""
    scsp_avatar: Path | None = None
    """Her avatar in SCSP's set, which users can switch to in settings; None, and they see
    her usual avatar either way."""
    speakers: tuple[str, ...] = ()
    """Names her lines are credited to in story scripts, e.g. 真乃."""
    facts: tuple[Fact, ...] = ()
    """Her character facts, one entry per section of her facts file."""
    sheet_path: Path | None = None
    idol_id: int | None = None
    """Who she is in the original's Spine mirror (`idolId` in `spine_manifest.json`)."""
    default_outfit: str | None = None
    """The outfit her stand wears unless the user picks another, as `<category>/<enzaId>`:
    `stand/1030010010` is her everyday clothes on that card, `stand_costume/...` its stage
    costume. Its files are at `spine/idols/<category>/<enzaId>/` under the stand assets."""
    greeting: Greeting | None = None
    """Her greeting on the landing page; None shows her there without one."""
    call_opener: CallOpener | None = None
    """Her call opener on the landing page; None, and her call there has no words."""
    name_zh: str | None = None
    """Her name in simplified Chinese as the community writes it, e.g. 八宫巡."""
    name_kana: str | None = None
    """Her full name in hiragana, family name and given name a space apart, e.g. さくらぎ まの."""
    name_romaji: str | None = None
    """Her full name in romaji, family name first, a space apart, e.g. Sakuragi Mano."""
    liked_gifts: tuple[str, ...] = ()
    """The gifts she likes, by identifier in the gift catalog; giving her one raises
    affinity more. Never told to the user."""
    photo_tag: str | None = None
    """Her character tag as Danbooru writes it, e.g. `sakuragi mano`, for her photos."""
    photo_appearance: str | None = None
    """Her looks as tags, e.g. `brown hair, medium hair, brown eyes`, for her photos."""
    heart: "Heart | None" = None
    """What makes her happy and hurts her, and how she shows each tier of affinity
    (GLOSSARY.md「她的心」); None goes by the generic wording."""

    @property
    def photographed(self) -> bool:
        """Whether she can send photos: she has both her character tag and her looks."""
        return self.photo_tag is not None and self.photo_appearance is not None


def load_characters(path: Path, require_sheets: bool = True) -> dict[str, Character]:
    """Read the character configuration at `path`, keyed by identifier in file order.

    Without `require_sheets`, a character whose sheet file is missing is loaded with an
    empty sheet, as when drafting it."""
    path = path.resolve()
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CharacterConfigError(f"找不到角色配置文件 {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise CharacterConfigError(f"角色配置文件 {path} 格式有误：{e}") from None

    readings = load_readings(readings_path(path))
    characters: dict[str, Character] = {}
    for entry in config.get("characters", []):
        character = _parse_character(entry, path.parent, require_sheets, readings)
        if character.id in characters:
            raise CharacterConfigError(f"角色标识 {character.id} 重复")
        for other in characters.values():
            if shared := set(character.speakers) & set(other.speakers):
                raise CharacterConfigError(
                    f"角色 {other.id} 和 {character.id} 的 speakers 都有 {'、'.join(sorted(shared))}"
                )
        characters[character.id] = character
    if not characters:
        raise CharacterConfigError(f"角色配置文件 {path} 里没有任何角色")
    return characters


def in_idol_order(characters: Iterable[Character]) -> list[Character]:
    """The characters in the order of their idol numbers in the original, those without one
    after them as given."""
    return sorted(characters, key=lambda c: (c.idol_id is None, c.idol_id or 0))


def _parse_character(
    entry: dict, base: Path, require_sheet: bool, readings: tuple[Reading, ...]
) -> Character:
    label = entry.get("id", "（没有标识）")
    missing = [field for field in _REQUIRED if not isinstance(entry.get(field), str)]
    if missing:
        raise CharacterConfigError(f"角色 {label} 缺少字段 {'、'.join(missing)}")
    if not _VALID_ID.fullmatch(entry["id"]):
        raise CharacterConfigError(f"角色标识 {label} 只能包含小写字母、数字、- 和 _")
    if not _IMAGE_COLOR.fullmatch(entry["color"]):
        raise CharacterConfigError(
            f"角色 {label} 的 color 应为 #rrggbb 格式的印象色，现在是 {entry['color']!r}"
        )

    sheet_path = base / entry["sheet"]
    try:
        sheet = sheet_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        if require_sheet:
            raise CharacterConfigError(
                f"角色 {label} 的人设文件 {sheet_path} 不存在"
                "（可参考 sheets/example.md，或用 eidolon character draft-sheet 生成初稿）"
            ) from None
        sheet = ""
    if is_draft(sheet):
        log.warning("sheet %s of character %s is an unreviewed draft", sheet_path, label)
        sheet = sheet.replace(DRAFT_MARKER, "", 1).strip()

    facts: tuple[Fact, ...] = ()
    if isinstance(entry.get("facts"), str):
        facts_path = base / entry["facts"]
        try:
            facts = parse_facts(facts_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("facts %s of character %s are missing; retrieving none", facts_path, label)

    speakers = entry.get("speakers", [])
    if not isinstance(speakers, list) or not all(isinstance(s, str) and s for s in speakers):
        raise CharacterConfigError(f"角色 {label} 的 speakers 应为说话人名字的列表")

    liked_gifts = entry.get("liked_gifts", [])
    if not isinstance(liked_gifts, list) or not all(isinstance(g, str) for g in liked_gifts):
        raise CharacterConfigError(f"角色 {label} 的 liked_gifts 应为礼物标识的列表")

    avatar = _optional_file(entry, "avatar", base, label)
    background = _optional_file(entry, "background", base, label)
    scsp_avatar = _optional_file(entry, "scsp_avatar", base, label)

    idol_id = entry.get("idol_id")
    # bool is an int in Python, but `idol_id = true` is a mistake all the same.
    if idol_id is not None and (type(idol_id) is not int or idol_id <= 0):
        raise CharacterConfigError(
            f"角色 {label} 的 idol_id 应为原作的偶像编号（spine_manifest.json 的 idolId，正整数），"
            f"现在是 {idol_id!r}"
        )
    default_outfit = entry.get("default_outfit")
    if default_outfit is not None and not (
        isinstance(default_outfit, str) and _OUTFIT.fullmatch(default_outfit)
    ):
        raise CharacterConfigError(
            f"角色 {label} 的 default_outfit 应写成 stand/<enzaId> 或 stand_costume/<enzaId>"
            f"（enzaId 是十位数字），现在是 {default_outfit!r}"
        )

    return Character(
        id=entry["id"],
        name=entry["name"],
        intro=entry["intro"],
        voice_id=entry["voice_id"],
        sheet=sheet,
        avatar=avatar,
        color=entry["color"].lower(),
        background=background,
        scsp_avatar=scsp_avatar,
        speakers=tuple(speakers),
        facts=facts,
        sheet_path=sheet_path,
        idol_id=idol_id,
        default_outfit=default_outfit,
        greeting=_greeting(entry, label, readings),
        call_opener=_call_opener(entry, label, readings),
        name_zh=_other_name(entry, "name_zh", label),
        name_kana=_other_name(entry, "name_kana", label),
        name_romaji=_other_name(entry, "name_romaji", label),
        liked_gifts=tuple(liked_gifts),
        photo_tag=_other_name(entry, "photo_tag", label),
        photo_appearance=_other_name(entry, "photo_appearance", label),
    )


def _other_name(entry: dict, field: str, label: str) -> str | None:
    """One of the other names the conversation list searches her by, or what her photos are
    prompted with, if given: a line of text."""
    value = entry.get(field)
    if value is None:
        return None
    if not (isinstance(value, str) and value.strip()) or len(value.splitlines()) > 1:
        raise CharacterConfigError(f"角色 {label} 的 {field} 应为不空的单行字符串，现在是 {value!r}")
    return value.strip()


def _greeting(entry: dict, label: str, readings: tuple[Reading, ...]) -> Greeting | None:
    """Her greeting, if she has one."""
    return _scripted(Greeting, "招呼", "greeting", entry, label, readings)


def _call_opener(entry: dict, label: str, readings: tuple[Reading, ...]) -> CallOpener | None:
    """Her call opener, if she has one."""
    return _scripted(CallOpener, "开场白", "call_opener", entry, label, readings)


L = TypeVar("L", bound=ScriptedLine)


def _scripted(
    kind: type[L], called: str, field: str, entry: dict, label: str, readings: tuple[Reading, ...]
) -> L | None:
    """A line she says on the landing page (`kind`, which the deployer knows as `called`),
    if she has one: its words (`field`) and its subtitle (`<field>_subtitle`), both or
    neither; and the `readings` voicing it uses."""
    subtitle_field = f"{field}_subtitle"
    text, subtitle = entry.get(field), entry.get(subtitle_field)
    if text is None and subtitle is None:
        return None
    if not (isinstance(text, str) and text.strip()):
        raise CharacterConfigError(
            f"角色 {label} 的{called}缺少日语原文：{field} 应为她说的日语（可以带语气提示），和 {subtitle_field} 一起配"
        )
    if not without_cues(text):
        raise CharacterConfigError(f"角色 {label} 的{called} {field} 去掉语气提示后什么都不剩，现在是 {text!r}")
    if not (isinstance(subtitle, str) and subtitle.strip()):
        raise CharacterConfigError(
            f"角色 {label} 的{called}缺少中文字幕：{subtitle_field} 应为 {field} 的中文字幕，和它一起配"
        )
    text = text.strip()
    return kind(text, subtitle.strip(), used_in(voiced(text), readings))


def _optional_file(entry: dict, field: str, base: Path, label: str) -> Path | None:
    """The file an optional field names, relative to the configuration; None when the
    field is absent or the file is missing (with a warning, so she shows without it)."""
    if not isinstance(entry.get(field), str):
        return None
    path = base / entry[field]
    if not path.is_file():
        log.warning("%s %s of character %s is missing; showing none", field, path, label)
        return None
    return path


def is_draft(sheet: str) -> bool:
    """Whether a sheet is a draft nobody has reviewed yet."""
    return sheet.lstrip().startswith(DRAFT_MARKER)


_HEADING = re.compile(r"(#{1,6})\s+(.+?)\s*#*\s*")


def parse_facts(markdown: str) -> tuple[Fact, ...]:
    """Split a facts file into entries: the text under each heading, titled with the
    headings above it (e.g. `人物关系 / 風野灯織`). Headings with nothing under them
    make no entry."""
    facts: list[Fact] = []
    titles: list[str] = []
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            title = " / ".join(titles) or None
            facts.append(Fact(title, f"{title}\n{text}" if title else text))
        body.clear()

    for line in markdown.splitlines():
        if heading := _HEADING.fullmatch(line):
            flush()
            del titles[len(heading.group(1)) - 1 :]
            titles.append(heading.group(2))
        else:
            body.append(line)
    flush()
    return tuple(facts)
