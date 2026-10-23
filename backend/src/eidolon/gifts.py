"""Gifts (GLOSSARY.md「礼物」「抽奖」「重置卡」): each user's draw chances, what they have drawn
and their reset cards, the one way into them for the gifts page and the admin console, but
for drawing and giving, which count towards the daily tasks and the achievements and so are
kept with them (`happenings.Happenings.draw`, `Happenings.give`).

Draw chances are given one a day (`affinity.DRAWS_PER_DAY`) as the day begins, at midnight
in the producer's own time zone (the profile's, Beijing's without one), kept up to
`affinity.MAX_DRAWS`.
Nothing gives them on a schedule: whoever reads them first gives what the days since they
were last given have brought. A user without any yet is given one that day, new or not.

Gifts are open where affinity is (`LiveConfig.affinity_on`): the deployment has the judge
model and the switch「好感度和礼物」is on. Closed, what each user has is kept and read all
the same, but no chances are given; and the days it was closed give none once it opens
again, which gives one that day. So when it last opened is kept (`notice`), as the backend
starts and as the switch changes. That is no one's; what each user has is theirs, deleted
with them (`database.USER_DATA`).

A user is rewarded for what they do (`reward`), as daily tasks are done and achievements'
stages unlocked (`happenings`): each reward given once by its key, its draw chances over and
above the most kept of those given each day, and its reset cards, never anything of her
affinity. As the achievements launch, everyone is rewarded for what they did before, open
or not (`reward_everyone`).

An admin may give every user there is a reset card at once (`grant_reset_cards`), banned
ones and admins too, but no one user anything on their own. Each grant is kept, when and
how many, saying nothing of who granted it or to whom: that too is no one's.

Each draw uses a chance and gives a reset card, a gift or a ticket (GLOSSARY.md「体验券」), as
likely as `affinity.DRAW_ODDS` says, any of the rarity's gifts not retired as likely as
another, any of the kinds of ticket the user can use now (`ticket_kinds`) as likely as
another, a common gift where they can use none; whom a gift is liked by plays no part. The
draw `affinity.PITY` draws after the last rare gift is one (GLOSSARY.md「保底」): how many
draws since is kept with the chances. Tickets stack, a count of each kind, until used.
A gift named in a promise to her fulfils it, and each gift given is told those who keep
something of it (`on_given`), once written (`after_giving`). Drawn gifts stack in the backpack, which keeps
them retired since or not. The numbers come from the random source the backend is built
with (`randomness.Random`). Drawing calls no model and leaves no activity record.
"""

import logging
import sqlite3
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import accumulate
from zoneinfo import ZoneInfo

from eidolon.affinity import (
    BELOW_TRUST,
    DRAW_ODDS,
    DRAWS_PER_DAY,
    MAX_DRAWS,
    PITY,
    Affinities,
    Change,
    Status,
    day_of,
)
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.gift_catalog import Gift
from eidolon.producer import effective_timezone
from eidolon.promises import Promises
from eidolon.randomness import Random
from eidolon.store import add_gift, read_profile

log = logging.getLogger(__name__)

