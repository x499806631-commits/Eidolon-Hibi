"""The producer profile and what it tells the character about the user, and the prompt
section about now: the time where the user is, and whether they are typing, in a call, or
not talking to her at all, as when she reaches out (GLOSSARY.md「搭话」).

A profile field left unset keeps its default: the character sheet decides how
the character addresses the producer, the producer is the man of the original
story, the time zone is the one the user's device reports (Asia/Shanghai
when it reports none), and the character does not know the producer's birthday
(a month and a day, never a year). On the birthday, counted in the producer's
own time zone, the character is asked to wish them a happy birthday once. The
profile only shapes how the character treats the user now; the stories she
recalls stay as they happened.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cache
from typing import Literal
from zoneinfo import ZoneInfo, available_timezones

Gender = Literal["male", "female"]
Talking = Literal["typing", "in_call", "apart", "together"]
"""How the user and she are talking now: typing, in a call, or not at all."""

DEFAULT_TIMEZONE = "Asia/Shanghai"
MAX_FORM_OF_ADDRESS_LENGTH = 20

_GENDER_NAMES: dict[Gender, str] = {"male": "男性", "female": "女性"}
_WEEKDAYS = "一二三四五六日"


@dataclass(frozen=True)
class Birthday:
    """The producer's birthday: a month and a day, never a year."""

    month: int
    day: int


def is_day_of_the_year(month: int, day: int) -> bool:
    """Whether some year has this day; February 29 counts."""
    return 1 <= month <= 12 and 1 <= day <= calendar.monthrange(2000, month)[1]


def is_birthday(birthday: Birthday, today: date) -> bool:
    """Whether `today`, the producer's local date, is their birthday.

    February 29 falls on February 28 in a year that has no February 29.
    """
    if (birthday.month, birthday.day) == (today.month, today.day):
        return True
    return (
        (birthday.month, birthday.day) == (2, 29)
        and (today.month, today.day) == (2, 28)
        and not calendar.isleap(today.year)
    )


Elder = Literal["her", "producer"]
"""Who is the elder of the two sworn as sisters (or siblings): she, or the producer."""


@dataclass(frozen=True)
class Ties:
    """What the producer asked of one character alone (GLOSSARY.md「拜托」), kept from then
    on: how she calls them, in place of the profile's (`address`), and whether they are sworn
    sisters, and who is the elder (`bond`); and, for a few days after another made peace
    between them (GLOSSARY.md「托人和好」), who did (`made_up_by`, her name); and for a few
    days after she would not call them as asked, how (`declined`)."""

    address: str | None = None
    bond: Elder | None = None
    made_up_by: str | None = None
    declined: str | None = None
    """A way to call the producer she was lately asked to and would not take up."""


def bond_words(bond: Elder, gender: "Gender | None") -> tuple[str, str]:
    """What the two sworn are, and what the producer is to her: (义姐妹, 姐姐) and the like."""
    female = gender == "female"
    if bond == "producer":
        return ("义姐妹" if female else "义兄妹"), ("姐姐" if female else "哥哥")
    return ("义姐妹" if female else "义兄妹"), ("妹妹" if female else "弟弟")


@dataclass(frozen=True)
class ProducerProfile:
    form_of_address: str | None = None
    gender: Gender | None = None
    timezone: str | None = None
    birthday: Birthday | None = None


@cache
def _known_timezones() -> frozenset[str]:
    # The server's own local time is no place a user lives in.
    return frozenset(available_timezones() - {"localtime", "Factory"})


def is_timezone(name: str) -> bool:
    """Whether `name` is an IANA time zone, such as Asia/Tokyo."""
    return name in _known_timezones()


def effective_timezone(profile: ProducerProfile, device_timezone: str | None) -> str:
    """The profile's time zone if set, else the device's, else Asia/Shanghai.

    Either may name a zone this server does not know (tzdata drops or renames
    zones now and then); such a zone is passed over rather than failing the turn.
    """
    for timezone in (profile.timezone, device_timezone):
        if timezone and is_timezone(timezone):
            return timezone
    return DEFAULT_TIMEZONE


def local_date(now: datetime, timezone: str) -> date:
    """The day it is at `now` where the producer is: the day `now_prompt` tells."""
    return now.astimezone(ZoneInfo(timezone)).date()


