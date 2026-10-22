"""Trying trip photos out (GLOSSARY.md「外景照片」): under the development sign-in only
(`EIDOLON_DEV_LOGIN=1`), `POST /api/trip-photos/send-now` has her send one from a trip at once,
whatever her schedule says (and `POST /api/trip-photos/date-now` one after a day out with
the user, `what`, the producer maybe in it), with others on the trip with her if named (`with`, by id; or
`"unit"` for the rest of her unit), for her to send a photo of them all if she likes, or this time (`group`). The
photo itself is read as any photo is (`photos_api`)."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from eidolon.assembly import Backend
from eidolon.sessions import UserId


class SendNow(BaseModel):
    character_id: str
    place: str = "京都"
    with_: list[str] | str = Field(default_factory=list, alias="with")
    group: bool = False
    """Have her take one of them all this time."""


class DateNow(BaseModel):
    character_id: str
    what: str = "去看电影"


def add_trip_photo_routes(app: FastAPI, backend: Backend) -> None:
    if not backend.settings.dev_login:
        return

    @app.post("/api/trip-photos/send-now", status_code=201)
    async def send_now(user_id: UserId, body: SendNow) -> dict:
        others = (
            backend.engine.unit_mates(body.character_id) if body.with_ == "unit"
            else [c for c in body.with_ if c != body.character_id] if isinstance(body.with_, list)
            else []
        )
        unknown = [c for c in others if c not in backend.characters]
        if unknown:
            raise HTTPException(404, f"没有这些偶像：{'、'.join(unknown)}")
        if not await backend.trip_photos.send_now(
            user_id, body.character_id, body.place[:20], tuple(others[:3]), body.group
        ):
            raise HTTPException(404, "还没有和她的对话，或者没有配生图")
        return {"ok": True}

    @app.post("/api/trip-photos/date-now", status_code=201)
    async def date_now(user_id: UserId, body: DateNow) -> dict:
        if body.character_id not in backend.characters:
            raise HTTPException(404, f"没有这位偶像：{body.character_id}")
        if not await backend.trip_photos.date_now(user_id, body.character_id, body.what[:30]):
            raise HTTPException(404, "还没有和她的对话，或者没有配生图")
        return {"ok": True}
