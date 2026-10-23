"""Backing the database up (`eidolon backup`, ADR-0012): a consistent copy, checked,
compressed and encrypted to the deployer's age key, put in a private bucket; backups
past the retention deleted, save the newest; how much is left, with a warning over the
deployer's size; a ping when all went well; each run recorded, one at a time. Checked
against a fake bucket that keeps what it is sent, and a fake ping service."""

import asyncio
import gzip
import re
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pyrage
import pytest

from eidolon.backup import (
    BackupRecord, BackupSettings, Backups, Source, abandon_unfinished, back_up, backup_key,
)
from eidolon.clock import Clock, utc_now
from eidolon.config import Settings
from eidolon.data_access import Database
from eidolon.s3 import S3Settings
from tests.conftest import Admin, aside, users_in
from tests.fakes import START
from tests.test_stand_upload import FakeBucket

IDENTITY = pyrage.x25519.Identity.generate()
PING = "https://ping.test/backup"


@asynccontextmanager
async def backups_in(
    database: Path, clock: Clock = utc_now, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[Backups]:
    """The backups of the database at `database`, on a data access layer of their own, as
    another process has them."""
    db = Database(database)
    try:
        yield Backups(db, clock, transport=transport)
    finally:
        await db.close()


async def records_in(database: Path) -> list[BackupRecord]:
    async with backups_in(database) as backups:
        return await backups.records()


async def begun_in(database: Path, source: Source, clock: Clock = utc_now) -> None:
    """A run begun in the database at `database`, as in another process, not finished."""
    async with backups_in(database, clock) as backups:
        await backups.start(source)


class Deployment:
    """The database with a user in it, the private bucket, the ping service, and where
    the temporary files go."""

    def __init__(self, tmp_path: Path) -> None:
        self.database = tmp_path / "eidolon.db"
        aside(users_in(self.database, "producer"))
        self.storage = FakeBucket("backups")
        self.refuses_puts = False
        self.cancelled_during_put = False
        self.refuses_deletes = False
        self.lists_html = False
        self.lists_sizes = True
        self.pings: list[str] = []
        self.ping_down = False

    def settings(self, ping_url: str | None = PING, **settings) -> BackupSettings:
        return BackupSettings(
            S3Settings("http://storage.test", "backups", "key-id", "secret"),
            IDENTITY.to_public(),
            ping_url,
            **settings,
        )

    def keep(self, at: datetime, size: int = 7) -> str:
        """A backup made at `at`, of `size` bytes, already in the bucket."""
        key = backup_key(at)
        self.storage.objects[key] = (b"x" * size, {})
        return key

    async def back_up(self, now: datetime = START, ping_url: str | None = PING, **settings):
        return await back_up(
            self.database,
            self.settings(ping_url, **settings),
            now=now,
            transport=httpx.MockTransport(self._handle),
        )

    async def run(
        self, source: Source = "manual", now: datetime = START, ping_url: str | None = PING, **settings
    ):
        """A backup run, as `eidolon backup` and the admin console's button make one."""
        async with self.backups(now) as backups:
            return await backups.run(source, lambda: self.settings(ping_url, **settings))

    def backups(
        self, now: datetime = START, transport: httpx.AsyncBaseTransport | None = None
    ):
        return backups_in(
            self.database, lambda: now, transport or httpx.MockTransport(self._handle)
        )

    async def records(self) -> list[BackupRecord]:
        return await records_in(self.database)

    def restore(self, key: str, to: Path) -> list[str]:
        """The names of the users in the backup, decrypted with the deployer's key."""
        encrypted, _ = self.storage.objects[key]
        to.write_bytes(gzip.decompress(pyrage.decrypt(encrypted, [IDENTITY])))
        with sqlite3.connect(to) as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            names = [name for (name,) in db.execute("SELECT name FROM users")]
        db.close()
        return names

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "ping.test":
            self.pings.append(str(request.url))
            return httpx.Response(503 if self.ping_down else 200)
        if request.method == "PUT" and self.cancelled_during_put:
            raise asyncio.CancelledError  # as when the server shuts down
        if request.method == "PUT" and self.refuses_puts:
            return httpx.Response(507, text="<Error><Code>InsufficientStorage</Code></Error>")
        if request.method == "GET" and self.lists_html:
            return httpx.Response(200, text="<!doctype html><p>Welcome to nginx!")
        if request.method == "GET" and not self.lists_sizes:
            listing = self.storage.handle(request)
            return httpx.Response(200, text=re.sub("<Size>[0-9]+</Size>", "", listing.text))
        if request.method == "DELETE" and self.refuses_deletes:
            return httpx.Response(403, text="<Error><Code>AccessDenied</Code></Error>")
        return self.storage.handle(request)


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Deployment:
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temporary))
    return Deployment(tmp_path)


