"""The map (GLOSSARY.md「地图」): where on a drawn map of Tokyo each idol is now, as her
schedule has it, and the places the producer can invite one to (`DATE_SPOTS`).

Her schedule says where she is in her own words (`Slot.where`), so where on the map that is is
read off it here (`place_of`): a place the producer can take her out to, if it names one (a
day out with him is written into her schedule as one); else by the kind of slot it is, with a
few words telling a studio from the office; else, resting, at home. A day away from Tokyo puts
her at the edge of the map (`AWAY`). Nothing here is kept: it is read anew each time."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from eidolon.schedules import Slot

Place = Literal[
    "office", "lesson", "school", "studio", "venue", "home", "town", "away",
    "aquarium", "amusement", "cinema", "cafe", "shopping", "park", "tower", "sea", "zoo",
    "asakusa",
]
"""Where on the map: an everyday place, the edge of the map for a day away from Tokyo, or a
place to go out to (`DATE_SPOTS`)."""

AWAY: Place = "away"


@dataclass(frozen=True)
class DateSpot:
    """A place on the map the producer can invite her out to."""

    id: Place
    words: tuple[str, ...]
    """Words in her schedule, or in what a day out is to be, that say it is this place."""


DATE_SPOTS: tuple[DateSpot, ...] = (
    DateSpot("aquarium", ("水族",)),
    DateSpot("amusement", ("游乐园", "遊園地", "乐园", "过山车", "摩天轮")),
    DateSpot("zoo", ("动物园", "動物園")),
    DateSpot("tower", ("东京塔", "東京タワー", "晴空塔", "スカイツリー", "夜景", "展望")),
    DateSpot("sea", ("海边", "海滩", "江之岛", "江ノ島", "台场", "お台場", "海浜")),
    DateSpot("asakusa", ("浅草", "雷门", "神社", "寺", "参拜", "庙会", "祭")),
    DateSpot("cinema", ("电影", "映画")),
    DateSpot("cafe", ("咖啡", "カフェ", "甜品", "下午茶", "喫茶")),
    DateSpot("park", ("公园", "公園", "散步", "野餐", "赏花")),
    DateSpot("shopping", ("逛街", "购物", "商场", "商店街", "买东西", "涩谷", "原宿", "表参道")),
)
"""In the order they are looked for: the more particular first, so that "看水族馆的电影"
is the aquarium."""

_STUDIO_WORDS = (
    "摄影", "拍摄", "棚", "电视", "テレビ", "录音", "收录", "収録", "杂志", "广播", "ラジオ",
    "采访", "广告", "CM",
)
_HOME_WORDS = ("家", "宿舍", "寮", "房间")


def spot_of(text: str) -> Place | None:
    """The place to go out to that `text` names, if any (`DATE_SPOTS`)."""
    return next((s.id for s in DATE_SPOTS if any(w in text for w in s.words)), None)


def place_of(slot: Slot | None, away: bool = False) -> Place:
    """Where on the map she is in `slot`, the part of her schedule she is in now (None:
    nothing planned, so out and about); at the edge of the map all day on a day `away`."""
    if away:
        return AWAY
    if slot is None:
        return "town"
    said = f"{slot.where} {slot.what}"
    if (spot := spot_of(said)) is not None:
        return spot
    match slot.kind:
        case "school":
            return "school"
        case "lesson":
            return "lesson"
        case "live":
            return "venue"
        case "rest":
            return "home"
        case "work":
            return "studio" if any(w in said for w in _STUDIO_WORDS) else "office"
        case "private":
            return "home" if any(w in slot.where for w in _HOME_WORDS) else "town"
    return "town"


def resting_place(resting: str | None) -> Place | None:
    """Where she is resting (asleep, in the bath, about to sleep): at home; None otherwise."""
    return "home" if resting in ("asleep", "bath", "bedtime") else None


def spots() -> Sequence[Place]:
    """The places to go out to, as the map lists them."""
    return [s.id for s in DATE_SPOTS]
