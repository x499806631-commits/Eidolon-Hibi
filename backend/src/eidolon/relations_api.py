"""The relations table over HTTP, for the deployer only (GLOSSARY.md「关系表」): users never
see or edit it; under the development sign-in (`EIDOLON_DEV_LOGIN=1`), or as an admin, the
signed-in user's own table can be read and changed, for trying out or setting it straight.

- `GET /api/relations/{id}`: how she stands with each of the others she has a tie with, in the
  user's story (`canon` as the source has it or as set, `unit`, `changes` newest last), and the
  news she has heard (GLOSSARY.md「传话」).
- `PUT /api/relations/{id}/{other}` `{"note": "…"}`: how she sees the other, instead of the
  source; `{"note": null}` takes it back to the source.
- `POST /api/relations/{id}/{other}/changes` `{"note": "…"}`: a change between the two, as the
  story's would be.
- `DELETE /api/relations/{id}/{other}/changes`: take back every change between the two.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from eidolon.accounts import is_admin
from eidolon.assembly import Backend
from eidolon.relations import MAX_TEXT
from eidolon.sessions import UserId


class Override(BaseModel):
    note: str | None = Field(default=None, max_length=MAX_TEXT)


class Change(BaseModel):
    note: str = Field(min_length=1, max_length=MAX_TEXT)


def add_relation_routes(app: FastAPI, backend: Backend) -> None:
    relations = backend.relations

    async def allowed(user_id: str, *ids: str) -> None:
        if not (backend.settings.dev_login or await is_admin(backend.store, backend.settings, user_id)):
            raise HTTPException(403, "只有部署者能看和改关系表")
        for character_id in ids:
            if character_id not in backend.characters:
                raise HTTPException(404, f"没有这位角色：{character_id}")

    @app.get("/api/relations/{character_id}")
    async def read_relations(user_id: UserId, character_id: str) -> dict:
        await allowed(user_id, character_id)
        return {
            "ties": [t.shown() for t in await relations.ties(user_id, character_id)],
            "heard": [
                {"about_id": n.about_id, "text": n.text, "at": n.at.isoformat()}
                for n in await relations.heard(user_id, character_id)
            ],
        }

    @app.put("/api/relations/{character_id}/{other_id}", status_code=204)
    async def set_relation(user_id: UserId, character_id: str, other_id: str, body: Override) -> None:
        await allowed(user_id, character_id, other_id)
        await relations.override(user_id, character_id, other_id, body.note or None)

    @app.post("/api/relations/{character_id}/{other_id}/changes", status_code=204)
    async def add_change(user_id: UserId, character_id: str, other_id: str, body: Change) -> None:
        await allowed(user_id, character_id, other_id)
        await relations.change(user_id, character_id, other_id, body.note, "deployer")

    @app.delete("/api/relations/{character_id}/{other_id}/changes", status_code=204)
    async def forget_changes(user_id: UserId, character_id: str, other_id: str) -> None:
        await allowed(user_id, character_id, other_id)
        await relations.forget_changes(user_id, character_id, other_id)
