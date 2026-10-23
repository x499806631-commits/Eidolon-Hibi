"""The relations between the characters (GLOSSARY.md「关系表」), and what word of mouth carries
between them (GLOSSARY.md「传话」).

The relations are laid down by the source (`canon`): from each character's facts, the section
under 人物关系 on another of the characters says how she sees her; the others of her unit are
her unit mates even without one. Each user's story then changes them: what the producer has
them do together (a party, making up, a trip) is written down as a change between the two
(`change`), so that they are closer in that user's story only. Users never edit them; the
deployer may, under the development sign-in or as an admin, set how one sees another for a
user (`override`), and take it back.

Word of mouth: what happens between the producer and one of them that the others would hear
of (a gift, a day out, a scene, what she was moved by) is told as news (`tell`); she hears it
if she is related to the one it is about, within `NEWS_DAYS`, and may bring it up. With the
user's switch for it off (`word_of_mouth`), nothing is told and nobody hears, and the
memories told to any of them are known to all, as they always were (`shares_with`).
"""

import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from eidolon.characters import Character
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.features import AllOn, Switches
from eidolon.world import World

RELATIONS = "人物关系"
"""The heading of the facts' section on the others in her life."""
CANON_CHARS = 200
"""The most of a fact's section kept as how she sees another, by whole sentences."""
NEWS_DAYS = 7
"""How long news goes on being heard of."""
MAX_NEWS = 3
"""The most news she is told of in a turn, newest first."""
MAX_CHANGES = 3
"""The most changes between two of them she is told of, newest last."""
MAX_TEXT = 200
"""The longest a change, an override or a piece of news may be."""

_SCHEMA = """
-- How two of the characters came to stand in this user's story (GLOSSARY.md「关系表」), as it
-- changed, newest last: what they did together, from `source` (a favor, a trip, by hand).
CREATE TABLE IF NOT EXISTS relation_changes (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    a TEXT NOT NULL,
    b TEXT NOT NULL,
    note TEXT NOT NULL,
    source TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relation_changes_by_user ON relation_changes (user_id, a, b);
-- How one of them sees another in this user's story, set by the deployer instead of the source.
CREATE TABLE IF NOT EXISTS relation_overrides (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    other_id TEXT NOT NULL,
    note TEXT NOT NULL,
    PRIMARY KEY (user_id, character_id, other_id)
);
-- What happened between the producer and one of them, as the others would hear of it
-- (GLOSSARY.md「传话」); heard of for a week.
CREATE TABLE IF NOT EXISTS relation_news (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    about_id TEXT NOT NULL,
    text TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relation_news_by_user ON relation_news (user_id, at);
"""


@dataclass(frozen=True)
class Tie:
    """How she stands with another of them: as the source has it, if it says, her unit if
    they share one, and how it changed in the user's story."""

    other_id: str
    canon: str | None
    unit: str | None
    changes: tuple[str, ...] = ()
    overridden: bool = False

    def shown(self) -> dict:
        return {
            "other_id": self.other_id, "canon": self.canon, "unit": self.unit,
            "changes": list(self.changes), "overridden": self.overridden,
        }


@dataclass(frozen=True)
class News:
    about_id: str
    text: str
    at: datetime


def canon(characters: Mapping[str, Character]) -> dict[tuple[str, str], str]:
    """How each of them sees each other, as her facts say, by (her, the other); the first
    sentences of the section, up to `CANON_CHARS`."""
    named = {c.name: c.id for c in characters.values()}
    seen: dict[tuple[str, str], str] = {}
    for character in characters.values():
        for fact in character.facts:
            if fact.title is None or not fact.title.startswith(f"{RELATIONS} / "):
                continue
            other = named.get(fact.title.split(" / ", 1)[1].strip())
            if other is None or other == character.id:
                continue
            body = fact.text.split("\n", 1)[1] if "\n" in fact.text else fact.text
            seen[(character.id, other)] = _first_sentences(body.strip(), CANON_CHARS)
    return seen


def units(characters: Mapping[str, Character], world: World | None) -> dict[str, str]:
    """Her unit, by her id, where the world setting says it."""
    if world is None:
        return {}
    found: dict[str, str] = {}
    for c in characters.values():
        unit = world.unit_of(c.name)
        if unit is not None:
            found[c.id] = unit[0]
    return found