_SCHEMA = """
-- One row per user given any draw chances: how many they have, the day they were last given
-- on (`affinity.day_of`), and how many reset cards they have.
CREATE TABLE IF NOT EXISTS gift_balances (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    draws INTEGER NOT NULL,
    given_on TEXT NOT NULL,
    reset_cards INTEGER NOT NULL DEFAULT 0,
    since_rare INTEGER NOT NULL DEFAULT 0
);
-- How many of each kind of ticket each user has (GLOSSARY.md「体验券」).
CREATE TABLE IF NOT EXISTS gift_tickets (
    user_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (user_id, kind)
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
-- One row per reward each user was given, by its key, given once: how many draw chances and
-- reset cards it gave, and when.
CREATE TABLE IF NOT EXISTS gift_rewards (
    user_id TEXT NOT NULL REFERENCES users(id),
    key TEXT NOT NULL,
    draws INTEGER NOT NULL,
    reset_cards INTEGER NOT NULL,
    rewarded_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""

RESET_CARD = "reset_card"
"""What a draw gives that is not a gift."""

TICKET = "ticket:"
"""What a ticket of a kind is drawn as: `ticket:letter`."""


class NoTickets(Exception):
    """The user has no ticket of the kind left."""

    def __init__(self) -> None:
        super().__init__("没有这种体验券了")


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
                {"kind": "reset_card"} if d == RESET_CARD
                else {"kind": "ticket", "ticket": d.removeprefix(TICKET)} if d.startswith(TICKET)
                else {"kind": "gift", "gift_id": d}
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
    tickets: list[tuple[str, int]]
    """Each kind of ticket the user has, and how many."""
    to_rare: int
    """How many draws at most until a rare gift."""
    next_draw_at: datetime | None
    """When the next draw chance is given, kept up to the most or not: none while closed."""
    characters: list[tuple[str, Status]]
    """Each character's affinity with the user, for choosing whom to give to and telling
    whom a reset card would bring back: none while closed, as affinity shows nothing then."""
    rewarded_at: datetime | None
    """When the user was last rewarded (`Gifts.reward`); None if never."""

    def shown(self) -> dict:
        """As the browser is told it."""
        return {
            "open": self.open,
            "draws": self.draws,
            "max_draws": MAX_DRAWS,
            "backpack": [{"gift_id": g, "count": n} for g, n in self.backpack],
            "reset_cards": self.reset_cards,
            "tickets": [{"kind": k, "count": n} for k, n in self.tickets],
            "to_rare": self.to_rare,
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
            "rewarded_at": self.rewarded_at and self.rewarded_at.isoformat(),
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
class Reward:
    """What a user is given for something they did, once by its `key`: draw chances, over
    and above the most kept, and reset cards."""

    key: str
    draws: int
    reset_cards: int = 0


@dataclass(frozen=True)
class Grant:
    """A time every user was given a reset card: when, and how many were given."""

    granted_at: datetime
    count: int

    def shown(self) -> dict:
        """As the admin console is told it."""
        return {"granted_at": self.granted_at.isoformat(), "count": self.count}


class Gifts:
    """Every user's draw chances, backpack and reset cards, kept in the database, next to the
    users they belong to. Drawing, giving and rewarding are bodies of the write tasks they
    are part of (`Happenings`), given the task's connection, so that what they count towards
    is kept with them or not at all."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        is_open: Callable[[], bool],
        affinities: Affinities,
        characters: dict[str, Character],
        catalog: dict[str, Gift],
        random: Random,
        promises: Promises | None = None,
        ticket_kinds: Callable[[sqlite3.Connection, str], Sequence[str]] = lambda _db, _user: (),
    ) -> None:
        """`is_open` tells whether gifts are open now (the live configuration's affinity
        switch); draws take their numbers from `random`, and give tickets of the kinds
        `ticket_kinds` says the user can use; a gift named in a promise to her fulfils it
        (`promises`). Notices whether they have opened since last noticed."""
        self._db = db
        self._promises = promises
        self._ticket_kinds = ticket_kinds
        self._given: list[GivenListener] = []
        self._clock = clock
        self._is_open = is_open
        self._affinities = affinities
        self._characters = characters
        self._catalog = catalog
        self._random = random
        db.prepare(_open)
        db.prepare(self._notice, is_open())

    async def notice(self) -> None:
        """Keep when gifts opened, if they were closed when last noticed and are open now:
        as the backend starts and whenever the configuration changes. Read first, and
        written only when it changes."""
        is_open = self._is_open()
        if not await self._db.read(_noticed, is_open):
            await self._db.write(self._notice, is_open)

    async def refuse_unless_open(self) -> None:
        """Raise GiftsClosed unless gifts are open now, having noticed whether they are:
        before anything is done with what the user has."""
        await self.notice()
        if not self._is_open():
            raise GiftsClosed()

    async def state(self, user_id: str) -> GiftState:
        """What the user has now, having been given the draw chances due today, if gifts are
        open. Notices first whether they have opened, as the switch may have just been
        turned on and not yet noticed. Read first, and written only when chances are due."""
        await self.notice()
        is_open = self._is_open()
        now = self._clock()
        found = await self._db.read(self._state, user_id, is_open, now, False)
        if found is None:
            found = await self._db.write(self._state, user_id, is_open, now, True)
        assert found is not None
        return found

    def draw(self, db: sqlite3.Connection, user_id: str, *, all: bool = False) -> Drawn:
        """Draw once, or as many times as the user has chances if `all`, having been given
        the chances due today, through `db`, in the transaction what drawing counts towards
        is kept in (`Happenings.draw`), so that two drawing at once draw no more than there
        are chances. Gifts are to be open (`refuse_unless_open`). Raises NoDraws if there are
        no chances to draw."""
        _give(db, user_id, _user_day(db, user_id, self._clock()))
        draws, since_rare = db.execute(
            "SELECT draws, since_rare FROM gift_balances WHERE user_id = ?", (user_id,)
        ).fetchone()
        if draws == 0:
            raise NoDraws()
        kinds = list(self._ticket_kinds(db, user_id))
        drawn: list[str] = []
        for _ in range(draws if all else 1):
            d = self._draw_one(kinds, pity=since_rare >= PITY - 1)
            since_rare = 0 if self._is_rare(d) else since_rare + 1
            drawn.append(d)
        for d in drawn:
            if d.startswith(TICKET):
                db.execute(
                    "INSERT INTO gift_tickets (user_id, kind, count) VALUES (?, ?, 1)"
                    " ON CONFLICT (user_id, kind) DO UPDATE SET count = count + 1",
                    (user_id, d.removeprefix(TICKET)),
                )
            elif d == RESET_CARD:
                db.execute(
                    "UPDATE gift_balances SET reset_cards = reset_cards + 1 WHERE user_id = ?",
                    (user_id,),
                )
            else:
                db.execute(
                    "INSERT INTO gift_backpacks (user_id, gift_id, count) VALUES (?, ?, 1)"
                    " ON CONFLICT (user_id, gift_id) DO UPDATE SET count = count + 1",
                    (user_id, d),
                )
        left = draws - len(drawn)
        db.execute(
            "UPDATE gift_balances SET draws = ?, since_rare = ? WHERE user_id = ?",
            (left, since_rare, user_id),
        )
        return Drawn(drawn, left)

    def refuse_unknown(self, gift_id: str, character_id: str) -> None:
        """Raise NoSuchCharacter for no such character, NoneLeft for no such gift: before
        anything is written, as neither can be given."""
        if character_id not in self._characters:
            raise NoSuchCharacter()
        if gift_id not in self._catalog:
            raise NoneLeft()

    def give(
        self, db: sqlite3.Connection, user_id: str, gift_id: str, character_id: str
    ) -> Given:
        """Give the character one of the gift, through `db`, in the transaction what giving
        it counts towards is kept in (`Happenings.give`): taken from the backpack, raising
        her affinity, and recorded where `store.add_gift` places it, all or none of it, so
        two giving the last one at once give it once. Gifts are to be open
        (`refuse_unless_open`), and the gift and the character to be there
        (`refuse_unknown`). Raises NoneLeft if the user has none of it."""
        taken = db.execute(
            "UPDATE gift_backpacks SET count = count - 1"
            " WHERE user_id = ? AND gift_id = ? AND count > 0 RETURNING count",
            (user_id, gift_id),
        ).fetchone()
        if taken is None:
            raise NoneLeft()
        liked = gift_id in self._characters[character_id].liked_gifts
        change, affinity = self._affinities.receive_gift(
            db, user_id, character_id, self._catalog[gift_id].rarity, liked
        )
        conversation = add_gift(db, user_id, character_id, gift_id, liked, change)
        return Given(conversation.id, change, affinity, taken[0])

    def on_given(self, listener: "GivenListener") -> None:
        """Have `listener` told each gift given once it is recorded: the user, the character,
        the gift's name, whether she liked it, how her affinity changed (None if it did not),
        and when."""
        self._given.append(listener)

    async def after_giving(
        self, user_id: str, character_id: str, gift_id: str, given: Given
    ) -> None:
        """What comes of the gift `given` once it is written, on the event loop: a promise to
        her naming it is kept, those `on_given` has are told, and a door her affinity opened
        is told (`Affinities.scene_done`); each failing only logged."""
        gift = self._catalog[gift_id]
        liked = gift_id in self._characters[character_id].liked_gifts
        if self._promises is not None:
            # 约定里写着这件礼物（例如「给她一块布丁」），送了就算是兑现了
            try:
                await self._promises.by_gift(user_id, character_id, gift.name)
            except Exception:
                log.exception("keeping a promise by a gift failed")
        now = self._clock()
        for listener in self._given:
            try:
                await listener(user_id, character_id, gift.name, liked, given.change, now)
            except Exception:
                log.exception("telling of a gift given failed")
        await self._affinities.scene_done(user_id, character_id, given.affinity)

    # Tickets (GLOSSARY.md「体验券」).

    async def take_ticket(self, user_id: str, kind: str) -> int:
        """Take one of the user's tickets of `kind`, to use: how many are left. Raises
        GiftsClosed if gifts are not open, NoTickets if they have none."""
        await self.refuse_unless_open()
        return await self._db.write(take_ticket, user_id, kind)

    async def put_back_ticket(self, user_id: str, kind: str) -> None:
        """Give back a ticket taken whose use failed."""
        await self._db.write(put_back_ticket, user_id, kind)

    async def tickets(self, user_id: str, kind: str) -> int:
        """How many tickets of `kind` the user has."""
        return await self._db.read(_tickets, user_id, kind)

    async def add_liked_gift(self, user_id: str, character_id: str) -> str:
        """Put one of the gifts she likes in the user's backpack, any of them as likely as
        another, those not retired if any are: its identifier. Raises NoSuchCharacter for
        no such character, or one who likes none."""
        character = self._characters.get(character_id)
        liked = [g for g in (character.liked_gifts if character else ()) if g in self._catalog]
        if not liked:
            raise NoSuchCharacter()
        current = [g for g in liked if not self._catalog[g].retired] or liked
        gift_id = current[self._random(len(current))]
        await self._db.write(put_back_gifts, user_id, [gift_id])
        return gift_id

    async def take_gifts(self, user_id: str, gift_ids: Sequence[str]) -> None:
        """Take the gifts from the user's backpack, one for each time named, to use up on a
        favor (GLOSSARY.md「拜托」); all of them or none. Raises GiftsClosed if gifts are not
        open, NoneLeft if the user has not all of them."""
        await self.refuse_unless_open()
        await self._db.write(take_gifts, user_id, list(gift_ids))

    async def put_back_gifts(self, user_id: str, gift_ids: Sequence[str]) -> None:
        """Give back gifts taken for a favor that did not come about."""
        await self._db.write(put_back_gifts, user_id, list(gift_ids))

    async def use_reset_card(self, user_id: str) -> Reset:
        """Use one of the user's reset cards, bringing every character below trust with them
        now back to it (`Affinities.reset`), in one transaction: should no one be below
        trust by now, or bringing them back fail, the card is still theirs, so two using the
        last card at once use it once. It leaves no trace: no gift record, no conversation
        event. Raises GiftsClosed if gifts are not open, NoResetCards if the user has none,
        NoOneToReset if no one is below trust."""
        await self.refuse_unless_open()
        return await self._db.write(self._use_reset_card, user_id)

    def reward(
        self,
        db: sqlite3.Connection,
        user_id: str,
        due: Callable[[sqlite3.Connection, datetime], Iterable[Reward]],
    ) -> tuple[list[Reward], datetime]:
        """Give the user each reward `due` says is due that they have not been given yet by
        its key, having been given the draw chances due today; one with no draw chances yet
        is given the new user's one first. Gifts are to be open, as the one who rewards
        found them on the event loop; they are noticed open first. Through `db`, in the
        transaction what they are rewarded for is kept in, so that it is rewarded with it or
        not at all, and two rewarded at once for the same are rewarded once; `due` is
        called with the connection and now, the same now the draw chances are given by. The
        rewards given now, and when."""
        self._notice(db, True)
        now = self._clock()
        rewards = list(due(db, now))
        _give(db, user_id, _user_day(db, user_id, now))
        given = [r for r in rewards if _credit(db, user_id, r, now)]
        return given, now

    def reward_everyone(
        self,
        db: sqlite3.Connection,
        due: Callable[[sqlite3.Connection, datetime], Iterable[tuple[str, Reward]]],
    ) -> list[tuple[str, Reward]]:
        """Give each user each reward `due` says is due them that they have not been given
        yet by its key, open or not, as the achievements launch, through `db`: none of the
        draw chances due today, but the new user's one first to one with none yet. `due` is
        called as `reward`'s is. The rewards given, and to whom."""
        given = []
        now = self._clock()
        for user_id, r in list(due(db, now)):
            if not db.execute(
                "SELECT 1 FROM gift_balances WHERE user_id = ?", (user_id,)
            ).fetchone():
                _start(db, user_id, _user_day(db, user_id, now))
            if _credit(db, user_id, r, now):
                given.append((user_id, r))
        return given

    async def grant_reset_cards(self) -> Grant:
        """Give every user there is now a reset card, banned ones and admins too, all at
        once, so that whoever signs up meanwhile gets none; one who has had no draw chances
        yet is given the new user's one too, that day. Raises GiftsClosed if gifts are not
        open."""
        await self.refuse_unless_open()
        return await self._db.write(_grant_reset_cards, self._clock())

    async def last_grant(self) -> Grant | None:
        """The latest time every user was given a reset card; None if never."""
        return await self._db.read(_last_grant)

    def _notice(self, db: sqlite3.Connection, is_open: bool) -> None:
        """`notice`, gifts being open now or not as `is_open` says, through `db`."""
        if _noticed(db, is_open):
            return
        if is_open:
            db.execute(
                "INSERT OR REPLACE INTO gifts_opened (id, open, opened_at) VALUES (1, 1, ?)",
                (self._clock().isoformat(),),
            )
        else:
            db.execute(
                "INSERT INTO gifts_opened (id, open) VALUES (1, 0)"
                " ON CONFLICT (id) DO UPDATE SET open = 0"
            )

    def _state(
        self,
        db: sqlite3.Connection,
        user_id: str,
        is_open: bool,
        now: datetime,
        giving: bool,
    ) -> GiftState | None:
        """What the user has at `now`, read through `db`: giving the draw chances due if
        `giving`, else None if any are due."""
        zone = _zone(db, user_id)
        today = now.astimezone(zone).date()
        if is_open:
            if giving:
                _give(db, user_id, today)
            elif _due(db, user_id, today):
                return None
        row = db.execute(
            "SELECT draws, reset_cards, since_rare FROM gift_balances WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        tickets = db.execute(
            "SELECT kind, count FROM gift_tickets WHERE user_id = ? AND count > 0 ORDER BY kind",
            (user_id,),
        ).fetchall()
        held = dict(
            db.execute(
                "SELECT gift_id, count FROM gift_backpacks WHERE user_id = ? AND count > 0",
                (user_id,),
            ).fetchall()
        )
        draws, reset_cards, since_rare = row or (0, 0, 0)
        backpack = [(g, held[g]) for g in self._catalog if g in held]
        # Open where affinity is on: what is shown of her affinity is shown while open.
        characters = [
            (c, self._affinities.read_status(db, user_id, c))
            for c in (self._characters if is_open else ())
        ]
        next_draw_at = _next_local_day(now, zone) if is_open else None
        return GiftState(
            is_open, draws, backpack, reset_cards, [(k, n) for k, n in tickets],
            PITY - since_rare, next_draw_at, characters, _last_rewarded(db, user_id),
        )

    def _use_reset_card(self, db: sqlite3.Connection, user_id: str) -> Reset:
        taken = db.execute(
            "UPDATE gift_balances SET reset_cards = reset_cards - 1"
            " WHERE user_id = ? AND reset_cards > 0 RETURNING reset_cards",
            (user_id,),
        ).fetchone()
        if taken is None:
            raise NoResetCards()
        brought_back = self._affinities.reset(db, user_id, self._characters)
        if not brought_back:
            raise NoOneToReset()
        return Reset(brought_back, taken[0])

    def _draw_one(self, kinds: Sequence[str], pity: bool = False) -> str:
        """What one draw gives: `RESET_CARD`, a ticket of one of `kinds` (`ticket:<kind>`),
        or the identifier of a gift not retired; a rare one if `pity`."""
        if pity:
            kind: str = "rare"
        else:
            number = self._random(100)
            kind = next(
                k for k, upto in zip(DRAW_ODDS, accumulate(DRAW_ODDS.values())) if number < upto
            )
        if kind == RESET_CARD:
            return RESET_CARD
        if kind == "ticket":
            if kinds:
                return TICKET + kinds[self._random(len(kinds))]
            kind = "common"
        pool = [g.id for g in self._catalog.values() if g.rarity == kind and not g.retired]
        return pool[self._random(len(pool))]

    def _is_rare(self, drawn: str) -> bool:
        gift = self._catalog.get(drawn)
        return gift is not None and gift.rarity == "rare"


