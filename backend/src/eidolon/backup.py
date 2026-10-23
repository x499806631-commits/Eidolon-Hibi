"""Backing the database up (GLOSSARY.md「备份」, `eidolon backup`, ADR-0012), once a day
from the host's cron through `deploy/db-backup.sh`.

The whole database, story corpus and all, is copied as it is at one moment without
stopping the server (`VACUUM INTO`), the copy checked, compressed, and encrypted to the
deployer's age public key, whose private key is kept off the server; then put in a
private S3-compatible bucket, apart from the public one the stands are in, under a name
saying when it was made (`backup_key`). Backups past the retention, 30 days unless the
deployer says less, are deleted every time, even when this one could not be made, save
the newest, so the bucket is never emptied; that is what keeps a deleted user's data in
backups no longer than 30 days. How many are left and how large they are is reported,
with a warning over the size the deployer set.

Each run, the cron's, the deployer's or an admin's in the admin console, is recorded in
the database (`Backups`), and the record of one under way keeps another from beginning.

Encrypted with pyrage, the Python binding of rage, a Rust implementation of age: it
comes as wheels for the image's platforms, needs nothing installed beside it, encrypts a
file a chunk at a time, and what it writes the deployer decrypts with `age` or `rage`
alike."""

import asyncio
import gzip
import logging
import math
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx
import pyrage

from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.database import connect
from eidolon.s3 import NEEDED, Bucket, S3Error, S3Settings

log = logging.getLogger(__name__)

MOST_DAYS_KEPT = 30
"""The longest backups may be kept, and how long they are unless the deployer says less:
what the privacy policy promises a deleted user (ADR-0012, revised 2026-10-15). The newest
is kept however old it is."""

_NAME = re.compile(r"eidolon-(\d{8}T\d{6}Z)\.db\.gz\.age")
_TIME_FORMAT = "%Y%m%dT%H%M%SZ"

GB = 1_000_000_000
"""Bytes in a gigabyte, as storage is billed."""

_PING_TIMEOUT = 10
"""Seconds."""

SUCCEEDED, MISCONFIGURED, EXPORT_FAILED, UPLOAD_FAILED, PRUNE_FAILED = range(5)
"""The exit codes of `eidolon backup`: when more than one thing failed, the first of
these that did."""
BUSY = 6
"""The exit code of `eidolon backup` when another backup was under way; 5 is
`deploy/db-backup.sh`'s own, for when it could not run the command at all."""

Source = Literal["cron", "manual", "admin"]
"""Who began a backup run: the host's daily cron, the deployer running
`deploy/db-backup.sh` by hand, or an admin with the admin console's button."""

STALE = timedelta(hours=1)
"""How long after a run began, unfinished, another may begin all the same, its process
taken to have been killed: a backup takes some 3 minutes (2026-10-15)."""

RECORDS_KEPT = timedelta(days=90)
"""How long the record of a run is kept, as activity records are."""

OVERDUE = timedelta(hours=26)
"""How long after the last backup that went well the dashboard says something is wrong:
the cron's are a day apart, and one takes minutes."""

BUSY_MESSAGE = "上一次备份还没结束（cron、手动或后台按钮发起的），这次没有运行"

_BUCKET_PREFIX = "EIDOLON_BACKUP_S3_"