def _first_sentences(text: str, limit: int) -> str:
    sentences = re.findall(r"[^。！？]*[。！？]?", text)
    kept = ""
    for sentence in sentences:
        if not sentence:
            continue
        if kept and len(kept) + len(sentence) > limit:
            break
        kept += sentence
    return kept[:limit] if kept else text[:limit]


class Relations:
    """The relations between the characters and the news passed between them, every user's,
    in `db` beside the users they belong to (ADR-0028)."""

    def __init__(
        self,
        db: Database,
        characters: Mapping[str, Character],
        world: World | None,
        clock: Callable[[], datetime],
        features: Switches = AllOn(),
    ) -> None:
        self._db = db
        self._characters = characters
        self._canon = canon(characters)
        self._units = units(characters, world)
        self._clock = clock
        self._features = features
        db.prepare(_open)

    def now(self) -> datetime:
        return self._clock()

    def canon_of(self, character_id: str, other_id: str) -> str | None:
        return self._canon.get((character_id, other_id))

    def unit_mates(self, a: str, b: str) -> bool:
        unit = self._units.get(a)
        return unit is not None and unit == self._units.get(b)

    async def ties(self, user_id: str, character_id: str) -> list[Tie]:
        """How she stands with each of the others she has anything to do with: those the
        source says, her unit mates, and those the user's story changed or the deployer set,
        in the characters' order."""
        return await self._db.read(self.read_ties, user_id, character_id)

    def read_ties(self, db: sqlite3.Connection, user_id: str, character_id: str) -> list[Tie]:
        changes = _changes(db, user_id, character_id)
        overrides = _overrides(db, user_id, character_id)
        found = []
        for other in self._characters:
            if other == character_id:
                continue
            unit = self._units.get(other) if self.unit_mates(character_id, other) else None
            said = overrides.get(other) or self.canon_of(character_id, other)
            changed = tuple(changes.get(other, [])[-MAX_CHANGES:])
            if said is None and unit is None and not changed:
                continue
            found.append(Tie(other, said, unit, changed, other in overrides))
        return found

    async def related(self, user_id: str, character_id: str) -> set[str]:
        """Those she would hear of: the others she has a tie with (`ties`)."""
        return {t.other_id for t in await self.ties(user_id, character_id)}

    async def change(self, user_id: str, a: str, b: str, note: str, source: str) -> None:
        """Write down how `a` and `b` came to stand in the user's story, both ways."""
        if a == b or a not in self._characters or b not in self._characters:
            return
        await self._db.write(_add_change, user_id, a, b, note[:MAX_TEXT], source, self._clock())

    async def override(self, user_id: str, character_id: str, other_id: str, note: str | None) -> None:
        """Set how she sees another in the user's story (None takes it back to the source)."""
        await self._db.write(
            _set_override, user_id, character_id, other_id, note[:MAX_TEXT] if note else None
        )

    async def forget_changes(self, user_id: str, a: str, b: str) -> None:
        """Take back how the user's story changed `a` and `b`."""
        await self._db.write(_forget_changes, user_id, a, b)

    # Word of mouth.

    async def tell(self, user_id: str, about_id: str, text: str, at: datetime) -> None:
        """Tell as news what happened between the producer and `about_id`, for the others
        related to her to hear of; nothing with the user's word of mouth off."""
        if about_id not in self._characters or not await self._features.on(user_id, "word_of_mouth"):
            return
        await self._db.write(_add_news, user_id, about_id, text[:MAX_TEXT], at)

    async def heard(self, user_id: str, character_id: str) -> list[News]:
        """The news she has heard: of the others related to her, within `NEWS_DAYS`, newest
        first, `MAX_NEWS` at most; none with the user's word of mouth off."""
        if not await self._features.on(user_id, "word_of_mouth"):
            return []
        related = await self.related(user_id, character_id)
        since = self._clock() - timedelta(days=NEWS_DAYS)
        rows = await self._db.read(_news_since, user_id, since)
        return [
            News(r["about_id"], r["text"], datetime.fromisoformat(r["at"]))
            for r in rows if r["about_id"] in related
        ][:MAX_NEWS]

    async def shares_with(self, user_id: str, character_id: str) -> set[str] | None:
        """Whose memories of the producer she knows besides her own: the others related to
        her with the user's word of mouth on; None, all of them, with it off."""
        if not await self._features.on(user_id, "word_of_mouth"):
            return None
        return await self.related(user_id, character_id)

    def prompt(self, ties: Iterable[Tie], character: Character) -> str | None:
        """The prompt section telling her how she stands with the others; None for none."""
        lines = []
        for tie in ties:
            other = self._characters.get(tie.other_id)
            if other is None:
                continue
            head = f"- {other.name}" + (f"（同组合 {tie.unit}）" if tie.unit else "")
            body = tie.canon or ("同组合的伙伴。" if tie.unit else "")
            lines.append(f"{head}：{body}" if body else head)
            lines += [f"  后来：{c}" for c in tie.changes]
        if not lines:
            return None
        return (
            f"# 你和事务所里其他人的关系\n这是{character.name}眼中的她们，聊到时照这个来，"
            "不要说错称呼和关系；「后来」是你们之间最近的变化。\n" + "\n".join(lines)
        )

    def news_lines(self, news: Iterable[News], timezone: str) -> list[str]:
        """What she is told of the news she heard, a line each."""
        lines = []
        for item in news:
            other = self._characters.get(item.about_id)
            if other is None:
                continue
            local = item.at.astimezone(ZoneInfo(timezone))
            lines.append(
                f"你听说（{local.month}月{local.day}日）：{item.text}。这是从{other.name}那里或者大家之间"
                "传开的，不是制作人告诉你的；合适的时候可以自然地提一句，用听说的口吻，不要每次都提。"
            )
        return lines


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _add_change(
    db: sqlite3.Connection, user_id: str, a: str, b: str, note: str, source: str, at: datetime
) -> None:
    first, second = _pair(a, b)
    db.execute(
        "INSERT INTO relation_changes (user_id, a, b, note, source, at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, first, second, note, source, at.astimezone(UTC).isoformat()),
    )


