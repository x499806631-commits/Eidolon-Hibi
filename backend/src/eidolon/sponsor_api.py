"""Sponsoring over HTTP (GLOSSARY.md「赞赏」): admins switch it, upload the code and clear both
under `/api/admin/sponsor`, which answers 404 to anyone else as the rest of the admin
console does; users read the code at `/api/sponsor/image` while they are shown it, and
its version from `/api/account`. The code's address carries its version, when it was
uploaded (`?v=`), so a browser keeps each for good and reads a new one anew; asked for a
version since replaced, it is given the new one, not to keep."""

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, StrictBool

from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.sessions import UserId
from eidolon.sponsor import MAX_UPLOAD_BYTES, NotAPicture, SponsorState

_CODE_CACHE = {"Cache-Control": "private, max-age=31536000, immutable"}
"""A code's picture never changes for its version."""

_NOT_KEPT = {"Cache-Control": "no-store"}

NOT_FOUND = "没有收款码"

_TOO_LARGE = f"图片不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB"


class SponsorInput(BaseModel):
    enabled: StrictBool


def _described(state: SponsorState) -> dict:
    return {"enabled": state.enabled, "image_at": state.image_at}


def add_sponsor_routes(app: FastAPI, backend: Backend) -> None:
    sponsor = backend.sponsor

    async def code_response(asked: str | None) -> Response:
        """The code there is; kept for good only if it is the version `asked` for."""
        found = await sponsor.image()
        if found is None:
            raise HTTPException(404, NOT_FOUND)
        image, version = found
        headers = _CODE_CACHE if asked == version else _NOT_KEPT
        return Response(image, media_type="image/png", headers=headers)

    @app.get("/api/admin/sponsor")
    async def state(_: Admin) -> dict:
        return _described(await sponsor.state())

    @app.put("/api/admin/sponsor")
    async def switch(body: SponsorInput, _: Admin) -> dict:
        """Switch it on or off; on without a code, users see nothing yet."""
        return _described(await sponsor.switch(body.enabled))

    @app.put("/api/admin/sponsor/image")
    async def upload(request: Request, _: Admin) -> dict:
        """Keep the picture in the request body as the code, replacing the one there was:
        413 past `MAX_UPLOAD_BYTES`, 422 unless a PNG, JPEG or WebP."""
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, _TOO_LARGE)
        try:
            return _described(await sponsor.upload(bytes(body)))
        except NotAPicture:
            raise HTTPException(422, "不是 PNG、JPEG 或 WebP 图片") from None

    @app.delete("/api/admin/sponsor")
    async def clear(_: Admin) -> dict:
        """Switch it off and delete the code."""
        return _described(await sponsor.clear())

    @app.get("/api/admin/sponsor/image")
    async def admin_code(_: Admin, v: str | None = None) -> Response:
        """The code, switched on or not."""
        return await code_response(v)

    @app.get("/api/sponsor/image")
    async def user_code(_: UserId, v: str | None = None) -> Response:
        """The code, only while users are shown it."""
        if (await sponsor.state()).version is None:
            raise HTTPException(404, NOT_FOUND)
        return await code_response(v)