async def test_the_backup_is_the_database_encrypted_to_the_deployers_key_and_pings_once(
    deployment: Deployment, tmp_path: Path
):
    report = await deployment.back_up()

    assert report.exit_code == 0
    assert report.key == "eidolon-20260925T120000Z.db.gz.age"
    assert list(deployment.storage.objects) == [report.key]
    assert deployment.restore(report.key, tmp_path / "restored.db") == ["producer"]
    assert list((tmp_path / "tmp").iterdir()) == []  # the temporary files are gone
    assert deployment.pings == [PING]


async def test_backups_over_30_days_old_are_deleted_and_nothing_else(deployment: Deployment):
    old = deployment.keep(START - timedelta(days=30, minutes=1))
    recent = deployment.keep(START - timedelta(days=29, hours=23))
    deployment.storage.objects["notes.txt"] = (b"the deployer's", {})

    report = await deployment.back_up()

    assert report.exit_code == 0
    assert report.deleted == [old]
    assert report.key is not None
    assert sorted(deployment.storage.objects) == sorted([recent, report.key, "notes.txt"])
    assert report.kept == 2


async def test_a_shorter_retention_deletes_what_is_older_and_keeps_the_newest(
    deployment: Deployment,
):
    old = deployment.keep(START - timedelta(days=7, minutes=1))
    recent = deployment.keep(START - timedelta(days=6, hours=23))

    report = await deployment.back_up(keep_days=7)

    assert report.deleted == [old]
    assert report.key is not None
    assert sorted(deployment.storage.objects) == sorted([recent, report.key])


async def test_the_report_adds_up_the_backups_left_in_the_bucket_and_nothing_else(
    deployment: Deployment,
):
    deployment.keep(START - timedelta(days=31), size=1000)
    deployment.keep(START - timedelta(days=2), size=300)
    deployment.storage.objects["notes.txt"] = (b"x" * 50, {})

    report = await deployment.back_up()

    assert report.kept == 2
    assert report.total == 300 + report.size
    assert report.space_warning is None


async def test_over_the_space_warning_the_backup_warns_but_succeeds_and_pings(
    deployment: Deployment,
):
    deployment.keep(START - timedelta(days=2), size=2_000_000)

    report = await deployment.back_up(warn_gb=0.001)

    assert report.exit_code == 0
    assert report.space_warning is not None and "0.001 GB" in report.space_warning
    assert deployment.pings == [PING]


async def test_a_bucket_that_does_not_list_sizes_has_no_total_to_warn_by(
    deployment: Deployment,
):
    deployment.keep(START - timedelta(days=2), size=2_000_000)
    deployment.lists_sizes = False

    report = await deployment.back_up(warn_gb=0.001)

    assert report.exit_code == 0
    assert report.kept == 2
    assert report.total is None and report.space_warning is None


async def test_under_the_space_warning_there_is_no_warning(deployment: Deployment):
    deployment.keep(START - timedelta(days=2), size=2_000_000)

    report = await deployment.back_up(warn_gb=1)

    assert report.space_warning is None


