"""The health check: whether the backend can serve, which it cannot unless it reads
the database in time; needing no session, for compose to ask it."""

import time
from pathlib import Path

import pytest

import eidolon.app
from eidolon.database import readable
from tests.conftest import ClientFactory
from tests.fakes import FakeLLM


async def test_the_backend_is_healthy_while_it_reads_the_database(make_client: ClientFactory):
    res = await make_client(FakeLLM(), user=None).get("/api/health")

    assert (res.status_code, res.json()) == (200, {"ok": True})


async def test_the_backend_is_not_healthy_when_it_cannot_read_the_database(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(eidolon.app, "readable", lambda path: False)

    res = await make_client(FakeLLM(), user=None).get("/api/health")

    assert (res.status_code, res.json()) == (503, {"ok": False})


async def test_the_backend_is_not_healthy_when_reading_takes_too_long(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
):
    def slow(path: Path) -> bool:
        time.sleep(0.3)
        return True

    monkeypatch.setattr(eidolon.app, "readable", slow)
    monkeypatch.setattr(eidolon.app, "HEALTH_TIMEOUT", 0.05)

    res = await make_client(FakeLLM(), user=None).get("/api/health")

    assert res.status_code == 503


def test_a_file_that_is_not_a_database_is_not_readable(tmp_path: Path):
    broken = tmp_path / "eidolon.db"
    broken.write_bytes(b"not a database, though long enough to look like a header" * 4)

    assert not readable(broken)


def test_a_missing_database_is_not_readable_and_not_made(tmp_path: Path):
    missing = tmp_path / "eidolon.db"

    assert not readable(missing)
    assert not missing.exists()
