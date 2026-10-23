"""Favors (GLOSSARY.md「拜托」): what the producer asks of one of the characters with a favor
ticket (GLOSSARY.md「体验券」), giving up gifts from the backpack for it (`MATERIALS`), each a
kind of slot a gift fills: of a category, of a rarity, or any.

- `address`: she calls the producer as they ask from then on, she alone (`Ties.address`),
  if she takes it up, as the name and her character have it; if not, she calls them so
  that once, and knows for a few days she would not (`Ties.declined`); asked again, as she
  used to. Not while she is cold or distant to them, or cooling.
- `wake`: she wakes the producer at a time of the morning, for some days on end, with a few
  words in their conversation (`run_due`).
- `bond`: the two of them sworn sisters (or siblings), one of them the elder (`Ties.bond`),
  in close or intimate; she gives a keepsake for it, and her diary has it.
- `make_up`: one asks another the producer has fallen out with, cold, distant or cooling,
  to make peace, taking her the gift: some hours on (`MAKE_UP_AFTER`), she is brought back
  to trust and out of any cooldown, as a reset card does, and knows for a few days who made
  peace (`Ties.made_up_by`); she tells the producer she forgives them and was not all in the
  right either, of her own accord where it went far (cooling or cold; distant, now and then);
  and the one who made peace tells how it went.
- `party`: one helps the producer give a party, a birthday's or any other, on a day from
  tomorrow to a week on, for some of the others: her schedule has her getting it ready the
  days before, and all of theirs have it that evening (`twists_on`); after it, she sends a
  photo of them all where there is image generation, and one whose birthday it was thanks
  the producer, and gives a keepsake the next day.

She answers each as she is asked, in her current conversation, which is to be open; what
she says, and says later, is a reach-out (GLOSSARY.md「搭话」), on the deployer's chat model.
Once she has answered, the ticket and the gifts are used up, whatever she answered: asked to
call the producer otherwise, she may not take it up (`<answer>no</answer>`), calling them so
that once only. Only a favor she never got to answer (the model failing) is not kept, and
its ticket and gifts are given back; one that cannot be asked (`CannotAsk`) takes nothing.
What comes of a favor later is tried again `RETRY_AFTER` later, `TRIES` times in all.
"""

import asyncio
import json
import re
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import permutations
from typing import Literal, get_args
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog
from eidolon.affinity import BELOW_TRUST, Affinities
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.engine import TRIP_PHOTO_OTHERS, WITH_YOU, ConversationEngine, photo_rules
from eidolon.features import AllOn, Switches
from eidolon.gift_catalog import Category, Gift, Rarity
from eidolon.gifts import Gifts
from eidolon.keepsakes import Keepsakes
from eidolon.live_config import LiveConfig
from eidolon.producer import (
    MAX_FORM_OF_ADDRESS_LENGTH,
    Elder,
    Ties,
    bond_words,
    effective_timezone,
)
from eidolon.randomness import Random
from eidolon.reply_format import Segment
from eidolon.special_days import SpecialDays
from eidolon.store import Conversation, Store
from eidolon.trip_photos import NoConversation, TripPhotos

log = logging.getLogger(__name__)

FavorKind = Literal["address", "wake", "bond", "make_up", "party"]
FAVOR_KINDS: tuple[FavorKind, ...] = get_args(FavorKind)

TICK = timedelta(minutes=1)
TRIES = 3
RETRY_AFTER = timedelta(minutes=30)
MAX_DAYS_AHEAD = 7
"""How many days ahead a party may be, and how many mornings she wakes the producer at most."""
WAKE_FROM = time(4)
WAKE_UNTIL = time(12)
"""Between when of the morning she may be asked to wake the producer."""
WAKE_LATE = timedelta(hours=2)
"""How late she still wakes the producer, the time gone by (the backend having been down)."""
MAKE_UP_AFTER = timedelta(hours=2)
MAKE_UP_SPREAD = timedelta(hours=2)
"""When, after she is asked, the one asked has made peace: some time within the spread."""
MADE_UP_FOR = timedelta(days=3)
"""How long the one made peace with knows who made it."""
DISTANT_ODDS = 2
"""One in so many who were only distant tell the producer of their own accord."""
PARTY_FROM = time(18)
PARTY_UNTIL = time(20)
AFTER_PARTY = time(20, 30)
AFTER_PARTY_SPREAD = 60
"""Minutes after `AFTER_PARTY` within which she tells of the party."""
MAX_GUESTS = 4
"""How many come to a party besides the one helping, at most."""
IN_PHOTO = 3
"""How many of them may be in its photo besides her (four girls at most in one)."""
THANKS_NEXT_DAY = time(9)
ADDRESS_MAX = MAX_FORM_OF_ADDRESS_LENGTH
WHAT_MAX = 30
NOT_NOW = BELOW_TRUST
BOND_TIERS = frozenset({"close", "intimate"})


@dataclass(frozen=True)
class Slot:
    """One gift a favor takes: one of `categories` (any if empty), of `rarity` (any if None)."""

    categories: tuple[Category, ...] = ()
    rarity: Rarity | None = None

    def fits(self, gift: Gift) -> bool:
        return (not self.categories or gift.category in self.categories) and (
            self.rarity is None or gift.rarity == self.rarity
        )

    def shown(self) -> dict:
        return {"categories": list(self.categories), "rarity": self.rarity}


MATERIALS: dict[FavorKind, tuple[Slot, ...]] = {
    "address": (Slot(),),
    "wake": (Slot(("drink",)),),
    "bond": (Slot(rarity="rare"),),
    "make_up": (Slot(),),
    "party": (Slot(("food",)), Slot(("food",)), Slot(("drink",)), Slot(("flower", "trinket"))),
}
"""The gifts each favor takes from the backpack."""


