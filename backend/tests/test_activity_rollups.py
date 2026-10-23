"""The dashboard's sums from the rollups (`ActivityLog.tally`) against the records they roll
up, read one by one as they were before there were rollups (`scanned`): over records of
every kind, written as turns and calls are, some reach-outs then answered and a user then
deleted, for each range in each of a few time zones. Counts, usage and users are as many;
what a percentile took, within its bucket."""

import json
import random
import sqlite3
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from eidolon import database
from eidolon.activity import (
    CALL_END_REASONS,
    DURATIONS,
    JUDGEMENT_COLUMNS,
    PERCENTILES,
    ROLLUPS,
    ActivityLog,
    CallGroup,
    Origin,
    RecordGroup,
    RecordKind,
    ServiceCall,
    Spread,
    Taken,
    Tally,
    TURNS_AND_CALLS,
    _reading_judgement,
    range_start,
)
from eidolon.affinity import Judged
from eidolon.billing import Billing
from eidolon.dashboard import SPANS, Range, stretches
from tests.conftest import activity_in
from tests.fakes import START, FakeClock

USERS = ["u1", "u2", "u3", "u4"]
CHARACTERS = ["mano", "hiori", "meguru"]
ORIGINS = [
    Origin("chat_llm", "llm.invalid", "m"),
    Origin("chat_llm", "own", "own", own_key=True),
    Origin("memory_llm", "llm.invalid", "m"),
    Origin("tts", "api.fish.audio", "s1"),
    Origin("image", "image.invalid", "v4"),
]
KINDS: tuple[RecordKind, ...] = ("turn", "turn", "call", "reach_out")
ZONES = ["UTC", "Asia/Shanghai", "Asia/Kolkata", "Asia/Kathmandu"]
"""Days apart from UTC's; half an hour off a round hour; three quarters."""
WITHIN = 2 ** (1 / 32)
"""How far off what a percentile took may be told, as a ratio: half a bucket."""


@asynccontextmanager
async def activity(path: Path, clock: FakeClock, seed: int = 7) -> AsyncIterator[ActivityLog]:
    """An activity log over three days of records of every kind, by the `USERS` and one deleted
    meanwhile, with service calls in them and on their own; some reach-outs then answered,
    one user then deleted."""
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO users VALUES (?)", [(u,) for u in USERS])
    rng = random.Random(seed)
    async with activity_in(path, clock) as log:
        await _fill(log, path, clock, rng)
        yield log


async def _fill(log: ActivityLog, path: Path, clock: FakeClock, rng: random.Random) -> None:
    while clock.now < START:
        clock.advance(seconds=rng.uniform(1, 1200))
        kind = rng.choice(KINDS)
        record = log.begin(
            kind, rng.choice([*USERS, "gone"]), rng.choice(CHARACTERS),
            own_llm=rng.random() < 0.3, exempt=rng.random() < 0.2,
        )
        for _ in range(rng.randint(0, 3)):
            await log.record(service_call(rng, clock.now), record)
        if kind == "turn":
            record.time_to_speak_ms = rng.choice([None, 0.0, rng.uniform(200, 9000)])
            if rng.random() < 0.6:
                record.judged(
                    Judged(rng.randint(0, 4), rng.random() < 0.2, rng.random() < 0.1),
                    rng.random() < 0.3, rng.choice([None, "fed_up", "cooldown"]),
                )
            await record.finish(rng.choice(["success", "success", "failed", "rejected", "left"]))
        elif kind == "call":
            for _ in range(rng.randint(0, 2)):
                turn = record.turn(own_llm=rng.random() < 0.5)
                turn.time_to_speak_ms = rng.uniform(300, 4000)
                await turn.finish("success")
            connected = rng.random() < 0.8
            await record.finish(
                "success" if connected else rng.choice(["not_connected", "rejected"]),
                rng.choice([0.0, 59.9, 60.0, rng.uniform(1, 4000)]) if connected else None,
                rng.choice(CALL_END_REASONS) if connected else None,
            )
        else:
            await record.finish(rng.choice(["success", "failed"]))
        if rng.random() < 0.3:
            await log.record(service_call(rng, clock.now), None)
        if rng.random() < 0.1:
            clock.advance(hours=rng.uniform(1, 10))
    with closing(database.connect(path)) as db:  # as the store marks them, and deletes a user
        with db:
            db.execute(
                "UPDATE activity_records SET answered = 1"
                " WHERE kind = 'reach_out' AND id % 3 = 0"
            )
        with db:
            database.delete_user_data(db, "u2")


def service_call(rng: random.Random, at: datetime) -> ServiceCall:
    origin = rng.choice(ORIGINS)
    billed = rng.choice([
        Billing(),
        Billing("tokens", rng.randint(10, 900), rng.randint(1, 300), rng.choice([None, 64])),
        Billing("characters", rng.randint(5, 80)),
    ])
    return ServiceCall(
        origin, at, rng.choice([None, None, None, "timeout", "server_error"]),
        rng.choice([None, rng.uniform(50, 20000), rng.uniform(1, 5) * 1000]), billed,
        rng.choice([None, rng.uniform(100, 3000)]) if origin.service == "chat_llm" else None,
        rng.choice([None, {"nudity": rng.randint(1, 2)}]) if origin.service == "image" else None,
    )


