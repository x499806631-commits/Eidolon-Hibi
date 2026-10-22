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

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.engine import WOKEN_FOR
from eidolon.producer import effective_timezone
from eidolon.schedules import bath_began, rest
from eidolon.sessions import UserId


class WriteNow(BaseModel):
    character_id: str


def add_schedule_routes(app: FastAPI, backend: Backend) -> None:
    schedules, store, clock, features = (
        backend.schedules, backend.store, backend.clock, backend.features
    )

    @app.get("/api/characters/{character_id}/schedule")
    def read_schedule(user_id: UserId, character_id: str, day: date | None = None) -> dict | None:
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        if day is None:
            zone = ZoneInfo(effective_timezone(store.get_profile(user_id), None))
            day = clock().astimezone(zone).date()
        found = schedules.of(user_id, character_id, day)
        return found.shown() if found else None

    @app.get("/api/characters/{character_id}/schedule/now")
    def read_now(user_id: UserId, character_id: str) -> dict | None:
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        if not features.on(user_id, "schedules"):
            return None
        zone = ZoneInfo(effective_timezone(store.get_profile(user_id), None))
        now = clock()
        local = now.astimezone(zone)
        found = schedules.of(user_id, character_id, local.date())
        yesterday = schedules.of(user_id, character_id, local.date() - timedelta(days=1))
        resting = rest(found, yesterday, local)
        if (resting == "bath" and not features.on(user_id, "bath")) or (
            resting in ("asleep", "bedtime") and not features.on(user_id, "sleep")
        ):
            resting = None
        if resting == "bath":
            began = bath_began(found, yesterday, local)
            contact = store.last_contact(user_id, character_id)
            if began is not None and (contact is None or contact < began):
                return {"bath": True}
        if resting == "asleep":
            contact = store.last_contact(user_id, character_id)
            return {"asleep": True, "woken": contact is not None and now - contact <= WOKEN_FOR}
        if found is None:
            return None
        slot = found.now(local.time())
        bedtime = {"bedtime": True} if resting == "bedtime" else {}
        if slot is None:
            return {"free": True, **bedtime}
        return {
            "what": slot.what, "where": slot.where, "kind": slot.kind,
            "until": slot.end.strftime("%H:%M"), **bedtime,
        }

    if backend.settings.dev_login:

        @app.post("/api/schedules/write-now", status_code=201)
        async def write_now(user_id: UserId, body: WriteNow) -> dict | None:
            if not await backend.schedule_writer.write_now(user_id, body.character_id):
                raise HTTPException(404, "还没有和她的对话")
            return read_schedule(user_id, body.character_id)