async def test_when_the_upload_fails_old_backups_are_still_deleted_save_the_newest(
    deployment: Deployment,
):
    oldest = deployment.keep(START - timedelta(days=40))
    newest = deployment.keep(START - timedelta(days=35))
    deployment.refuses_puts = True

    report = await deployment.back_up()

    assert report.exit_code == 3
    assert report.upload_error is not None and "507" in report.upload_error
    assert report.deleted == [oldest]
    assert list(deployment.storage.objects) == [newest]
    assert deployment.pings == []


async def test_a_database_that_cannot_be_copied_whole_is_not_uploaded(
    deployment: Deployment, tmp_path: Path
):
    await users_in(deployment.database, *(f"user {n:03d} " + "x" * 200 for n in range(500)))
    middle = deployment.database.stat().st_size // 2 // 4096 * 4096
    with deployment.database.open("r+b") as file:  # break a page among the users
        file.seek(middle)
        file.write(b"\xff" * 200)
    old = deployment.keep(START - timedelta(days=31))
    deployment.keep(START - timedelta(days=1))

    report = await deployment.back_up()

    assert report.exit_code == 2
    assert report.export_error is not None
    assert deployment.storage.puts == []
    assert list((tmp_path / "tmp").iterdir()) == []
    assert report.deleted == [old]
    assert deployment.pings == []


async def test_old_backups_that_cannot_be_deleted_fail_the_backup_without_a_ping(
    deployment: Deployment,
):
    deployment.keep(START - timedelta(days=31))
    deployment.refuses_deletes = True

    report = await deployment.back_up()

    assert report.exit_code == 4
    assert report.key is not None and report.key in deployment.storage.objects
    assert report.prune_error is not None and "AccessDenied" in report.prune_error
    assert deployment.pings == []


async def test_a_ping_that_fails_is_only_a_warning(deployment: Deployment):
    deployment.ping_down = True

    report = await deployment.back_up()

    assert report.exit_code == 0
    assert report.ping_error is not None and "503" in report.ping_error


async def test_a_bucket_that_answers_with_something_else_fails_without_crashing(
    deployment: Deployment,
):
    deployment.lists_html = True

    report = await deployment.back_up()

    assert report.exit_code == 4
    assert report.prune_error is not None and "不是 XML" in report.prune_error


async def test_without_a_ping_url_there_is_no_ping(deployment: Deployment):
    report = await deployment.back_up(ping_url=None)

    assert report.exit_code == 0
    assert deployment.pings == []


async def test_each_run_leaves_a_record_of_what_it_did(deployment: Deployment):
    deployment.keep(START - timedelta(days=2), size=300)

    report = await deployment.run("cron", warn_gb=0.000_000_001)

    (record,) = await deployment.records()
    assert record.source == "cron"
    assert record.started_at == record.finished_at == START.isoformat()
    assert record.exit_code == 0 and not record.abandoned
    assert (record.key, record.size) == (report.key, report.size)
    assert (record.kept, record.total) == (2, 300 + report.size)
    assert record.over_warning
    assert (record.export_error, record.upload_error, record.prune_error) == (None, None, None)


async def test_a_run_that_failed_records_why(deployment: Deployment):
    deployment.refuses_puts = True

    await deployment.run("admin")

    (record,) = await deployment.records()
    assert record.source == "admin"
    assert record.exit_code == 3
    assert record.key is None
    assert record.upload_error is not None and "507" in record.upload_error


async def test_a_run_without_the_settings_is_recorded_too(deployment: Deployment):
    def missing() -> BackupSettings:
        raise RuntimeError("备份数据库需要存储桶的设置")

    async with deployment.backups() as backups:
        report = await backups.run("cron", missing)

    assert report.exit_code == 1
    assert report.config_error == "备份数据库需要存储桶的设置"
    assert deployment.storage.puts == []
    (record,) = await deployment.records()
    assert (record.source, record.exit_code) == ("cron", 1)
    assert record.config_error == "备份数据库需要存储桶的设置"
    assert record.finished_at == START.isoformat()
    assert (record.kept, record.total) == (None, None)