def producer_prompt(profile: ProducerProfile, ties: Ties | None = None) -> str:
    """The prompt section about the user: who they are, the same from turn to turn; with
    what they asked of her alone (`ties`), the way she calls them and their bond."""
    lines = ["# 制作人", "和你对话的是你的制作人，也就是 283 事务所的制作人。"]
    if ties is not None and ties.address:
        lines.append(
            f"制作人拜托过你，以后叫对方「{ties.address}」，这是只属于你们俩的叫法。"
            "对方仍然是你的制作人；照你自己的性格和习惯，平时就这样称呼。"
        )
    elif profile.form_of_address:
        lines.append(
            f"制作人希望你称呼对方为「{profile.form_of_address}」。这只是叫法，"
            "对方仍然是你的制作人；请按你自己的性格和习惯，在合适的时候这样称呼。"
        )
    if profile.gender:
        lines.append(
            f"制作人是{_GENDER_NAMES[profile.gender]}。这只影响你现在怎么对待对方；"
            "回忆原作剧情时，剧情中发生过的事保持原样，不要改写。"
        )
    else:
        lines.append("制作人是男性，和原作一致。")
    if ties is not None and ties.declined:
        lines.append(
            f"前几天制作人拜托你改叫对方「{ties.declined}」，你没有答应，只在那一次这样叫过。"
            "平时还是照你原来的习惯称呼制作人。"
        )
    if ties is not None and ties.bond:
        pair, producer_is = bond_words(ties.bond, profile.gender)
        you_are = "妹妹" if ties.bond == "producer" else "姐姐"
        lines.append(
            f"你和制作人结拜成了{pair}，制作人是你的{producer_is}，你是{you_are}。"
            "这是你们俩郑重许下的约定，你一直记得、很珍惜；工作上对方仍然是你的制作人，"
            "私下相处时照你的性格，自然地带着这份亲近，偶尔以这个身份说话。"
        )
    if ties is not None and ties.made_up_by:
        lines.append(
            f"前几天你和制作人闹得不太愉快，后来{ties.made_up_by}替制作人来找你说情，你们已经和好了。"
            "你原谅了制作人，也想过自己有不对的地方；照你的性格，别扭也好坦率也好，别再翻旧账。"
        )
    if profile.birthday:
        lines.append(
            f"制作人的生日是{profile.birthday.month}月{profile.birthday.day}日，"
            "这是制作人自己在档案里填的；记忆里如果有不一样的说法，以这里为准。"
            "被问起时可以回答，平时不要主动提起。"
        )
    return "\n".join(lines)


_ONLINE = (
    "你们是隔着手机联系，不在一起：不会有牵手、摸头、递东西这种见了面才有的接触，"
    "也不要写成你就在制作人身边；想见面就说想见面。"
)
"""Text and calls are online, apart (GLOSSARY.md「约会」「偶遇」 are face to face)."""
_TYPING = (
    "制作人正在用文字和你聊天，你们没有在打电话；不想聊的时候就说不想聊，不要说挂电话或挂断。" + _ONLINE
)
_IN_A_CALL = "制作人正在和你打电话。" + _ONLINE
_APART = "你们这会儿没在说话。"
_TOGETHER = "你和制作人现在面对面在一起（不是在网上聊天，也不是打电话），可以有当面的动作和接触，照你的性格来。"
_TALKING: dict[Talking, str] = {
    "typing": _TYPING, "in_call": _IN_A_CALL, "apart": _APART, "together": _TOGETHER,
}
_BIRTHDAY = (
    "今天是制作人的生日。请在合适的时候用你自己的方式祝福一次：先回应制作人说的话，不要每句都提。"
)
_BIRTHDAY_APART = "今天是制作人的生日。今天还没祝福过的话，用你自己的方式祝福一次。"


def _how_long(gap: timedelta) -> str:
    """`gap` as she is told it: in minutes under an hour, in hours under two days, else days."""
    minutes = int(gap.total_seconds() // 60)
    if minutes < 1:
        return "不到 1 分钟"
    if minutes < 60:
        return f"{minutes} 分钟"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小时"
    return f"{gap.days} 天"


def now_prompt(
    profile: ProducerProfile,
    now: datetime,
    timezone: str,
    talking: Talking,
    affinity_lines: Sequence[str] = (),
    last_contact: datetime | None = None,
    today_lines: Sequence[str] = (),
) -> str:
    """The prompt section about now: the time where the user is, how they are `talking` with
    the character, when they were `last_contact` in touch, if ever, and how long ago, and
    whether it is their birthday; then `today_lines`, what else the day is
    (`special_days.SpecialDays.prompt_lines`); then `affinity_lines`, how the character feels
    toward them now (`affinity.affinity_prompt`)."""
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    lines = [
        "# 此刻",
        f"现在是 {local.year}年{local.month}月{local.day}日"
        f"（星期{_WEEKDAYS[local.weekday()]}）{local:%H:%M}（制作人所在时区 {timezone}）。",
        _TALKING[talking],
    ]
    if last_contact is not None:
        then = last_contact.astimezone(zone)
        year = f"{then.year}年" if then.year != local.year else ""
        lines.append(
            f"上次和制作人说话是 {year}{then.month}月{then.day}日 {then:%H:%M}，"
            f"隔了 {_how_long(now - last_contact)}。"
        )
    if profile.birthday and is_birthday(profile.birthday, local.date()):
        lines.append(_BIRTHDAY_APART if talking == "apart" else _BIRTHDAY)
    lines += today_lines
    lines += affinity_lines
    return "\n".join(lines)
