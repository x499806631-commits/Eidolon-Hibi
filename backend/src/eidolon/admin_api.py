"""The admin console's API, under `/api/admin`, for admins only (ADR-0013).

To anyone else, signed in or not, and to everyone when the deployment lists no admins,
every admin route answers 404, as though there were no admin console: not 401 or 403,
which would tell them there is one.
"""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request

from eidolon.accounts import is_admin
from eidolon.assembly import Backend
from eidolon.sessions import session_user
from eidolon.store import User


async def admin_user(request: Request) -> User:
    """The signed-in admin, who has agreed to the terms and is not banned; 404 for anyone
    else."""
    backend: Backend = request.app.state.backend
    if not backend.settings.admins:
        raise HTTPException(404)
    user = await session_user(request)
    if (
        user is None
        or user.consented_at is None
        or user.banned_at is not None
        or not await is_admin(backend.store, backend.settings, user.id)
    ):
        raise HTTPException(404)
    return user


Admin = Annotated[User, Depends(admin_user)]


def add_admin_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/admin")
    def admin(user: Admin) -> dict:
        """The admin console's front door: who the admin is."""
        return {"name": user.name}