class CannotAsk(Exception):
    """The favor cannot be asked: who of, when, or with what."""


_SCHEMA = """
-- What the producer asked of one character alone (GLOSSARY.md「拜托」): how she calls them,
-- whether they are sworn sisters and who is the elder (her, producer), and when; who made
-- peace between them last and when; a way to call them she lately would not take up, and when.
CREATE TABLE IF NOT EXISTS favor_ties (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    address TEXT,
    bond TEXT,
    bonded_at TEXT,
    made_up_by TEXT,
    made_up_at TEXT,
    declined TEXT,
    declined_at TEXT,
    PRIMARY KEY (user_id, character_id)
);
-- The mornings she is to wake the producer: when, the how-many-th of how many, done or not.
CREATE TABLE IF NOT EXISTS favor_wakes (
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    due_at TEXT NOT NULL,
    nth INTEGER NOT NULL,
    of_days INTEGER NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    PRIMARY KEY (user_id, character_id, due_at)
);
-- Peace one makes between the producer and another: the gift she takes her, when it is
-- made, whether the other is to tell the producer herself, what is done of it.
CREATE TABLE IF NOT EXISTS favor_make_ups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    mediator_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    gift TEXT NOT NULL,
    due_at TEXT NOT NULL,
    reaches INTEGER NOT NULL,
    made INTEGER NOT NULL DEFAULT 0,
    target_told INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT
);
-- The parties one helps the producer give: on which day, who helps, who comes (JSON), what
-- it is, whose birthday if any, when it is told of, what is done of it.
CREATE TABLE IF NOT EXISTS favor_parties (
    user_id TEXT NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    helper_id TEXT NOT NULL,
    guests TEXT NOT NULL,
    what TEXT NOT NULL,
    honoree_id TEXT,
    due_at TEXT NOT NULL,
    created_on TEXT NOT NULL,
    helper_told INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    tries INTEGER NOT NULL DEFAULT 0,
    last_at TEXT,
    PRIMARY KEY (user_id, day)
);
"""

ADDRESS_RULES = """# 拜托：换个称呼
制作人拜托你，以后叫对方「{address}」，还送了你{gifts}当谢礼。答不答应由你自己决定：想想这个叫法和你的性格、说话习惯、你们现在的关系合不合，你叫得出口吗？
- 先单独写一行 <answer>yes</answer>（答应）或 <answer>no</answer>（不答应），再写你说的话。
- 答应的话，回应时就这样叫一次，以后一直这样叫，直到制作人再拜托你换。
- 不答应的话，照你的性格说清楚为什么，但看在礼物的份上，就这一次这样叫对方一声；说明以后还是照原来的叫法。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给制作人打电话。"""

ADDRESS_BACK_RULES = """# 拜托：换回原来的称呼
制作人拜托你，以后还是照你原来的习惯称呼对方，不用再叫「{address}」了，还送了你{gifts}当谢礼。照你的性格回应，你会答应。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给制作人打电话。"""

DECLINED_FOR = timedelta(days=3)
"""How long she remembers a way to call the producer she would not take up."""

_ANSWER = re.compile(r"<answer>\s*(yes|no)\s*</answer>", re.IGNORECASE)


def answered_no(raw: str) -> bool:
    """Whether she wrote she would not; an answer left out is a yes."""
    found = _ANSWER.search(raw)
    return found is not None and found.group(1).lower() == "no"

WAKE_ACCEPT_RULES = """# 拜托：叫起床
制作人拜托你从{first}起，连续{days}天，每天早上{at}叫对方起床，还送了你{gifts}当谢礼。照你的性格回应：你会答应，可以调侃一句、叮嘱一句。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给制作人打电话。"""

WAKE_RULES = """# 叫制作人起床
现在是早上{at}，你答应过制作人这个时候叫对方起床（{which}）。给制作人发几句叫醒的话：照你的性格，可以温柔、可以催、可以撒娇，可以提一句今天的天气或你今天的安排。
- 用 <ja><zh> 写一到两段，简短一点。
- 不发照片，也不说要给制作人打电话。"""

WANTS_TO_WAKE = "（现在是早上，你要叫制作人起床。制作人还没醒，没在和你说话。）"

BOND_RULES = """# 拜托：结拜
制作人郑重地拜托你，想和你结拜成{pair}：你当{you_are}，制作人当{producer_is}。制作人还送了你{gifts}当信物。照你的性格回应这份心意：你会答应。可以说说你的感受，也可以像结拜那样许一句约定。
- 用 <ja><zh> 写一到三段。
- 不发照片，也不说要给制作人打电话。"""

MAKE_UP_ASK_RULES = """# 拜托：帮忙和好
制作人和{target}最近闹得不太愉快，拜托你去替制作人说说情，还托你把{gifts}转交给她。照你的性格和你与{target}的关系回应：你会答应，可以问问发生了什么、给制作人出出主意、安慰几句。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给制作人打电话。"""

MADE_UP_RULES = """# 和好
刚才{mediator}来找你，替制作人说了情，还转交了制作人托她带来的{gifts}。你冷静下来想了想，决定原谅制作人，也觉得自己有做得不对的地方。主动给制作人发几句：说你原谅对方了，也反省一下自己；照你的性格，别扭也好坦率也好。
- 用 <ja><zh> 写一到三段。
- 不发照片，也不说要给制作人打电话。"""

WANTS_TO_MAKE_UP = "（{mediator}刚刚替制作人来找你说过情。制作人这会儿没在和你说话。你想先给对方发几句。）"

