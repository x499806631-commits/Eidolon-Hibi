"""Importing the story corpus from SCTranslationData CSV files and the game's own scripts.

Each CSV (`id,name,text,trans`, ending in an `info` row naming the game script it
was translated from) becomes one episode keyed by that script. The source
(card or event) is the file's directory and the title its file name, as
SCTranslationData lays them out.

Each JSON file is one game script as the game serves it: an array of frames with
`speaker`, `text`, `select` and more. It is keyed by the script its path names, either
the game's own layout (`produce_events/100100101.json`) or the translation plugin's
name for a download (`produce_events_100100101.json`). The source and title come from
the directory and file name when the file is filed like SCTranslationData; a file kept
in the game's layout has neither, so its card or event is its script number without
the episode (`produce_events/2011005`) and its title the plugin's name. A file whose
path names no script is skipped: it could not be matched with a CSV of the same
script. A caller that knows the card or event and the title (the story sync, from
the mirror's API) passes them with each file instead. Scripts carry no translation.
A script SCTranslationData has translated is kept from the CSV: the JSON import
leaves a CSV version alone, and a CSV import replaces a JSON one.

Both follow the same rules. Only canon is kept: failure endings are recognised by
their script and left out (and removed, if an earlier import had them), and the
producer's choices (`select`) are dropped while every reaction to them is kept, so
the corpus never says which one was picked. An episode imported with a title that
reads like a failure (敗, 失敗) although its script is not a failure ending is reported,
so that the rules can be checked against it: SCTranslationData titles an episode by
its file name, the mirror's sync by the game's title. The cast is every configured character
whose speaker name appears; the producer and other people are not cast. An episode
the corpus already has exactly as imported is left alone.
"""

import csv
import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from eidolon.characters import Character
from eidolon.corpus import CatalogEntry, Episode, ImportFormat, Line, StoryCorpus

log = logging.getLogger(__name__)

_SAVE_BATCH = 32
"""Episodes saved (and embedded) together."""

_LOOKS_LIKE_FAILURE = re.compile(r"敗|失敗|失败|败")
_NOT_FAILURE_WORDS = re.compile(r"成敗")  # punishing the villain, in period-drama events

_JOINT_SPEAKERS = re.compile(r"[＆&]")

# `produce_events/100100101` as the game lays scripts out, or `produce_events_100100101`
# as the translation plugin names a download.
_SCRIPT_PATH = re.compile(r"(?:^|/)(?P<type>[a-z]+(?:_[a-z]+)*)[/_](?P<number>\d{8,})$")

# Script identifiers look like `produce_events/TIIISSSEE`: type T, idol III, scenario
# SSS (001 W.I.N.G., 002 fan appreciation, 003 G.R.A.D., 004 Landing Point, 005
# S.T.E.P.), episode EE. These are the scripts only reached by losing, including
# G.R.A.D.'s repechage, which follows a lost preliminary.
_FAILURE_ENDINGS = [
    re.compile(p)
    for p in (
        r"produce_events/7\d{3}001(02|04|06|08)",  # W.I.N.G. season not cleared
        r"produce_events/6\d{3}001(02|07)",  # W.I.N.G. knocked out, lost the final
        r"produce_events/6\d{3}003(03|04|05|06|09|10)",  # G.R.A.D. lost a round, repechage
        r"produce_events/1\d{3}003(071|072|08)",  # G.R.A.D. elimination route
        r"produce_communication_auditions/\d+2",  # lost an audition
        r"produce_communication_promise_results/\d+2",  # broke a promise
    )
]

_CATEGORIES = [
    (re.compile(r"produce_events/2"), "P卡剧情"),
    (re.compile(r"produce_events/3"), "S卡剧情"),
    (re.compile(r"produce_"), "培育剧情"),
    (re.compile(r"game_event_communications/"), "活动剧情"),
    (re.compile(r"special_communications/"), "特别剧情"),
    (re.compile(r"mypage_communications/"), "主页剧情"),
    (re.compile(r"business_unit_communication/"), "营业剧情"),
]


def is_failure_ending(source_id: str) -> bool:
    return any(p.fullmatch(source_id) for p in _FAILURE_ENDINGS)


def looks_like_failure(title: str) -> bool:
    """Whether a title reads like a failure ending. Only the script tells for sure
    (`is_failure_ending`); an episode titled like one that it does not recognise is
    imported, and listed for the admin to check the rules against."""
    return _LOOKS_LIKE_FAILURE.search(_NOT_FAILURE_WORDS.sub("", title)) is not None


