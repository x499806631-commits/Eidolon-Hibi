"""Keepsakes over HTTP (GLOSSARY.md「纪念品」), for users who agreed to the terms: the cabinet,
the keepsakes she gave the user by now, newest first, with how many have no picture yet,
whether they are being drawn and whether they can be (`/api/keepsakes`); one keepsake
(`/api/keepsakes/{id}`) and its picture (`/api/keepsakes/{id}/image`, 404 without one); how
many the user has not seen yet (`/api/keepsakes/unseen`, for the dot on the navigation) and
seeing them all (`POST /api/keepsakes/seen`, the cabinet opened); and drawing the pictures
missing, one at a time, on the deployer's image generation (`POST /api/keepsakes/fill-images`);
drawing one's picture again (`POST /api/keepsakes/{id}/redraw`) and taking one out of the
cabinet, its card in the conversation with it (`DELETE /api/keepsakes/{id}`).
To anyone but the user they were given to, there is no such keepsake (404).

Under the development sign-in only (`EIDOLON_DEV_LOGIN=1`), `POST /api/keepsakes/give-now` has
her give one at once, to try keepsakes out without waiting for a reason."""

from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.sessions import UserId

NOT_FOUND = "没有这件纪念品"
_CACHE = {"Cache-Control": "private, max-age=86400"}


class GiveNow(BaseModel):
    character_id: str
    occasion: Literal[
        "scene", "anniversary", "birthday", "gift", "promise", "souvenir", "holiday"
    ] = "gift"
    about: str = "一块布丁"


def add_keepsake_routes(app: FastAPI, backend: Backend) -> None:
    keepsakes, clock = backend.keepsakes, backend.clock

    async def drawing(user_id: str) -> dict:
        drawn = await keepsakes.can_draw(user_id) or backend.config.image_generator() is None
        return {  # pictures switched off: none missing, none to draw (GLOSSARY.md「玩法开关」)
            "missing": await keepsakes.missing(user_id) if drawn else 0,
            "filling": keepsakes.filling(user_id),
            "can_draw": await keepsakes.can_draw(user_id),
        }

    @app.get("/api/keepsakes")
    async def list_keepsakes(user_id: UserId) -> dict:
        return {
            "keepsakes": [k.shown() for k in await keepsakes.of(user_id, clock())],
            **await drawing(user_id),
        }

    @app.get("/api/keepsakes/unseen")
    async def unseen_keepsakes(user_id: UserId) -> dict:
        return {"unseen": await keepsakes.unseen(user_id, clock())}

    @app.post("/api/keepsakes/seen", status_code=204)
    async def see_keepsakes(user_id: UserId) -> None:
        await keepsakes.seen(user_id, clock())

    @app.post("/api/keepsakes/fill-images")
    async def fill_images(user_id: UserId) -> dict:
        return {"started": await keepsakes.fill_images(user_id), **await drawing(user_id)}

    @app.get("/api/keepsakes/{keepsake_id}")
    async def read_keepsake(user_id: UserId, keepsake_id: int) -> dict:
        found = await keepsakes.one(user_id, keepsake_id)
        if found is None:
            raise HTTPException(404, NOT_FOUND)
        return found.shown()

    @app.get("/api/keepsakes/{keepsake_id}/image")
    async def read_keepsake_image(user_id: UserId, keepsake_id: int) -> Response:
        image = await keepsakes.image(user_id, keepsake_id)
        if image is None:
            raise HTTPException(404, NOT_FOUND)
        return Response(image, media_type="image/webp", headers=_CACHE)

    @app.post("/api/keepsakes/{keepsake_id}/redraw")
    async def redraw(keepsake_id: int, user_id: UserId) -> dict:
        """Its picture drawn again: the keepsake as it is now. 404 for none of the user's,
        409 where nothing draws, 502 where the picture could not be drawn."""
        if await keepsakes.one(user_id, keepsake_id) is None:
            raise HTTPException(404, "没有这件纪念品")
        if not await keepsakes.can_draw(user_id):
            raise HTTPException(409, "现在没有生图服务，画不了")
        if not await keepsakes.redraw(user_id, keepsake_id):
            raise HTTPException(502, "这次没画出来，稍后再试")
        kept = await keepsakes.one(user_id, keepsake_id)
        assert kept is not None
        return kept.shown()

    @app.delete("/api/keepsakes/{keepsake_id}", status_code=204)
    async def take_out(keepsake_id: int, user_id: UserId) -> Response:
        """Out of the cabinet, and its card out of the conversation; 404 for none of the user's."""
        if not await keepsakes.take_out(user_id, keepsake_id):
            raise HTTPException(404, "没有这件纪念品")
        return Response(status_code=204)

    if backend.settings.dev_login:

        @app.post("/api/keepsakes/give-now", status_code=201)
        async def give_now(user_id: UserId, body: GiveNow) -> dict:
            if not await keepsakes.give_now(user_id, body.character_id, body.occasion, body.about):
                raise HTTPException(404, "还没有和她的对话")
            return {"ok": True}
