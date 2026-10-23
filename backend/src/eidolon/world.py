"""The world setting: the facts of the original that belong to no one character, from the
data file `eidolon world sync` writes (`world_sync`) beside the character configuration.

The file holds the date the data runs to, the office's president and clerk, the idols
(with their CV, which is only for telling which lives an idol stood on and for people
checking the data: it never reaches a prompt, ADR-0008), the eight units, the
releases and their songs, and the lives with who stood on each day and what was performed
on it (its setlist, covers of other brands' songs included, which are none of the
songs), each entry with the URLs it came from. Every character is one
of its idols or of the office's staff, matched by display name; without the file,
characters go without the world setting.

Every turn carries the world setting in full, after the character sheet
(`World.prompt`), with the rules for facts of the original that the data does not have,
and then, for an idol, her songs: those she sings alone and those her whole unit sings,
no one else joining in, each with the releases it is on and when they came or come out,
which goes by the day of the turn, not by the day the data runs to; and her lives: the
days of each live she stood on or is to stand on, those her CV was absent from left out.
One of the staff (the clerk, 七草はづき) has neither: she does not sing or stand on stage.

Every song, release and live day is also one of the work facts all characters share
(`World.entries`, `work_facts`), which retrieval searches along with the story and the
character's facts. The setlists are only there: every turn carrying them would be far
too long.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from eidolon.characters import Character

WORLD_FILE = "world.json"
"""The data file's name, in the directory of the character configuration."""


class WorldError(RuntimeError):
    """The data file cannot be read, or a character is neither one of its idols nor one of
    the office's staff."""


class WorldSyncError(RuntimeError):
    """A source `eidolon world sync` imports from could not be reached, or answered with
    something unexpected."""


