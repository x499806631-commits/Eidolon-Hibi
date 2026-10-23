"""Gifts over HTTP (GLOSSARY.md「礼物」「抽奖」), for users who agreed to the terms: the
catalog at `/api/gifts/catalog`, what the user has at `/api/gifts`, and drawing at
`/api/gifts/draws`, the latter two having given the draw chances due today; giving one to
a character at `/api/characters/{id}/gifts`; using a reset card at `/api/gifts/resets`.
Her own achievements (GLOSSARY.md「成就」) are at `/api/characters/{id}/achievements`, those
of all the characters together with what the user has.
None says which gifts a character likes: the gifts page gives none of that away. Admins give every user a reset card at
`/api/admin/gifts/reset-card-grants`, which answers 404 to anyone else as the rest of the
admin console does. Gifts not open, drawing, giving, using a reset card and granting them
are refused with 409 and `gifts_closed`, told apart from a cooldown's 409, so the page shows
them closed."""

from dataclasses import replace

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.gifts import (
    GiftsClosed,
    NoDraws,
    NoneLeft,
    NoOneToReset,
    NoResetCards,
    NoSuchCharacter,
)
from eidolon.sessions import UserId


class DrawInput(BaseModel):
    all: bool = False
    """Draw every chance the user has, not just one."""


class GiftInput(BaseModel):
    gift_id: str


def add_gift_routes(app: FastAPI, backend: Backend) -> None:
    catalog, gifts, store = backend.gift_catalog, backend.gifts, backend.store
    happenings = backend.happenings

    @app.exception_handler(GiftsClosed)
    async def gifts_closed(_: Request, e: GiftsClosed) -> JSONResponse:
        return JSONResponse({"detail": str(e), "gifts_closed": True}, status_code=409)

    @app.exception_handler(NoDraws)
    @app.exception_handler(NoneLeft)
    @app.exception_handler(NoResetCards)
    @app.exception_handler(NoOneToReset)
    async def none_to_use(_: Request, e: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.get("/api/gifts/catalog")
    def read_catalog(_: UserId) -> list[dict]:
        """Every gift, retired ones too (whoever has one still sees it), in catalog order."""
        return [g.described() for g in catalog.values()]

    @app.get("/api/gifts")
    async def read_state(user_id: UserId) -> dict:
        """What the user has, having been given the draw chances due today; whether gifts
        are open, and if so when the next chance is given and each character's affinity,
        for choosing whom to give to, how far the user is with today's daily tasks and with
        the achievements of all the characters together."""
        daily_tasks = await happenings.daily_tasks(user_id)
        of_all = await happenings.of_all(user_id)
        return (await gifts.state(user_id)).shown() | {
            "daily_tasks": daily_tasks and daily_tasks.shown(),
            "achievements": of_all and [p.shown() for p in of_all],
        }

    @app.post("/api/gifts/draws")
    async def draw(body: DrawInput, user_id: UserId) -> dict:
        """Draw once or every chance, having been given those due today: what each draw
        gave, in order, how many chances are left, and what drawing was rewarded with (None
        if nothing), its draw chances among those left."""
        drawn, rewarded = await happenings.draw(user_id, all=body.all)
        if rewarded is not None:
            drawn = replace(drawn, draws=drawn.draws + rewarded.draws)
        return drawn.shown() | {"rewarded": rewarded and rewarded.shown()}

    @app.post("/api/characters/{character_id}/gifts")
    async def give(character_id: str, body: GiftInput, user_id: UserId) -> dict:
        """Give her one of the gift: where its record went, how her affinity changed, where
        it is now, how many of the gift are left, and what giving it was rewarded with (None
        if nothing). Not a turn: no limit or cooldown holds
        it, and the user need not bring their own LLM."""
        try:
            given, rewarded = await happenings.give(user_id, body.gift_id, character_id)
        except NoSuchCharacter as e:
            raise HTTPException(404, str(e)) from None
        return given.shown() | {"rewarded": rewarded and rewarded.shown()}

    @app.get("/api/characters/{character_id}/achievements")
    async def read_hers(character_id: str, user_id: UserId) -> dict | None:
        """How far the user has come with her own achievements, and whether she is complete;
        None while affinity and gifts are closed."""
        if character_id not in backend.characters:
            raise HTTPException(404, "角色不存在")
        achieved = await happenings.hers(user_id, character_id)
        return achieved and achieved.shown()

    @app.post("/api/gifts/resets")
    async def use_reset_card(user_id: UserId) -> dict:
        """Use a reset card: whom it brought back to trust, and how many cards are left."""
        return (await gifts.use_reset_card(user_id)).shown()

    @app.get("/api/admin/gifts")
    async def read_admin_gifts(_: Admin) -> dict:
        """How many users there are now, each to be given a reset card; when they last were,
        and how many; and whether gifts are open."""
        users = await store.list_users()
        last = await gifts.last_grant()
        return {
            "users": len(users),
            "last_grant": last and last.shown(),
            "open": backend.config.affinity_on,
        }

    @app.post("/api/admin/gifts/reset-card-grants")
    async def grant_reset_cards(_: Admin) -> dict:
        """Give every user there is now a reset card: when, and how many were given."""
        return (await gifts.grant_reset_cards()).shown()
