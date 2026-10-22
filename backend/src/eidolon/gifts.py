"""Gifts (GLOSSARY.md「礼物」「抽奖」「重置卡」): each user's draw chances, what they have drawn
and their reset cards, the one way into them for the gifts page and the admin console.

Draw chances are given one a day (`affinity.DRAWS_PER_DAY`) as the day begins, at midnight
Beijing time wherever the user is (`affinity.day_of`), kept up to `affinity.MAX_DRAWS`.
Nothing gives them on a schedule: whoever reads them first gives what the days since they
were last given have brought. A user without any yet is given one that day, new or not.

Gifts are open where affinity is (`LiveConfig.affinity_on`): the deployment has the judge
model and the switch「好感度和礼物」is on. Closed, what each user has is kept and read all
the same, but no chances are given; and the days it was closed give none once it opens
again, which gives one that day. So when it last opened is kept (`notice`), as the backend
starts and as the switch changes. That is no one's; what each user has is theirs, deleted
with them (`database.USER_DATA`).

An admin may give every user there is a reset card at once (`grant_reset_cards`), banned
ones and admins too, but no one user anything on their own. Each grant is kept, when and
how many, saying nothing of who granted it or to whom: that too is no one's.

Each draw uses a chance and gives a reset card or a gift, as likely as `affinity.DRAW_ODDS`
says, any of the rarity's gifts not retired as likely as another: nothing is guaranteed,
and whom a gift is liked by plays no part. Drawn gifts stack in the backpack, which keeps
them retired since or not. The numbers come from the random source the backend is built
with (`randomness.Random`). Drawing calls no model and leaves no activity record.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from itertools import accumulate
from pathlib import Path

from eidolon.affinity import (
    BELOW_TRUST,
    DRAW_ODDS,
    DRAWS_PER_DAY,
    MAX_DRAWS,
    Affinities,
    Change,
    Status,
    day_of,
    next_day,
)
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.database import connect
from eidolon.gift_catalog import Gift
from eidolon.promises import Promises
from eidolon.randomness import Random
from eidolon.store import Conversation, Store

log = logging.getLogger(__name__)

_SCHEMA = """
-- One row per user given any draw chances: how many they have, the day they were last given
-- on (`affinity.day_of`), and how many reset cards they have.
CREATE TABLE IF NOT EXISTS gift_balances (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    draws INTEGER NOT NULL,
    given_on TEXT NOT NULL,
    reset_cards INTEGER NOT NULL DEFAULT 0
);
-- One row: whether gifts were open when last noticed, and when they last opened (none if
-- never noticed opening).
CREATE TABLE IF NOT EXISTS gifts_opened (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    open INTEGER NOT NULL,
    opened_at TEXT
);
-- How many of each gift each user has; a row of none is as good as no row.
CREATE TABLE IF NOT EXISTS gift_backpacks (
    user_id TEXT NOT NULL REFERENCES users(id),
    gift_id TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (user_id, gift_id)
);
-- One row each time an admin gave every user a reset card: when, and how many were given.
CREATE TABLE IF NOT EXISTS reset_card_grants (
    granted_at TEXT NOT NULL,
    count INTEGER NOT NULL
);
"""

RESET_CARD = "reset_card"
"""What a draw gives that is not a gift."""


class GiftsClosed(Exception):
    """Gifts are not open: what the user has can be read, but nothing done with it."""

    def __init__(self) -> None:
        super().__init__("礼物暂时没有开放")


class NoDraws(Exception):
    """The user has no draw chances left."""

    def __init__(self) -> None:
        super().__init__("没有抽奖次数了")


class NoneLeft(Exception):
    """The user has none of the gift to give."""

    def __init__(self) -> None:
        super().__init__("已经没有了")


class NoResetCards(Exception):
    """The user has no reset cards left."""

    def __init__(self) -> None:
        super().__init__("没有重置卡了")


class NoOneToReset(Exception):
    """No character is below trust with the user: a reset card would bring no one back."""

    def __init__(self) -> None:
        super().__init__("现在没有低于信任的角色")


class NoSuchCharacter(LookupError):
    """There is no such character to give to."""

    def __init__(self) -> None:
        super().__init__("角色不存在")


@dataclass(frozen=True)
class Given:
    """What giving a gift did: the conversation its record is in, how it changed her
    affinity (None for no change to show), her affinity now, and how many of the gift are
    left."""

    conversation_id: str
    change: Change | None
    affinity: Status
    left: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "conversation_id": self.conversation_id,
            "change": self.change and asdict(self.change),
            "affinity": {"cooling": self.affinity.cooling} | self.affinity.shown(),
            "left": self.left,
        }


@dataclass(frozen=True)
class Drawn:
    """What drawing gave, in the order drawn (a gift's identifier each, or `RESET_CARD`),
    and how many chances are left."""

    drawn: list[str]
    draws: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "drawn": [
                {"kind": "reset_card"} if d == RESET_CARD else {"kind": "gift", "gift_id": d}
                for d in self.drawn
            ],
            "draws": self.draws,
        }


@dataclass(frozen=True)
class GiftState:
    """What the user has, as the gifts page shows it, and whether gifts are open."""

    open: bool
    draws: int
    backpack: list[tuple[str, int]]
    """Each gift the user has, in catalog order, and how many."""
    reset_cards: int
    next_draw_at: datetime | None
    """When the next draw chance is given, kept up to the most or not: none while closed."""
    characters: list[tuple[str, Status]]
    """Each character's affinity with the user, for choosing whom to give to and telling
    whom a reset card would bring back: none while closed, as affinity shows nothing then."""

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "open": self.open,
            "draws": self.draws,
            "max_draws": MAX_DRAWS,
            "backpack": [{"gift_id": g, "count": n} for g, n in self.backpack],
            "reset_cards": self.reset_cards,
            "next_draw_at": self.next_draw_at and self.next_draw_at.isoformat(),
            "resettable": [
                {"id": c, "tier": status.tier, "cooling": status.cooling}
                for c, status in self.characters
                if status.tier in BELOW_TRUST
            ],
            "characters": [
                {"id": c, "cooling": status.cooling} | status.shown()
                for c, status in self.characters
            ],
        }


@dataclass(frozen=True)
class Reset:
    """Whom using a reset card brought back, and how many cards are left."""

    brought_back: list[str]
    reset_cards: int

    def shown(self) -> dict:
        """As the browser is told it."""
        return asdict(self)


@dataclass(frozen=True)
class Grant:
    """A time every user was given a reset card: when, and how many were given."""

    granted_at: datetime
    count: int

    def shown(self) -> dict:
        """As the admin console is told it."""
        return {"granted_at": self.granted_at.isoformat(), "count": self.count}


class Gifts:
    """Every user's draw chances, backpack and reset cards, kept in the database at `path`,
    next to the users they belong to."""

    def __init__(
        self,
        path: Path,
        clock: Clock,
        is_open: Callable[[], bool],
        affinities: Affinities,
        characters: dict[str, Character],
        catalog: dict[str, Gift],
        random: Random,
        store: Store,
        promises: Promises | None = None,
    ) -> None:
        """`is_open` tells whether gifts are open now (the live configuration's affinity
        switch); draws take their numbers from `random`; gift records are kept in `store`, and
        a gift named in a promise to her fulfils it (`promises`). Notices whether they have
        opened since last noticed."""
        self._db = connect(path)
        self._store = store
        self._lock = threading.Lock()
        self._clock = clock
        self._is_open = is_open
        self._affinities = affinities
        self._characters = characters
        self._catalog = catalog
        self._random = random
        self._promises = promises
        self._given: list[Callable[[str, str, str, bool, Change | None, datetime], None]] = []
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
        self.notice()

    def notice(self) -> None:
        """Keep when gifts opened, if they were closed when last noticed and are open now:
        as the backend starts and whenever the configuration changes."""
        is_open = self._is_open()
        with self._lock, self._db:
            row = self._db.execute("SELECT open FROM gifts_opened WHERE id = 1").fetchone()
            if row is not None and bool(row[0]) == is_open:
                return
            if is_open:
                self._db.execute(
                    "INSERT OR REPLACE INTO gifts_opened (id, open, opened_at) VALUES (1, 1, ?)",
                    (self._clock().isoformat(),),
                )
            else:
                self._db.execute(
                    "INSERT INTO gifts_opened (id, open) VALUES (1, 0)"
                    " ON CONFLICT (id) DO UPDATE SET open = 0"
                )

    def state(self, user_id: str) -> GiftState:
        """What the user has now, having been given the draw chances due today, if gifts are
        open. Notices first whether they have opened, as the switch may have just been
        turned on and not yet noticed."""
        self.notice()
        is_open = self._is_open()
        now = self._clock()
        with self._lock, self._db:
            if is_open:
                self._give(user_id, day_of(now))
            row = self._db.execute(
                "SELECT draws, reset_cards FROM gift_balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            held = dict(
                self._db.execute(
                    "SELECT gift_id, count FROM gift_backpacks WHERE user_id = ? AND count > 0",
                    (user_id,),
                ).fetchall()
            )
        draws, reset_cards = row or (0, 0)
        backpack = [(g, held[g]) for g in self._catalog if g in held]
        statuses = (
            (character_id, self._affinities.status(user_id, character_id))
            for character_id in (self._characters if is_open else ())
        )
        characters = [(c, status) for c, status in statuses if status is not None]
        next_draw_at = next_day(now) if is_open else None
        return GiftState(is_open, draws, backpack, reset_cards, next_draw_at, characters)

    def draw(self, user_id: str, *, all: bool = False) -> Drawn:
        """Draw once, or as many times as the user has chances if `all`, having been given
        the chances due today; in one go, so that two drawing at once draw no more than
        there are chances. Raises GiftsClosed if gifts are not open, NoDraws if there are
        no chances to draw."""
        self.notice()
        if not self._is_open():
            raise GiftsClosed()
        with self._lock, self._db:
            self._give(user_id, day_of(self._clock()))
            (draws,) = self._db.execute(
                "SELECT draws FROM gift_balances WHERE user_id = ?", (user_id,)
            ).fetchone()
            if draws == 0:
                raise NoDraws()
            drawn = [self._draw_one() for _ in range(draws if all else 1)]
            for d in drawn:
                if d == RESET_CARD:
                    self._db.execute(
                        "UPDATE gift_balances SET reset_cards = reset_cards + 1"
                        " WHERE user_id = ?",
                        (user_id,),
                    )
                else:
                    self._db.execute(
                        "INSERT INTO gift_backpacks (user_id, gift_id, count) VALUES (?, ?, 1)"
                        " ON CONFLICT (user_id, gift_id) DO UPDATE SET count = count + 1",
                        (user_id, d),
                    )
            left = draws - len(drawn)
            self._db.execute(
                "UPDATE gift_balances SET draws = ? WHERE user_id = ?", (left, user_id)
            )
        return Drawn(drawn, left)

    def give(self, user_id: str, gift_id: str, character_id: str) -> Given:
        """Give the character one of the gift; its record goes where `Store.add_gift`
        places it. The one taken is put back
        should recording it fail, so two giving the last one at once give it once. Raises
        GiftsClosed if gifts are not open, NoSuchCharacter for no such character, NoneLeft
        if the user has none of it."""
        self.notice()
        if not self._is_open():
            raise GiftsClosed()
        character = self._characters.get(character_id)
        if character is None:
            raise NoSuchCharacter()
        gift = self._catalog.get(gift_id)
        if gift is None:
            raise NoneLeft()
        with self._lock, self._db:
            taken = self._db.execute(
                "UPDATE gift_backpacks SET count = count - 1"
                " WHERE user_id = ? AND gift_id = ? AND count > 0 RETURNING count",
                (user_id, gift_id),
            ).fetchone()
        if taken is None:
            raise NoneLeft()
        liked = gift_id in character.liked_gifts

        def record(change: Change | None) -> Conversation:
            return self._store.add_gift(
                user_id, character_id,
                gift_id=gift_id, liked=liked, affinity=change,
            )

        try:
            (conversation, change), affinity = self._affinities.receive_gift(
                user_id, character_id, gift.rarity, liked,
                lambda change: (record(change), change),
            )
        except BaseException:
            with self._lock, self._db:
                self._db.execute(
                    "UPDATE gift_backpacks SET count = count + 1 WHERE user_id = ? AND gift_id = ?",
                    (user_id, gift_id),
                )
            raise
        if self._promises is not None:
            # 约定里写着这件礼物（例如「给她一块布丁」），送了就算是兑现了
            self._promises.by_gift(user_id, character_id, gift.name)
        now = self._clock()
        for given in self._given:
            try:
                given(user_id, character_id, gift.name, liked, change, now)
            except Exception:
                log.exception("telling of a gift given failed")
        return Given(conversation.id, change, affinity, taken[0])

    def on_given(
        self, listener: Callable[[str, str, str, bool, Change | None, datetime], None]
    ) -> None:
        """Have `listener` told each gift given as it is recorded: the user, the character,
        the gift's name, whether she liked it, how her affinity changed (None if it did not),
        and when."""
        self._given.append(listener)

    def use_reset_card(self, user_id: str) -> Reset:
        """Use one of the user's reset cards, bringing every character below trust with them
        now back to it (`Affinities.reset`). It leaves no trace: no gift record, no
        conversation event. The card taken is put back should no one be below trust by now,
        or bringing them back fail, so two using the last card at once use it once. Raises
        GiftsClosed if gifts are not open, NoResetCards if the user has none, NoOneToReset
        if no one is below trust."""
        self.notice()
        if not self._is_open():
            raise GiftsClosed()
        with self._lock, self._db:
            taken = self._db.execute(
                "UPDATE gift_balances SET reset_cards = reset_cards - 1"
                " WHERE user_id = ? AND reset_cards > 0 RETURNING reset_cards",
                (user_id,),
            ).fetchone()
        if taken is None:
            raise NoResetCards()
        try:
            brought_back = self._affinities.reset(user_id, self._characters)
            if not brought_back:
                raise NoOneToReset()
        except BaseException:
            with self._lock, self._db:
                self._db.execute(
                    "UPDATE gift_balances SET reset_cards = reset_cards + 1 WHERE user_id = ?",
                    (user_id,),
                )
            raise
        return Reset(brought_back, taken[0])

    def grant_reset_cards(self) -> Grant:
        """Give every user there is now a reset card, banned ones and admins too, all at
        once, so that whoever signs up meanwhile gets none; one who has had no draw chances
        yet is given the new user's one too, that day. Raises GiftsClosed if gifts are not
        open."""
        self.notice()
        if not self._is_open():
            raise GiftsClosed()
        now = self._clock()
        with self._lock, self._db:
            # Writing first holds the database for writing until done: no one signs up in
            # between.
            given = self._db.execute(
                "UPDATE gift_balances SET reset_cards = reset_cards + 1"
            ).rowcount
            unbalanced = self._db.execute(
                "SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM gift_balances)"
            ).fetchall()
            for (user_id,) in unbalanced:
                self._start(user_id, day_of(now), reset_cards=1)
            grant = Grant(now, given + len(unbalanced))
            self._db.execute(
                "INSERT INTO reset_card_grants (granted_at, count) VALUES (?, ?)",
                (now.isoformat(), grant.count),
            )
        return grant

    def last_grant(self) -> Grant | None:
        """The latest time every user was given a reset card; None if never."""
        with self._lock:
            row = self._db.execute(
                "SELECT granted_at, count FROM reset_card_grants"
                " ORDER BY granted_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return row and Grant(datetime.fromisoformat(row[0]), row[1])

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _give(self, user_id: str, today: date) -> None:
        """Give the user the draw chances due by `today`: one if they have none yet; else
        one for each day since they were last given, counted from the day before gifts
        last opened if they opened since, up to the most kept. Called holding the lock, in
        a transaction."""
        row = self._db.execute(
            "SELECT draws, given_on FROM gift_balances WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            self._start(user_id, today)
            return
        draws, given_on = row[0], date.fromisoformat(row[1])
        if today <= given_on:
            return
        since = given_on
        opened = self._opened_on()
        if opened is not None and opened > given_on:
            since = opened - timedelta(days=1)
        due = (today - since).days * DRAWS_PER_DAY
        self._db.execute(
            "UPDATE gift_balances SET draws = ?, given_on = ? WHERE user_id = ?",
            (max(draws, min(draws + due, MAX_DRAWS)), today.isoformat(), user_id),
        )

    def _start(self, user_id: str, today: date, reset_cards: int = 0) -> None:
        """Give a user who has had no draw chances yet the new user's one, `today`, and
        `reset_cards`. Called holding the lock, in a transaction."""
        self._db.execute(
            "INSERT INTO gift_balances (user_id, draws, given_on, reset_cards) VALUES (?, ?, ?, ?)",
            (user_id, DRAWS_PER_DAY, today.isoformat(), reset_cards),
        )

    def _draw_one(self) -> str:
        """What one draw gives: `RESET_CARD`, or the identifier of a gift not retired."""
        number = self._random(100)
        kind = next(
            k for k, upto in zip(DRAW_ODDS, accumulate(DRAW_ODDS.values())) if number < upto
        )
        if kind == RESET_CARD:
            return RESET_CARD
        pool = [g.id for g in self._catalog.values() if g.rarity == kind and not g.retired]
        return pool[self._random(len(pool))]

    def _opened_on(self) -> date | None:
        """The day gifts last opened on (`affinity.day_of`); None if never noticed
        opening."""
        row = self._db.execute("SELECT opened_at FROM gifts_opened WHERE id = 1").fetchone()
        if row is None or row[0] is None:
            return None
        return day_of(datetime.fromisoformat(row[0]))
