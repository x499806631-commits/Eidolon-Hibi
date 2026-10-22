"""Using tickets over HTTP (GLOSSARY.md「体验券」): `POST /api/tickets/<kind>`, what for in the
body. A ticket is taken as it is used and given back should using it fail: 409 with what
went wrong, 404 for no such character or diary page. What each kind does is `tickets`'.

The photo ticket names an outfit from her outfit list (`outfit_id`, with the card's name and
whether it is her everyday clothes or its stage costume), or one described (`described`), or
neither, for her to choose; an outfit from the list is drawn with its tags where the
deployment can read them (`outfit_tags`), else she is told the card's name."""

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from eidolon.assembly import Backend
from eidolon.diary import NothingToUnlock
from eidolon.gifts import NoSuchCharacter, NoTickets
from eidolon.sessions import UserId
from eidolon.store import NotFound
from eidolon.tickets import TicketKind
from eidolon.trip_photos import DateTaken, NoConversation, NoImages

ACTIVITY_MAX = 30
DESCRIBED_MAX = 80


class ForHer(BaseModel):
    character_id: str


class PhotoWish(ForHer):
    outfit_id: str | None = Field(default=None, max_length=40)
    card: str | None = Field(default=None, max_length=40)
    """The card the outfit is on, by its name."""
    look: Literal["everyday", "stage"] = "everyday"
    described: str | None = Field(default=None, max_length=DESCRIBED_MAX)


class Invitation(ForHer):
    what: str = Field(min_length=1, max_length=ACTIVITY_MAX)
    day: date


class DiaryPage(BaseModel):
    entry_id: int


def add_ticket_routes(app: FastAPI, backend: Backend) -> None:
    gifts, trip_photos = backend.gifts, backend.trip_photos

    @app.exception_handler(NoTickets)
    @app.exception_handler(NoConversation)
    @app.exception_handler(NoImages)
    @app.exception_handler(DateTaken)
    @app.exception_handler(NothingToUnlock)
    async def cannot_use(_: Request, e: Exception) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    async def using(user_id: str, kind: TicketKind, use: Callable[[], Awaitable[dict]]) -> dict:
        """Take a ticket of `kind`, `use` it, and give it back should that fail."""
        gifts.take_ticket(user_id, kind)
        try:
            done = await use()
        except BaseException:
            gifts.put_back_ticket(user_id, kind)
            raise
        return done | {"left": gifts.tickets(user_id, kind)}

    def her(character_id: str) -> None:
        if character_id not in backend.characters:
            raise HTTPException(404, "角色不存在")

    @app.post("/api/tickets/liked_gift")
    async def liked_gift(user_id: UserId, body: ForHer) -> dict:
        """One of the gifts she likes, in the backpack: which."""
        her(body.character_id)

        async def use() -> dict:
            try:
                return {"gift_id": gifts.add_liked_gift(user_id, body.character_id)}
            except NoSuchCharacter as e:
                raise HTTPException(404, str(e)) from None

        return await using(user_id, "liked_gift", use)

    @app.post("/api/tickets/letter")
    async def letter(user_id: UserId, body: ForHer) -> dict:
        """A letter from her, in the letters now."""
        her(body.character_id)

        async def use() -> dict:
            if not await backend.letters.write_now(user_id, body.character_id, "ordinary"):
                raise NoConversation()
            return {}

        return await using(user_id, "letter", use)

    @app.post("/api/tickets/diary_key")
    async def diary_key(user_id: UserId, body: DiaryPage) -> dict:
        """The diary page opened, all of it: as it is read now."""

        async def use() -> dict:
            try:
                return {"entry": backend.diary.unlock(user_id, body.entry_id)}
            except NotFound:
                raise HTTPException(404, "没有这一页") from None

        return await using(user_id, "diary_key", use)

    @app.post("/api/tickets/photo")
    async def photo(user_id: UserId, body: PhotoWish) -> dict:
        """She sends the photo: the conversation it comes in. Its picture follows."""
        her(body.character_id)

        async def use() -> dict:
            tags = None
            if body.outfit_id:
                tags = await backend.outfit_tags.of(body.outfit_id)
                look = "私服" if body.look == "everyday" else "演出服"
                wish = f"穿「{body.card or '那张卡'}」的{look}拍一张"
            elif body.described and body.described.strip():
                wish = f"穿这样的衣服拍一张：{body.described.strip()}"
                tags = None
            else:
                wish = "衣服你自己挑，拍一张你最想给他看的样子"
            conversation_id = await trip_photos.ask_photo(user_id, body.character_id, wish, tags)
            return {"conversation_id": conversation_id}

        return await using(user_id, "photo", use)

    @app.post("/api/tickets/date")
    async def date_out(user_id: UserId, body: Invitation) -> dict:
        """She is invited, and answers: the conversation she answers in."""
        her(body.character_id)

        async def use() -> dict:
            conversation_id = await trip_photos.invite(
                user_id, body.character_id, body.day, body.what.strip()
            )
            return {"conversation_id": conversation_id}

        return await using(user_id, "date", use)
