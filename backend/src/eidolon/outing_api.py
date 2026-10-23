"""Days out over HTTP (GLOSSARY.md「约会」), for users who agreed to the terms:

- `GET /api/outings/today?character=`: the day out with her today, if any, and the outing if met.
- `POST /api/outings` `{"character_id"}`: meet her (the outing met already, if they met).
- `POST /api/outings/{id}/next`: on to the next stop.
- `POST /api/outings/{id}/photo` `{"wish"}`: a photo of them together, sent in the conversation.
- `POST /api/outings/{id}/end`: it is over; what was written down of it.
- `GET /api/outings/background/{spot}/{stop}`: the stop's background drawn, 404 if none.
- `POST /api/outings/{id}/background` `{"again"}`: draw the background of the stop they are at.
- `POST /api/outings/emotions`: how she feels saying each segment of a reply, for her stand.

Its text turns are the conversation's own (`/api/conversations/{id}/turns`): while she is out,
her turns are told where they are.
"""

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from eidolon.assembly import Backend
from eidolon.outing import (
    CannotDraw,
    NoDateToday,
    NoMorePhotos,
    NoSuchOuting,
    NotYet,
    OutingOver,
    TooLate,
)
from eidolon.sessions import UserId

_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}


class Meet(BaseModel):
    character_id: str


class Wish(BaseModel):
    wish: str = Field(default="", max_length=60)


class Again(BaseModel):
    again: bool = False


class SegmentIn(BaseModel):
    ja: str = Field(max_length=2000)
    zh: str = Field(max_length=2000)


class ToJudge(BaseModel):
    character_id: str
    user_said: str = Field(default="", max_length=2000)
    segments: list[SegmentIn] = Field(max_length=20)


def add_outing_routes(app: FastAPI, backend: Backend) -> None:
    outings = backend.outings

    @app.exception_handler(NoDateToday)
    @app.exception_handler(NotYet)
    @app.exception_handler(TooLate)
    @app.exception_handler(OutingOver)
    @app.exception_handler(NoMorePhotos)
    @app.exception_handler(CannotDraw)
    async def refused(_: Request, e: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(NoSuchOuting)
    async def none(_: Request, e: NoSuchOuting) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    def her(character_id: str) -> None:
        if character_id not in backend.characters:
            raise HTTPException(404, "没有这位角色")

    @app.get("/api/outings/today")
    async def today(user_id: UserId, character: str) -> dict:
        her(character)
        return await outings.today(user_id, character)

    @app.post("/api/outings")
    async def meet(user_id: UserId, body: Meet) -> dict:
        her(body.character_id)
        return (await outings.start(user_id, body.character_id)).shown()

    @app.post("/api/outings/{outing_id}/next")
    async def next_stop(user_id: UserId, outing_id: int) -> dict:
        return (await outings.next_stop(user_id, outing_id)).shown()

    @app.post("/api/outings/{outing_id}/photo")
    async def photo(user_id: UserId, outing_id: int, body: Wish) -> dict:
        conversation_id = await outings.photo(user_id, outing_id, body.wish)
        return {"conversation_id": conversation_id, **(await outings.of(user_id, outing_id)).shown()}

    @app.post("/api/outings/{outing_id}/end")
    async def end(user_id: UserId, outing_id: int) -> dict:
        return (await outings.end(user_id, outing_id)).shown()

    @app.get("/api/outings/background/{spot}/{stop}")
    async def background(user_id: UserId, spot: str, stop: str) -> Response:
        image = await outings.background(user_id, spot, stop)
        if image is None:
            raise HTTPException(404, "这一站还没画")
        return Response(image, media_type="image/webp", headers=_CACHE)

    @app.post("/api/outings/{outing_id}/background")
    async def draw(user_id: UserId, outing_id: int, body: Again) -> dict:
        drawn = await outings.draw_background(user_id, outing_id, body.again)
        if not drawn:
            raise HTTPException(502, "这次没画出来，稍后再试")
        return {"drawn": True}

    @app.post("/api/outings/emotions")
    async def emotions(user_id: UserId, body: ToJudge) -> list[dict]:
        her(body.character_id)
        return await outings.emotions(
            body.character_id, body.user_said, [(s.ja, s.zh) for s in body.segments]
        )
