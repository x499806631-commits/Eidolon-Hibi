"""The user's data package over HTTP (GLOSSARY.md「数据包」): exported at
`GET /api/account/data-package?include=…` as a download, imported by posting the zip itself
to the same address. Only the signed-in user's own, never anyone else's, admins' neither
(ADR-0013). Neither is recorded; a failure is logged without anything of the package.

`include` names the categories, comma-separated (`data_package.CATEGORIES`). Refusals carry
a `code` beside the `detail`, for the page to tell them apart: `too_new` (422) for a package
from a newer version, `broken` (422) for anything else wrong with it, `too_large` (413) for
one past `data_package.MAX_PACKAGE_BYTES`, `busy` (409) while the user is already importing
or exporting one, and `used_up` (429, with `retry_at`) once they have made today's imports.

The settings page downloads a package by sending the browser to it, so that it is never held
in the page's memory: a refusal there would be a page of JSON. It asks
`GET /api/account/data-package/export-check?include=…` first, refused as the export would be
but for what happens while the package is written, and shows a refusal itself."""

import logging
import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from eidolon.affinity import day_of
from eidolon.assembly import Backend
from eidolon import data_package
from eidolon.data_package import (
    CATEGORIES,
    BrokenPackage,
    Busy,
    Category,
    ImportsUsedUp,
    TooLarge,
    TooNew,
)
from eidolon.sessions import UserId

log = logging.getLogger(__name__)

_TOO_NEW = "这个数据包来自更新的版本，请部署者升级后再导入"
_BROKEN = "这不是一个能用的数据包"
_TOO_LARGE = "数据包太大了（最大 200 MB），可以不带照片重新导出"
_BUSY = "已经有一个数据包在导入或导出"


def _categories(include: str) -> list[Category]:
    """The categories `include` names; 422 for one there is no such category."""
    names = [name.strip() for name in include.split(",") if name.strip()]
    unknown = [name for name in names if name not in CATEGORIES]
    if unknown:
        raise HTTPException(422, f"没有这一类：{'、'.join(unknown)}")
    return [cast(Category, name) for name in names]


def _declared_length(request: Request) -> int:
    """The length the request says its body is; 0 if it does not say."""
    try:
        return int(request.headers.get("content-length") or 0)
    except ValueError:
        return 0


def _temporary_file() -> Path:
    handle, name = tempfile.mkstemp(prefix="eidolon-data-package-", suffix=".zip")
    os.close(handle)
    return Path(name)


def add_data_package_routes(app: FastAPI, backend: Backend) -> None:
    packages, clock = backend.data_packages, backend.clock

    @app.get("/api/account/data-package/export-check", status_code=204)
    async def check_data_package_export(user_id: UserId, include: str = "") -> None:
        """Whether the user could export a package with the categories `include` names now:
        204 if so, refused as the export would be if not."""
        _categories(include)
        packages.check_not_busy(user_id)

    @app.get("/api/account/data-package")
    async def export_data_package(user_id: UserId, include: str = "") -> FileResponse:
        """The user's data package with the categories `include` names, as a download named
        after the day it is in Beijing, as the deployment's days are: written to a temporary
        file first, streamed from it, then deleted. One at a time for each user, as imports
        are."""
        categories = _categories(include)
        path = _temporary_file()
        try:
            with packages.one_at_a_time(user_id):
                await packages.export(user_id, categories, path)
        except BaseException as e:
            path.unlink(missing_ok=True)
            if not isinstance(e, Busy):
                log.error("exporting a data package failed", exc_info=True)
            raise
        return FileResponse(
            path,
            media_type="application/zip",
            filename=f"eidolon-data-package-{day_of(clock()).isoformat()}.zip",  # Beijing's day
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/account/data-package")
    async def import_data_package(request: Request, user_id: UserId, include: str = "") -> dict:
        """Import the package in the request body, the zip itself, into the user's data: the
        categories both `include` names and the package holds. What it added
        (`data_package.Imported`). One at a time for each user, and so many a day; the body
        counted as it is read, refused once past the largest a package may be."""
        categories = _categories(include)
        limit = data_package.MAX_PACKAGE_BYTES
        if _declared_length(request) > limit:
            raise TooLarge()
        path = _temporary_file()
        try:
            with packages.one_at_a_time(user_id):
                await packages.check_imports_left(user_id)
                with path.open("wb") as file:
                    size = 0
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > limit:
                            raise TooLarge()
                        file.write(chunk)
                imported = await packages.import_(user_id, path, categories)
        except (Busy, ImportsUsedUp, TooLarge, TooNew, BrokenPackage):
            raise
        except Exception:
            log.error("importing a data package failed", exc_info=True)
            raise
        finally:
            path.unlink(missing_ok=True)
        return asdict(imported)

    @app.exception_handler(TooNew)
    async def too_new(_: Request, e: TooNew) -> JSONResponse:
        log.info("refused a data package from a newer version")
        return JSONResponse({"detail": _TOO_NEW, "code": "too_new"}, status_code=422)

    @app.exception_handler(Busy)
    async def busy(_: Request, e: Busy) -> JSONResponse:
        return JSONResponse({"detail": _BUSY, "code": "busy"}, status_code=409)

    @app.exception_handler(ImportsUsedUp)
    async def used_up(_: Request, e: ImportsUsedUp) -> JSONResponse:
        """As other limits are refused (`app.over_limit`), with when the user can import
        again (also as `Retry-After`)."""
        wait = max(1, math.ceil((e.retry_at - e.now).total_seconds()))
        return JSONResponse(
            {"detail": str(e), "retry_at": e.retry_at.isoformat(), "code": "used_up"},
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    @app.exception_handler(TooLarge)
    async def too_large(_: Request, e: TooLarge) -> JSONResponse:
        log.info("refused a data package too large")
        return JSONResponse({"detail": _TOO_LARGE, "code": "too_large"}, status_code=413)

    @app.exception_handler(BrokenPackage)
    async def broken(_: Request, e: BrokenPackage) -> JSONResponse:
        log.info("refused a broken data package: %s", e)
        return JSONResponse({"detail": _BROKEN, "code": "broken"}, status_code=422)
