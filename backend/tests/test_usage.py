"""The usage limit module: each user's text turns and calls over the last hour, counted
against their limits unless on their own keys, given back when they fail, and kept a day
for the admin to look over."""

import sqlite3
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from eidolon import housekeeping
from eidolon.config import UsageLimits
from eidolon.data_access import Database
from eidolon.usage import OverLimit, Tally, UsageLimiter, UserLimits
from tests.conftest import users_in
from tests.fakes import START, FakeClock


class Deployment:
    """A database with two users, on a data access layer, and a clock the tests move."""

    def __init__(self, path: Path, user: str, friend: str) -> None:
        self.path = path
        self.user, self.friend = user, friend
        self.db = Database(path)
        self.clock = FakeClock()

    def limiter(self, turns: int | None = 2, calls: int | None = 1) -> UsageLimiter:
        limits = UsageLimits(turns_per_hour=turns, calls_per_hour=calls)
        return UsageLimiter(self.db, limits, self.clock)


@pytest.fixture
async def deployment(tmp_path: Path) -> AsyncIterator[Deployment]:
    path = tmp_path / "eidolon.db"
    deployment = Deployment(path, *await users_in(path, "producer", "friend"))
    yield deployment
    await deployment.db.close()


@pytest.fixture
def usage(deployment: Deployment) -> UsageLimiter:
    return deployment.limiter()


async def test_the_last_day_is_tallied_by_hour_with_what_was_on_own_keys(
    deployment: Deployment, usage: UsageLimiter
):
    deployment.clock.advance(minutes=10)  # 12:10
    await usage.take(deployment.user, "turn")
    await usage.take(deployment.user, "turn", exempt=True)
    await usage.take(deployment.friend, "turn")
    deployment.clock.advance(hours=2)  # 14:10
    await usage.take(deployment.user, "call", exempt=True)
    await usage.take(deployment.user, "turn")

    day = (await usage.last_day(deployment.user))

    hour = START.replace(minute=0)
    assert day.hours == {
        hour: Tally(turns=2, exempt_turns=1),
        hour + timedelta(hours=2): Tally(turns=1, calls=1, exempt_calls=1),
    }
    assert day.total == Tally(turns=3, calls=1, exempt_turns=1, exempt_calls=1)


async def test_uses_on_own_keys_are_recorded_but_do_not_count(
    deployment: Deployment, usage: UsageLimiter
):
    for _ in range(2):
        await usage.take(deployment.user, "call", exempt=True)

    await usage.take(deployment.user, "call")  # the limit of 1 is still all there
    await usage.take(deployment.user, "call", exempt=True)  # and even once it is used up

    with pytest.raises(OverLimit):
        await usage.take(deployment.user, "call")
    assert (await usage.last_day(deployment.user)).total == Tally(calls=4, exempt_calls=3)


async def test_a_limit_of_none_lets_everything_through(deployment: Deployment):
    usage = deployment.limiter(turns=None)
    for _ in range(100):
        await usage.take(deployment.user, "turn")


async def test_a_use_given_back_is_as_if_never_taken(deployment: Deployment, usage: UsageLimiter):
    await (await usage.take(deployment.user, "call")).give_back()
    use = await usage.take(deployment.user, "call")

    await use.give_back()
    await use.give_back()  # once is enough
    await usage.take(deployment.user, "call")

    assert (await usage.last_day(deployment.user)).total == Tally(calls=1)


async def test_uses_are_kept_a_day(deployment: Deployment, usage: UsageLimiter):
    await usage.take(deployment.user, "turn", exempt=True)
    deployment.clock.advance(hours=23)
    await usage.take(deployment.user, "turn", exempt=True)

    deployment.clock.advance(hours=1)
    await usage.take(deployment.user, "call", exempt=True)

    assert (await usage.last_day(deployment.user)).total == Tally(turns=1, calls=1, exempt_turns=1,
                                                         exempt_calls=1)


async def test_uses_kept_before_own_keys_existed_still_count(deployment: Deployment):
    with sqlite3.connect(deployment.path) as db:  # a database from before own keys
        db.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                   " user_id TEXT NOT NULL, kind TEXT NOT NULL, at REAL NOT NULL)")
        db.execute("INSERT INTO usage (user_id, kind, at) VALUES (?, 'call', ?)",
                   (deployment.user, START.timestamp()))
    db.close()
    usage = deployment.limiter()
    with pytest.raises(OverLimit):
        await usage.take(deployment.user, "call")
    await usage.take(deployment.user, "call", exempt=True)
    assert (await usage.last_day(deployment.user)).total == Tally(calls=2, exempt_calls=1)


async def test_a_users_own_limits_kept_before_photos_were_limited_follow_the_deployments_photos(
    deployment: Deployment,
):
    with sqlite3.connect(deployment.path) as db:  # a database from before photos
        db.execute("CREATE TABLE user_limits (user_id TEXT PRIMARY KEY, turns_per_hour INTEGER,"
                   " calls_per_hour INTEGER, note TEXT, updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO user_limits VALUES (?, 3, -1, '老数据', ?)",
                   (deployment.user, START.isoformat()))
    db.close()
    usage = deployment.limiter()
    assert await usage.user_limits(deployment.user) == UserLimits(
        3, "unlimited", "老数据", START.isoformat(), photos_per_hour=None
    )


async def test_forgetting_old_uses_forgets_everyones_over_a_day_old_in_batches(
    deployment: Deployment, usage: UsageLimiter, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(housekeeping, "BATCH", 1)
    await usage.take(deployment.user, "turn", exempt=True)
    await usage.take(deployment.friend, "turn")  # and the friend never comes back
    deployment.clock.advance(hours=12)
    await usage.take(deployment.friend, "call")
    deployment.clock.advance(hours=12, minutes=1)
    await usage.take(deployment.user, "turn")

    await usage.forget_old()

    with sqlite3.connect(deployment.path) as db:
        kept = db.execute("SELECT user_id, kind FROM usage ORDER BY at").fetchall()
    db.close()
    assert kept == [(deployment.friend, "call"), (deployment.user, "turn")]


async def test_forgetting_old_uses_needs_no_scan_of_the_whole_table(
    deployment: Deployment, usage: UsageLimiter
):
    with sqlite3.connect(deployment.path) as db:
        plan = db.execute("EXPLAIN QUERY PLAN DELETE FROM usage WHERE at <= 0").fetchall()
    db.close()

    assert "SEARCH" in " ".join(detail for *_, detail in plan)