@dataclass(frozen=True)
class BackupSettings:
    bucket: S3Settings
    recipient: pyrage.x25519.Recipient
    """The deployer's age public key; the private key is not on the server."""
    ping_url: str | None = None
    """Visited after each backup that went well, for a service such as healthchecks.io
    to say when the backups stop."""
    keep_days: int = MOST_DAYS_KEPT
    """How many days a backup is kept, 1 to `MOST_DAYS_KEPT`."""
    warn_gb: float | None = None
    """Gigabytes (10⁹ bytes) of backups in the bucket over which each backup warns; None
    for no warning."""

    @classmethod
    def from_env(cls) -> "BackupSettings":
        """From `EIDOLON_BACKUP_S3_*`, `EIDOLON_BACKUP_AGE_RECIPIENT` and, if they are
        set, `EIDOLON_BACKUP_PING_URL`, `EIDOLON_BACKUP_KEEP_DAYS` and
        `EIDOLON_BACKUP_WARN_GB`."""
        bucket = S3Settings.from_env(_BUCKET_PREFIX, "备份数据库")
        recipient = (os.environ.get("EIDOLON_BACKUP_AGE_RECIPIENT") or "").strip()
        if not recipient:
            raise RuntimeError("备份数据库需要 age 公钥，环境变量 EIDOLON_BACKUP_AGE_RECIPIENT 未设置")
        try:
            parsed = pyrage.x25519.Recipient.from_str(recipient)
        except pyrage.RecipientError:
            raise RuntimeError(
                "环境变量 EIDOLON_BACKUP_AGE_RECIPIENT 应为 age1 开头的 age 公钥"
                "（age-keygen 输出的 public key，不是 AGE-SECRET-KEY 开头的私钥）"
            ) from None
        ping_url = (os.environ.get("EIDOLON_BACKUP_PING_URL") or "").strip() or None
        if ping_url is not None and not ping_url.startswith(("https://", "http://")):
            raise RuntimeError(f"环境变量 EIDOLON_BACKUP_PING_URL 应为 http:// 或 https:// 开头的地址，而不是 {ping_url}")
        days = (os.environ.get("EIDOLON_BACKUP_KEEP_DAYS") or "").strip() or str(MOST_DAYS_KEPT)
        if not re.fullmatch(r"\d+", days) or not 1 <= int(days) <= MOST_DAYS_KEPT:
            raise RuntimeError(
                f"环境变量 EIDOLON_BACKUP_KEEP_DAYS 应为 1 到 {MOST_DAYS_KEPT} 的整数天数，而不是 {days}"
                f"（最长 {MOST_DAYS_KEPT} 天，是隐私政策的承诺：删除的数据最多 {MOST_DAYS_KEPT} 天后从备份里消失）"
            )
        warn = (os.environ.get("EIDOLON_BACKUP_WARN_GB") or "").strip()
        try:
            warn_gb = float(warn) if warn else None
        except ValueError:
            warn_gb = math.nan
        if warn_gb is not None and not (math.isfinite(warn_gb) and warn_gb > 0):
            raise RuntimeError(
                f"环境变量 EIDOLON_BACKUP_WARN_GB 应为正数（单位 GB，可以是小数），而不是 {warn}"
            )
        return cls(bucket, parsed, ping_url, int(days), warn_gb)

    @staticmethod
    def any_set() -> bool:
        """Whether the environment has any of the settings a backup needs: a deployment
        with none has not set backups up, one with some has set them up wrong."""
        needed = [f"{_BUCKET_PREFIX}{name}" for name in NEEDED] + ["EIDOLON_BACKUP_AGE_RECIPIENT"]
        return any((os.environ.get(name) or "").strip() for name in needed)


@dataclass
class BackupReport:
    key: str | None = None
    """The backup made this time, if it was."""
    size: int = 0
    """Its bytes, compressed and encrypted."""
    deleted: list[str] = field(default_factory=list)
    """The backups past the retention deleted this time."""
    kept: int | None = None
    """The backups left in the bucket, this one among them; None if it could not be
    listed."""
    total: int | None = None
    """Their bytes; None if they could not be listed, or the bucket did not say the size
    of each."""
    space_warning: str | None = None
    """Why the backups left take more space than the deployer would be warned over;
    only a warning, as a ping that failed is."""
    export_error: str | None = None
    upload_error: str | None = None
    prune_error: str | None = None
    ping_error: str | None = None
    """A ping that failed only warns: the backup was made all the same."""
    config_error: str | None = None
    """Why the settings would not do, when a run did nothing for it."""
    busy: bool = False
    """Whether a run did nothing for another being under way."""

    @property
    def exit_code(self) -> int:
        if self.config_error is not None:
            return MISCONFIGURED
        if self.busy:
            return BUSY
        if self.export_error is not None:
            return EXPORT_FAILED
        if self.upload_error is not None:
            return UPLOAD_FAILED
        if self.prune_error is not None:
            return PRUNE_FAILED
        return SUCCEEDED


