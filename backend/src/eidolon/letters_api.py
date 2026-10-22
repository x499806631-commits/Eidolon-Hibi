"""Letters over HTTP (GLOSSARY.md「来信」), for users who agreed to the terms: the user's
letters come by now, newest first (`/api/letters`, only hers with `?character=`); how many are unread
(`/api/letters/unread`, for the dot on the navigation); and reading one, which marks it read
(`POST /api/letters/{id}/read`). Delivery cues are left out: letters are read, not voiced. A
letter a keepsake came with carries it (`keepsake`, GLOSSARY.md「纪念品」), None for one without.

Under the development sign-in only (`EIDOLON_DEV_LOGIN=1`), `POST /api/letters/write-now` has
her write one at once, to try letters out without waiting for the schedule."""

from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.sessions import UserId
from eidolon.store import Letter, NotFound


class WriteNow(BaseModel):
    character_id: str
    occasion: Literal["ordinary", "producer_birthday", "her_birthday", "holiday"] = "ordinary"
    about: str = ""
    """For a holiday: which, with its note, as 「七夕。牛郎织女相会的日子。」."""


def add_letter_routes(app: FastAPI, backend: Backend) -> None:
    store, clock, keepsakes = backend.store, backend.clock, backend.keepsakes

    def shown(user_id: str, letter: Letter) -> dict:
        keepsake = keepsakes.for_letter(user_id, letter.id)
        return letter.shown() | {"keepsake": keepsake.shown() if keepsake else None}

    @app.get("/api/letters")
    def list_letters(user_id: UserId, character: str | None = None) -> list[dict]:
        return [shown(user_id, letter) for letter in store.letters(user_id, character, clock())]

    @app.get("/api/letters/unread")
    def unread_letters(user_id: UserId) -> dict:
        return {"unread": store.unread_letters(user_id, clock())}

    @app.post("/api/letters/{letter_id}/read")
    def read_letter(user_id: UserId, letter_id: int) -> dict:
        try:
            return shown(user_id, store.read_letter(user_id, letter_id, clock()))
        except NotFound:
            raise HTTPException(404, "没有这封信") from None

    if backend.settings.dev_login:

        @app.post("/api/letters/write-now", status_code=201)
        async def write_now(user_id: UserId, body: WriteNow) -> dict:
            if not await backend.letters.write_now(
                user_id, body.character_id, body.occasion, body.about
            ):
                raise HTTPException(404, "还没有和她的对话")
            return {"ok": True}
