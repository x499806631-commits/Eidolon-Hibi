"""Titles (GLOSSARY.md「称号」), this fork's own: each named, earned once as what it asks of the
user is first found done, and kept earned whatever happens after. Two kinds: what the user did
with the app (`DOINGS`: letters, the diary, gifts, draws, keepsakes, days out, running into
them, days come, calls, turns), counted up to a hundred times at most; and how close the
characters are (`each one's` a hundred, so many close or intimate, a whole unit close).

Nothing is counted as it happens: each count is read off what is kept already, as titles are
looked for (`Badges.check`): after every turn recorded, as the user's titles are read, and as
the perks they bring are (`perk`). Those of affinity go by the affinities as they are then.

The more titles, the more the user may do a day (`perk`): 10 give a second encounter and a
second draw chance a day, 30 a third (GLOSSARY.md「偶遇」「抽奖」).

Each user's are theirs, deleted with them (`database.USER_DATA`), and go in no data package.
"""

import logging
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC

from eidolon.achievements import TIERS_REACHED
from eidolon.affinity import HIGHEST, TIERS
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database

log = logging.getLogger(__name__)

_SCHEMA = """
-- One row per title each user has earned (GLOSSARY.md「称号」), when, and when they first saw it.
CREATE TABLE IF NOT EXISTS badges (
    user_id TEXT NOT NULL REFERENCES users(id),
    badge TEXT NOT NULL,
    earned_at TEXT NOT NULL,
    seen_at TEXT,
    PRIMARY KEY (user_id, badge)
);
"""

PERKS = ((30, 3), (10, 2))
"""How many titles give how many encounters and daily draw chances: the first reached."""


@dataclass(frozen=True)
class Badge:
    id: str
    name: str
    """What it is called, as the user is shown it."""
    how: str
    """How it is earned, as the user is shown it."""
    kind: str
    """`doing` or `affinity`."""
    done: Callable[["_Facts"], bool]


@dataclass(frozen=True)
class _Facts:
    """What a user has done and how close the characters are, read once for every title."""

    counts: Mapping[str, float]
    values: Mapping[str, float]
    """Each character's affinity now, by her id."""
    reached: Mapping[str, int]
    """The highest tier each character's affinity has reached, by her id, as its rank."""


# 做过的事：(数什么, 多少次, 称号, 怎么得到)。次数最多一百。
_DOINGS: tuple[tuple[str, int, str, str], ...] = (
    ("letters", 1, "信箱有回音了", "第一次收到她们的来信"),
    ("letters", 10, "情意渐浓", "收到第 10 封来信"),
    ("letters", 30, "一叠信纸", "收到第 30 封来信"),
    ("letters", 100, "283 的邮差", "收到第 100 封来信"),
    ("diary_unlocked", 1, "她们的心", "第一次用日记钥匙打开一篇日记"),
    ("diary_unlocked", 10, "日记的守密人", "用日记钥匙打开第 10 篇日记"),
    ("diary_unlocked", 30, "心事全知道", "用日记钥匙打开第 30 篇日记"),
    ("diary_read", 1, "偷偷看了一眼", "第一次读她们的日记"),
    ("diary_read", 30, "每天的读者", "读了 30 篇日记"),
    ("diary_read", 100, "日记里的常客", "读了 100 篇日记"),
    ("gifts", 1, "第一份心意", "第一次送她们礼物"),
    ("gifts", 10, "送礼新手", "送出第 10 份礼物"),
    ("gifts", 30, "礼物小能手", "送出第 30 份礼物"),
    ("gifts", 100, "梦想成为好感度大师！", "送出第 100 份礼物"),
    ("draws", 1, "试试手气", "第一次抽奖"),
    ("draws", 10, "转蛋爱好者", "抽奖 10 次"),
    ("draws", 100, "欧气满满", "抽奖 100 次"),
    ("keepsakes", 1, "珍藏的回忆", "第一次收到她们送的纪念品"),
    ("keepsakes", 10, "满满一柜子", "收到 10 件纪念品"),
    ("keepsakes", 30, "宝物满屋", "收到 30 件纪念品"),
    ("dates", 1, "初次约会", "第一次和她们约会"),
    ("dates", 10, "约会达人", "约会 10 次"),
    ("dates", 30, "东京漫步专家", "约会 30 次"),
    ("encounters", 1, "真巧啊", "第一次在地图上偶遇她们"),
    ("encounters", 10, "命中注定的偶遇", "偶遇 10 次"),
    ("encounters", 50, "这真的是偶然吗？", "偶遇 50 次"),
    ("days", 7, "一周的约定", "来过 7 天"),
    ("days", 30, "风雨无阻", "来过 30 天"),
    ("days", 100, "百日之约", "来过 100 天"),
    ("call_minutes", 1, "电话那头", "和她们通话满 1 分钟"),
    ("call_minutes", 60, "煲电话粥", "通话加起来满 60 分钟"),
    ("turns", 100, "聊不完的话", "和她们说了 100 句话"),
)