def describe(report: BackupReport) -> list[tuple[bool, str]]:
    """What a run did, a line at a time, each with whether it says something went wrong or
    is a warning: what `eidolon backup` prints, and the server logs of the button's runs."""
    if report.config_error is not None:
        return [(True, report.config_error)]
    if report.busy:
        return [(True, BUSY_MESSAGE)]
    lines: list[tuple[bool, str]] = []
    if report.export_error is not None:
        lines.append((True, f"没有备份：{report.export_error}"))
    if report.upload_error is not None:
        lines.append((True, f"备份没有上传：{report.upload_error}"))
    if report.key is not None:
        lines.append((False, f"已备份为 {report.key}（压缩加密后 {report.size / 1_000_000:.1f} MB）"))
    deleted = f"：{'、'.join(report.deleted)}" if report.deleted else ""
    if report.prune_error is not None:
        lines.append((
            True,
            f"没能删完过期的备份（已删除 {len(report.deleted)} 份{deleted}），下次备份再删：{report.prune_error}",
        ))
    else:
        lines.append((False, f"删除了 {len(report.deleted)} 份过期的备份{deleted}"))
    if report.kept is not None:
        size = f"，共 {report.total / GB:.2f} GB" if report.total is not None else "（存储桶没有列出大小）"
        lines.append((False, f"存储桶里现有 {report.kept} 份备份{size}"))
    if report.space_warning is not None:
        lines.append((True, f"注意：{report.space_warning}"))
    if report.ping_error is not None:
        lines.append((True, f"注意：{report.ping_error}"))
    return lines


def backup_key(made_at: datetime) -> str:
    """The name in the bucket of the backup made at `made_at`, in UTC."""
    return f"eidolon-{made_at.astimezone(UTC):{_TIME_FORMAT}}.db.gz.age"


async def back_up(
    database: Path,
    settings: BackupSettings,
    *,
    now: datetime,
    transport: httpx.AsyncBaseTransport | None = None,
) -> BackupReport:
    """Back the database at `database` up to the bucket, delete the backups past the
    retention, and ping if all went well; what went wrong is in the report, not raised.
    The temporary files are deleted whatever happens."""
    report = BackupReport()
    bucket = Bucket(settings.bucket, transport=transport)
    try:
        with tempfile.TemporaryDirectory(prefix="eidolon-backup-") as work:
            try:
                # In a thread: in the server, its event loop has calls to keep going.
                encrypted = await asyncio.to_thread(
                    _export, database, Path(work), settings.recipient
                )
            except _ExportFailed as e:
                report.export_error = str(e)
            else:
                key = backup_key(now)
                try:
                    await bucket.put_file(key, encrypted, "application/octet-stream")
                except S3Error as e:
                    report.upload_error = str(e)
                else:
                    report.key, report.size = key, encrypted.stat().st_size
        try:
            await _prune(bucket, now - timedelta(days=settings.keep_days), report)
        except S3Error as e:
            report.prune_error = str(e)
        if (
            settings.warn_gb is not None
            and report.total is not None
            and report.total > settings.warn_gb * GB
        ):
            report.space_warning = (
                f"存储桶里的备份共 {report.total / GB:.2f} GB，"
                f"超过了 EIDOLON_BACKUP_WARN_GB 设的 {settings.warn_gb:g} GB"
            )
    finally:
        await bucket.aclose()
    if report.exit_code == SUCCEEDED and settings.ping_url is not None:
        report.ping_error = await _ping(settings.ping_url, transport)
    return report


_SCHEMA = """
-- One row a backup run (`Backups`): when it began, and when it finished, NULL while it is
-- under way, which keeps another from beginning; abandoned if it was taken never to
-- finish. Nothing in it is any user's.
CREATE TABLE IF NOT EXISTS backup_runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    abandoned INTEGER NOT NULL DEFAULT 0,
    exit_code INTEGER,
    key TEXT,
    size INTEGER,
    kept INTEGER,
    total INTEGER,
    over_warning INTEGER NOT NULL DEFAULT 0,
    config_error TEXT,
    export_error TEXT,
    upload_error TEXT,
    prune_error TEXT
);
"""

_COLUMNS = (
    "source, started_at, finished_at, abandoned, exit_code, key, size, kept, total,"
    " over_warning, config_error, export_error, upload_error, prune_error"
)


Stage = Literal["config", "export", "upload", "prune", "abandoned"]
"""Where a run went wrong: its settings would not do, the database was not exported or
the backup not uploaded, old backups not deleted; or it was taken never to finish."""


