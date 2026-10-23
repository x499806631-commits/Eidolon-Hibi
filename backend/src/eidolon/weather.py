"""The weather where the producer is (GLOSSARY.md「天气」): asked of Open-Meteo, which wants no
key, and said to her in every turn and to the judge model that picks the ambience of a call.

`Weather.now` says it in one line, as she is told it: the sky over there and the temperature,
such as「制作人那边现在下着小雨，12°C。」. A prompt asks for it on every turn, so it is asked
of the service once in `FRESH`, and the line kept meanwhile is said again rather than nothing.
A request that fails, or that takes longer than `TIMEOUT`, says the last line it had, or
nothing at all: she goes on without the weather rather than a turn that waits on it or fails.
Nor is the service asked again for `RETRY` after it failed, so that while it is down, or out of
reach, turns are not each held up by its timeout.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

import httpx

from eidolon.clock import Clock, utc_now

log = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"
"""Open-Meteo's forecast: the conditions there now, by latitude and longitude, without a key."""

TIMEOUT = httpx.Timeout(5, connect=3)
"""How long the service has, in seconds: the weather is worth waiting a little for, no more."""

FRESH = timedelta(minutes=30)
"""How long a line said of the weather is said again before the service is asked again."""

RETRY = timedelta(minutes=10)
"""How long after a request failed the service is not asked again: the last line, or nothing,
is said meanwhile."""

PLACE = "制作人那边"
"""Where the weather is, as she is told it: the producer's own place, which `.env` gives."""

SKIES: dict[int, str] = {
    0: "晴",
    1: "晴间多云",
    2: "多云",
    3: "阴天",
    45: "有雾",
    48: "有雾凇",
    51: "下着毛毛雨",
    53: "下着毛毛雨",
    55: "下着毛毛雨",
    56: "下着冻毛毛雨",
    57: "下着冻毛毛雨",
    61: "下着小雨",
    63: "下着中雨",
    65: "下着大雨",
    66: "下着冻雨",
    67: "下着冻雨",
    71: "下着小雪",
    73: "下着中雪",
    75: "下着大雪",
    77: "飘着米雪",
    80: "有阵雨",
    81: "有阵雨",
    82: "有强阵雨",
    85: "有阵雪",
    86: "有阵雪",
    95: "有雷雨",
    96: "有雷雨和冰雹",
    99: "有雷雨和冰雹",
}
"""The sky each WMO weather code Open-Meteo answers with stands for (its `weather_code`),
worded to follow「现在」: 「现在下着小雨」，「现在晴」."""


Sky = Literal["clear", "cloudy", "fog", "rain", "snow", "storm"]
"""What the sky looks like, as a window shows it (GLOSSARY.md「制作人的家」)."""


def sky_of(code: int) -> Sky | None:
    """The sky a WMO weather code looks like; None for one no one here knows."""
    if code not in SKIES:
        return None
    if code <= 1:
        return "clear"
    if code <= 3:
        return "cloudy"
    if code <= 48:
        return "fog"
    if code >= 95:
        return "storm"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    return "rain"


def say(code: int, celsius: float) -> str | None:
    """The weather as she is told it, from a code and a temperature; None for a code no one
    here knows, which is not worth telling her wrongly."""
    sky = SKIES.get(code)
    if sky is None:
        return None
    return f"{PLACE}现在{sky}，{round(celsius)}°C。"


@dataclass
class Weather:
    """The weather where the producer is, for the prompt of a turn and the ambience of a
    call: at `latitude` and `longitude`, in degrees, north and east positive, asked as
    `clock` says the line it has has gone stale, through `transport` if it is given (as the
    tests give one) and over the network otherwise."""

    latitude: float
    longitude: float
    clock: Clock = field(default=utc_now)
    transport: httpx.AsyncBaseTransport | None = None
    _said: str | None = field(default=None, init=False)
    """The last line said of the weather, kept past `FRESH` to say when the service fails."""
    _asked_at: datetime | None = field(default=None, init=False)
    _failed_at: datetime | None = field(default=None, init=False)
    """When a request last failed, so that the service is let be for `RETRY`."""
    _client: httpx.AsyncClient | None = field(default=None, init=False, repr=False)
    _code: int | None = field(default=None, init=False)
    """The weather code the line said last was said from."""

    async def now(self) -> str | None:
        """The weather as she is told it now; None where it cannot be told, or is not known."""
        now = self.clock()
        if self._asked_at is not None and now - self._asked_at < FRESH:
            return self._said
        if self._failed_at is not None and now - self._failed_at < RETRY:
            return self._said
        try:
            said = await self._ask()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            log.warning("could not tell the weather; keeping what was said last: %s", e)
            self._failed_at = now
            return self._said
        if said is None:
            self._failed_at = now
            return self._said
        self._said, self._asked_at, self._failed_at = said, now, None
        return said

    async def sky(self) -> Sky | None:
        """What the sky looks like there now, as `now` has it; None where it is not known."""
        await self.now()
        return sky_of(self._code) if self._code is not None else None

    async def _ask(self) -> str | None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT, transport=self.transport)
        response = await self._client.get(
            URL,
            params={
                "latitude": self.latitude,
                "longitude": self.longitude,
                "current": "temperature_2m,weather_code",
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        body = response.json()
        said = _read(body)
        if said is not None:
            self._code = int(body["current"]["weather_code"])
        if said is None:
            log.warning("Open-Meteo answered a weather code this deployment does not know")
        return said

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _read(body: Any) -> str | None:
    """The line Open-Meteo's answer says, or None where it does not say one."""
    current: Mapping[str, Any] = body["current"]
    return say(int(current["weather_code"]), float(current["temperature_2m"]))