MADE_UP_REPORT_RULES = """# 帮忙和好之后
你刚刚去找{target}替制作人说了情，礼物也交给她了，她已经消气了。给制作人报个好消息，说几句。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给制作人打电话。"""

WANTS_TO_REPORT = "（你刚替制作人说完情回来。制作人这会儿没在和你说话。）"

PARTY_ACCEPT_RULES = """# 拜托：一起准备{what}
制作人拜托你帮忙准备{when}傍晚的{what}，{for_whom}要来的有{guests}。制作人已经准备好了{gifts}。照你的性格回应：你会答应，可以说说你想怎么布置、还要准备什么{secret}。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给制作人打电话。"""

AFTER_PARTY_SITUATION = "今天傍晚大家一起{what}，是制作人拜托你张罗的，刚刚结束。{names}都来了。"

WANTS_TO_TELL_OF_PARTY = "（{what}刚刚结束。制作人这会儿没在和你说话。你想先给对方发几句。）"

PARTY_THANKS_RULES = """# 生日会之后
今天大家给你过了生日：是制作人拜托{helper}张罗的{what}，{names}都来了。你想谢谢制作人，说说今天的心情。
- 用 <ja><zh> 写一到三段。
- 不发照片，也不说要给制作人打电话。"""

WANTS_TO_THANK = "（今天的生日会刚刚结束。制作人这会儿没在和你说话。你想先给对方发几句。）"

PREPARING_TWIST = "这几天在帮制作人悄悄准备{when}傍晚的{what}"
PARTY_TWIST = "傍晚六点到八点，和制作人、{others}一起{what}"
HELPER_TWIST = "傍晚六点到八点，和制作人、{others}一起{what}（是你帮制作人张罗的）"
SURPRISE_TWIST = "傍晚六点有人约你去事务所，好像有什么安排，你还不知道是什么"
"""The twists a party is to the schedules of those at it (GLOSSARY.md「行程」)."""


def _when(today: date, day: date) -> str:
    days = (day - today).days
    if days == 1:
        return "明天"
    if days == 2:
        return "后天"
    return f"{day.month}月{day.day}日"


def match(slots: Sequence[Slot], gifts: Sequence[Gift]) -> bool:
    """Whether the gifts fill the slots, one each."""
    if len(slots) != len(gifts):
        return False
    return any(all(s.fits(g) for s, g in zip(slots, order)) for order in permutations(gifts))


