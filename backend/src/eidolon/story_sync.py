"""Keeping the story corpus in step with the game: `eidolon story sync`.

A sync imports SCTranslationData's translated episodes first, then fills in what they
lack from the ShinyColorsDB mirror (ADR-0004) as untranslated episodes, under the
card's real name and the episode's title as the mirror's API gives them. The import
rules are the usual ones (`story_import`): failure endings are left out, a translated
episode is never replaced by an untranslated one, and a translation that arrives
later replaces the untranslated episode under the same card, since SCTranslationData
names a card's directory the way the game names the card.

Scripts are kept in a cache directory that stays out of the repository (ADR-0003),
by `script_cache`: SCTranslationData's translations and failure endings are never
downloaded, and a script whose file is gone is downloaded again. What the API said
about each card is kept there too, so a card whose data has not changed since the
last sync is not asked about again, only its scripts checked for on disk; a card that
has changed has its scripts asked for conditionally. A script the mirror does not
have is not an error; it is asked for again next time.

Event episodes have no listing: their scripts are numbered `4001` + the event's three
digits + the episode's two (`game_event_communications/400109701`), so they are found
by trying numbers. The events known are those the corpus or the cache has; each one
up to the latest is walked once, episode by episode until the mirror has no more,
which fills in the events and episodes SCTranslationData lacks. The latest event is
walked again every sync, in case the mirror has more of it, and a few numbers after
it are tried for new events, so a sync with nothing new costs a handful of 404s. A
number the mirror had nothing under while a later event existed is taken as skipped
for good, and an event no longer the latest is not asked about again: the game
releases an event's episodes together; its episodes are only checked for on disk.
Episodes already downloaded are not asked for again, since events carry no version to
tell they changed.

Produce episodes (W.I.N.G. and the other produce modes, and the communications
along the way) and special ones come from each idol's album in the game, which
lists them with their script, title and the part of the album they are in. The
albums have not changed since 2024-03, so each is asked for conditionally, on its
ETag, and only one that has changed has its scripts asked about again (they are
checked for on disk every sync); a script it lists that the mirror does not have is
not asked for again until then. An episode is filed under the name SCTranslationData
files the translated episodes of the same part of her album under (`W.I.N.G.編`,
`Morning`), if any, or else under the album's name for that part, so a translation
that arrives later replaces it in the same place; its title is the album's. Produce
episodes imported with a title that reads like a failure ending, although the rules
do not recognise their script as one, are listed for the admin to check.

An event's name comes from the admin's table of event names (`load_event_names`),
read at every import so that a name filled in later reaches the catalog; an event
not in the table takes the name SCTranslationData files its translated episodes
under, if any, and is otherwise 「活动剧情 NNN」, never a made-up name. Its episodes
are 「第N話」.

The mirror being unreachable only costs this sync its untranslated episodes: what was
downloaded before it failed is still imported, and the next sync picks up from there.
"""

import json
import logging
import os
import re
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from eidolon.characters import Character
from eidolon.corpus import CatalogEntry, StoryCorpus
from eidolon.mirror import ALBUM_IDOLS, AlbumEpisode, CardEpisode, Mirror, MirrorError
from eidolon.script_cache import Fetched, ScriptCache
from eidolon.story_import import (
    ImportReport,
    ScriptFile,
    category_of,
    import_csv_directory,
    import_script_files,
)

log = logging.getLogger(__name__)

_CARDS_FILE = "cards.json"
_EVENTS_FILE = "events.json"
_ALBUMS_FILE = "albums.json"
_SCRIPTS_DIR = "scripts"

_EVENT_SCRIPTS = "game_event_communications"
_EVENT_SCRIPT = re.compile(_EVENT_SCRIPTS + r"/4001(?P<event>\d{3})(?P<episode>\d{2})")
PROBE_AHEAD = 3
"""How many event numbers after the latest known are tried, in case some are skipped."""
_EPISODES_AT_MOST = 99
"""Two digits of episode; events have about eight."""


class EventNamesError(RuntimeError):
    """The table of event names could not be read."""


