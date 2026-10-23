"""The map over HTTP (GLOSSARY.md「地图」), for users who agreed to the terms: `/api/map`
gives where on the map each idol is now, by her schedule, and the user's days out from today
on, each at the place it names, if it names one of the map's (`city_map.DATE_SPOTS`).

An idol without a schedule for today is not on the map; with schedules off, by the deployer
or the user, none is (`schedules: false`), and only the user's days out are."""

import asyncio
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from eidolon.assembly import Backend
from eidolon.city_map import AWAY, Place, place_of, resting_place, spot_of
from eidolon.producer import effective_timezone
from eidolon.schedules_api import Now, now_of
from eidolon.sessions import UserId


def add_map_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/map")
    async def read_map(user_id: UserId) -> dict:
        schedules_on = backend.settings.schedules and await backend.features.on(
            user_id, "schedules"
        )
        ids = list(backend.characters)
        found = (
            await asyncio.gather(*(now_of(backend, user_id, c) for c in ids))
            if schedules_on else [None] * len(ids)
        )
        zone = ZoneInfo(effective_timezone(await backend.store.get_profile(user_id), None))
        today = backend.clock().astimezone(zone).date()
        return {
            "schedules": schedules_on,
            "idols": [
                _shown(character_id, now)
                for character_id, now in zip(ids, found, strict=True)
                if now is not None
            ],
            "dates": [
                {
                    "character_id": character_id, "day": day.isoformat(), "what": what,
                    "spot": spot_of(what),
                }
                for character_id, day, what in await backend.dates.upcoming(user_id, today)
                if character_id in backend.characters
            ],
        }


def place_now(now: Now) -> Place:
    """Where on the map she is, as she is now."""
    away = now.trip is not None
    resting = resting_place("bath" if now.bath else "asleep" if now.asleep else None)
    return (AWAY if away else resting) if resting else place_of(now.slot, away)


def _shown(character_id: str, now: Now) -> dict:
    return {"character_id": character_id, "place": place_now(now), "trip": now.trip, **now.shown()}