def category_of(source_id: str) -> str:
    return next((name for p, name in _CATEGORIES if p.match(source_id)), "其他剧情")


@dataclass
class ImportReport:
    imported: int = 0
    skipped_failures: int = 0
    removed: int = 0
    """Episodes an earlier import had that are now recognised as failure endings."""
    embedded_passages: int = 0
    csv_versions_kept: int = 0
    """Scripts in a JSON import that were left alone because a CSV of them is in the corpus."""
    unchanged: int = 0
    """Episodes the corpus already had exactly as imported, so were not saved again."""
    added: int = 0
    """Of those imported, the episodes the corpus did not have before."""
    untranslated_replaced: int = 0
    """Of those imported, the untranslated episodes (from JSON) a CSV translation replaced."""
    looks_like_failures: list[CatalogEntry] = field(default_factory=list)
    """Of those added, the episodes titled like a failure ending that are not one by
    their script, to be checked by hand. An episode is listed when it first comes in,
    not again when it is updated (renamed, or replaced by a translation)."""
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScriptFile:
    """A game script JSON whose card or event and title the caller knows."""

    path: Path
    source_id: str
    """The script, e.g. `produce_events/200100401`."""
    source: str
    title: str


@dataclass(frozen=True)
class _Script:
    """One story file as read, before the import rules are applied."""

    relative_path: Path
    source_id: str
    source: str
    title: str
    lines: list[Line]


async def import_csv_directory(
    directory: Path, corpus: StoryCorpus, characters: Iterable[Character]
) -> ImportReport:
    """Import every `*.csv` under `directory` as an episode.

    Of several files translated from the same script, the one with the most lines wins
    (stray partial copies exist), then the first in path order.
    """
    report = ImportReport()
    return await _import(_csv_scripts(directory, report), "csv", corpus, characters, report)


async def import_json_directory(
    directory: Path, corpus: StoryCorpus, characters: Iterable[Character]
) -> ImportReport:
    """Import every `*.json` under `directory` as an episode, unless a CSV of the same
    script is already in the corpus.

    Of several files of the same script, the one with the most lines wins, then the
    first in path order.
    """
    report = ImportReport()
    return await _import(_json_scripts(directory, report), "json", corpus, characters, report)


async def import_script_files(
    files: Iterable[ScriptFile], corpus: StoryCorpus, characters: Iterable[Character]
) -> ImportReport:
    """Import these script JSON files like `import_json_directory` does, under the card
    or event and title given with each."""
    report = ImportReport()
    return await _import(_script_files(files, report), "json", corpus, characters, report)


def _csv_scripts(directory: Path, report: ImportReport) -> Iterator[_Script]:
    for path in sorted(directory.rglob("*.csv")):
        relative = path.relative_to(directory)
        try:
            source_id, lines = _read_csv(path)
        except (csv.Error, UnicodeDecodeError) as e:
            report.warnings.append(f"{relative}：无法读取（{e}），已跳过")
            continue
        if source_id is None:
            source_id = f"csv/{relative.with_suffix('').as_posix()}"
            report.warnings.append(f"{relative}：没有 info 行，以文件路径作为来源标识")
        yield _Script(relative, source_id, relative.parent.name or relative.stem, relative.stem, lines)


def _json_scripts(directory: Path, report: ImportReport) -> Iterator[_Script]:
    for path in sorted(directory.rglob("*.json")):
        relative = path.relative_to(directory)
        try:
            lines = _read_json(path)
        except (ValueError, UnicodeDecodeError) as e:  # JSONDecodeError is a ValueError
            report.warnings.append(f"{relative}：无法读取（{e}），已跳过")
            continue
        if (match := _SCRIPT_PATH.search(relative.with_suffix("").as_posix())) is None:
            report.warnings.append(f"{relative}：路径里没有剧本编号（来源标识），已跳过")
            continue
        kind, number = match["type"], match["number"]
        source_id = f"{kind}/{number}"
        if relative.parent.name == kind or not relative.parent.name:  # the game's layout
            source, title = f"{kind}/{number[:-2]}", f"{kind}_{number}"
        else:
            source, title = relative.parent.name, relative.stem
        yield _Script(relative, source_id, source, title, lines)


