"""Titles over HTTP (GLOSSARY.md「称号」), for users who agreed to the terms:

- `GET /api/badges`: every title, earned or not, how each is earned, and what they give.
- `GET /api/badges/unseen`: how many earned titles the user has not looked at yet.
- `POST /api/badges/seen`: the user looked at them.
"""

from fastapi import FastAPI

from eidolon.assembly import Backend
from eidolon.sessions import UserId


def add_badge_routes(app: FastAPI, backend: Backend) -> None:
    badges = backend.badges

    @app.get("/api/badges")
    async def read(user_id: UserId) -> dict:
        return await badges.shown(user_id)

    @app.get("/api/badges/unseen")
    async def unseen(user_id: UserId) -> dict:
        await badges.check(user_id)
        return {"unseen": await badges.unseen(user_id)}

    @app.post("/api/badges/seen")
    async def seen(user_id: UserId) -> dict:
        await badges.seen(user_id)
        return {}