def load_event_names(path: Path, *, required: bool = False) -> dict[int, str]:
    """The admin's table of event names: `[events]` with one `NNN = "name"` per event.

    A table that does not exist is empty, unless it is `required`."""
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if required:
            raise EventNamesError(f"找不到活动名对照表 {path}") from None
        return {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise EventNamesError(f"活动名对照表 {path} 格式有误：{e}") from None
    events = config.get("events", {})
    if not isinstance(events, dict):
        raise EventNamesError(f"活动名对照表 {path} 里的 events 需要是一张表")
    names: dict[int, str] = {}
    for key, name in events.items():
        if not (key.isascii() and key.isdigit() and len(key) <= 3 and int(key) > 0):
            raise EventNamesError(f"活动名对照表 {path}：{key} 不是 001–999 的活动编号")
        if not isinstance(name, str) or not name.strip():
            raise EventNamesError(f"活动名对照表 {path}：活动 {key} 的名称需要是非空字符串")
        names[int(key)] = name.strip()
    return names


def event_script(event: int, episode: int) -> str:
    """The script of an event's episode, e.g. `game_event_communications/400109701`."""
    return f"{_EVENT_SCRIPTS}/4001{event:03d}{episode:02d}"


def _event_of(script: str) -> tuple[int, int] | None:
    """The event and episode of an event script, if it is one."""
    match = _EVENT_SCRIPT.fullmatch(script)
    return (int(match["event"]), int(match["episode"])) if match else None


@dataclass
class SyncReport:
    translated: ImportReport
    """The SCTranslationData import."""
    untranslated: ImportReport = field(default_factory=ImportReport)
    """The import of the mirror's scripts."""
    translations_kept: int = 0
    """Episodes on the cards and in the albums asked about this sync that
    SCTranslationData has translated, so were not downloaded."""
    downloaded: int = 0
    missing: int = 0
    """Scripts a card lists that the mirror does not have, asked for this sync."""
    album_missing: int = 0
    """Scripts an album asked about this sync lists that the mirror does not have."""
    album_failures: int = 0
    """Failure endings in the albums asked about this sync, which are not downloaded."""
    unnamed_events: list[int] = field(default_factory=list)
    """Events with untranslated episodes that neither the table of event names nor
    SCTranslationData names."""
    mirror_error: str | None = None
    """Why the mirror could not be synced from, if it could not."""

    @property
    def embedded_passages(self) -> int:
        return self.translated.embedded_passages + self.untranslated.embedded_passages

    @property
    def skipped_failures(self) -> int:
        return (
            self.translated.skipped_failures + self.untranslated.skipped_failures + self.album_failures
        )

    @property
    def looks_like_failures(self) -> list[CatalogEntry]:
        """The produce episodes imported this sync that are titled like a failure ending
        but not recognised as one by their script, for the admin to check."""
        return [
            entry
            for entry in [*self.translated.looks_like_failures, *self.untranslated.looks_like_failures]
            if category_of(entry.episode_id) == "培育剧情"
        ]


@dataclass
class _CardState:
    name: str
    version: str
    """The card's version when it was last asked about, or an earlier one (or "") if
    the sync stopped before its scripts were all asked for."""
    episodes: list[CardEpisode]


@dataclass
class _AlbumState:
    etag: str | None
    """The album's ETag, once every script it lists has been asked for."""
    episodes: list[AlbumEpisode]


class _Cache:
    """What the mirror said about each card, the events walked and the albums read,
    kept in the cache directory beside the scripts."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self.cards: dict[str, _CardState] = {}
        path = directory / _CARDS_FILE
        if path.exists():
            for uuid, card in json.loads(path.read_text(encoding="utf-8")).items():
                self.cards[uuid] = _CardState(
                    card["name"],
                    card["version"],
                    [CardEpisode(**episode) for episode in card["episodes"]],
                )
        self.events: dict[int, int] = {}
        """Each event walked to its end, and how many episodes it has."""
        path = directory / _EVENTS_FILE
        if path.exists():
            self.events = {
                int(event): episodes
                for event, episodes in json.loads(path.read_text(encoding="utf-8")).items()
            }
        self.albums: dict[int, _AlbumState] = {}
        path = directory / _ALBUMS_FILE
        if path.exists():
            self.albums = {
                int(idol): _AlbumState(
                    album["etag"], [AlbumEpisode(**episode) for episode in album["episodes"]]
                )
                for idol, album in json.loads(path.read_text(encoding="utf-8")).items()
            }

    def save(self) -> None:
        self._write(_CARDS_FILE, {uuid: asdict(card) for uuid, card in self.cards.items()})

    def save_events(self) -> None:
        self._write(_EVENTS_FILE, {f"{event:03d}": n for event, n in sorted(self.events.items())})

    def save_albums(self) -> None:
        self._write(
            _ALBUMS_FILE, {f"{idol:02d}": asdict(album) for idol, album in sorted(self.albums.items())}
        )

    def _write(self, name: str, data: object) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / name
        partial = path.with_name(path.name + ".part")
        partial.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, path)


async def sync_stories(
    story_directory: Path,
    cache_directory: Path,
    corpus: StoryCorpus,
    characters: Iterable[Character],
    mirror: Mirror,
    *,
    event_names: Mapping[int, str] | None = None,
    idols: Sequence[int] | None = None,
) -> SyncReport:
    """Import SCTranslationData's CSVs from `story_directory`, then the mirror's card,
    album and event episodes the corpus lacks, events named from `event_names`. `idols`
    limits the mirror to those idols' cards and albums, and leaves events out."""
    characters = list(characters)
    report = SyncReport(await import_csv_directory(story_directory, corpus, characters))
    cache = _Cache(cache_directory)
    scripts = ScriptCache(cache_directory / _SCRIPTS_DIR, mirror, corpus)
    errors = []
    try:
        await _fetch_cards(mirror, scripts, cache, report, idols)
    except MirrorError as e:
        errors.append(str(e))
    try:  # the albums are elsewhere
        await _fetch_albums(mirror, scripts, cache, report, idols)
    except MirrorError as e:
        errors.append(str(e))
    if idols is None:
        try:  # the viewer may well be up when the API is not
            await _fetch_events(scripts, cache, corpus, report)
        except MirrorError as e:
            errors.append(str(e))
    report.mirror_error = "；".join(errors) or None
    names = event_names or {}
    event_files, report.unnamed_events = await _untranslated_event_scripts(scripts, corpus, names)
    files = {
        file.source_id: file
        for file in [
            *event_files,
            *await _untranslated_album_scripts(scripts, cache, corpus),
            *await _untranslated_scripts(scripts, cache, corpus),
        ]
    }  # a script both a card and an album list is the card's
    report.untranslated = await import_script_files(
        sorted(files.values(), key=lambda file: file.source_id), corpus, characters
    )
    return report


async def _fetch_cards(
    mirror: Mirror,
    scripts: ScriptCache,
    cache: _Cache,
    report: SyncReport,
    idols: Sequence[int] | None,
) -> None:
    seen: set[str] = set()  # a unit's card is listed under each of its idols
    for idol in idols if idols is not None else await mirror.idols():
        for card in await mirror.cards(idol):
            if card.uuid in seen:
                continue
            seen.add(card.uuid)
            known = cache.cards.get(card.uuid)
            changed = known is None or known.version != card.version
            if known is not None and not changed:
                # Only a script deleted from the cache, or not yet on the mirror, is asked for.
                for episode in known.episodes:
                    _count_card_script(await scripts.fetch(episode.script), episode, report)
                continue
            episodes = await mirror.episodes(card)
            # Its episodes are known now, so that what is downloaded is imported even if the
            # mirror fails before the card is done; until it is, it still counts as changed.
            unfinished = known.version if known is not None else ""
            cache.cards[card.uuid] = _CardState(card.name, unfinished, episodes)
            for episode in episodes:
                fetched = await scripts.fetch(episode.script, recheck=True)
                if fetched == "translated":
                    report.translations_kept += 1
                _count_card_script(fetched, episode, report)
            cache.cards[card.uuid] = _CardState(card.name, card.version, episodes)
            if episodes:
                log.info("%s：%d 段剧情", card.name, len(episodes))
            cache.save()  # so an interrupted sync picks up after the last card done


def _count_card_script(fetched: Fetched, episode: CardEpisode, report: SyncReport) -> None:
    if fetched == "downloaded":
        report.downloaded += 1
    elif fetched == "missing":
        report.missing += 1
        log.info("镜像上还没有 %s", episode.script)


async def _fetch_albums(
    mirror: Mirror,
    scripts: ScriptCache,
    cache: _Cache,
    report: SyncReport,
    idols: Sequence[int] | None,
) -> None:
    seen: set[str] = set()  # a unit's episodes are in each of its idols' albums
    for idol in ALBUM_IDOLS if idols is None else [idol for idol in idols if idol in ALBUM_IDOLS]:
        known = cache.albums.get(idol)
        episodes, etag = await mirror.album(idol, known.etag if known is not None else None)
        changed = episodes is not None
        if episodes is None:  # not changed since every script it lists was asked for
            assert known is not None  # asked for on its ETag, so known
            episodes = known.episodes
        else:
            # Its episodes are known now, so that what is downloaded is imported even if the
            # mirror fails before the album is done; until it is, it has no ETag.
            cache.albums[idol] = _AlbumState(None, episodes)
        for episode in episodes:
            if episode.script in seen:
                continue
            seen.add(episode.script)
            # Of an unchanged album, only a script deleted from the cache (or its mark that
            # the mirror does not have it) is asked for.
            fetched = await scripts.fetch(episode.script, retry_missing=changed)
            if fetched == "downloaded":
                report.downloaded += 1
            elif fetched == "missing":
                report.album_missing += 1
                log.info("镜像上没有 %s", episode.script)
            elif changed and fetched == "failure":
                report.album_failures += 1  # never imported, so not downloaded
            elif changed and fetched == "translated":
                report.translations_kept += 1
        if changed:
            cache.albums[idol] = _AlbumState(etag, episodes)
            if episodes:
                log.info("相册 %02d：%d 段剧情", idol, len(episodes))
            cache.save_albums()  # so an interrupted sync picks up after the last album done


async def _fetch_events(
    scripts: ScriptCache,
    cache: _Cache,
    corpus: StoryCorpus,
    report: SyncReport,
) -> None:
    known = {
        found[0]
        for episode_id in await corpus.episode_ids(f"{_EVENT_SCRIPTS}/")
        if (found := _event_of(episode_id))
    }
    known.update(event for event, episodes in cache.events.items() if episodes)
    latest = max(known, default=0)
    for event in range(1, latest + 1):
        if event in cache.events and event < latest:
            for episode in range(1, cache.events[event] + 1):  # only a script deleted is asked for
                if await scripts.fetch(event_script(event, episode)) == "downloaded":
                    report.downloaded += 1
            continue
        cache.events[event] = await _walk_event(scripts, report, event)
        cache.save_events()
    # Events after the latest: numbers are sometimes skipped, so a few are tried.
    tried: list[int] = []
    event = latest + 1
    while len(tried) < PROBE_AHEAD:
        episodes = await _walk_event(scripts, report, event)
        if episodes:
            log.info("镜像上有新活动 %03d：%d 话", event, episodes)
            cache.events.update({skipped: 0 for skipped in tried})
            cache.events[event] = episodes
            cache.save_events()
            tried = []
        else:
            tried.append(event)
        event += 1


async def _walk_event(scripts: ScriptCache, report: SyncReport, event: int) -> int:
    """Fetch the event's episodes from the first until the mirror has no more; returns
    how many it has."""
    episode = 1
    while episode <= _EPISODES_AT_MOST:
        fetched = await scripts.fetch(event_script(event, episode))
        if fetched == "missing":
            break
        if fetched == "downloaded":
            report.downloaded += 1
        episode += 1
    return episode - 1


async def _untranslated_event_scripts(
    scripts: ScriptCache, corpus: StoryCorpus, names: Mapping[int, str]
) -> tuple[list[ScriptFile], list[int]]:
    """Every event script kept so far that SCTranslationData has not translated, and the
    events of those that go unnamed.

    An event is named from the table as it is now; one the table lacks takes the name
    SCTranslationData files its translated episodes under, if it has any, so that the
    event stays together in the catalog."""
    files = []
    unnamed: set[int] = set()
    for script, path in scripts.kept(_EVENT_SCRIPTS).items():
        if (found := _event_of(script)) is None or await corpus.is_translated(script):
            continue
        event, episode = found
        name = names.get(event) or await _translated_event_name(corpus, event)
        if name is None:
            unnamed.add(event)
        source = name or f"活动剧情 {event:03d}"
        files.append(ScriptFile(path, script, source, f"第{episode}話"))
    return files, sorted(unnamed)


async def _translated_event_name(corpus: StoryCorpus, event: int) -> str | None:
    for episode_id in await corpus.episode_ids(f"{_EVENT_SCRIPTS}/4001{event:03d}"):
        if await corpus.is_translated(episode_id) and (episode := await corpus.get(episode_id)):
            return episode.source
    return None


async def _untranslated_album_scripts(
    scripts: ScriptCache, cache: _Cache, corpus: StoryCorpus
) -> list[ScriptFile]:
    """Every album script kept so far that SCTranslationData has not translated, filed
    under the name SCTranslationData files that part of the album under, if it has
    translated any of it, or else the album's."""
    files: dict[str, ScriptFile] = {}
    for _, album in sorted(cache.albums.items()):
        names: dict[str, str] = {}
        for episode in album.episodes:
            if episode.script in files or await corpus.is_translated(episode.script):
                continue
            if (path := scripts.path(episode.script)) is None:
                continue
            if episode.section not in names:
                names[episode.section] = (
                    await _translated_section_name(corpus, album, episode.section)
                    or episode.section
                )
            files[episode.script] = ScriptFile(
                path, episode.script, names[episode.section], episode.title
            )
    return list(files.values())


async def _translated_section_name(
    corpus: StoryCorpus, album: _AlbumState, section: str
) -> str | None:
    """The name SCTranslationData files most of its translations of that part of the
    album under."""
    names = Counter(
        translated.source
        for translated in await corpus.episodes(
            episode.script for episode in album.episodes if episode.section == section
        )
        if translated.translated
    )
    return names.most_common(1)[0][0] if names else None


async def _untranslated_scripts(
    scripts: ScriptCache, cache: _Cache, corpus: StoryCorpus
) -> list[ScriptFile]:
    """Every card script kept so far that SCTranslationData has not translated."""
    files: dict[str, ScriptFile] = {}
    for card in cache.cards.values():
        for episode in card.episodes:
            if episode.script in files or await corpus.is_translated(episode.script):
                continue
            if (path := scripts.path(episode.script)) is not None:
                files[episode.script] = ScriptFile(path, episode.script, card.name, episode.title)
    return sorted(files.values(), key=lambda file: file.source_id)
