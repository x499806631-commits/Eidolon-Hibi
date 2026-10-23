"""The backups on the admin's dashboard (`/api/admin/backups`): whether they are set up,
how the last ones went, which is under way, and whether something is wrong, all of it
worked out by the backend; and the button that begins one in the server's process, the
same run as `eidolon backup`'s. Driven through the backend's external API with a clock
the tests control and a fake bucket."""

import asyncio
from datetime import timedelta

import httpx
import pytest

from eidolon.backup import Source, abandon_unfinished
from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import START, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_activity_records import admin_client, settings  # noqa: F401  (with an admin)
from tests.test_backup import BACKUP_ENV, backup_env, begun_in, records_in  # noqa: F401
from tests.test_stand_upload import FakeBucket


@pytest.fixture
def set_up(backup_env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:  # noqa: F811
    """The backups set up, to the fake bucket."""
    backup_env.setenv("EIDOLON_BACKUP_S3_ENDPOINT", "http://storage.test")
    backup_env.setenv("EIDOLON_BACKUP_S3_BUCKET", "backups")
    return backup_env


@pytest.fixture
def storage() -> FakeBucket:
    return FakeBucket("backups")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def admin(
    make_client: ClientFactory, github: FakeIdentityProvider, clock: FakeClock, storage: FakeBucket
) -> httpx.AsyncClient:
    client = await admin_client(
        make_client, github, clock, backup_transport=httpx.MockTransport(storage.handle)
    )
    return client


async def status(client: httpx.AsyncClient) -> dict:
    res = await client.get("/api/admin/backups")
    assert res.status_code == 200, res.text
    return res.json()


async def back_up_now(client: httpx.AsyncClient) -> dict:
    """Press the button, and wait for the backup it began to finish: the status then."""
    res = await client.post("/api/admin/backups")
    assert res.status_code == 200, res.text
    assert res.json()["under_way"] is not None
    for _ in range(500):
        now = await status(client)
        if now["under_way"] is None:
            return now
        await asyncio.sleep(0.01)
    raise AssertionError("the backup never finished")


async def begun_elsewhere(settings: Settings, clock: FakeClock, source: Source = "cron") -> None:  # noqa: F811
    """A run begun now in another process, as the cron's `eidolon backup` is, and not
    finished."""
    await begun_in(settings.database_path, source, clock)


async def test_only_admins_see_the_backups_or_begin_one(
    make_client: ClientFactory, set_up: pytest.MonkeyPatch, storage: FakeBucket
):
    producer = make_client(FakeLLM(), backup_transport=httpx.MockTransport(storage.handle))
    signed_out = make_client(FakeLLM(), user=None)

    for client in [producer, signed_out]:
        assert (await client.get("/api/admin/backups")).status_code == 404
        assert (await client.post("/api/admin/backups")).status_code == 404
    assert storage.puts == []


async def test_without_any_backup_settings_backups_are_not_set_up_and_that_is_wrong(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch
):
    for name in BACKUP_ENV:
        set_up.delenv(name)

    board = await status(admin)

    assert board["configured"] is False
    assert board["config_error"] is None
    assert board["problem"] == "unconfigured"
    assert board["keep_days"] is None
    res = await admin.post("/api/admin/backups")
    assert res.status_code == 400
    assert "EIDOLON_BACKUP_S3_ENDPOINT" in res.json()["detail"]


async def test_settings_that_will_not_do_are_wrong_and_say_why(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, storage: FakeBucket
):
    set_up.setenv("EIDOLON_BACKUP_KEEP_DAYS", "60")

    board = await status(admin)

    assert board["configured"] is True
    assert "EIDOLON_BACKUP_KEEP_DAYS" in board["config_error"]
    assert board["problem"] == "misconfigured"
    res = await admin.post("/api/admin/backups")
    assert res.status_code == 400
    assert "EIDOLON_BACKUP_KEEP_DAYS" in res.json()["detail"]
    assert storage.puts == []
    assert (await status(admin))["last"] is None  # the button recorded nothing


async def test_backups_set_up_but_never_made_are_wrong(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch
):
    set_up.setenv("EIDOLON_BACKUP_KEEP_DAYS", "7")
    set_up.setenv("EIDOLON_BACKUP_WARN_GB", "2.5")

    board = await status(admin)

    assert board["configured"] is True
    assert board["config_error"] is None
    assert (board["keep_days"], board["warn_gb"]) == (7, 2.5)
    assert board["problem"] == "never_succeeded"
    assert (board["under_way"], board["last_success"], board["last"], board["bucket"]) == (
        None, None, None, None
    )


async def test_the_button_backs_up_in_the_background_as_the_admins_run(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, storage: FakeBucket, settings: Settings  # noqa: F811
):
    board = await back_up_now(admin)

    (key,) = storage.puts
    assert board["problem"] is None
    success = board["last_success"]
    assert (success["source"], success["started_at"]) == ("admin", START.isoformat())
    assert success["size"] == len(storage.objects[key][0])
    assert board["last"]["errors"] == []
    assert board["bucket"] == {"kept": 1, "total": success["size"], "over_warning": False}
    assert [record.source for record in await records_in(settings.database_path)] == ["admin"]


async def test_a_backup_over_26_hours_ago_is_overdue(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, clock: FakeClock
):
    await back_up_now(admin)

    clock.advance(hours=25)
    assert (await status(admin))["problem"] is None
    clock.advance(hours=2)
    assert (await status(admin))["problem"] == "overdue"


async def test_a_failed_run_after_the_last_success_says_why_and_who_began_it(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, storage: FakeBucket, clock: FakeClock
):
    first = await back_up_now(admin)
    clock.advance(hours=1)
    storage.down = True

    board = await back_up_now(admin)

    assert board["last_success"] == first["last_success"]
    last = board["last"]
    assert last["source"] == "admin"
    assert last["started_at"] == (START + timedelta(hours=1)).isoformat()
    assert [error["stage"] for error in last["errors"]] == ["upload", "prune"]
    assert all("503" in error["error"] for error in last["errors"])
    # It could not list the bucket: what is in it is as the last run that could saw it.
    assert board["bucket"] == first["bucket"]
    assert board["problem"] is None


async def test_a_run_abandoned_last_is_the_last_and_says_so(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, settings: Settings, clock: FakeClock  # noqa: F811
):
    await begun_elsewhere(settings, clock)
    abandon_unfinished(settings.database_path, clock)

    board = await status(admin)

    assert board["last"]["source"] == "cron"
    assert board["last"]["errors"] == [{"stage": "abandoned", "error": None}]
    assert board["problem"] == "never_succeeded"


async def test_a_run_begun_over_an_hour_ago_and_never_finished_is_abandoned_before_another_begins(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, settings: Settings, clock: FakeClock  # noqa: F811
):
    await back_up_now(admin)
    clock.advance(hours=2)
    await begun_elsewhere(settings, clock)
    clock.advance(hours=1, seconds=1)

    board = await status(admin)

    assert board["under_way"] is None
    assert board["last"]["source"] == "cron"
    assert board["last"]["errors"] == [{"stage": "abandoned", "error": None}]
    assert board["last_success"]["source"] == "admin"


async def test_while_a_backup_is_under_way_the_button_answers_conflict(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch, settings: Settings,  # noqa: F811
    storage: FakeBucket, clock: FakeClock,
):
    await begun_elsewhere(settings, clock)

    board = await status(admin)
    res = await admin.post("/api/admin/backups")

    assert board["under_way"] == {"source": "cron", "started_at": START.isoformat()}
    assert board["last"] is None
    assert res.status_code == 409
    assert "上一次备份还没结束" in res.json()["detail"]
    assert storage.puts == []


async def test_backups_over_the_space_warning_are_marked(
    admin: httpx.AsyncClient, set_up: pytest.MonkeyPatch
):
    set_up.setenv("EIDOLON_BACKUP_WARN_GB", "0.000000001")

    board = await back_up_now(admin)

    assert board["bucket"]["over_warning"] is True
    assert board["problem"] is None  # only marked, as the run only warns