@pytest.mark.parametrize("zone", ZONES)
# 90 days is summed up as 30 are: in days, each holding all three days of records.
@pytest.mark.parametrize("name", ["24h", "7d", "30d"])
async def test_the_rollups_sum_up_as_the_records_do(tmp_path: Path, name: Range, zone: str):
    clock = FakeClock(START - timedelta(days=3, minutes=7))
    async with activity(tmp_path / "eidolon.db", clock) as log:
        span, now = SPANS[name], clock.now
        starts = stretches(now, span, ZoneInfo(zone))
        tallies = [
            (moment, await log.tally(moment, starts))
            for moment in [now - span.length, now - timedelta(hours=30, minutes=22), now]
        ]

    for moment, rolled in tallies:
        with closing(database.connect(tmp_path / "eidolon.db")) as db:
            read = scanned(db, range_start(moment).timestamp(), starts)

        assert sorted(rolled.records, key=repr) == sorted(read.records, key=repr), moment
        assert sorted(rolled.calls, key=repr) == sorted(read.calls, key=repr), moment
        assert (rolled.users, rolled.cooled_users) == (read.users, read.cooled_users), moment
        assert rolled.stretches == read.stretches, moment
        assert rolled.lasted_within == read.lasted_within, moment
        assert rolled.latency.keys() == read.latency.keys(), moment
        for key, spread in rolled.latency.items():
            assert_near(spread, read.latency[key], (moment, key))
        assert rolled.first_token.keys() == read.first_token.keys(), moment
        for key, spread in rolled.first_token.items():
            assert_near(spread, read.first_token[key], (moment, key))
        assert_near(rolled.time_to_speak, read.time_to_speak, moment)
        assert_near(rolled.lasted, read.lasted, moment)
        if moment == now - span.length:  # some of each
            assert read.users > 0 and read.calls and read.time_to_speak.known
            assert read.lasted.known and any(r.answered for r in read.records)


async def test_rollups_dropped_are_made_anew_from_the_records(tmp_path: Path):
    clock = FakeClock(START - timedelta(days=3))
    async with activity(tmp_path / "eidolon.db", clock, seed=11) as log:
        span = SPANS["7d"]
        starts = stretches(clock.now, span, ZoneInfo("Asia/Shanghai"))
        before = await log.tally(clock.now - span.length, starts)
    with closing(database.connect(tmp_path / "eidolon.db")) as db:
        for table in ROLLUPS:
            db.execute(f"DROP TABLE {table}")

    async with activity_in(tmp_path / "eidolon.db", clock) as again:
        assert await again.tally(clock.now - span.length, starts) == before


async def test_what_a_percentile_took_is_told_exactly_where_it_alone_is_in_its_bucket(
    tmp_path: Path,
):
    clock = FakeClock()
    async with activity_in(tmp_path / "eidolon.db", clock) as log:
        for latency in [900.0, 120.0, 0.0, 4321.5]:
            await log.record(ServiceCall(ORIGINS[0], clock.now, None, latency, Billing()), None)

        spread = (await log.tally(clock.now, [clock.now])).latency[("chat_llm", False)]

    assert spread == Spread(4, {50: 120.0, 90: 4321.5, 95: 4321.5})


def assert_near(rolled: Spread, read: Spread, of: object) -> None:
    assert rolled.known == read.known, of
    assert rolled.percentiles.keys() == read.percentiles.keys(), of
    for p, value in read.percentiles.items():
        told = rolled.percentiles[p]
        assert (told == value == 0) or value / WITHIN <= told <= value * WITHIN, (
            of, p, told, value
        )


# What follows is how the records were summed up before there were rollups, read one by
# one, from `at` on.


