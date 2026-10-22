"""Her diary over HTTP (GLOSSARY.md「日记」), for users who agreed to the terms: every
character's entries, newest day first, each as much of it as the user may read now
(`/api/diary`); how many they may read and have not opened (`/api/diary/unread`, for the dot
on the navigation); and opening one, which marks it read if they may read it
(`POST /api/diary/{id}/read`).

Under the development sign-in only (`EIDOLON_DEV_LOGIN=1`), `POST /api/diary/write-now` has
her write today's entry at once, of what it is given, to try the diary out without waiting for
the day to end."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.sessions import UserId
from eidolon.store import NotFound


class WriteNow(BaseModel):
    character_id: str
    what: str = "和制作人聊了很久"
    """What made the day matter, as she is told it."""


def add_diary_routes(app: FastAPI, backend: Backend) -> None:
    diary = backend.diary

    @app.get("/api/diary")
    def list_diary(user_id: UserId) -> list[dict]:
        return diary.entries(user_id)

    @app.get("/api/diary/unread")
    def unread_diary(user_id: UserId) -> dict:
        return {"unread": diary.unread(user_id)}

    @app.post("/api/diary/{entry_id}/read")
    def read_entry(user_id: UserId, entry_id: int) -> dict:
        try:
            return diary.read(user_id, entry_id)
        except NotFound:
            raise HTTPException(404, "没有这一篇日记") from None

    if backend.settings.dev_login:

        @app.post("/api/diary/write-now", status_code=201)
        async def write_now(user_id: UserId, body: WriteNow) -> dict:
            entry = await diary.write_now(user_id, body.character_id, body.what)
            if entry is None:
                raise HTTPException(404, "还没有和她的对话")
            return diary.read(user_id, entry.id)