GivenListener = Callable[[str, str, str, bool, Change | None, datetime], Awaitable[None]]
"""Told of a gift given: the user, the character, the gift's name, whether she liked it, how
her affinity changed, and when."""


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(gift_balances)")}
    if "since_rare" not in columns:
        db.execute("ALTER TABLE gift_balances ADD COLUMN since_rare INTEGER NOT NULL DEFAULT 0")


def take_ticket(db: sqlite3.Connection, user_id: str, kind: str) -> int:
    """Take one of the user's tickets of `kind` through `db`: how many are left. Raises
    NoTickets if they have none."""
    taken = db.execute(
        "UPDATE gift_tickets SET count = count - 1"
        " WHERE user_id = ? AND kind = ? AND count > 0 RETURNING count",
        (user_id, kind),
    ).fetchone()
    if taken is None:
        raise NoTickets()
    return taken[0]


def put_back_ticket(db: sqlite3.Connection, user_id: str, kind: str) -> None:
    """Give the user back a ticket of `kind` through `db`."""
    db.execute(
        "INSERT INTO gift_tickets (user_id, kind, count) VALUES (?, ?, 1)"
        " ON CONFLICT (user_id, kind) DO UPDATE SET count = count + 1",
        (user_id, kind),
    )


def _tickets(db: sqlite3.Connection, user_id: str, kind: str) -> int:
    row = db.execute(
        "SELECT count FROM gift_tickets WHERE user_id = ? AND kind = ?", (user_id, kind)
    ).fetchone()
    return row[0] if row else 0