def scanned(db: sqlite3.Connection, at: float, starts: Sequence[datetime]) -> Tally:
    who = "COALESCE(user_id, anonymous_id)"
    records = []
    for (
        kind, character, result, own, in_call, reason, level, out_of_line, manipulation,
        cooled, ended, answered, count,
    ) in db.execute(
        "SELECT kind, character_id, result, own_keys != 0, call_id IS NOT NULL, end_reason,"
        f" {', '.join(JUDGEMENT_COLUMNS)}, ended_conversation IS NOT NULL, answered != 0,"
        " COUNT(*) FROM activity_records WHERE started_at >= ?"
        " GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12",
        (at,),
    ):
        judged, was_cooled = _reading_judgement(level, out_of_line, manipulation, cooled)
        records.append(RecordGroup(
            kind=kind, character_id=character, result=result, own_keys=bool(own),
            in_call=bool(in_call), end_reason=reason, judged=judged, cooled=was_cooled,
            ended_conversation=bool(ended), answered=bool(answered), count=count,
        ))
    stretch, stretch_params = stretched("at", starts)
    calls = [
        CallGroup(
            stretch=index, service=service, provider=provider, model=model,
            own_key=bool(own), error=error, usage_unit=unit, input_told=bool(input_told),
            output_told=bool(output_told),
            stripped=json.loads(stripped) if stripped is not None else None, calls=count,
            usage_input=used_in, usage_output=used_out, usage_cached=cached,
        )
        for (
            index, service, provider, model, own, error, unit, input_told, output_told,
            stripped, count, used_in, used_out, cached,
        ) in db.execute(
            f"SELECT {stretch}, service, provider, model, own_key != 0, error, usage_unit,"
            " usage_input IS NOT NULL, usage_output IS NOT NULL, stripped_tags, COUNT(*),"
            " SUM(usage_input), SUM(usage_output), SUM(usage_cached) FROM service_calls"
            " WHERE at >= ? GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 ORDER BY MIN(at)",
            (*stretch_params, at),
        )
    ]
    users, cooled_users = db.execute(
        f"SELECT COUNT(DISTINCT {who}), COUNT(DISTINCT CASE WHEN kind = 'turn'"
        " AND result = 'success' AND affinity_level IS NOT NULL AND cooled"
        f" THEN {who} END) FROM activity_records WHERE started_at >= ? AND {TURNS_AND_CALLS}",
        (at,),
    ).fetchone()
    stretch, stretch_params = stretched("started_at", starts)
    taken = {
        index: Taken(turns, calls_taken, takers)
        for index, turns, calls_taken, takers in db.execute(
            f"SELECT {stretch}, SUM(kind = 'turn'), SUM(kind = 'call'), COUNT(DISTINCT {who})"
            f" FROM activity_records WHERE started_at >= ? AND {TURNS_AND_CALLS} GROUP BY 1",
            (*stretch_params, at),
        )
    }
    bounds = [
        "SUM(duration_s >= ?" + (" AND duration_s < ?)" if high is not None else ")")
        for _, high in DURATIONS
    ]
    within = db.execute(
        f"SELECT {', '.join(bounds)} FROM activity_records"
        " WHERE started_at >= ? AND kind = 'call' AND duration_s IS NOT NULL",
        (*(b for low, high in DURATIONS for b in (low, high) if b is not None), at),
    ).fetchone()
    by_service = ["service", "own_key != 0"]
    latency, first_token = (
        {
            (service, bool(own)): spread
            for (service, own), spread in spreads(
                db, "service_calls", by_service, measure, "at >= ?", (at,)
            ).items()
        }
        for measure in ("latency_ms", "first_token_ms")
    )
    speaking, lasted = (
        spreads(db, "activity_records", ["kind"], measure, "started_at >= ? AND kind = ?",
                (at, kind)).get((kind,), Spread(0, {}))
        for kind, measure in (("turn", "time_to_speak_ms"), ("call", "duration_s"))
    )
    return Tally(
        records=records,
        calls=calls,
        users=users,
        cooled_users=cooled_users,
        stretches=[taken.get(i, Taken(0, 0, 0)) for i in range(len(starts))],
        latency=latency,
        first_token=first_token,
        time_to_speak=speaking,
        lasted=lasted,
        lasted_within=[n or 0 for n in within],
    )


def stretched(column: str, starts: Sequence[datetime]) -> tuple[str, list[float]]:
    if len(starts) < 2:
        return "0", []
    first, length = starts[0].timestamp(), (starts[1] - starts[0]).total_seconds()
    return (
        f"MAX(0, MIN(?, CAST(ROUND({column} - ?, 6) / ? AS INTEGER)))",
        [len(starts) - 1, first, length],
    )


def spreads(
    db: sqlite3.Connection,
    table: str,
    group: Sequence[str],
    measure: str,
    where: str,
    params: Sequence[object],
) -> dict[tuple, Spread]:
    keys = ", ".join(f"{column} AS g{i}" for i, column in enumerate(group))
    named = ", ".join(f"g{i}" for i in range(len(group)))
    ranks = " OR ".join(f"rank = MAX(1, ({p} * known + 99) / 100)" for p in PERCENTILES)
    found: dict[tuple, Spread] = {}
    for *key, known, rank, value in db.execute(
        f"SELECT {named}, known, rank, value FROM (SELECT {keys}, {measure} AS value,"
        " COUNT(*) OVER whole AS known, ROW_NUMBER() OVER (whole ORDER BY"
        f" {measure}) AS rank FROM {table} WHERE {where} AND {measure} IS NOT NULL"
        f" WINDOW whole AS (PARTITION BY {', '.join(group)})) WHERE {ranks}",
        params,
    ):
        spread = found.setdefault(tuple(key), Spread(known, {}))
        for p in PERCENTILES:
            if rank == max(1, (p * known + 99) // 100):
                spread.percentiles[p] = value
    return found
