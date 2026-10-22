"""Promises over HTTP (GLOSSARY.md「约定」), for users who agreed to the terms: what the
producer promised each idol and has not done yet (`/api/promises`), with the day each is for;
and saying one was done (`POST /api/promises/{id}/fulfil`), which takes it away as giving her
the gift of its name would."""

from fastapi import FastAPI, HTTPException

from eidolon.assembly import Backend
from eidolon.sessions import UserId


def add_promise_routes(app: FastAPI, backend: Backend) -> None:
    promises = backend.promises
    if promises is None:
        return

    @app.get("/api/promises")
    def list_promises(user_id: UserId) -> list[dict]:
        return [promise.shown() for promise in promises.open_for(user_id)]

    @app.post("/api/promises/{promise_id}/fulfil")
    def fulfil(user_id: UserId, promise_id: int) -> dict:
        if not promises.fulfil(user_id, promise_id):
            raise HTTPException(404, "没有这个约定")
        return {"ok": True}