class Favors:
    def __init__(
        self,
        db: Database,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        catalog: Mapping[str, Gift],
        config: LiveConfig,
        activity: ActivityLog,
        clock: Clock,
        random: Random,
        affinities: Affinities,
        gifts: Gifts,
        keepsakes: Keepsakes,
        trip_photos: TripPhotos,
        special_days: SpecialDays | None = None,
        moment: Callable[[str, str, str, datetime], Awaitable[None]] | None = None,
        in_call: Callable[[str], bool] = lambda _: False,
        features: Switches = AllOn(),
    ) -> None:
        """`moment` notes what mattered to her for her diary (`Diary.moment`)."""
        self._store = store
        self._engine = engine
        self._characters = characters
        self._catalog = catalog
        self._config = config
        self._activity = activity
        self._clock = clock
        self._random = random
        self._affinities = affinities
        self._gifts = gifts
        self._keepsakes = keepsakes
        self._trip_photos = trip_photos
        self._special_days = special_days
        self._moment = moment
        self._together: list[Callable[[str, str, str, str, datetime], Awaitable[None]]] = []
        self._in_call = in_call
        self._features = features
        self._db = db
        db.prepare(_open)

    # What the rest read.

    async def ties(self, user_id: str, character_id: str) -> Ties | None:
        """What the producer asked of her alone, for her prompt; None if nothing."""
        row = await self._db.read(_tie_row, user_id, character_id)
        if row is None:
            return None
        made_up_by = None
        if row["made_up_at"] and self._clock() - datetime.fromisoformat(row["made_up_at"]) < MADE_UP_FOR:
            mediator = self._characters.get(row["made_up_by"])
            made_up_by = mediator.name if mediator else None
        declined = None
        if row["declined_at"] and self._clock() - datetime.fromisoformat(row["declined_at"]) < DECLINED_FOR:
            declined = row["declined"]
        ties = Ties(row["address"], row["bond"], made_up_by, declined)
        return ties if ties != Ties() else None

    async def twists_on(self, user_id: str, character_id: str, day: date) -> list[str]:
        """What the parties have her do on `day` (GLOSSARY.md「行程」): getting one ready, the
        days before, as the one helping; being at it, that evening."""
        names = {c.id: c.name for c in self._characters.values()}
        twists = []
        for row in await self._db.read(_parties_of, user_id):
            on = date.fromisoformat(row["day"])
            guests: list[str] = json.loads(row["guests"])
            at_it = [row["helper_id"], *guests]
            if character_id not in at_it:
                continue
            if on == day:
                others = "、".join(
                    str(names.get(c) or c) for c in at_it if c != character_id
                ) or "大家"
                if character_id == row["honoree_id"]:
                    twists.append(SURPRISE_TWIST)
                elif character_id == row["helper_id"]:
                    twists.append(HELPER_TWIST.format(others=others, what=row["what"]))
                else:
                    twists.append(PARTY_TWIST.format(others=others, what=row["what"]))
            elif character_id == row["helper_id"] and date.fromisoformat(row["created_on"]) < day < on:
                twists.append(PREPARING_TWIST.format(when=_when(day, on), what=row["what"]))
        return twists

    async def overview(self, user_id: str) -> dict:
        """What the favors page shows: what each favor takes, how each is called and who is
        sworn, the mornings and parties to come, the birthdays within a week."""
        zone = await self._zone(user_id)
        now = self._clock()
        today = now.astimezone(zone).date()
        ties, wakes, parties = await self._db.read(_overview, user_id)
        birthdays = []
        if self._special_days is not None:
            for ahead in range(1, MAX_DAYS_AHEAD + 1):
                day = today + timedelta(days=ahead)
                for character_id in self._special_days.birthdays_on(day):
                    if character_id in self._characters:
                        birthdays.append({"character_id": character_id, "day": day.isoformat()})
        return {
            "materials": {k: [s.shown() for s in MATERIALS[k]] for k in FAVOR_KINDS},
            "ties": [dict(r) for r in ties],
            "wakes": [
                {
                    "character_id": r["character_id"],
                    "at": datetime.fromisoformat(r["next"]).astimezone(zone).strftime("%H:%M"),
                    "left": r["left"],
                }
                for r in wakes
            ],
            "parties": [
                {"day": r["day"], "helper_id": r["helper_id"], "what": r["what"]}
                for r in parties
                if not r["done"] and date.fromisoformat(r["day"]) >= today
            ],
            "birthdays": birthdays,
            "max_days_ahead": MAX_DAYS_AHEAD,
        }

    # Asking.

    async def ask_address(
        self, user_id: str, character_id: str, address: str | None, gift_ids: Sequence[str]
    ) -> tuple[str, bool]:
        """Ask her to call the producer `address`, or, None, as she used to: the conversation
        she answers in, and whether she took it up. She may not, as her character and the
        name have it: then she calls them so this once and goes on as before, the ticket
        and the gifts used up all the same."""
        conversation = await self._open(user_id, character_id)
        await self._willing(user_id, character_id)
        address = (address or "").strip() or None
        if address is not None and len(address) > ADDRESS_MAX:
            raise CannotAsk("称呼太长了")
        gifts = self._fitting("address", gift_ids)
        if address is None:
            before = await self.ties(user_id, character_id)
            rules = ADDRESS_BACK_RULES.format(
                address=(before and before.address) or "之前说好的叫法", gifts=gifts
            )
        else:
            rules = ADDRESS_RULES.format(address=address, gifts=gifts)
        accepted = True

        async def done(raw: str) -> None:
            nonlocal accepted
            if address is not None and answered_no(raw):
                accepted = False
                await self._db.write(
                    _declined, user_id, character_id, address, self._clock()
                )
            else:
                await self._db.write(_set_tie, user_id, character_id, "address", address)

        await self._ask(user_id, conversation, gift_ids, rules, "（制作人刚刚拜托你换个称呼。）", done)
        return conversation.id, accepted

    async def ask_wake(
        self, user_id: str, character_id: str, at: time, days: int, gift_ids: Sequence[str]
    ) -> str:
        """Ask her to wake the producer at `at`, `days` mornings on end from the next."""
        conversation = await self._open(user_id, character_id)
        await self._willing(user_id, character_id)
        if not WAKE_FROM <= at < WAKE_UNTIL:
            raise CannotAsk("只能约早上四点到中午十二点之间")
        if not 1 <= days <= MAX_DAYS_AHEAD:
            raise CannotAsk("最多连续七天")
        gifts = self._fitting("wake", gift_ids)
        zone = await self._zone(user_id)
        now = self._clock().astimezone(zone)
        first = now.date() if now.time() < at else now.date() + timedelta(days=1)
        mornings = [datetime.combine(first + timedelta(days=d), at, zone) for d in range(days)]
        first_said = "明天早上" if first > now.date() else "今天早上"
        rules = WAKE_ACCEPT_RULES.format(
            first=first_said, days=days, at=at.strftime("%H:%M"), gifts=gifts
        )

        async def done(_: str) -> None:
            await self._db.write(_plan_wakes, user_id, character_id, mornings)

        await self._ask(user_id, conversation, gift_ids, rules, "（制作人刚刚拜托你叫他起床。）", done)
        return conversation.id

    async def ask_bond(
        self, user_id: str, character_id: str, elder: Elder, gift_ids: Sequence[str]
    ) -> str:
        """Ask her to be sworn sisters with the producer, `elder` the elder."""
        conversation = await self._open(user_id, character_id)
        status = await self._affinities.status(user_id, character_id)
        if status is not None and (status.tier not in BOND_TIERS or status.cooling):
            raise CannotAsk("要和她亲近以上才能结拜")
        gifts = self._fitting("bond", gift_ids)
        gender = (await self._store.get_profile(user_id)).gender
        pair, producer_is = bond_words(elder, gender)
        you_are = "妹妹" if elder == "producer" else "姐姐"
        rules = BOND_RULES.format(pair=pair, you_are=you_are, producer_is=producer_is, gifts=gifts)

        async def done(_: str) -> None:
            now = self._clock()
            await self._db.write(_set_tie, user_id, character_id, "bond", elder, now)
            await self._keepsakes.favor_done(
                user_id, character_id, f"bond:{now.date()}", "bond", f"结拜成了{pair}",
                now + timedelta(minutes=10),
            )
            await self._noted(user_id, character_id, f"和制作人结拜成了{pair}", now)

        await self._ask(user_id, conversation, gift_ids, rules, "（制作人刚刚郑重地拜托了你一件事。）", done)
        return conversation.id

    async def ask_make_up(
        self, user_id: str, mediator_id: str, target_id: str, gift_ids: Sequence[str]
    ) -> str:
        """Ask one to make peace between the producer and another, taking her the gift."""
        if mediator_id == target_id:
            raise CannotAsk("要请另一位偶像帮忙")
        if target_id not in self._characters:
            raise CannotAsk("没有这位偶像")
        conversation = await self._open(user_id, mediator_id)
        await self._willing(user_id, mediator_id)
        status = await self._affinities.status(user_id, target_id)
        if status is None or not (status.tier in NOT_NOW or status.cooling):
            raise CannotAsk("你们之间没有闹别扭")
        if await self._db.read(_making_up, user_id, target_id):
            raise CannotAsk("已经有人在帮你说情了")
        gifts = self._fitting("make_up", gift_ids)
        target = self._characters[target_id]
        reaches = (
            status.cooling or status.tier == "cold" or self._random(DISTANT_ODDS) == 0
        )
        rules = MAKE_UP_ASK_RULES.format(target=target.name, gifts=gifts)
        due = self._clock() + MAKE_UP_AFTER + timedelta(
            minutes=5 * self._random(int(MAKE_UP_SPREAD.total_seconds() // 300))
        )

        async def done(_: str) -> None:
            await self._db.write(
                _plan_make_up, user_id, mediator_id, target_id, gifts, due, reaches
            )

        await self._ask(user_id, conversation, gift_ids, rules, "（制作人刚刚拜托你帮忙说情。）", done)
        return conversation.id

    async def ask_party(
        self, user_id: str, helper_id: str, guest_ids: Sequence[str], what: str,
        honoree_id: str | None, day: date, gift_ids: Sequence[str],
    ) -> str:
        """Ask one to help give a party on `day`, `what` it is, for the guests, one of them
        whose birthday it is if `honoree_id`."""
        conversation = await self._open(user_id, helper_id)
        await self._willing(user_id, helper_id)
        what = what.strip()
        if not what or len(what) > WHAT_MAX:
            raise CannotAsk("写一下是什么聚会")
        guests = list(dict.fromkeys(g for g in guest_ids if g != helper_id))
        if honoree_id is not None:
            if honoree_id == helper_id:
                raise CannotAsk("过生日的人不能自己张罗")
            if honoree_id not in guests:
                guests.insert(0, honoree_id)
        if not guests:
            raise CannotAsk("至少请一位偶像来")
        if len(guests) > MAX_GUESTS or any(g not in self._characters for g in guests):
            raise CannotAsk(f"最多请 {MAX_GUESTS} 位")
        zone = await self._zone(user_id)
        today = self._clock().astimezone(zone).date()
        if not 1 <= (day - today).days <= MAX_DAYS_AHEAD:
            raise CannotAsk("只能约明天到一周以内")
        gifts = self._fitting("party", gift_ids)
        names = [self._characters[g].name for g in guests]
        honoree = self._characters[honoree_id].name if honoree_id else None
        rules = PARTY_ACCEPT_RULES.format(
            what=what, when=_when(today, day),
            for_whom=f"是给{honoree}过生日的，" if honoree else "",
            guests="、".join(names), gifts=gifts,
            secret=f"，要不要瞒着{honoree}给她一个惊喜" if honoree else "",
        )
        due = datetime.combine(day, AFTER_PARTY, zone) + timedelta(
            minutes=5 * self._random(AFTER_PARTY_SPREAD // 5)
        )

        async def done(_: str) -> None:
            if not await self._db.write(
                _plan_party, user_id, day, helper_id, guests, what, honoree_id, due, today
            ):
                raise CannotAsk("那天已经有一场聚会了")

        if await self._db.read(_party_on, user_id, day):
            raise CannotAsk("那天已经有一场聚会了")
        await self._ask(user_id, conversation, gift_ids, rules, "（制作人刚刚拜托你帮忙准备聚会。）", done)
        return conversation.id

    async def _ask(
        self, user_id: str, conversation: Conversation, gift_ids: Sequence[str], rules: str,
        wants: str, done: Callable[[str], Awaitable[None]],
    ) -> None:
        """Take the gifts, have her answer, keep what she said and what was asked (`done`,
        given all the model wrote). Once she has answered they are used up, whatever she
        answered; given back only should she not get to (the model failing), or what was
        asked not be kept."""
        await self._gifts.take_gifts(user_id, gift_ids)
        try:
            said, _, raw = await self._say(conversation, rules, wants)
            # Keeping it fails only should the same day be taken meanwhile: nothing is kept.
            await done(raw)
        except BaseException:
            await self._gifts.put_back_gifts(user_id, gift_ids)
            raise
        await self._store.add_photo_reach_out(conversation.id, said, None)

    async def _say(
        self, conversation: Conversation, rules: str, wants: str, with_photo: bool = False
    ) -> tuple[list[Segment], str | None, str]:
        recording = self._activity.begin(
            "reach_out", conversation.user_id, conversation.character_id, own_llm=False,
            exempt=False,
        )
        lease = self._config.lease()
        try:
            with recording.current():
                said, tag, raw = await self._engine.favor_reply(
                    conversation, lease.keys.chat_llm, rules, wants, with_photo
                )
        except Exception:
            await recording.finish("failed")
            raise
        finally:
            lease.release()
        await recording.finish("success")
        return said, tag, raw

    async def _open(self, user_id: str, character_id: str) -> Conversation:
        if character_id not in self._characters:
            raise CannotAsk("没有这位偶像")
        conversation = await self._current(user_id, character_id)
        if conversation is None or await self._store.ended(conversation.id) is not None:
            raise NoConversation()
        return conversation

    async def _willing(self, user_id: str, character_id: str) -> None:
        status = await self._affinities.status(user_id, character_id)
        if status is not None and (status.tier in NOT_NOW or status.cooling):
            raise CannotAsk("她现在不想听你的拜托")

    def _fitting(self, kind: FavorKind, gift_ids: Sequence[str]) -> str:
        """The gifts' names, if they are what the favor takes; else raises CannotAsk."""
        gifts = [self._catalog.get(g) for g in gift_ids]
        if any(g is None for g in gifts) or not match(MATERIALS[kind], [g for g in gifts if g]):
            raise CannotAsk("准备的礼物不对")
        return "、".join(g.name for g in gifts if g)

    # What comes of them.

    async def keep_running(self) -> None:
        """`run_due` at every `TICK`, until cancelled."""
        while True:
            await asyncio.sleep(TICK.total_seconds())
            try:
                await self.run_due()
            except Exception:
                log.exception("carrying out favors failed")

    async def run_due(self) -> None:
        """Wake those due, make the peace due, tell of the parties over; one going wrong keeps
        none of the others from it."""
        now = self._clock()
        for row in await self._db.read(_due, "favor_wakes", now):
            try:
                await self._wake(row, now)
            except Exception:
                log.exception("waking the producer failed")
        for row in await self._db.read(_due, "favor_make_ups", now):
            try:
                await self._make_up(row, now)
            except Exception:
                log.exception("making peace failed")
        for row in await self._db.read(_due, "favor_parties", now):
            try:
                await self._tell_party(row, now)
            except Exception:
                log.exception("telling of a party failed")

    async def _wake(self, row: sqlite3.Row, now: datetime) -> None:
        key = (row["user_id"], row["character_id"], row["due_at"])
        due = datetime.fromisoformat(row["due_at"])
        conversation = await self._current(row["user_id"], row["character_id"])
        if (
            now - due > WAKE_LATE or conversation is None
            or await self._store.ended(conversation.id) is not None
        ):
            await self._db.write(_mark, "favor_wakes", "done = 1", key)
            return
        if self._busy(conversation):
            return
        zone = await self._zone(row["user_id"])
        which = (
            "今天就这一次" if row["of_days"] == 1
            else f"第 {row['nth']} 天，一共 {row['of_days']} 天"
        )
        rules = WAKE_RULES.format(at=due.astimezone(zone).strftime("%H:%M"), which=which)
        try:
            said, _, _ = await self._say(conversation, rules, WANTS_TO_WAKE)
        except Exception as e:
            log.warning("waking the producer failed: %s", type(e).__name__)
            await self._db.write(_tried, "favor_wakes", key, now)
            return
        await self._db.write(_mark, "favor_wakes", "done = 1", key)
        await self._store.add_photo_reach_out(conversation.id, said, None)

    async def _make_up(self, row: sqlite3.Row, now: datetime) -> None:
        user_id, target_id, mediator_id = row["user_id"], row["target_id"], row["mediator_id"]
        key = (row["id"],)
        mediator = self._characters.get(mediator_id)
        target = self._characters.get(target_id)
        if mediator is None or target is None:
            await self._db.write(_mark, "favor_make_ups", "made = 1, done = 1", key)
            return
        if not row["made"]:
            await self._db.write(self._made_up, user_id, target_id, mediator_id, now, key)
            await self._noted(user_id, target_id, f"{mediator.name}替制作人来说情，和制作人和好了", now)
            await self._were_together(
                user_id, mediator_id, target_id,
                f"{mediator.name}替制作人去找{target.name}说情，两人因此好好聊了一次", now,
            )
        if row["reaches"] and not row["target_told"]:
            conversation = await self._current(user_id, target_id)
            if conversation is None or await self._store.ended(conversation.id) is not None:
                conversation = await self._store.create_conversation(user_id, target_id)
            if self._busy(conversation):
                return
            try:
                said, _, _ = await self._say(
                    conversation, MADE_UP_RULES.format(mediator=mediator.name, gifts=row["gift"]),
                    WANTS_TO_MAKE_UP.format(mediator=mediator.name),
                )
            except Exception as e:
                log.warning("making peace failed: %s", type(e).__name__)
                await self._db.write(_tried, "favor_make_ups", key, now)
                return
            await self._store.add_photo_reach_out(conversation.id, said, None)
            await self._db.write(_mark, "favor_make_ups", "target_told = 1", key)
        conversation = await self._current(user_id, mediator_id)
        if conversation is None or await self._store.ended(conversation.id) is not None:
            await self._db.write(_mark, "favor_make_ups", "done = 1", key)
            return
        if self._busy(conversation):
            return
        try:
            said, _, _ = await self._say(
                conversation, MADE_UP_REPORT_RULES.format(target=target.name), WANTS_TO_REPORT
            )
        except Exception as e:
            log.warning("telling of the peace made failed: %s", type(e).__name__)
            await self._db.write(_tried, "favor_make_ups", key, now)
            return
        await self._store.add_photo_reach_out(conversation.id, said, None)
        await self._db.write(_mark, "favor_make_ups", "done = 1", key)

    async def _tell_party(self, row: sqlite3.Row, now: datetime) -> None:
        user_id, helper_id = row["user_id"], row["helper_id"]
        key = (user_id, row["day"])
        guests: list[str] = [g for g in json.loads(row["guests"]) if g in self._characters]
        helper = self._characters.get(helper_id)
        names = "、".join(self._characters[g].name for g in guests)
        if helper is None:
            await self._db.write(_mark, "favor_parties", "helper_told = 1, done = 1", key)
            return
        if not row["helper_told"]:
            conversation = await self._current(user_id, helper_id)
            if conversation is not None and await self._store.ended(conversation.id) is None:
                if self._busy(conversation):
                    return
                if not await self._party_photo(conversation, row, guests, names, now):
                    return
            await self._db.write(_mark, "favor_parties", "helper_told = 1", key)
            for character_id in [helper_id, *guests]:
                await self._noted(user_id, character_id, f"和大家一起{row['what']}", now)
            together = [helper_id, *guests]
            for i, a in enumerate(together):
                for b in together[i + 1:]:
                    await self._were_together(user_id, a, b, f"一起{row['what']}", now)
        honoree_id = row["honoree_id"]
        if honoree_id in self._characters:
            conversation = await self._current(user_id, honoree_id)
            if conversation is not None and await self._store.ended(conversation.id) is None:
                if self._busy(conversation):
                    return
                others = "、".join(
                    self._characters[c].name for c in [helper_id, *guests] if c != honoree_id
                )
                try:
                    said, _, _ = await self._say(
                        conversation,
                        PARTY_THANKS_RULES.format(helper=helper.name, what=row["what"], names=others),
                        WANTS_TO_THANK,
                    )
                except Exception as e:
                    log.warning("thanking for a party failed: %s", type(e).__name__)
                    await self._db.write(_tried, "favor_parties", key, now)
                    return
                await self._store.add_photo_reach_out(conversation.id, said, None)
                zone = await self._zone(user_id)
                tomorrow = date.fromisoformat(row["day"]) + timedelta(days=1)
                await self._keepsakes.favor_done(
                    user_id, honoree_id, f"party:{row['day']}", "party", row["what"],
                    datetime.combine(tomorrow, THANKS_NEXT_DAY, zone)
                    + timedelta(minutes=self._random(120)),
                )
        await self._db.write(_mark, "favor_parties", "done = 1", key)

    async def _party_photo(
        self, conversation: Conversation, row: sqlite3.Row, guests: list[str], names: str,
        now: datetime,
    ) -> bool:
        """The one who helped tells of the party, with a photo of them all where she can:
        whether she has."""
        user_id = conversation.user_id
        helper = self._characters[conversation.character_id]
        situation = AFTER_PARTY_SITUATION.format(what=row["what"], names=names)
        talk = "今天怎么样、最开心的一刻、想对制作人说的话。"
        in_photo = [
            self._characters[g] for g in guests
            if self._characters[g].photo_tag is not None
            and self._characters[g].photo_appearance is not None
        ][:IN_PHOTO]
        with_photo = (
            self._config.image_generator() is not None
            and await self._features.on(user_id, "photos")
            and helper.photo_tag is not None and helper.photo_appearance is not None
        )
        if with_photo:
            rules = photo_rules(
                "聚会的合照", situation + "你想发一张今天的合照给制作人，再聊聊今天。", talk,
                others=TRIP_PHOTO_OTHERS if in_photo else "", you=WITH_YOU,
                nobody=(
                    "照片里只有你、今天来的她们和制作人，没有别的人。" if in_photo
                    else "照片里只有你，或者你和制作人，没有别的人。"
                ),
            )
        else:
            rules = (
                f"# 聚会之后\n{situation}你想聊聊今天。\n- 用 <ja><zh> 写一到三段：{talk}\n"
                "- 不发照片，也不说要给制作人打电话。"
            )
        wants = WANTS_TO_TELL_OF_PARTY.format(what=row["what"])
        try:
            said, tag, _ = await self._say(conversation, rules, wants, with_photo)
        except Exception as e:
            log.warning("telling of a party failed: %s", type(e).__name__)
            await self._db.write(_tried, "favor_parties", (user_id, row["day"]), now)
            return False
        if tag is None:
            await self._store.add_photo_reach_out(conversation.id, said, None)
        else:
            await self._trip_photos.send_with_photo(
                conversation, said, tag, in_photo, producer_there=True
            )
        return True

    # Kept.

    def _made_up(
        self, db: sqlite3.Connection, user_id: str, target_id: str, mediator_id: str,
        now: datetime, key: tuple,
    ) -> None:
        """She made peace for the producer with `target_id`: her affinity brought back, who
        did it kept for her prompt, the favor marked made; all in one."""
        self._affinities.reset(db, user_id, [target_id])
        db.execute(
            "INSERT INTO favor_ties (user_id, character_id, made_up_by, made_up_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (user_id, character_id) DO UPDATE SET"
            " made_up_by = excluded.made_up_by, made_up_at = excluded.made_up_at",
            (user_id, target_id, mediator_id, now.astimezone(UTC).isoformat()),
        )
        _mark(db, "favor_make_ups", "made = 1", key)

    def on_together(self, listener: Callable[[str, str, str, str, datetime], Awaitable[None]]) -> None:
        """Have `listener` told of two of them doing something together for the producer (the
        user, the one, the other, what, when): making up through her, a party (GLOSSARY.md
        「关系表」)."""
        self._together.append(listener)

    async def _were_together(self, user_id: str, a: str, b: str, what: str, at: datetime) -> None:
        for listener in self._together:
            try:
                await listener(user_id, a, b, what, at)
            except Exception:
                log.exception("telling of two of them together failed")

    async def _noted(self, user_id: str, character_id: str, what: str, at: datetime) -> None:
        if self._moment is None:
            return
        try:
            await self._moment(user_id, character_id, what, at)
        except Exception:
            log.exception("noting a favor for the diary failed")

    def _busy(self, conversation: Conversation) -> bool:
        return self._store.is_taking_turn(conversation.id) or self._in_call(conversation.id)

    async def _current(self, user_id: str, character_id: str) -> Conversation | None:
        return next(
            (
                c for c, _, _ in await self._store.latest_conversations(user_id)
                if c.character_id == character_id
            ),
            None,
        )

    async def _zone(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(effective_timezone(await self._store.get_profile(user_id), None))


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(favor_ties)")}
    for column in ("declined", "declined_at"):
        if column not in have:
            db.execute(f"ALTER TABLE favor_ties ADD COLUMN {column} TEXT")


def _tie_row(db: sqlite3.Connection, user_id: str, character_id: str) -> sqlite3.Row | None:
    return by_name(db).execute(
        "SELECT * FROM favor_ties WHERE user_id = ? AND character_id = ?",
        (user_id, character_id),
    ).fetchone()


def _overview(
    db: sqlite3.Connection, user_id: str
) -> tuple[list[sqlite3.Row], list[sqlite3.Row], list[sqlite3.Row]]:
    named = by_name(db)
    ties = named.execute(
        "SELECT character_id, address, bond FROM favor_ties WHERE user_id = ?"
        " AND (address IS NOT NULL OR bond IS NOT NULL)",
        (user_id,),
    ).fetchall()
    wakes = named.execute(
        "SELECT character_id, MIN(due_at) AS next, COUNT(*) AS left FROM favor_wakes"
        " WHERE user_id = ? AND done = 0 GROUP BY character_id",
        (user_id,),
    ).fetchall()
    return ties, wakes, _parties_of(db, user_id)


def _making_up(db: sqlite3.Connection, user_id: str, target_id: str) -> bool:
    return db.execute(
        "SELECT 1 FROM favor_make_ups WHERE user_id = ? AND target_id = ? AND made = 0",
        (user_id, target_id),
    ).fetchone() is not None


def _plan_make_up(
    db: sqlite3.Connection, user_id: str, mediator_id: str, target_id: str, gifts: str,
    due: datetime, reaches: bool,
) -> None:
    db.execute(
        "INSERT INTO favor_make_ups (user_id, mediator_id, target_id, gift, due_at, reaches)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, mediator_id, target_id, gifts, due.astimezone(UTC).isoformat(), int(reaches)),
    )


def _party_on(db: sqlite3.Connection, user_id: str, day: date) -> bool:
    return db.execute(
        "SELECT 1 FROM favor_parties WHERE user_id = ? AND day = ?", (user_id, day.isoformat())
    ).fetchone() is not None


def _plan_party(
    db: sqlite3.Connection, user_id: str, day: date, helper_id: str, guests: list[str],
    what: str, honoree_id: str | None, due: datetime, today: date,
) -> bool:
    """Plan the party; False if there is one that day already."""
    return db.execute(
        "INSERT OR IGNORE INTO favor_parties (user_id, day, helper_id, guests, what,"
        " honoree_id, due_at, created_on) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, day.isoformat(), helper_id, json.dumps(guests), what, honoree_id,
         due.astimezone(UTC).isoformat(), today.isoformat()),
    ).rowcount > 0


def _set_tie(
    db: sqlite3.Connection, user_id: str, character_id: str, field: Literal["address", "bond"],
    value: str | None, at: datetime | None = None,
) -> None:
    db.execute(
        "INSERT INTO favor_ties (user_id, character_id) VALUES (?, ?)"
        " ON CONFLICT (user_id, character_id) DO NOTHING",
        (user_id, character_id),
    )
    db.execute(
        f"UPDATE favor_ties SET {field} = ? WHERE user_id = ? AND character_id = ?",
        (value, user_id, character_id),
    )
    if field == "address":
        db.execute(
            "UPDATE favor_ties SET declined = NULL, declined_at = NULL"
            " WHERE user_id = ? AND character_id = ?",
            (user_id, character_id),
        )
    if at is not None and field == "bond":
        db.execute(
            "UPDATE favor_ties SET bonded_at = ? WHERE user_id = ? AND character_id = ?",
            (at.astimezone(UTC).isoformat(), user_id, character_id),
        )


def _declined(
    db: sqlite3.Connection, user_id: str, character_id: str, address: str, now: datetime
) -> None:
    db.execute(
        "INSERT INTO favor_ties (user_id, character_id, declined, declined_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT (user_id, character_id) DO UPDATE SET"
        " declined = excluded.declined, declined_at = excluded.declined_at",
        (user_id, character_id, address, now.astimezone(UTC).isoformat()),
    )


def _plan_wakes(
    db: sqlite3.Connection, user_id: str, character_id: str, mornings: Sequence[datetime]
) -> None:
    # Asked again, the mornings asked before are let go.
    db.execute(
        "DELETE FROM favor_wakes WHERE user_id = ? AND character_id = ? AND done = 0",
        (user_id, character_id),
    )
    for nth, at in enumerate(mornings, 1):
        db.execute(
            "INSERT OR IGNORE INTO favor_wakes (user_id, character_id, due_at, nth, of_days)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, character_id, at.astimezone(UTC).isoformat(), nth, len(mornings)),
        )


def _parties_of(db: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
    return by_name(db).execute(
        "SELECT * FROM favor_parties WHERE user_id = ? ORDER BY day", (user_id,)
    ).fetchall()


_KEYS = {
    "favor_wakes": "user_id = ? AND character_id = ? AND due_at = ?",
    "favor_make_ups": "id = ?",
    "favor_parties": "user_id = ? AND day = ?",
}


def _due(db: sqlite3.Connection, table: str, now: datetime) -> list[sqlite3.Row]:
    rows = by_name(db).execute(
        f"SELECT * FROM {table} WHERE done = 0 AND tries < ? AND due_at <= ?",
        (TRIES, now.astimezone(UTC).isoformat()),
    ).fetchall()
    return [
        r for r in rows
        if r["last_at"] is None or now - datetime.fromisoformat(r["last_at"]) >= RETRY_AFTER
    ]


def _mark(db: sqlite3.Connection, table: str, change: str, key: tuple) -> None:
    db.execute(f"UPDATE {table} SET {change} WHERE {_KEYS[table]}", key)


def _tried(db: sqlite3.Connection, table: str, key: tuple, now: datetime) -> None:
    db.execute(
        f"UPDATE {table} SET tries = tries + 1, last_at = ? WHERE {_KEYS[table]}",
        (now.astimezone(UTC).isoformat(), *key),
    )
