"""The backups on the admin's dashboard (GLOSSARY.md「备份」), under `/api/admin/backups`:
how they stand, worked out here for the browser only to show, and the button that begins
one in the server's process, the same backup run as the cron's `eidolon backup`, its
output in the server's log rather than `logs/db-backup.log`. The settings are read from
the environment each time, as `eidolon backup` reads them; they are changed only in
`.env`."""

from fastapi import FastAPI, HTTPException

from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.backup import BUSY_MESSAGE, BackupRecord, BackupStatus


def add_admin_backup_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/admin/backups")
    async def backup_status(_: Admin) -> dict:
        """Whether backups are set up, how the last ones went, which is under way, and
        what is wrong, if anything."""
        return _status(await backend.backups.status())

    @app.post("/api/admin/backups")
    async def back_up_now(_: Admin) -> dict:
        """Begin a backup in the background; 409 if one is under way, 400 if the settings
        will not do. How the backups stand, with it under way."""
        try:
            began = await backend.backups.begin("admin")
        except RuntimeError as e:
            raise HTTPException(400, str(e)) from None
        if not began:
            raise HTTPException(409, BUSY_MESSAGE)
        return _status(await backend.backups.status())


def _status(status: BackupStatus) -> dict:
    settings, under_way, success, last = (
        status.settings, status.under_way, status.last_success, status.last
    )
    return {
        "configured": status.any_set,
        "config_error": status.config_error,
        "keep_days": settings.keep_days if settings else None,
        "warn_gb": settings.warn_gb if settings else None,
        "under_way": under_way and {"source": under_way.source, "started_at": under_way.started_at},
        "last_success": success and {
            "source": success.source,
            "started_at": success.started_at,
            "size": success.size,
        },
        "last": last and _run(last),
        "bucket": status.listed and {
            "kept": status.listed.kept,
            "total": status.listed.total,
            "over_warning": status.over_warning,
        },
        "problem": status.problem,
    }


def _run(record: BackupRecord) -> dict:
    return {
        "source": record.source,
        "started_at": record.started_at,
        "errors": [{"stage": stage, "error": error} for stage, error in record.errors],
    }
