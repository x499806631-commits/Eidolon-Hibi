"""The producer's home over HTTP (GLOSSARY.md「制作人的家」): `GET /api/home` is the user's
room, the photo framed in it, and the sky over there now (`clear`, `rain`… or null where the
deployment has no place for the weather); `PUT /api/home` changes the room or the photo
(`framed_photo`: an id, or null to take it down; 404 for a photo not sent them).
`GET /api/album` is the photos sent them, newest first.

The room's picture: `background` in the home is how many times it has been drawn (null if
never; its picture at `GET /api/home/background/{room}?v=…`), `can_draw` whether it can be,
`layout` where the user set each thing in it (`PUT /api/home` with `layout`, for the room it
is). `POST /api/home/background` draws it (again): 409 where nothing draws, 502 where it was
not drawn."""

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.home import NoSuchPhoto, Room
from eidolon.sessions import UserId

_UNSET = "unset"


class HomeChange(BaseModel):
    room: Room | None = None
    framed_photo: str | None = _UNSET
    layout: dict[str, tuple[float, float]] | None = None


def add_home_routes(app: FastAPI, backend: Backend) -> None:
    home = backend.home

    async def shown(user_id: str) -> dict:
        state = await home.of(user_id)
        sky = await backend.weather.sky() if backend.weather is not None else None
        return {
            "room": state.room, "framed_photo": state.framed_photo, "sky": sky,
            "background": await home.drawn(user_id, state.room), "can_draw": home.can_draw(),
            "layout": {thing: list(at) for thing, at in state.layouts.get(state.room, {}).items()},
        }

    @app.get("/api/home")
    async def read_home(user_id: UserId) -> dict:
        return await shown(user_id)

    @app.put("/api/home")
    async def change_home(user_id: UserId, body: HomeChange) -> dict:
        framed = body.framed_photo
        try:
            await home.set(
                user_id, body.room,
                framed if framed not in (None, _UNSET) else None, unframe=framed is None,
                layout=body.layout,
            )
        except NoSuchPhoto:
            raise HTTPException(404, "照片不存在") from None
        return await shown(user_id)

    @app.post("/api/home/background")
    async def draw_background(user_id: UserId) -> dict:
        if not home.can_draw():
            raise HTTPException(409, "现在没有生图服务，画不了")
        if not await home.draw(user_id, (await home.of(user_id)).room):
            raise HTTPException(502, "这次没画出来，稍后再试")
        return await shown(user_id)

    @app.get("/api/home/background/{room}")
    async def background(room: Room, user_id: UserId) -> Response:
        image = await home.background(user_id, room)
        if image is None:
            raise HTTPException(404, "这间屋子还没画")
        return Response(image, media_type="image/webp", headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/album")
    async def album(user_id: UserId) -> list[dict]:
        return [
            {"id": p.id, "character_id": p.character_id, "sent_at": p.sent_at}
            for p in await home.album(user_id)
        ]
