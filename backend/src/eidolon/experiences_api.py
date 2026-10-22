"""Her own doings over HTTP (GLOSSARY.md「她们的经历」), for users who agreed to the terms: what
one character said she had done (`/api/experiences?character=`), everyone's that are about to be
forgotten, soonest first and with whose they are (`/api/experiences?due_soon=true`), and saying
of one that it stays — recalled again (`POST /{id}/keep`), kept for good (`PATCH /{id}`), or
gone (`DELETE /{id}`)."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.assembly import Backend
from eidolon.sessions import UserId


class PinBody(BaseModel):
    """Whether it is kept for good, out of time's way."""

    pinned: bool


def add_experience_routes(app: FastAPI, backend: Backend) -> None:
    experiences, clock = backend.experiences, backend.clock
    if experiences is None:
        return

    def name_of(character_id: str) -> str:
        character = backend.characters.get(character_id)
        return character.name if character else character_id

    @app.get("/api/experiences")
    def list_experiences(
        user_id: UserId, character: str | None = None, due_soon: bool = False
    ) -> list[dict]:
        now = clock()
        if due_soon:
            # 跨所有角色：快到期的那几条，带着是谁的
            return [
                e.shown(now) | {"character_name": name_of(e.character_id)}
                for e in experiences.due_soon(user_id)
            ]
        if character is None:
            return []
        return [e.shown(now) for e in experiences.of(user_id, character)]

    @app.post("/api/experiences/{experience_id}/keep")
    def keep_experience(user_id: UserId, experience_id: int) -> dict:
        """Kept thirty days more, as being recalled keeps it."""
        if not experiences.keep(user_id, experience_id):
            raise HTTPException(404, "没有这条经历")
        return {"ok": True}

    @app.patch("/api/experiences/{experience_id}")
    def pin_experience(user_id: UserId, experience_id: int, body: PinBody) -> dict:
        """Kept for good, or counted from when it was last recalled again."""
        if not experiences.pin(user_id, experience_id, body.pinned):
            raise HTTPException(404, "没有这条经历")
        return {"ok": True}

    @app.delete("/api/experiences/{experience_id}", status_code=204)
    def delete_experience(user_id: UserId, experience_id: int) -> None:
        if not experiences.delete(user_id, experience_id):
            raise HTTPException(404, "没有这条经历")
