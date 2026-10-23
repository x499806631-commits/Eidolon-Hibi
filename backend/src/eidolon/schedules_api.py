"""Her schedule over HTTP (GLOSSARY.md「行程」), for users who agreed to the terms: hers for a
day with the user (`/api/characters/{id}/schedule?day=YYYY-MM-DD`, today in the producer's time
zone without `day`), null if she has none for it; and what she is up to now
(`/api/characters/{id}/schedule/now`), for the line under her name on her conversation page:
the slot she is in and when it ends, `{"free": true}` between slots, either with `bedtime` near
her bedtime; `{"asleep": true}` asleep, `woken` if the user and she were in touch within
`engine.WOKEN_FOR`; in the bath (GLOSSARY.md「洗澡」), till they are in touch, `{"bath": true}`;
null without a schedule today, or where the user has her schedule off (GLOSSARY.md「玩法开关」),
and nothing of her sleep or her bath where they have that off.

Under the development sign-in only (`EIDOLON_DEV_LOGIN=1`), `POST /api/schedules/write-now` has
hers for today written at once, to try schedules out without waiting for the morning."""

from dataclasses import dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.engine import WOKEN_FOR
from eidolon.producer import effective_timezone
from eidolon.schedules import Slot, bath_began, rest
from eidolon.sessions import UserId
from eidolon.store import read_last_contact


class WriteNow(BaseModel):
    character_id: str


def add_schedule_routes(app: FastAPI, backend: Backend) -> None:
    schedules, store, clock = backend.schedules, backend.store, backend.clock

    @app.get("/api/characters/{character_id}/schedule")
    async def read_schedule(user_id: UserId, character_id: str, day: date | None = None) -> dict | None:
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        if day is None:
            zone = ZoneInfo(effective_timezone(await store.get_profile(user_id), None))
            day = clock().astimezone(zone).date()
        found = await schedules.of(user_id, character_id, day)
        return found.shown() if found else None

    @app.get("/api/characters/{character_id}/schedule/now")
    async def read_now(user_id: UserId, character_id: str) -> dict | None:
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        found = await now_of(backend, user_id, character_id)
        return found.shown() if found is not None else None

    if backend.settings.dev_login:

        @app.post("/api/schedules/write-now", status_code=201)
        async def write_now(user_id: UserId, body: WriteNow) -> dict | None:
            if not await backend.schedule_writer.write_now(user_id, body.character_id):
                raise HTTPException(404, "还没有和她的对话")
            return await read_schedule(user_id, body.character_id)


@dataclass(frozen=True)
class Now:
    """Where her schedule has her now, as the user may be shown it: asleep (and whether the
    user woke her), in the bath, free, or in a slot of it, about to go to bed or not."""

    slot: Slot | None = None
    asleep: bool = False
    woken: bool = False
    bath: bool = False
    bedtime: bool = False
    trip: str | None = None
    """Where she is away from Tokyo today, if she is (GLOSSARY.md「纪念品」)."""

    def shown(self) -> dict:
        if self.bath:
            return {"bath": True}
        if self.asleep:
            return {"asleep": True, "woken": self.woken}
        bedtime = {"bedtime": True} if self.bedtime else {}
        if self.slot is None:
            return {"free": True, **bedtime}
        return {
            "what": self.slot.what, "where": self.slot.where, "kind": self.slot.kind,
            "until": self.slot.end.strftime("%H:%M"), **bedtime,
        }


async def now_of(backend: Backend, user_id: str, character_id: str) -> Now | None:
    """Where her schedule for today has her now (GLOSSARY.md「行程」); None without one, or
    with the user's schedules off. Her sleep and bath only where the user has them on; in
    the bath only until the user is in touch with her since it began."""
    features, store, schedules = backend.features, backend.store, backend.schedules
    if not await features.on(user_id, "schedules"):
        return None
    zone = ZoneInfo(effective_timezone(await store.get_profile(user_id), None))
    now = backend.clock()
    local = now.astimezone(zone)
    found = await schedules.of(user_id, character_id, local.date())
    yesterday = await schedules.of(user_id, character_id, local.date() - timedelta(days=1))
    resting = rest(found, yesterday, local)
    if (resting == "bath" and not await features.on(user_id, "bath")) or (
        resting in ("asleep", "bedtime") and not await features.on(user_id, "sleep")
    ):
        resting = None
    trip = found.trip if found is not None else None
    if resting == "bath":
        began = bath_began(found, yesterday, local)
        contact = await backend.database.read(read_last_contact, user_id, character_id)
        if began is not None and (contact is None or contact < began):
            return Now(bath=True, trip=trip)
    if resting == "asleep":
        contact = await backend.database.read(read_last_contact, user_id, character_id)
        return Now(
            asleep=True, woken=contact is not None and now - contact <= WOKEN_FOR, trip=trip
        )
    if found is None:
        return None
    return Now(found.now(local.time()), bedtime=resting == "bedtime", trip=trip)