async def test_a_run_under_way_keeps_another_from_starting(deployment: Deployment):
    async with deployment.backups(START - timedelta(minutes=59)) as backups:
        # a run under way, as if in another process
        assert await backups.start("cron") is not None

    report = await deployment.run("admin")

    assert report.exit_code == 6 and report.busy
    assert deployment.storage.puts == []
    assert [record.source for record in await deployment.records()] == ["cron"]


async def test_a_run_begun_over_an_hour_ago_and_never_finished_does_not_keep_another_from_starting(
    deployment: Deployment,
):
    # and its process killed
    await begun_in(deployment.database, "cron", lambda: START - timedelta(minutes=61))

    report = await deployment.run("manual")

    assert report.exit_code == 0
    manual, cron = await deployment.records()
    assert manual.source == "manual" and manual.exit_code == 0
    assert cron.abandoned and cron.finished_at == START.isoformat()


async def test_runs_abandoned_no_longer_keep_another_from_starting(deployment: Deployment):
    await begun_in(deployment.database, "cron", lambda: START)
    assert abandon_unfinished(deployment.database, lambda: START) == 1

    report = await deployment.run("manual")

    assert report.exit_code == 0
    manual, cron = await deployment.records()
    assert cron.abandoned and cron.finished_at == START.isoformat() and cron.exit_code is None
    assert not manual.abandoned


async def test_a_run_cancelled_part_way_is_abandoned(deployment: Deployment):
    deployment.cancelled_during_put = True

    with pytest.raises(asyncio.CancelledError):
        await deployment.run("admin")

    (record,) = await deployment.records()
    assert record.abandoned and record.finished_at is not None
    deployment.cancelled_during_put = False
    assert (await deployment.run("manual")).exit_code == 0


async def test_a_run_begun_in_the_background_is_abandoned_as_the_server_shuts_down(
    deployment: Deployment,
):
    uploading = asyncio.Event()

    async def never_uploads(request: httpx.Request) -> httpx.Response:
        uploading.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with deployment.backups(transport=httpx.MockTransport(never_uploads)) as backups:
        assert await backups.begin("admin", deployment.settings)
        await asyncio.wait_for(uploading.wait(), 10)

        await backups.aclose()

    (record,) = await deployment.records()
    assert (record.source, record.abandoned) == ("admin", True)


async def test_records_over_90_days_old_are_deleted_as_a_run_begins(deployment: Deployment):
    await deployment.run("cron", now=START - timedelta(days=90, minutes=1))
    await deployment.run("cron", now=START - timedelta(days=89))

    await deployment.run("manual")

    assert [record.started_at for record in await deployment.records()] == [
        START.isoformat(), (START - timedelta(days=89)).isoformat()
    ]


BACKUP_ENV = {
    "EIDOLON_BACKUP_S3_ENDPOINT": "https://account.r2.cloudflarestorage.com",
    "EIDOLON_BACKUP_S3_BUCKET": "eidolon-backups",
    "EIDOLON_BACKUP_S3_ACCESS_KEY_ID": "key-id",
    "EIDOLON_BACKUP_S3_SECRET_ACCESS_KEY": "secret",
    "EIDOLON_BACKUP_AGE_RECIPIENT": str(IDENTITY.to_public()),
}


@pytest.fixture
def backup_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in [
        *BACKUP_ENV,
        "EIDOLON_BACKUP_PING_URL",
        "EIDOLON_BACKUP_S3_REGION",
        "EIDOLON_BACKUP_S3_ADDRESSING",
        "EIDOLON_BACKUP_KEEP_DAYS",
        "EIDOLON_BACKUP_WARN_GB",
    ]:
        monkeypatch.delenv(name, raising=False)
    for name, value in BACKUP_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_settings_come_from_the_environment(backup_env: pytest.MonkeyPatch):
    backup_env.setenv("EIDOLON_BACKUP_PING_URL", PING)

    settings = BackupSettings.from_env()

    assert settings.bucket == S3Settings(
        "https://account.r2.cloudflarestorage.com", "eidolon-backups", "key-id", "secret"
    )
    assert str(settings.recipient) == str(IDENTITY.to_public())
    assert settings.ping_url == PING
    assert settings.keep_days == 30
    assert settings.warn_gb is None


