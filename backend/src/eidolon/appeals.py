"""Appeals (GLOSSARY.md「我不是这个意思」): the judge model can take a turn the wrong way, and
the user may say they did not mean it so. Her latest reply, as it lowered her affinity, can
be appealed once: what the turn took off is given back, the turn no longer counts towards her
ending the conversation or hanging up, and as she replies next she is told the user said it
was a joke or a misunderstanding (`affinity.EXPLAINED`). A few a day (`APPEALS_PER_DAY`, every
character together, on the days of `affinity.day_of`); none in a cooldown, nor in a
conversation she ended, nor for a turn of a call, which keeps no reckoning to undo."""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from eidolon.affinity import Affinities, Cooling, Status, day_of
from eidolon.clock import Clock
from eidolon.store import Store

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
    def __init__(self, store: Store, affinities: Affinities, clock: Clock) -> None:
        self._store = store
        self._affinities = affinities
        self._clock = clock
        self._lock = threading.Lock()

    def left(self, user_id: str) -> int:
        """How many appeals the user has left today."""
        return self._left(user_id, self._clock())

    def appeal(self, user_id: str, reply_id: int) -> Appealed:
        """Appeal her reply `reply_id`. Raises NoSuchReply if the user has no such reply,
        AppealRefused if it cannot be appealed, saying why."""
        with self._lock:
            target = self._store.appeal_target(user_id, reply_id)
            if target is None:
                raise NoSuchReply()
            if self._affinities.status(user_id, target.character_id) is None:
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
            left = self._left(user_id, now)
            if left <= 0:
                raise AppealRefused("none_left")
            said_id = target.said_id
            try:
                _, affinity = self._affinities.give_back(
                    user_id, target.character_id, target.hurt,
                    lambda: self._store.record_appeal(reply_id, said_id, now.isoformat()),
                )
            except Cooling:
                raise AppealRefused("cooling") from None
            return Appealed(affinity, left - 1)

    def _left(self, user_id: str, now: datetime) -> int:
        today = day_of(now)
        made = sum(
            day_of(datetime.fromisoformat(at)) == today
            for at in self._store.appeals_made(user_id)
        )
        return max(APPEALS_PER_DAY - made, 0)