def _script_files(files: Iterable[ScriptFile], report: ImportReport) -> Iterator[_Script]:
    for file in files:
        try:
            lines = _read_json(file.path)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            report.warnings.append(f"{file.source_id}：无法读取（{e}），已跳过")
            continue
        yield _Script(Path(file.source_id), file.source_id, file.source, file.title, lines)


async def _import(
    scripts: Iterable[_Script],
    imported_from: ImportFormat,
    corpus: StoryCorpus,
    characters: Iterable[Character],
    report: ImportReport,
) -> ImportReport:
    speakers = _speaker_index(characters)
    episodes: dict[str, Episode] = {}
    previously: dict[str, ImportFormat | None] = {}
    seen: dict[str, tuple[Path, int]] = {}
    for script in scripts:
        relative, source_id, lines = script.relative_path, script.source_id, script.lines
        if source_id in seen:
            kept, kept_lines = seen[source_id]
            if len(lines) <= kept_lines:
                report.warnings.append(f"{relative}：与 {kept} 来源相同（{source_id}），以后者为准")
                continue
            report.warnings.append(f"{kept}：与 {relative} 来源相同（{source_id}），以后者为准")
        seen[source_id] = (relative, len(lines))

        if is_failure_ending(source_id):
            report.skipped_failures += 1
            if await corpus.remove(source_id):
                report.removed += 1
            continue
        if imported_from != "csv" and await corpus.is_translated(source_id):
            report.csv_versions_kept += 1
            continue
        episode = Episode(
            id=source_id,
            category=category_of(source_id),
            source=script.source,
            title=script.title,
            cast=_cast(lines, speakers),
            lines=tuple(lines),
            imported_from=imported_from,
        )
        existing = await corpus.get(source_id)
        if existing == episode:
            episodes.pop(source_id, None)  # a shorter copy seen earlier is not wanted either
            report.unchanged += 1
            continue
        episodes[source_id] = episode
        previously[source_id] = existing.imported_from if existing is not None else None

    pending = list(episodes.values())
    report.looks_like_failures = [
        CatalogEntry(episode.id, episode.title)
        for episode in pending
        if previously[episode.id] is None and looks_like_failure(episode.title)
    ]
    for start in range(0, len(pending), _SAVE_BATCH):
        batch = pending[start : start + _SAVE_BATCH]
        report.embedded_passages += await corpus.save(batch)
        report.imported += len(batch)
        before = [previously[episode.id] for episode in batch]
        report.added += before.count(None)
        if imported_from == "csv":
            report.untranslated_replaced += before.count("json")
        log.info("已保存 %d / %d 段剧情", report.imported, len(pending))
    return report


def _read_csv(path: Path) -> tuple[str | None, list[Line]]:
    """The script the file was translated from, and its lines."""
    source_id = None
    lines: list[Line] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.reader(f):
            row = [cell.strip() for cell in row] + [""] * (4 - len(row))
            line_id, name, text, trans = row[:4]
            if line_id == "info":
                source_id = name.removesuffix(".json") or None
            elif line_id in ("id", "select", "译者") or not text:
                continue  # header, the producer's choices, translator credit
            else:
                lines.append(Line(name or None, _unescape(text), _unescape(trans) or None))
    return source_id, lines


def _read_json(path: Path) -> list[Line]:
    """The lines of a game script. A frame's text is its line (a choice is kept only in
    `select`); frames without text are stage directions."""
    frames = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(frames, list):
        raise ValueError("不是剧本的帧数组")
    lines = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        text, speaker = frame.get("text"), frame.get("speaker")
        if not isinstance(text, str) or not text.strip():
            continue
        speaker = speaker.strip() if isinstance(speaker, str) else ""
        # The game breaks lines with CRLF; SCTranslationData, and so the corpus, with LF.
        lines.append(Line(speaker or None, text.strip().replace("\r\n", "\n")))
    return lines


def _unescape(text: str) -> str:
    return text.replace("\\n", "\n")


def _speaker_index(characters: Iterable[Character]) -> dict[str, str]:
    index = {}
    for character in characters:
        if not character.speakers:
            log.warning("character %s has no speakers configured; she is never cast", character.id)
        for name in character.speakers:
            index[name] = character.id
    return index


def _cast(lines: Iterable[Line], speakers: dict[str, str]) -> frozenset[str]:
    cast = set()
    for line in lines:
        for name in _JOINT_SPEAKERS.split(line.speaker or ""):
            if (character := speakers.get(name.strip())) is not None:
                cast.add(character)
    return frozenset(cast)
