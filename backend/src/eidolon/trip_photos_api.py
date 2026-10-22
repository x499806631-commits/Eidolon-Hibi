"""Trying trip photos out (GLOSSARY.md「外景照片」): under the development sign-in only
(`EIDOLON_DEV_LOGIN=1`), `POST /api/trip-photos/send-now` has her send one from a trip at once,
whatever her schedule says. The photo itself is read as any photo is (`photos_api`)."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.sessions import UserId


class SendNow(BaseModel):
    character_id: str
    place: str = "京都"


def add_trip_photo_routes(app: FastAPI, backend: Backend) -> None:
    if not backend.settings.dev_login:
        return

    @app.post("/api/trip-photos/send-now", status_code=201)
    async def send_now(user_id: UserId, body: SendNow) -> dict:
        if not await backend.trip_photos.send_now(user_id, body.character_id, body.place[:20]):
            raise HTTPException(404, "还没有和她的对话，或者没有配生图")
        return {"ok": True}