@dataclass(frozen=True)
class BackupRecord:
    """A backup run, as the database keeps it."""

    source: Source
    started_at: str
    """ISO 8601, as is `finished_at`."""
    finished_at: str | None
    """None while it is under way."""
    abandoned: bool
    """Whether it was taken never to finish (`abandon_unfinished`)."""
    exit_code: int | None
    """`eidolon backup`'s; None until it finished, and if it was abandoned."""
    key: str | None
    """The backup it made, if it did, and its bytes."""
    size: int | None
    kept: int | None
    """The backups it left in the bucket, and their bytes; None if it did not list them."""
    total: int | None
    over_warning: bool
    """Whether those took more space than the deployer would be warned over."""
    config_error: str | None
    export_error: str | None
    upload_error: str | None
    prune_error: str | None

    @property
    def errors(self) -> list[tuple[Stage, str | None]]:
        """What went wrong, in the order of the exit codes; an abandoned run's only that
        it was, with no reason. Empty for a run that went well."""
        if self.abandoned:
            return [("abandoned", None)]
        errors: list[tuple[Stage, str | None]] = [
            ("config", self.config_error),
            ("export", self.export_error),
            ("upload", self.upload_error),
            ("prune", self.prune_error),
        ]
        return [(stage, error) for stage, error in errors if error is not None]


Problem = Literal["unconfigured", "misconfigured", "never_succeeded", "overdue"]
"""What is wrong with the backups, as the dashboard says: none of their settings set, or
some set wrong; or none has gone well, or the last that did is over `OVERDUE` ago."""


@dataclass(frozen=True)
class BackupStatus:
    """The backups as the dashboard shows them."""

    any_set: bool
    """Whether any of the settings a backup needs is set."""
    settings: BackupSettings | None
    """None when they will not do, or none are set."""
    config_error: str | None
    """Why they will not do, if any is set."""
    under_way: BackupRecord | None
    """The run begun within `STALE` and not finished, if there is one."""
    last_success: BackupRecord | None
    """The latest run to go well (exit code 0)."""
    last: BackupRecord | None
    """The latest run that is not under way, however it went; one never finished,
    abandoned."""
    listed: BackupRecord | None
    """The latest run that listed the bucket: what is in it."""
    problem: Problem | None

    @property
    def over_warning(self) -> bool:
        """Whether the backups listed take more space than the deployer, as they say
        now, would be warned over."""
        return (
            self.settings is not None
            and self.settings.warn_gb is not None
            and self.listed is not None
            and self.listed.total is not None
            and self.listed.total > self.settings.warn_gb * GB
        )