def take_gifts(db: sqlite3.Connection, user_id: str, gift_ids: Sequence[str]) -> None:
    """Take the gifts from the user's backpack through `db`, one for each time named; all of
    them or none. Raises NoneLeft if the user has not all of them."""
    wanted: dict[str, int] = {}
    for gift_id in gift_ids:
        wanted[gift_id] = wanted.get(gift_id, 0) + 1
    for gift_id, count in wanted.items():
        row = db.execute(
            "SELECT count FROM gift_backpacks WHERE user_id = ? AND gift_id = ?",
            (user_id, gift_id),
        ).fetchone()
        if row is None or row[0] < count:
            raise NoneLeft()
    for gift_id, count in wanted.items():
        db.execute(
            "UPDATE gift_backpacks SET count = count - ? WHERE user_id = ? AND gift_id = ?",
            (count, user_id, gift_id),
        )


def put_back_gifts(db: sqlite3.Connection, user_id: str, gift_ids: Sequence[str]) -> None:
    """Put the gifts in the user's backpack through `db`, one for each time named."""
    for gift_id in gift_ids:
        db.execute(
            "INSERT INTO gift_backpacks (user_id, gift_id, count) VALUES (?, ?, 1)"
            " ON CONFLICT (user_id, gift_id) DO UPDATE SET count = count + 1",
            (user_id, gift_id),
        )


