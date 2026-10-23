"""Announcements over HTTP (GLOSSARY.md「公告」): admins write, publish, withdraw, pin and
delete them under `/api/admin/announcements`, which answers 404 to anyone else as the
rest of the admin console does; users who agreed to the terms see the one pinned and
the latest published at `/api/announcements`."""

from collections.abc import Awaitable
from typing import TypeVar

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from eidolon.admin_api import Admin
from eidolon.announcements import MAX_BODY, NoSuchAnnouncement, NotPublished
from eidolon.assembly import Backend
from eidolon.sessions import UserId

T = TypeVar("T")


class AnnouncementInput(BaseModel):
    body: str
    """Markdown; its whitespace at either end is dropped."""


def add_announcement_routes(app: FastAPI, backend: Backend) -> None:
    announcements = backend.announcements

    def body_of(given: AnnouncementInput) -> str:
        """The body to keep; 422 if it is empty or too long."""
        body = given.body.strip()
        if not body:
            raise HTTPException(422, "公告正文不能为空")
        if len(body) > MAX_BODY:
            raise HTTPException(422, f"公告正文最多 {MAX_BODY} 字")
        return body

    async def found(find: Awaitable[T]) -> T:
        """What `find` finds; 404 if there is no such announcement."""
        try:
            return await find
        except NoSuchAnnouncement:
            raise HTTPException(404, "没有这条公告") from None

    @app.get("/api/admin/announcements")
    async def list_announcements(_: Admin) -> list[dict]:
        """Every announcement, drafts first, then the latest published."""
        return [a.described() for a in await announcements.all()]

    @app.post("/api/admin/announcements", status_code=201)
    async def write(body: AnnouncementInput, _: Admin) -> dict:
        """A new draft."""
        return (await announcements.write(body_of(body))).described()

    @app.get("/api/admin/announcements/{id}")
    async def announcement(id: str, _: Admin) -> dict:
        return (await found(announcements.get(id))).described()

    @app.put("/api/admin/announcements/{id}")
    async def edit(id: str, body: AnnouncementInput, _: Admin) -> dict:
        """Replace its body; one published stays published as of when it was."""
        text = body_of(body)
        return (await found(announcements.edit(id, text))).described()

    @app.post("/api/admin/announcements/{id}/publish")
    async def publish(id: str, _: Admin) -> dict:
        """Publish it now, for every user to see; nothing for one published already."""
        return (await found(announcements.publish(id))).described()

    @app.delete("/api/admin/announcements/{id}/publish")
    async def withdraw(id: str, _: Admin) -> dict:
        """Make it a draft again, not pinned; published again later, it is new."""
        return (await found(announcements.withdraw(id))).described()

    @app.post("/api/admin/announcements/{id}/pin")
    async def pin(id: str, _: Admin) -> dict:
        """Make it the one users see first, unpinning the one that was; 409 for a draft."""
        try:
            return (await found(announcements.pin(id))).described()
        except NotPublished:
            raise HTTPException(409, "草稿不能置顶，先发布") from None

    @app.delete("/api/admin/announcements/{id}/pin")
    async def unpin(id: str, _: Admin) -> dict:
        """Make it no longer the one users see first."""
        return (await found(announcements.unpin(id))).described()

    @app.delete("/api/admin/announcements/{id}", status_code=204)
    async def delete(id: str, _: Admin) -> None:
        await found(announcements.delete(id))

    @app.get("/api/announcements")
    async def published(_: UserId) -> list[dict]:
        """The one pinned, if any, then the latest published, newest first."""
        return [a.shown() for a in await announcements.published()]