class Backups:
    """The backups of the database `db` is on, and the record of each run kept in it,
    which lets only one run be under way at a time, whether it is `eidolon backup` in a
    process of its own or the admin console's button in the server's. The lock is in the
    database rather than a lock file: the two processes share it, and on Windows, where
    the server is run in development, there is no `fcntl`."""

    def __init__(
        self,
        db: Database,
        clock: Clock,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._db = db
        self._clock = clock
        self._transport = transport
        self._background: set[asyncio.Task[None]] = set()
        db.prepare(_create)

    async def run(
        self,
        source: Source,
        settings: Callable[[], BackupSettings] = BackupSettings.from_env,
    ) -> BackupReport:
        """Back the database up as `back_up` does, with the `settings` read now, unless
        they will not do or another run is under way, and record it; a run that did
        nothing for the settings is recorded too, one that did nothing for another is
        not."""
        try:
            loaded = settings()
        except RuntimeError as e:
            report = BackupReport(config_error=str(e))
            await self._finish(await self._begin(source), report)
            return report
        run = await self.start(source)
        if run is None:
            return BackupReport(busy=True)
        return await self._carry_out(run, loaded)

    async def begin(
        self,
        source: Source,
        settings: Callable[[], BackupSettings] = BackupSettings.from_env,
    ) -> bool:
        """Begin a run as `run` does, on the running event loop in the background, its
        report logged once it is done; whether it began, not if another was under way.
        Raises RuntimeError, having recorded nothing, if the settings will not do. `aclose`
        abandons it if it has not finished."""
        loaded = settings()
        run = await self.start(source)
        if run is None:
            return False
        task = asyncio.create_task(self._in_background(run, loaded))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return True

    async def status(self) -> BackupStatus:
        """The backups as they are now, with their settings read from the environment
        now. A run begun over `STALE` ago and not finished is taken never to finish, as
        the next run to begin will take it."""
        any_set = BackupSettings.any_set()
        loaded, config_error = None, None
        if any_set:
            try:
                loaded = BackupSettings.from_env()
            except RuntimeError as e:
                config_error = str(e)
        stale = (self._clock() - STALE).isoformat()
        under_way, finished = None, []
        for record in await self.records():
            if record.finished_at is not None:
                finished.append(record)
            elif record.started_at > stale:
                under_way = under_way or record
            else:
                finished.append(replace(record, abandoned=True))
        last_success = next((r for r in finished if r.exit_code == SUCCEEDED), None)
        problem: Problem | None = None
        if not any_set:
            problem = "unconfigured"
        elif config_error is not None:
            problem = "misconfigured"
        elif last_success is None:
            problem = "never_succeeded"
        elif datetime.fromisoformat(last_success.started_at) < self._clock() - OVERDUE:
            problem = "overdue"
        return BackupStatus(
            any_set=any_set,
            settings=loaded,
            config_error=config_error,
            under_way=under_way,
            last_success=last_success,
            last=next(iter(finished), None),
            listed=next((r for r in finished if r.kept is not None), None),
            problem=problem,
        )

    async def start(self, source: Source) -> int | None:
        """Record a run begun now, unless another is under way (begun within `STALE` and
        not finished): its id, or None if there was one. Takes those begun before that
        and not finished never to finish, and forgets the records older than
        `RECORDS_KEPT`."""
        return await self._begin(source, exclusive=True)

    async def records(self) -> list[BackupRecord]:
        """Every run recorded, the latest first."""
        return await self._db.read(_records)

    async def aclose(self) -> None:
        """Abandon the runs begun in the background and not finished."""
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)

    async def _carry_out(self, run: int, settings: BackupSettings) -> BackupReport:
        try:
            report = await back_up(
                self._db.path, settings, now=self._clock(), transport=self._transport
            )
        except BaseException:  # cancelled, as when the server shuts down
            await self._db.write(_abandon, self._clock().isoformat(), run)
            raise
        await self._finish(run, report)
        return report

    async def _in_background(self, run: int, settings: BackupSettings) -> None:
        try:
            report = await self._carry_out(run, settings)
        except Exception:
            log.exception("the backup run %d failed", run)
            return
        for wrong, line in describe(report):
            (log.warning if wrong else log.info)("backup: %s", line)

    async def _begin(self, source: Source, *, exclusive: bool = False) -> int | None:
        return await self._db.write(_begin, source, self._clock(), exclusive)

    async def _finish(self, run: int | None, report: BackupReport) -> None:
        await self._db.write(_finish, run, report, self._clock().isoformat())


def abandon_unfinished(database: Path, clock: Clock) -> int:
    """Take every run not finished never to finish in the database at `database`, on a
    connection of its own, as after it is restored from a backup, which was made while
    its own run was under way; how many there were."""
    db = connect(database)
    try:
        with db:
            _create(db)
            return _abandon(db, clock().isoformat(), None)
    finally:
        db.close()


def _create(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _records(db: sqlite3.Connection) -> list[BackupRecord]:
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM backup_runs ORDER BY started_at DESC, id DESC"
    ).fetchall()
    return [_record(*row) for row in rows]


def _begin(db: sqlite3.Connection, source: Source, now: datetime, exclusive: bool) -> int | None:
    unless = (
        " WHERE NOT EXISTS (SELECT 1 FROM backup_runs"
        " WHERE finished_at IS NULL AND started_at > :stale)"
    )
    times = {"now": now.isoformat(), "stale": (now - STALE).isoformat()}
    db.execute(
        "DELETE FROM backup_runs WHERE started_at < ?", ((now - RECORDS_KEPT).isoformat(),)
    )
    if exclusive:  # their processes were killed
        db.execute(
            "UPDATE backup_runs SET finished_at = :now, abandoned = 1"
            " WHERE finished_at IS NULL AND started_at <= :stale",
            times,
        )
    # One statement, so that two processes cannot both find none under way.
    begun = db.execute(
        "INSERT INTO backup_runs (source, started_at) SELECT :source, :now"
        + (unless if exclusive else ""),
        times | {"source": source},
    )
    return begun.lastrowid if begun.rowcount == 1 else None