def _zone(db: sqlite3.Connection, user_id: str) -> ZoneInfo:
    """The producer's time zone, read through `db`: the profile's, Beijing's without one."""
    return ZoneInfo(effective_timezone(read_profile(db, user_id), None))


def _user_day(db: sqlite3.Connection, user_id: str, now: datetime) -> date:
    """The producer's day at `now`, in their time zone."""
    return now.astimezone(_zone(db, user_id)).date()


def _next_local_day(now: datetime, zone: ZoneInfo) -> datetime:
    """When the producer's next day begins, in UTC."""
    begins = datetime.combine(now.astimezone(zone).date() + timedelta(days=1), time(), zone)
    return begins.astimezone(UTC)


def _noticed(db: sqlite3.Connection, is_open: bool) -> bool:
    """Whether gifts are as open now, as `is_open` says, as when last noticed, read through
    `db`."""
    row = db.execute("SELECT open FROM gifts_opened WHERE id = 1").fetchone()
    return row is not None and bool(row[0]) == is_open


def _credit(db: sqlite3.Connection, user_id: str, reward: Reward, now: datetime) -> bool:
    """Give the user, who has draw chances, `reward` `now` unless given it before by its key,
    through `db`: whether given now."""
    new = db.execute(
        "INSERT INTO gift_rewards"
        " (user_id, key, draws, reset_cards, rewarded_at) VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT (user_id, key) DO NOTHING",
        (user_id, reward.key, reward.draws, reward.reset_cards, now.isoformat()),
    ).rowcount
    if new:
        db.execute(
            "UPDATE gift_balances SET draws = draws + ?,"
            " reset_cards = reset_cards + ? WHERE user_id = ?",
            (reward.draws, reward.reset_cards, user_id),
        )
    return bool(new)