@dataclass(frozen=True)
class Staff:
    name: str
    reading: str
    position: str
    """社長, 事務員."""
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Idol:
    name: str
    """Her full name in Japanese, without a space, e.g. 櫻木真乃."""
    reading: str
    unit: str | None
    """The name of her unit; None only if the sources could not agree on one."""
    cv: str | None
    """Her CV: never in a prompt."""
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Unit:
    name: str
    reading: str | None
    members: tuple[str, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Track:
    title: str
    """As the release lists it."""
    song: str | None
    """The id of the song it is; None for a track that is no song (an off-vocal
    version, a drama) or whose singers could not be told."""


@dataclass(frozen=True)
class Release:
    catalog: str
    """The catalog number, as the label writes it (LACA-9946～7): the release's id."""
    title: str
    date: date
    tracks: tuple[Track, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Song:
    id: str
    title: str
    singers: tuple[str, ...]
    """The idols who sing it, by name; a special unit's song is split into its idols."""
    special_unit: str | None
    """The special unit that sang it (Team.Stella and the like), which is no unit."""
    releases: tuple[str, ...]
    """The catalog numbers of the releases it is on, earliest first."""
    sources: tuple[str, ...]


@dataclass(frozen=True)
class SetlistItem:
    title: str
    """As the setlist writes it, a version note included: 乱舞る (short ver.)."""
    singers: tuple[str, ...]
    """The idols who sang it, by name, among the day's performers: no one from another
    brand, no one who only danced."""
    dancers: tuple[str, ...] = ()
    """The idols who only danced to it (backing dancers, or a medley danced without
    singing)."""
    brand: str | None = None
    """The brand whose song it is, if the setlist marks it as another's (a cover at a
    joint live: シンデレラガールズ, THE IDOLM@STERシリーズ). Covering it does not make it
    one of her songs."""


@dataclass(frozen=True)
class LiveDay:
    date: date
    venue: str
    performers: tuple[str, ...]
    """The idols who stood on it, by name: those whose CV performed that day, no one
    from another brand."""
    absent: tuple[str, ...]
    """The idols announced for it whose CV was absent, as an official news item says:
    not among the performers."""
    sources: tuple[str, ...]
    setlist: tuple[SetlistItem, ...] = ()
    """What was performed that day with any of Shiny Colors' idols in it, in order;
    empty if it is not known (a day still to come, or one of two shows)."""


@dataclass(frozen=True)
class Live:
    name: str
    days: tuple[LiveDay, ...]
    """In order; a day at two venues (a live's two halves) is two days."""
    sources: tuple[str, ...]


EntryKind = Literal["song", "release", "live"]


@dataclass(frozen=True)
class WorldEntry:
    """One of the work facts: a song, a release or a live day (`World.entries`)."""

    kind: EntryKind
    title: str
    """What it is about: 歌曲「…」, 唱片《…》, or 演出「…」 and the day."""
    text: str


@dataclass(frozen=True)
class World:
    as_of: date
    """The day the data was imported: the characters know nothing after it."""
    office: str
    staff: tuple[Staff, ...]
    units: tuple[Unit, ...]
    idols: tuple[Idol, ...]
    releases: tuple[Release, ...] = ()
    songs: tuple[Song, ...] = ()
    lives: tuple[Live, ...] = ()

    def idol(self, name: str) -> Idol | None:
        return next((idol for idol in self.idols if idol.name == name), None)

    def is_staff(self, name: str) -> bool:
        """Whether `name` is one of the office's staff rather than an idol."""
        return self.idol(name) is None and any(staff.name == name for staff in self.staff)

    def unit_of(self, name: str) -> tuple[str, int] | None:
        """The name of her unit and where it is among the office's units (the official
        order, as the data file lists them); None if the sources could not agree on it."""
        idol = self.idol(name)
        names = [unit.name for unit in self.units]
        if idol is None or idol.unit not in names:
            return None
        return idol.unit, names.index(idol.unit)

    def songs_of(self, name: str) -> list[Song]:
        """Her songs: those she sings alone and those her whole unit sings, no one else
        joining in. A special unit's song is none of them, whoever is in it."""
        idol = self.idol(name)
        unit = next((u for u in self.units if idol and u.name == idol.unit), None)
        hers = [{name}] + ([set(unit.members)] if unit else [])
        return [
            song for song in self.songs if song.special_unit is None and set(song.singers) in hers
        ]

    def lives_of(self, name: str) -> list[tuple[Live, list[LiveDay]]]:
        """The lives she stood on or is to stand on, each with the days she is among the
        performers of."""
        found = []
        for live in self.lives:
            days = [day for day in live.days if name in day.performers]
            if days:
                found.append((live, days))
        return found

    def entries(self) -> list[WorldEntry]:
        """The work facts retrieval searches: each song with who sings it and the releases
        it is on, each release with its date and tracks, and each live day with its venue,
        performers and setlist. No CV is in them, and they do not change with the day: a
        date after the turn's is told by the prompt of the day, not the entry."""
        releases = {release.catalog: release for release in self.releases}
        found = []
        for song in self.songs:
            lines = [f"歌曲「{song.title}」", f"演唱：{self._singers(song)}"]
            on = [
                f"《{r.title}》（{_day(r.date)}发售）"
                for catalog in song.releases
                if (r := releases.get(catalog)) is not None
            ]
            if on:
                lines.append(f"收录于：{'、'.join(on)}")
            found.append(WorldEntry("song", lines[0], "\n".join(lines)))
        for release in self.releases:
            lines = [f"唱片《{release.title}》（{release.catalog}）", f"发售日：{_day(release.date)}"]
            if release.tracks:
                lines.append("曲目：")
                lines += [f"{i}. {track.title}" for i, track in enumerate(release.tracks, 1)]
            found.append(WorldEntry("release", lines[0], "\n".join(lines)))
        for live in self.lives:
            for day in live.days:
                performers = "、".join(day.performers) or "（资料里没有记录）"
                lines = [
                    f"演出「{live.name}」",
                    f"日期：{_day(day.date)}",
                    f"会场：{day.venue}",
                    f"出演偶像：{performers}",
                ]
                if day.setlist:
                    lines.append("曲目单（标着别的品牌的是翻唱，不是闪耀色彩的歌）：")
                    lines += [
                        f"{i}. {self._setlist_line(item, day)}"
                        for i, item in enumerate(day.setlist, 1)
                    ]
                title = f"{lines[0]}{_day(day.date)}"  # one entry for each of its days
                found.append(WorldEntry("live", title, "\n".join(lines)))
        return found

    def _singers(self, song: Song) -> str:
        """Who sings `song`: the special unit or the whole unit it is, if it is one, then
        its idols."""
        idols = "、".join(song.singers)
        if song.special_unit:
            return f"{song.special_unit}（{idols}）"
        unit = next((u for u in self.units if set(u.members) == set(song.singers)), None)
        return f"{unit.name}（{idols}）" if unit else idols

    def _setlist_line(self, item: SetlistItem, day: LiveDay) -> str:
        line = item.title + (f"（{item.brand}的歌）" if item.brand else "")
        who = []
        if item.singers:
            who.append(f"{self._who(item.singers, day)}演唱")
        if item.dancers:
            who.append(f"{self._who(item.dancers, day)}伴舞，没有演唱")
        return f"{line}：{'；'.join(who)}" if who else f"{line}：资料里没能确定是谁演唱"

    def _who(self, idols: Sequence[str], day: LiveDay) -> str:
        """`idols` as briefly as they can be named: everyone on the day, one or two whole
        units, everyone but one or two, or the whole units among them and then the rest."""
        rest = list(idols)
        names = []
        for unit in self.units:
            if len(unit.members) > 1 and set(unit.members) <= set(rest):
                names.append(unit.name)
                rest = [idol for idol in rest if idol not in unit.members]
        left_out = [idol for idol in day.performers if idol not in idols]
        several_on_stage = len(idols) > 1 and set(idols) <= set(day.performers)
        if several_on_stage and not left_out:
            return "全体出演偶像"
        if not rest and len(names) <= 2:
            return "、".join(names)
        if several_on_stage and len(left_out) < min(3, len(idols)):
            return f"除{'、'.join(left_out)}以外的全体出演偶像"
        return "、".join(names + rest)

    def check_characters(self, characters: dict[str, Character], path: Path) -> None:
        """Raises WorldError naming the first character who is none of the idols and none
        of the staff."""
        for character in characters.values():
            if self.idol(character.name) is None and not self.is_staff(character.name):
                raise WorldError(
                    f"角色 {character.id} 的显示名「{character.name}」在世界设定资料 {path} 的偶像和事务所职员名单里都找不到"
                    "（显示名要和资料里的日文全名一致，例如「櫻木真乃」）"
                )

    def prompt(self, name: str, today: date) -> str:
        """The world setting as a turn on `today` with the character called `name` carries
        it: for an idol, her songs and her lives included, those on releases and days after
        `today` told as still to come; for one of the staff, neither. No CV is in it."""
        if self.is_staff(name):
            return self._setting_prompt(staff=True)
        return "\n\n".join(
            [self._setting_prompt(), self._songs_prompt(name, today), self._lives_prompt(name, today)]
        )

    def _lives_prompt(self, name: str, today: date) -> str:
        lives = self.lives_of(name)
        lines = ["# 你上过的演出"]
        if not lives:
            lines.append("资料里还没有你站上过的舞台。")
            return "\n".join(lines)
        lines.append(
            "下面是你站上过的舞台，和已经定下、你将要站上的舞台，按演出日列出日期和会场。"
            "你没有上台的演出和日子不在这里：那些演出你可能知道，但你没有站上去。"
        )
        for live, days in lives:
            runs: list[tuple[str, list[str]]] = []  # days in a row at one venue share it
            for day in days:
                when = f"{'将于' if day.date > today else ''}{_day(day.date)}"
                if runs and runs[-1][0] == day.venue:
                    runs[-1][1].append(when)
                else:
                    runs.append((day.venue, [when]))
            on = [f"{'、'.join(whens)}（{venue}）" for venue, whens in runs]
            lines.append(f"- {live.name}：{'、'.join(on)}")
        return "\n".join(lines)

    def _songs_prompt(self, name: str, today: date) -> str:
        songs = self.songs_of(name)
        lines = ["# 你的歌"]
        if not songs:
            lines.append("资料里还没有你唱的歌。")
            return "\n".join(lines)
        lines.append(
            "下面是你一个人唱的歌，和你所在的组合全员一起唱的歌，以及收录它们的唱片。"
            "别的偶像的歌、特别组合或者全体一起唱的歌不在这里。"
        )
        releases = {release.catalog: release for release in self.releases}
        for song in songs:
            on = []
            for catalog in song.releases:
                if (release := releases.get(catalog)) is None:
                    continue
                coming = "将于" if release.date > today else ""
                on.append(f"《{release.title}》（{coming}{_day(release.date)}发售）")
            lines.append(f"- {song.title}：{'、'.join(on)}" if on else f"- {song.title}")
        return "\n".join(lines)

    def _setting_prompt(self, *, staff: bool = False) -> str:
        as_of = _day(self.as_of)
        lines = [
            "# 世界设定",
            f"以下是你所在的{self.office}的基本情况，资料截止到 {as_of}。",
            "",
        ]
        for person in self.staff:
            reading = f"（{person.reading}）" if person.reading else ""
            lines.append(f"{person.position}：{person.name}{reading}")
        lines += ["", "事务所的组合和成员（每位偶像只属于一个组合）："]
        for unit in self.units:
            reading = f"（{unit.reading}）" if unit.reading else ""
            lines.append(f"- {unit.name}{reading}：{'、'.join(unit.members)}")
        lines += [
            "",
            "关于作品里的事实：",
            "- 歌曲、唱片、演出，上面的资料里查不到的，就老实说一时想不起来；"
            "既不说「没有这回事」，也不顺着对方的说法附和、编下去。",
            "- 不引用歌词，也不编造歌词。被要求唱或念歌词时，用你自己的方式岔开，"
            + ("比如说那是偶像们的歌，想听就去听她们唱。" if staff else "比如说想在舞台上唱给他听。"),
            "- 关于演出，你只知道名称、日期、会场、出演者和曲目这类事实，不编造台上发生了什么（MC、现场的情节等）。",
            "- 不谈论声优，也不知道「中の人」这回事；被问到时按你自己的性格应对，不说出任何人的名字。",
            "- 不谈论具体的周边商品（立牌、徽章之类），被问到时按你自己的性格应对，不编造商品。",
            f"- {as_of}以后的事（新的歌曲、唱片、演出、新来的偶像等）你一概不知道，被问到就说还没听说。",
        ]
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "office": {"name": self.office, "staff": [asdict(s) for s in self.staff]},
            "units": [asdict(u) for u in self.units],
            "idols": [asdict(i) for i in self.idols],
            "releases": [
                {
                    "catalog": r.catalog, "title": r.title, "date": r.date.isoformat(),
                    "tracks": [asdict(t) for t in r.tracks], "sources": list(r.sources),
                }
                for r in self.releases
            ],
            "songs": [asdict(s) for s in self.songs],
            "lives": [
                {
                    "name": live.name,
                    "days": [
                        {**asdict(day), "date": day.date.isoformat()} for day in live.days
                    ],
                    "sources": list(live.sources),
                }
                for live in self.lives
            ],
        }

    @classmethod
    def from_json(cls, data: dict) -> "World":
        return cls(
            as_of=date.fromisoformat(data["as_of"]),
            office=data["office"]["name"],
            staff=tuple(
                Staff(s["name"], s["reading"], s["position"], tuple(s["sources"]))
                for s in data["office"]["staff"]
            ),
            units=tuple(
                Unit(u["name"], u["reading"], tuple(u["members"]), tuple(u["sources"]))
                for u in data["units"]
            ),
            idols=tuple(
                Idol(i["name"], i["reading"], i["unit"], i["cv"], tuple(i["sources"]))
                for i in data["idols"]
            ),
            releases=tuple(
                Release(
                    r["catalog"], r["title"], date.fromisoformat(r["date"]),
                    tuple(Track(t["title"], t["song"]) for t in r["tracks"]), tuple(r["sources"]),
                )
                for r in data.get("releases", [])
            ),
            songs=tuple(
                Song(
                    s["id"], s["title"], tuple(s["singers"]), s["special_unit"],
                    tuple(s["releases"]), tuple(s["sources"]),
                )
                for s in data.get("songs", [])
            ),
            lives=tuple(
                Live(
                    live["name"],
                    tuple(
                        LiveDay(
                            date.fromisoformat(d["date"]), d["venue"], tuple(d["performers"]),
                            tuple(d["absent"]), tuple(d["sources"]),
                            tuple(
                                SetlistItem(
                                    i["title"], tuple(i["singers"]), tuple(i["dancers"]), i["brand"]
                                )
                                for i in d.get("setlist", [])
                            ),
                        )
                        for d in live["days"]
                    ),
                    tuple(live["sources"]),
                )
                for live in data.get("lives", [])
            ),
        )


def _day(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日"


def world_path(characters_path: Path) -> Path:
    """Where the data file is: beside the character configuration."""
    return characters_path.parent / WORLD_FILE


def load_world(path: Path) -> World | None:
    """The data file at `path`; None if there is none. Raises WorldError if it is broken."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise WorldError(f"世界设定资料 {path} 格式有误：{e}") from None
    try:
        return World.from_json(data)
    except (KeyError, TypeError, ValueError) as e:
        raise WorldError(f"世界设定资料 {path} 缺少或写错了字段：{e!r}（重新运行 eidolon world sync）") from None
