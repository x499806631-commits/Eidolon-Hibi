"""Favors over HTTP (GLOSSARY.md「拜托」): `GET /api/favors` for what the page shows, and
`POST /api/favors/<kind>` to ask one, using a favor ticket (GLOSSARY.md「体验券」) and the gifts
named (`gift_ids`): the conversation she answers in, whether she took it up (only a way to
call the producer may be turned down), and how many favor tickets are left. Once she has
answered, the ticket and the gifts are used up, whatever she answered; a favor that cannot be
asked takes nothing (409 with why), and one she never got to answer gives them back."""

from collections.abc import Awaitable, Callable
from datetime import date, time
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from eidolon.assembly import Backend
from eidolon.favors import ADDRESS_MAX, MAX_GUESTS, WHAT_MAX, CannotAsk
from eidolon.sessions import UserId

MAX_GIFTS = 4


class WithGifts(BaseModel):
    gift_ids: list[str] = Field(max_length=MAX_GIFTS)


class Address(WithGifts):
    character_id: str
    address: str | None = Field(default=None, max_length=ADDRESS_MAX)
    """None to have her call the producer as she used to."""


class Wake(WithGifts):
    character_id: str
    at: time
    days: int = Field(ge=1, le=7)


class Bond(WithGifts):
    character_id: str
    elder: Literal["her", "producer"]


class MakeUp(WithGifts):
    mediator_id: str
    target_id: str


class Party(WithGifts):
    helper_id: str
    guest_ids: list[str] = Field(max_length=MAX_GUESTS)
    what: str = Field(min_length=1, max_length=WHAT_MAX)
    honoree_id: str | None = None
    day: date


def add_favor_routes(app: FastAPI, backend: Backend) -> None:
    gifts, favors = backend.gifts, backend.favors

    @app.exception_handler(CannotAsk)
    async def cannot_ask(_: Request, e: CannotAsk) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    async def asking(user_id: str, ask: Callable[[], Awaitable[str | tuple[str, bool]]]) -> dict:
        """Use a favor ticket on `ask`; given back only should she not get to answer."""
        await gifts.take_ticket(user_id, "favor")
        try:
            asked = await ask()
        except BaseException:
            await gifts.put_back_ticket(user_id, "favor")
            raise
        conversation_id, accepted = asked if isinstance(asked, tuple) else (asked, True)
        return {
            "conversation_id": conversation_id, "accepted": accepted,
            "left": await gifts.tickets(user_id, "favor"),
        }

    @app.get("/api/favors")
    async def overview(user_id: UserId) -> dict:
        return await favors.overview(user_id)

    @app.post("/api/favors/address")
    async def address(user_id: UserId, body: Address) -> dict:
        return await asking(user_id, lambda: favors.ask_address(
            user_id, body.character_id, body.address, body.gift_ids
        ))

    @app.post("/api/favors/wake")
    async def wake(user_id: UserId, body: Wake) -> dict:
        return await asking(user_id, lambda: favors.ask_wake(
            user_id, body.character_id, body.at, body.days, body.gift_ids
        ))

    @app.post("/api/favors/bond")
    async def bond(user_id: UserId, body: Bond) -> dict:
        return await asking(user_id, lambda: favors.ask_bond(
            user_id, body.character_id, body.elder, body.gift_ids
        ))

    @app.post("/api/favors/make_up")
    async def make_up(user_id: UserId, body: MakeUp) -> dict:
        return await asking(user_id, lambda: favors.ask_make_up(
            user_id, body.mediator_id, body.target_id, body.gift_ids
        ))

    @app.post("/api/favors/party")
    async def party(user_id: UserId, body: Party) -> dict:
        return await asking(user_id, lambda: favors.ask_party(
            user_id, body.helper_id, body.guest_ids, body.what, body.honoree_id, body.day,
            body.gift_ids,
        ))