def _last_rewarded(db: sqlite3.Connection, user_id: str) -> datetime | None:
    """When the user was last rewarded; None if never."""
    (at,) = db.execute(
        "SELECT MAX(rewarded_at) FROM gift_rewards WHERE user_id = ?", (user_id,)
    ).fetchone()
    return at and datetime.fromisoformat(at)


def _grant_reset_cards(db: sqlite3.Connection, now: datetime) -> Grant:
    # Writing first holds the database for writing until done: no one signs up in between.
    given = db.execute("UPDATE gift_balances SET reset_cards = reset_cards + 1").rowcount
    unbalanced = db.execute(
        "SELECT id FROM users WHERE id NOT IN (SELECT user_id FROM gift_balances)"
    ).fetchall()
    for (user_id,) in unbalanced:
        _start(db, user_id, _user_day(db, user_id, now), reset_cards=1)
    grant = Grant(now, given + len(unbalanced))
    db.execute(
        "INSERT INTO reset_card_grants (granted_at, count) VALUES (?, ?)",
        (now.isoformat(), grant.count),
    )
    return grant


def _last_grant(db: sqlite3.Connection) -> Grant | None:
    row = db.execute(
        "SELECT granted_at, count FROM reset_card_grants"
        " ORDER BY granted_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return row and Grant(datetime.fromisoformat(row[0]), row[1])


def _due(db: sqlite3.Connection, user_id: str, today: date) -> bool:
    """Whether the user has draw chances due by `today` not given yet (`_give`)."""
    row = db.execute(
        "SELECT given_on FROM gift_balances WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row is None or today > date.fromisoformat(row[0])


def _give(db: sqlite3.Connection, user_id: str, today: date) -> None:
    """Give the user the draw chances due by `today`, through `db`: one if they have none
    yet; else one for each day since they were last given, counted from the day before
    gifts last opened if they opened since, up to the most kept."""
    row = db.execute(
        "SELECT draws, given_on FROM gift_balances WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        _start(db, user_id, today)
        return
    draws, given_on = row[0], date.fromisoformat(row[1])
    if today <= given_on:
        return
    since = given_on
    opened = _opened_on(db, today)
    if opened is not None and opened > given_on:
        since = opened - timedelta(days=1)
    due = (today - since).days * DRAWS_PER_DAY
    db.execute(
        "UPDATE gift_balances SET draws = ?, given_on = ? WHERE user_id = ?",
        (max(draws, min(draws + due, MAX_DRAWS)), today.isoformat(), user_id),
    )


def _start(db: sqlite3.Connection, user_id: str, today: date, reset_cards: int = 0) -> None:
    """Give a user who has had no draw chances yet the new user's one, `today`, and
    `reset_cards`, through `db`."""
    db.execute(
        "INSERT INTO gift_balances (user_id, draws, given_on, reset_cards) VALUES (?, ?, ?, ?)",
        (user_id, DRAWS_PER_DAY, today.isoformat(), reset_cards),
    )


def _opened_on(db: sqlite3.Connection, today: date) -> date | None:
    """The day gifts last opened on, in Beijing (`affinity.day_of`), at the latest `today`;
    None if never noticed opening."""
    row = db.execute("SELECT opened_at FROM gifts_opened WHERE id = 1").fetchone()
    if row is None or row[0] is None:
        return None
    return min(day_of(datetime.fromisoformat(row[0])), today)
