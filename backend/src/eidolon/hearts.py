"""Her heart (GLOSSARY.md「她的心」): what makes each idol happy and what hurts her, which the
judge model weighs her affinity by, and how she shows each tier of it in her own way; for an
adult idol, the romance of the closer tiers too, told apart for a male and a female producer.
And how she plays her scene at close's door and intimate's (GLOSSARY.md「重要的一幕」), and her
daily habits: when she sleeps (GLOSSARY.md「行程」) and how she bathes (GLOSSARY.md「洗澡」).

Each is a file of its own in the `hearts` directory beside the character configuration, named
by her identifier (`mano.toml`). Like the calendar it is configuration, not data. An idol
without one goes by the generic wording of `affinity`. Anything wrong in one keeps the backend
from starting (`HeartsError`), and so does romance written for an idol under `ADULT`: for her,
however close, closeness is trust and reliance, never romance.
"""

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, get_args

from eidolon.affinity import Tier
from eidolon.characters import Character

HEARTS_DIR = "hearts"
"""The directory's name, beside the character configuration."""

ADULT = 18
"""The age from which an idol's closer tiers may be romance."""

RomanceTier = Literal["close", "intimate"]
"""The tiers that may be romance, for an adult idol."""

BathKind = Literal["tub", "shower"]
"""How she bathes (GLOSSARY.md「洗澡」): soaking in the tub, or a quick shower."""

BATH_KINDS: dict[str, BathKind] = {"泡澡": "tub", "淋浴": "shower"}
"""How a heart file writes `bath`."""

_ROMANCE_KEYS = {f"{t}_{g}" for t in get_args(RomanceTier) for g in ("male", "female")}


class HeartsError(RuntimeError):
    """A heart is broken."""


@dataclass(frozen=True)
class Heart:
    """What makes her happy, what hurts her, and how she shows each tier; for an adult, the
    romance of `close` and `intimate`, by `<tier>_male` and `<tier>_female`."""

    age: int
    pleased: tuple[str, ...] = ()
    hurt: tuple[str, ...] = ()
    tiers: Mapping[Tier, str] = field(default_factory=dict)
    romance: Mapping[str, str] = field(default_factory=dict)
    scenes: Mapping[str, str] = field(default_factory=dict)
    """How she plays her scene at the door of `close` and of `intimate` (GLOSSARY.md「重要的一幕」)."""
    sleep: str | None = None
    """When she tends to go to bed and get up, for her schedule (GLOSSARY.md「行程」)."""
    bath: BathKind = "tub"
    """How she bathes (GLOSSARY.md「洗澡」); in the tub unless her file says otherwise."""
    bath_habit: str | None = None
    """Her habits in the bath, told her as part of her daily life."""

    @property
    def adult(self) -> bool:
        return self.age >= ADULT

    def romance_in(self, tier: Tier, female_producer: bool) -> str | None:
        """How she shows romance in `tier` to a producer who is a woman or not; None in a tier
        that is none, or for an idol under `ADULT`, whatever her file says."""
        if not self.adult or tier not in get_args(RomanceTier):
            return None
        return self.romance.get(f"{tier}_{'female' if female_producer else 'male'}")


def hearts_path(characters_path: Path) -> Path:
    """Where the hearts are: beside the character configuration."""
    return characters_path.parent / HEARTS_DIR


def load_hearts(path: Path, characters: Iterable[Character]) -> dict[str, Heart]:
    """The hearts in the directory at `path`, by character identifier; none without it.
    Raises HeartsError if one is broken, is not one of `characters`', or writes romance
    for an idol under `ADULT`."""
    known = {c.id for c in characters}
    if not path.is_dir():
        return {}
    hearts: dict[str, Heart] = {}
    for file in sorted(path.glob("*.toml")):
        character_id = file.stem
        if character_id not in known:
            raise HeartsError(f"{file}：角色配置里没有 {character_id}")
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise HeartsError(f"{file} 读不出来：{e}") from e
        hearts[character_id] = _parse(file, data)
    return hearts


def _parse(file: Path, data: dict) -> Heart:
    age = data.get("age")
    if not isinstance(age, int) or isinstance(age, bool) or not 0 < age < 100:
        raise HeartsError(f"{file}：age 要写她的年龄（整数）")
    pleased, hurt = _texts(file, data, "pleased"), _texts(file, data, "hurt")
    tiers = data.get("tiers", {})
    if not isinstance(tiers, dict) or not all(isinstance(v, str) and v.strip() for v in tiers.values()):
        raise HeartsError(f"{file}：[tiers] 里每一档都要是一段文字")
    unknown = set(tiers) - set(get_args(Tier))
    if unknown:
        raise HeartsError(f"{file}：[tiers] 里没有这些档位：{'、'.join(sorted(unknown))}")
    romance = data.get("romance", {})
    if romance and age < ADULT:
        raise HeartsError(f"{file}：她 {age} 岁，未满 {ADULT} 岁的偶像不能写 [romance]")
    if not isinstance(romance, dict) or not all(isinstance(v, str) and v.strip() for v in romance.values()):
        raise HeartsError(f"{file}：[romance] 里每一项都要是一段文字")
    unknown = set(romance) - _ROMANCE_KEYS
    if unknown:
        raise HeartsError(
            f"{file}：[romance] 只能写 {'、'.join(sorted(_ROMANCE_KEYS))}，不认识 {'、'.join(sorted(unknown))}"
        )
    scenes = data.get("scenes", {})
    if not isinstance(scenes, dict) or not all(isinstance(v, str) and v.strip() for v in scenes.values()):
        raise HeartsError(f"{file}：[scenes] 里每一项都要是一段文字")
    unknown = set(scenes) - set(get_args(RomanceTier))
    if unknown:
        raise HeartsError(f"{file}：[scenes] 只能写 close、intimate，不认识 {'、'.join(sorted(unknown))}")
    sleep = data.get("sleep")
    if sleep is not None and (not isinstance(sleep, str) or not sleep.strip()):
        raise HeartsError(f"{file}：sleep 要写一段文字（她的作息习惯）")
    bath = data.get("bath", "泡澡")
    if not isinstance(bath, str) or bath not in BATH_KINDS:
        raise HeartsError(f"{file}：bath 只能写「泡澡」或「淋浴」")
    habit = data.get("bath_habit")
    if habit is not None and (not isinstance(habit, str) or not habit.strip()):
        raise HeartsError(f"{file}：bath_habit 要写一段文字（她的洗澡习惯）")
    return Heart(
        age, pleased, hurt, {k: v.strip() for k, v in tiers.items()},
        {k: v.strip() for k, v in romance.items()}, {k: v.strip() for k, v in scenes.items()},
        sleep.strip() if sleep else None, BATH_KINDS[bath], habit.strip() if habit else None,
    )


def _texts(file: Path, data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise HeartsError(f"{file}：{key} 要写成一列文字")
    return tuple(v.strip() for v in value)


def with_hearts(characters: Mapping[str, Character], hearts: Mapping[str, Heart]) -> dict[str, Character]:
    """`characters`, each with her heart, if she has one."""
    return {id: replace(c, heart=hearts.get(id)) for id, c in characters.items()}