# 每一位好感度到 100 时：(她的 id, 称号)。
_HERS: tuple[tuple[str, str], ...] = (
    ("mano", "软绵绵的幸福"),
    ("hiori", "不再迷茫的舞步"),
    ("meguru", "最好的朋友！"),
    ("kogane", "长崎的约定"),
    ("mamimi", "紫色的秘密"),
    ("sakuya", "王子的真心"),
    ("yuika", "镜片后的真心"),
    ("kiriko", "幽谷里开的花"),
    ("kaho", "最强的英雄搭档"),
    ("chiyoko", "巧克力般的甜蜜"),
    ("juri", "别扭的真心话"),
    ("rinze", "一生一世"),
    ("natsuha", "完美的伙伴"),
    ("amana", "甘甜的每一天"),
    ("tenka", "甜花的勇气"),
    ("chiyuki", "温柔的手作"),
    ("asahi", "好奇心的终点"),
    ("fuyuko", "卸下面具的那一刻"),
    ("mei", "嘿嘿，最喜欢了"),
    ("toru", "透明的约定"),
    ("madoka", "冷淡之下"),
    ("koito", "小小的努力"),
    ("hinana", "幸福的~♪"),
    ("nichika", "梦想的舞台"),
    ("mikoto", "完美之外的心"),
    ("luca", "刺的另一边"),
    ("hana", "如花绽放"),
    ("haruki", "留白的画布"),
    ("hazuki", "事务所的灯一直亮着"),
)

# 一个组合的人都到了亲近：(组合, 成员, 称号)。
_UNITS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("illumination STARS", ("mano", "hiori", "meguru"), "照亮夜空的星光"),
    ("L'Antica", ("kogane", "mamimi", "sakuya", "yuika", "kiriko"), "月下的乐团"),
    ("放课后 CLIMAX GIRLS", ("kaho", "chiyoko", "juri", "rinze", "natsuha"), "放学后的高潮"),
    ("ALSTROEMERIA", ("amana", "tenka", "chiyuki"), "花开三姐妹"),
    ("Straylight", ("asahi", "fuyuko", "mei"), "迷途之光"),
    ("noctchill", ("toru", "madoka", "koito", "hinana"), "夜光的青梅竹马"),
    ("SHHis", ("nichika", "mikoto"), "并肩的两个人"),
    ("CoMETIK", ("luca", "hana", "haruki"), "划过的彗星"),
)

# 好感到了某一档的有几位：(档位, 几位, 称号, 怎么得到)。几位是 0 时是全部。
_MANY: tuple[tuple[str, int, str, str], ...] = (
    ("close", 3, "渐渐熟悉起来", "3 位偶像的好感度到过亲近"),
    ("close", 10, "只是朋友", "10 位偶像的好感度到过亲近"),
    ("close", 20, "大家的制作人", "20 位偶像的好感度到过亲近"),
    ("close", 0, "闪耀的太阳", "所有人的好感度都到过亲近"),
    ("intimate", 1, "特别的人", "第一次有偶像的好感度到了亲密"),
    ("intimate", 5, "受欢迎的制作人", "5 位偶像的好感度到过亲密"),
    ("intimate", 10, "全员的心上人？", "10 位偶像的好感度到过亲密"),
)