def _finish(db: sqlite3.Connection, run: int | None, report: BackupReport, now: str) -> None:
    db.execute(
        "UPDATE backup_runs SET finished_at = ?, exit_code = ?, key = ?, size = ?,"
        " kept = ?, total = ?, over_warning = ?, config_error = ?, export_error = ?,"
        " upload_error = ?, prune_error = ? WHERE id = ?",
        (
            now, report.exit_code, report.key,
            report.size if report.key is not None else None, report.kept,
            report.total, report.space_warning is not None, report.config_error,
            report.export_error, report.upload_error, report.prune_error, run,
        ),
    )


def _abandon(db: sqlite3.Connection, now: str, run: int | None) -> int:
    """Take the run `run`, or every one if None, never to finish, if it has not."""
    abandoned = db.execute(
        "UPDATE backup_runs SET finished_at = ?, abandoned = 1"
        " WHERE finished_at IS NULL AND (? IS NULL OR id = ?)",
        (now, run, run),
    )
    return abandoned.rowcount


def _record(
    source: Source, started_at: str, finished_at: str | None, abandoned: int,
    exit_code: int | None, key: str | None, size: int | None, kept: int | None,
    total: int | None, over_warning: int, *errors: str | None,
) -> BackupRecord:
    return BackupRecord(
        source, started_at, finished_at, bool(abandoned), exit_code, key, size, kept,
        total, bool(over_warning), *errors,
    )


class _ExportFailed(Exception):
    pass


def _export(database: Path, work: Path, recipient: pyrage.x25519.Recipient) -> Path:
    """The database copied whole into `work`, checked, compressed and encrypted; the path
    of the encrypted file."""
    if not database.is_file():
        raise _ExportFailed(f"找不到数据库 {database}")
    copy = work / "eidolon.db"
    try:
        db = connect(database)
        try:  # one read transaction: the database as it is at one moment
            db.execute("VACUUM INTO ?", (str(copy),))
        finally:
            db.close()
        db = connect(copy)
        try:
            check = [row for (row,) in db.execute("PRAGMA integrity_check")]
        finally:
            db.close()
        if check != ["ok"]:
            raise _ExportFailed(f"导出的副本没有通过完整性检查：{'；'.join(check[:5])}")
        compressed = work / "eidolon.db.gz"
        with copy.open("rb") as source, gzip.open(compressed, "wb") as target:
            shutil.copyfileobj(source, target)
        copy.unlink()
        encrypted = work / "eidolon.db.gz.age"
        pyrage.encrypt_file(str(compressed), str(encrypted), [recipient])
        compressed.unlink()
    except (sqlite3.Error, OSError, pyrage.EncryptError) as e:  # a broken database, a full disk
        raise _ExportFailed(f"导出数据库失败：{e}") from None
    return encrypted


async def _prune(bucket: Bucket, before: datetime, report: BackupReport) -> None:
    """Delete the backups made before `before`, save the newest, adding each to the
    report's `deleted` once it is, and keeping its `kept` and `total` to what is left, so
    that what was done before a failure is still reported. Anything else in the bucket is
    left alone."""
    made: dict[str, datetime] = {}
    sizes: dict[str, int | None] = {}
    for key, listed in (await bucket.objects()).items():
        if match := _NAME.fullmatch(key):
            made[key] = datetime.strptime(match[1], _TIME_FORMAT).replace(tzinfo=UTC)
            sizes[key] = listed.size
    listed = [size for size in sizes.values() if size is not None]
    # Without every size, the total would be too small to warn by.
    total = sum(listed) if len(listed) == len(sizes) else None
    report.kept, report.total = len(made), total
    newest = max(made, key=made.__getitem__, default=None)
    for key in sorted(made):
        if key != newest and made[key] < before:
            await bucket.delete(key)
            report.deleted.append(key)
            report.kept -= 1
            if report.total is not None:
                report.total -= sizes[key] or 0


async def _ping(url: str, transport: httpx.AsyncBaseTransport | None) -> str | None:
    """Why visiting `url` failed, or None if it did not."""
    async with httpx.AsyncClient(transport=transport, timeout=_PING_TIMEOUT) as client:
        try:
            response = await client.get(url)
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            return f"访问心跳地址失败：{e!r}"
    if not response.is_success:
        return f"心跳地址返回了 {response.status_code}"
    return None
