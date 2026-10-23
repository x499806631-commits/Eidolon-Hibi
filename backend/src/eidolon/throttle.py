"""How often users may have the server ask services on the internet for them: trying
their own keys before keeping them, and asking a service which models it offers a key
(ADR-0027). The address is the user's to write, so each user may ask only so many times
a minute and an hour, and only so many such requests run at once across all users: no one
can have the server hammer an address, or try keys stolen elsewhere in bulk from it, and
these never take up what turns and calls need. Kept in memory, as the server is one
process; a restart forgets it."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from eidolon.clock import Clock

PER_MINUTE = 10
PER_HOUR = 60
AT_ONCE = 20
"""Across all users: a try may take a minute, a list ten seconds."""

_MINUTE = timedelta(minutes=1)
_HOUR = timedelta(hours=1)


class Throttled(Exception):
    """Asked too often, or too many asking at once: what to tell the user, and in how many
    seconds they may ask again."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class Throttle:
    def __init__(
        self,
        clock: Clock,
        *,
        per_minute: int = PER_MINUTE,
        per_hour: int = PER_HOUR,
        at_once: int = AT_ONCE,
    ) -> None:
        self._clock = clock
        self._per_minute = per_minute
        self._per_hour = per_hour
        self._at_once = at_once
        self._asked: dict[str, list[datetime]] = {}
        self._running = 0

    @contextmanager
    def asking(self, user_id: str) -> Iterator[None]:
        """While the user's request asks a service: raises Throttled, before it asks, if
        they have asked `per_minute` times in the last minute or `per_hour` in the last
        hour, or `at_once` requests are asking already."""
        now = self._clock()
        asked = [t for t in self._asked.get(user_id, []) if now - t < _HOUR]
        self._asked[user_id] = asked
        recent = [t for t in asked if now - t < _MINUTE]
        if len(recent) >= self._per_minute:
            raise Throttled("试得太频繁了", _wait(recent[0] + _MINUTE - now))
        if len(asked) >= self._per_hour:
            raise Throttled("这一小时试得太多了", _wait(asked[0] + _HOUR - now))
        if self._running >= self._at_once:
            raise Throttled("现在试调的人太多了", 10)
        asked.append(now)
        self._running += 1
        try:
            yield
        finally:
            self._running -= 1


def _wait(delta: timedelta) -> int:
    return max(1, int(delta.total_seconds() + 0.999))