def _rank(tier: str) -> int:
    return [t for t, _ in TIERS].index(tier)


def _counted(what: str, at_least: int) -> Callable[[_Facts], bool]:
    return lambda f: f.counts.get(what, 0) >= at_least


def _hers(character_id: str) -> Callable[[_Facts], bool]:
    return lambda f: f.values.get(character_id, 0.0) >= HIGHEST


def _many(tier: str, how_many: int, everyone: Sequence[str]) -> Callable[[_Facts], bool]:
    def done(f: _Facts) -> bool:
        at = sum(1 for c in everyone if f.reached.get(c, -1) >= _rank(tier))
        return at >= (how_many or len(everyone)) > 0

    return done


def _unit(members: Sequence[str]) -> Callable[[_Facts], bool]:
    return lambda f: all(f.reached.get(c, -1) >= _rank("close") for c in members)


def catalog(characters: Mapping[str, Character]) -> list[Badge]:
    """Every title there is, by the characters there are."""
    found = [
        Badge(f"{what}_{n}", name, how, "doing", _counted(what, n))
        for what, n, name, how in _DOINGS
    ]
    for character_id, name in _HERS:
        her = characters.get(character_id)
        if her is not None:
            found.append(Badge(
                f"hers_{character_id}", name, f"{her.name}的好感度到了 100", "affinity",
                _hers(character_id),
            ))
    everyone = list(characters)
    for tier, n, name, how in _MANY:
        found.append(Badge(f"{tier}_{n or 'all'}", name, how, "affinity", _many(tier, n, everyone)))
    for unit, members, name in _UNITS:
        if all(m in characters for m in members):
            found.append(Badge(
                f"unit_{members[0]}", name, f"{unit}的每一位好感度都到过亲近", "affinity",
                _unit(members),
            ))
    return found


def _count(db: sqlite3.Connection, sql: str, *args: object) -> float:
    try:
        row = db.execute(sql, args).fetchone()
    except sqlite3.OperationalError:  # 那张表这个部署里没有
        return 0
    return float(row[0] or 0) if row else 0


def _facts(db: sqlite3.Connection, user_id: str) -> _Facts:
    def progress(achievement: str) -> float:
        return _count(
            db,
            "SELECT SUM(count) FROM achievement_progress WHERE user_id = ? AND achievement = ?",
            user_id, achievement,
        )

    counts = {
        "letters": _count(db, "SELECT COUNT(*) FROM letters WHERE user_id = ?", user_id),
        "diary_unlocked": _count(
            db, "SELECT COUNT(*) FROM diary_entries WHERE user_id = ? AND unlocked = 1", user_id
        ),
        "diary_read": _count(
            db, "SELECT COUNT(*) FROM diary_entries WHERE user_id = ? AND read_at IS NOT NULL",
            user_id,
        ),
        "gifts": progress("gifts"),
        "draws": progress("draws"),
        "keepsakes": _count(db, "SELECT COUNT(*) FROM keepsakes WHERE user_id = ?", user_id),
        "dates": _count(
            db, "SELECT COUNT(*) FROM outings WHERE user_id = ? AND kind = 'date'"
            " AND ended_at IS NOT NULL", user_id,
        ),
        "encounters": _count(
            db, "SELECT COUNT(*) FROM outings WHERE user_id = ? AND kind = 'encounter'"
            " AND ended_at IS NOT NULL", user_id,
        ),
        "days": progress("days"),
        "call_minutes": progress("call_time") / 60,
        "turns": progress("turns"),
    }
    try:
        values = {
            row[0]: row[1] for row in db.execute(
                "SELECT character_id, value FROM affinities WHERE user_id = ?", (user_id,)
            )
        }
        # 成就记下的是 TIERS_REACHED 里的第几档（亲近 1、亲密 2）
        reached = {
            row[0]: _rank(TIERS_REACHED[int(row[1]) - 1]) for row in db.execute(
                "SELECT character_id, count FROM achievement_progress"
                " WHERE user_id = ? AND achievement = 'tier' AND count > 0",
                (user_id,),
            )
        }
    except sqlite3.OperationalError:
        values, reached = {}, {}
    # 现在的档位也算到过（成就关着时没有记下）
    for character_id, value in values.items():
        rank = max(i for i, (_, start) in enumerate(TIERS) if value >= start)
        reached[character_id] = max(reached.get(character_id, -1), rank)
    return _Facts(counts, values, reached)