def test_the_retention_and_the_space_warning_come_from_the_environment(
    backup_env: pytest.MonkeyPatch,
):
    backup_env.setenv("EIDOLON_BACKUP_KEEP_DAYS", "7")
    backup_env.setenv("EIDOLON_BACKUP_WARN_GB", "2.5")

    settings = BackupSettings.from_env()

    assert settings.keep_days == 7
    assert settings.warn_gb == 2.5


@pytest.mark.parametrize("days", ["0", "31", "7.5"])
def test_a_retention_that_is_not_1_to_30_days_is_an_error(backup_env: pytest.MonkeyPatch, days: str):
    backup_env.setenv("EIDOLON_BACKUP_KEEP_DAYS", days)

    with pytest.raises(RuntimeError, match="EIDOLON_BACKUP_KEEP_DAYS.*1 到 30.*隐私政策"):
        BackupSettings.from_env()


@pytest.mark.parametrize("gb", ["0", "lots", "inf"])
def test_a_space_warning_that_is_not_a_positive_number_is_an_error(
    backup_env: pytest.MonkeyPatch, gb: str
):
    backup_env.setenv("EIDOLON_BACKUP_WARN_GB", gb)

    with pytest.raises(RuntimeError, match="EIDOLON_BACKUP_WARN_GB.*正数"):
        BackupSettings.from_env()


@pytest.mark.parametrize(
    "missing", ["EIDOLON_BACKUP_S3_SECRET_ACCESS_KEY", "EIDOLON_BACKUP_AGE_RECIPIENT"]
)
def test_settings_missing_from_the_environment_are_an_error(
    backup_env: pytest.MonkeyPatch, missing: str
):
    backup_env.delenv(missing)

    with pytest.raises(RuntimeError, match=missing):
        BackupSettings.from_env()


def test_a_recipient_that_is_not_an_age_public_key_is_an_error(backup_env: pytest.MonkeyPatch):
    backup_env.setenv("EIDOLON_BACKUP_AGE_RECIPIENT", str(IDENTITY))  # the private key

    with pytest.raises(RuntimeError, match="age1"):
        BackupSettings.from_env()


def test_a_ping_url_that_is_not_a_web_address_is_an_error(backup_env: pytest.MonkeyPatch):
    backup_env.setenv("EIDOLON_BACKUP_PING_URL", "hc-ping.com/abc")

    with pytest.raises(RuntimeError, match="EIDOLON_BACKUP_PING_URL"):
        BackupSettings.from_env()


def test_backing_up_without_the_settings_exits_1(admin: Admin, backup_env: pytest.MonkeyPatch):
    backup_env.delenv("EIDOLON_BACKUP_S3_BUCKET")

    code, _, err = admin.invoke("backup")

    assert code == 1
    assert "EIDOLON_BACKUP_S3_BUCKET" in err


def test_backing_up_with_a_retention_over_30_days_exits_1(
    admin: Admin, backup_env: pytest.MonkeyPatch
):
    backup_env.setenv("EIDOLON_BACKUP_KEEP_DAYS", "60")

    code, _, err = admin.invoke("backup")

    assert code == 1
    assert "EIDOLON_BACKUP_KEEP_DAYS" in err


def test_backing_up_records_who_began_it(
    admin: Admin, backup_env: pytest.MonkeyPatch, settings: Settings
):
    backup_env.delenv("EIDOLON_BACKUP_S3_BUCKET")

    admin.invoke("backup", "--source", "cron")
    admin.invoke("backup")

    records = asyncio.run(records_in(settings.database_path))
    assert [record.source for record in records] == ["manual", "cron"]


def test_backing_up_while_another_backup_is_under_way_exits_6(
    admin: Admin, backup_env: pytest.MonkeyPatch, settings: Settings
):
    asyncio.run(begun_in(settings.database_path, "admin"))

    code, _, err = admin.invoke("backup", "--source", "cron")

    assert code == 6
    assert "上一次备份还没结束" in err
