"""Her schedule over HTTP (GLOSSARY.md「行程」), for users who agreed to the terms: hers for a
day with the user (`/api/characters/{id}/schedule?day=YYYY-MM-DD`, today in the producer's time
zone without `day`), null if she has none for it. Nothing in the app shows it yet.

Under the development sign-in only (`EIDOLON_DEV_LOGIN=1`), `POST /api/schedules/write-now` has
hers for today written at once, to try schedules out without waiting for the morning."""

from datetime import date
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.producer import effective_timezone
from eidolon.sessions import UserId


class WriteNow(BaseModel):
    character_id: str


def add_schedule_routes(app: FastAPI, backend: Backend) -> None:
    schedules, store, clock = backend.schedules, backend.store, backend.clock

    @app.get("/api/characters/{character_id}/schedule")
    def read_schedule(user_id: UserId, character_id: str, day: date | None = None) -> dict | None:
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")
        if day is None:
            zone = ZoneInfo(effective_timezone(store.get_profile(user_id), None))
            day = clock().astimezone(zone).date()
        found = schedules.of(user_id, character_id, day)
        return found.shown() if found else None

    if backend.settings.dev_login:

        @app.post("/api/schedules/write-now", status_code=201)
        async def write_now(user_id: UserId, body: WriteNow) -> dict | None:
            if not await backend.schedule_writer.write_now(user_id, body.character_id):
                raise HTTPException(404, "还没有和她的对话")
            return read_schedule(user_id, body.character_id)