def _earned(db: sqlite3.Connection, user_id: str) -> dict[str, sqlite3.Row]:
    return {
        row[0]: row for row in db.execute(
            "SELECT badge, earned_at, seen_at FROM badges WHERE user_id = ?", (user_id,)
        )
    }


def earned_count(db: sqlite3.Connection, user_id: str) -> int:
    """How many titles the user has earned, through `db`: none where none are kept yet."""
    return int(_count(db, "SELECT COUNT(*) FROM badges WHERE user_id = ?", user_id))


def perk_of(earned: int) -> int:
    """How many encounters and daily draw chances `earned` titles give (GLOSSARY.md「称号」)."""
    for at_least, gives in PERKS:
        if earned >= at_least:
            return gives
    return 1


def _see(db: sqlite3.Connection, user_id: str, now: str) -> None:
    db.execute(
        "UPDATE badges SET seen_at = ? WHERE user_id = ? AND seen_at IS NULL", (now, user_id)
    )


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


class Badges:
    """Every user's titles (GLOSSARY.md「称号」)."""

    def __init__(self, db: Database, characters: Mapping[str, Character], clock: Clock) -> None:
        self._db = db
        self._catalog = catalog(characters)
        self._clock = clock
        db.prepare(_open)

    async def check(self, user_id: str) -> list[Badge]:
        """Earn the user every title they have done what it asks for and not earned yet: those
        newly earned."""
        now = self._clock().astimezone(UTC).isoformat()
        return await self._db.write(self._check, user_id, now)

    def _check(self, db: sqlite3.Connection, user_id: str, now: str) -> list[Badge]:
        facts = _facts(db, user_id)
        earned = _earned(db, user_id)
        new = [b for b in self._catalog if b.id not in earned and b.done(facts)]
        db.executemany(
            "INSERT OR IGNORE INTO badges (user_id, badge, earned_at) VALUES (?, ?, ?)",
            [(user_id, b.id, now) for b in new],
        )
        return new

    async def shown(self, user_id: str) -> dict:
        """Every title, earned or not, how each is earned, and what earning them gives."""
        await self.check(user_id)
        earned = await self._db.read(_earned, user_id)
        count = sum(1 for b in self._catalog if b.id in earned)
        return {
            "badges": [
                {
                    "id": b.id, "name": b.name, "how": b.how, "kind": b.kind,
                    "earned_at": earned[b.id][1] if b.id in earned else None,
                    "unseen": b.id in earned and earned[b.id][2] is None,
                }
                for b in self._catalog
            ],
            "earned": count,
            "perk": perk_of(count),
            "next": next((n for n, _ in reversed(PERKS) if count < n), None),
        }

    async def seen(self, user_id: str) -> None:
        now = self._clock().astimezone(UTC).isoformat()
        await self._db.write(_see, user_id, now)

    async def unseen(self, user_id: str) -> int:
        return int(await self._db.read(
            _count, "SELECT COUNT(*) FROM badges WHERE user_id = ? AND seen_at IS NULL", user_id
        ))

    async def perk(self, user_id: str) -> int:
        """How many encounters and daily draw chances the user's titles give now."""
        await self.check(user_id)
        return perk_of(await self._db.read(earned_count, user_id))