def _forget_changes(db: sqlite3.Connection, user_id: str, a: str, b: str) -> None:
    first, second = _pair(a, b)
    db.execute(
        "DELETE FROM relation_changes WHERE user_id = ? AND a = ? AND b = ?",
        (user_id, first, second),
    )


def _changes(db: sqlite3.Connection, user_id: str, character_id: str) -> dict[str, list[str]]:
    rows = by_name(db).execute(
        "SELECT a, b, note FROM relation_changes WHERE user_id = ? AND (a = ? OR b = ?)"
        " ORDER BY id",
        (user_id, character_id, character_id),
    ).fetchall()
    found: dict[str, list[str]] = {}
    for row in rows:
        other = row["b"] if row["a"] == character_id else row["a"]
        found.setdefault(other, []).append(row["note"])
    return found


def _overrides(db: sqlite3.Connection, user_id: str, character_id: str) -> dict[str, str]:
    rows = db.execute(
        "SELECT other_id, note FROM relation_overrides WHERE user_id = ? AND character_id = ?",
        (user_id, character_id),
    ).fetchall()
    return dict(rows)


def _set_override(
    db: sqlite3.Connection, user_id: str, character_id: str, other_id: str, note: str | None
) -> None:
    if note is None:
        db.execute(
            "DELETE FROM relation_overrides WHERE user_id = ? AND character_id = ? AND other_id = ?",
            (user_id, character_id, other_id),
        )
        return
    db.execute(
        "INSERT INTO relation_overrides (user_id, character_id, other_id, note) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (user_id, character_id, other_id) DO UPDATE SET note = excluded.note",
        (user_id, character_id, other_id, note),
    )


def _add_news(db: sqlite3.Connection, user_id: str, about_id: str, text: str, at: datetime) -> None:
    db.execute(  # news older than anyone hears of goes as new news comes
        "DELETE FROM relation_news WHERE user_id = ? AND at < ?",
        (user_id, (at - timedelta(days=NEWS_DAYS)).astimezone(UTC).isoformat()),
    )
    db.execute(
        "INSERT INTO relation_news (user_id, about_id, text, at) VALUES (?, ?, ?, ?)",
        (user_id, about_id, text, at.astimezone(UTC).isoformat()),
    )


def _news_since(db: sqlite3.Connection, user_id: str, since: datetime) -> list[sqlite3.Row]:
    return by_name(db).execute(
        "SELECT about_id, text, at FROM relation_news WHERE user_id = ? AND at >= ?"
        " ORDER BY at DESC, id DESC",
        (user_id, since.astimezone(UTC).isoformat()),
    ).fetchall()
