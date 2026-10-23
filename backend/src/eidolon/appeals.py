"""Appeals (GLOSSARY.md「我不是这个意思」): the judge model can take a turn the wrong way, and
the user may say they did not mean it so. Her latest reply, as it lowered her affinity, can
be appealed once: what the turn took off is given back, the turn no longer counts towards her
ending the conversation or hanging up, and as she replies next she is told the user said it
was a joke or a misunderstanding (`affinity.EXPLAINED`). A few a day (`APPEALS_PER_DAY`, every
character together, on the days of `affinity.day_of`); none in a cooldown, nor in a
conversation she ended, nor for a turn of a call, which keeps no reckoning to undo."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from eidolon.affinity import Affinities, Cooling, Status, day_of
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.store import read_appeal_target, read_appeals_made, record_appeal

APPEALS_PER_DAY = 3
"""How many appeals the user has a day, with every character together."""

Refusal = Literal["closed", "no_change", "not_latest", "appealed", "ended", "cooling", "none_left"]
"""Why an appeal is refused: affinity is off; the reply did not lower her affinity, or its turn
was taken in a call; it is no longer her latest message; it was appealed already; she ended the
conversation; she is in a cooldown; the user has none left today."""


class AppealRefused(Exception):
    def __init__(self, reason: Refusal) -> None:
        super().__init__(reason)
        self.reason: Refusal = reason


class NoSuchReply(Exception):
    """No reply of hers by that id in the user's conversations."""


@dataclass(frozen=True)
class Appealed:
    """Her affinity as the appeal left it, and how many appeals the user has left today."""

    affinity: Status
    left: int

    def shown(self) -> dict:
        return {"affinity": self.affinity.shown(), "left": self.left}


class Appeals:
    def __init__(self, db: Database, affinities: Affinities, clock: Clock) -> None:
        self._db = db
        self._affinities = affinities
        self._clock = clock

    async def left(self, user_id: str) -> int:
        """How many appeals the user has left today."""
        return await self._db.read(_left, user_id, self._clock())

    async def appeal(self, user_id: str, reply_id: int) -> Appealed:
        """Appeal her reply `reply_id`, in one transaction: refused, nothing changes. Raises
        NoSuchReply if the user has no such reply, AppealRefused if it cannot be appealed,
        saying why."""
        return await self._db.write(self._appeal, user_id, reply_id, self._affinities.on)

    def _appeal(
        self, db: sqlite3.Connection, user_id: str, reply_id: int, affinity_on: bool
    ) -> Appealed:
        target = read_appeal_target(db, user_id, reply_id)
        if target is None:
            raise NoSuchReply()
        if not affinity_on:
            raise AppealRefused("closed")
        if target.appealed:
            raise AppealRefused("appealed")
        change = target.change
        if change is None or change.up or target.said_id is None or target.hurt <= 0:
            raise AppealRefused("no_change")
        if target.ended:
            raise AppealRefused("ended")
        if not target.latest:
            raise AppealRefused("not_latest")
        now = self._clock()
        left = _left(db, user_id, now)
        if left <= 0:
            raise AppealRefused("none_left")
        try:
            affinity = self._affinities.give_back(db, user_id, target.character_id, target.hurt)
        except Cooling:
            raise AppealRefused("cooling") from None
        record_appeal(db, reply_id, target.said_id, now.isoformat())
        return Appealed(affinity, left - 1)


def _left(db: sqlite3.Connection, user_id: str, now: datetime) -> int:
    today = day_of(now)
    made = sum(
        day_of(datetime.fromisoformat(at)) == today for at in read_appeals_made(db, user_id)
    )
    return max(APPEALS_PER_DAY - made, 0)
