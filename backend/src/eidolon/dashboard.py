"""The dashboard (GLOSSARY.md「看板」): the activity records of a recent range of time,
summed up for the admin, under `/api/admin/dashboard`.

Everything is summed up here; the browser only shows it. For each service, on the
deployer's keys and on users' own apart: how many calls, how many failed and how, how
long they took, and what they were billed for; for image generation, from how many photos
tags were stripped for crossing the line, and how many for each part of it; for an LLM, how
soon its first word came and how much of its input hit the provider's prompt cache, at
whatever reasoning effort.
For turns (typed or in calls): how many, by how they turned out, how many on users' own
keys, how soon the character began to speak. For calls: how many, by how they turned out, how many on users' own keys, how long
they lasted and why they ended. How many users took any turn or call; how many turns and
calls with each character, and in each stretch of the range (`trend`), in stretches
beginning where the admin's clock (`X-Timezone`) has a round hour or day. Where affinity is
on (GLOSSARY.md「好感度」), how the judge model judged the turns recorded, and the cooldowns
they began, in all and for each character, with how often she hung up a call or ended a
conversation (`affinity`). How many times characters reached out (GLOSSARY.md「搭话」), how
many times that failed, and the share answered (`reach_outs`), where the deployer has them on
or any were recorded in the range. How many times each daily task was done (GLOSSARY.md
「每日任务」, `daily_tasks`), and how many achievements' stages were unlocked, the draw chances
they gave and the reset cards characters complete gave (「成就」, `achievements`): the two
together, where affinity is on or any were done or unlocked in the range. Nothing
anyone said is in it, nor whose any of it was.

How many calls are in progress now is not in the records but the calls themselves:
`/api/admin/calls/live`, which the page asks every minute.

The records are summed up from their rollups (`ActivityLog.tally`), not read one by one: a
range may hold millions. A range so begins where the slot of 15 minutes its first moment falls
in does (`activity.range_start`).
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, Header, Query

from eidolon.activity import (
    CALL_END_REASONS,
    COMPLETE,
    DURATIONS,
    LLM_SERVICES,
    RESULTS,
    SERVICES,
    CallGroup,
    RecordGroup,
    Spread,
    Taken,
    Tally,
    range_start,
    stage_of,
)
from eidolon.affinity import LEVEL_STEPS, Judged
from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.config import ServiceName
from eidolon.happenings import DAILY_TASKS
from eidolon.photo_prompt import CROSSINGS
from eidolon.prices import Pricing, cost

Range = Literal["24h", "7d", "30d", "90d"]


@dataclass(frozen=True)
class Span:
    """A range of the dashboard: how far back it goes, and how long each stretch of its
    trend is."""

    length: timedelta
    stretch: timedelta
    bucket: Literal["hour", "6h", "day"]


SPANS: dict[Range, Span] = {
    "24h": Span(timedelta(hours=24), timedelta(hours=1), "hour"),
    "7d": Span(timedelta(days=7), timedelta(hours=6), "6h"),
    "30d": Span(timedelta(days=30), timedelta(days=1), "day"),
    "90d": Span(timedelta(days=90), timedelta(days=1), "day"),
}


def add_dashboard_routes(app: FastAPI, backend: Backend) -> None:
    @app.get("/api/admin/dashboard")
    async def dashboard(
        _: Admin,
        range: Annotated[Range, Query()] = "24h",
        timezone: Annotated[str | None, Header(alias="X-Timezone")] = None,
    ) -> dict:
        """The activity of the last `range`, summed up."""
        summed = await summed_up(range, timezone, await backend.prices.pricing())
        backend.prices.catalog.refresh_soon(
            missing=any(
                r["unit"] == "tokens" and r["price"] is None for r in summed["cost"]["rows"]
            )
        )
        return summed

    async def summed_up(range: Range, timezone: str | None, pricing: Pricing) -> dict:
        span = SPANS[range]
        now = backend.clock()
        start = range_start(now - span.length)
        starts = stretches(now, span, _zone(timezone))
        tally = await backend.activity.tally(start, starts)
        # Reach-outs, daily tasks done and achievements are no turns nor calls, and taken by
        # no user; the reach-outs' service calls count.
        records = [r for r in tally.records if r.kind in ("turn", "call")]
        reach_out_records = [r for r in tally.records if r.kind == "reach_out"]
        task_records = [r for r in tally.records if r.kind == "daily_task"]
        achievement_records = [r for r in tally.records if r.kind == "achievement"]
        section_shown = bool(backend.config.affinity_on or task_records or achievement_records)
        names = {id: c.name for id, c in backend.characters.items()}
        spent = cost(tally.calls, pricing, starts)
        return {
            "range": range,
            "from": start.isoformat(),
            "to": now.isoformat(),
            "bucket": span.bucket,
            "health": health(tally),
            "active_users": tally.users,
            "turns": turns([r for r in records if r.kind == "turn"], tally.time_to_speak),
            "calls": calls([r for r in records if r.kind == "call"], tally),
            "trend": trend(tally.stretches, starts),
            "characters": characters(records, names),
            "affinity": (
                affinity(records, names, tally.cooled_users)
                if backend.config.affinity_on else None
            ),
            "reach_outs": (
                reach_outs(reach_out_records)
                if backend.config.reach_outs.enabled or reach_out_records else None
            ),
            "daily_tasks": daily_tasks(task_records) if section_shown else None,
            "achievements": achievements(achievement_records) if section_shown else None,
            "cost": spent,
        }

    @app.get("/api/admin/calls/live")
    def live_calls(_: Admin) -> dict:
        """How many calls there are right now, connected or still connecting."""
        return {"in_progress": backend.calls.in_progress}


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _percentiles(spread: Spread | None, *ps: int) -> dict:
    """The `ps`th percentiles of `spread`, to a tenth; None of none."""
    at = spread.percentiles if spread is not None else {}
    return {f"p{p}": round(at[p], 1) if p in at else None for p in ps}


def _count(groups: Iterable[RecordGroup]) -> int:
    return sum(g.count for g in groups)


def _calls(groups: Iterable[CallGroup]) -> int:
    return sum(g.calls for g in groups)


def health(tally: Tally) -> list[dict]:
    """Each service's calls, on the deployer's keys and on users' own apart, in the order
    of `SERVICES`; an LLM's at every reasoning effort together, with its cache hit rate."""
    groups: dict[tuple[ServiceName, bool], list[CallGroup]] = defaultdict(list)
    for call in tally.calls:
        groups[(call.service, call.own_key)].append(call)
    rows = []
    for (service, own_key), group in sorted(groups.items(), key=lambda g: (_order(g[0][0]), g[0][1])):
        # In the order the calls are in, each kind of failure as first seen.
        errors: Counter[str] = Counter()
        for call in group:
            if call.error is not None:
                errors[call.error] += call.calls
        total, failed = _calls(group), sum(errors.values())
        row = {
            "service": service,
            "own_key": own_key,
            "calls": total,
            "errors": failed,
            "error_rate": failed / total,
            "errors_by_kind": dict(errors),
            "latency_ms": _percentiles(tally.latency.get((service, own_key)), 50, 95),
            "usage": _usage(group, llm=service in LLM_SERVICES),
            "usage_unknown": _calls(c for c in group if c.usage_unit is None),
        }
        if service == "image":
            row["stripped_photos"] = _calls(c for c in group if c.stripped is not None)
            row["stripped"] = _stripped(group)
        if service in LLM_SERVICES:
            row["first_token_ms"] = _percentiles(tally.first_token.get((service, own_key)), 50, 95)
            row["cache_hit_rate"] = cache_hit_rate(group)
        rows.append(row)
    return rows


def _stripped(calls: Sequence[CallGroup]) -> dict[str, int]:
    """How many tags were stripped from her photo tags, by which part of the line each
    crossed, in the order of `CROSSINGS`; only those any crossed."""
    stripped: Counter[str] = Counter()
    for call in calls:
        for crossing, count in (call.stripped or {}).items():
            stripped[crossing] += count * call.calls
    return {c: stripped[c] for c in CROSSINGS if stripped[c]}


def cache_hit_rate(calls: Sequence[CallGroup]) -> float | None:
    """Of all the input tokens an LLM's calls said they were billed for, the share the
    provider read from its prompt cache; a call that did not say how much hit none. None
    where no call said what its input was."""
    told = [c for c in calls if c.usage_unit == "tokens" and c.input_told]
    input = sum(c.usage_input or 0 for c in told)
    return sum(c.usage_cached or 0 for c in told) / input if input else None


def _order(service: str) -> int:
    return SERVICES.index(service) if service in SERVICES else len(SERVICES)  # pyright: ignore[reportArgumentType]


def _usage(calls: Sequence[CallGroup], llm: bool) -> list[dict]:
    """What the calls were billed for, in each unit they were billed in; an `llm`'s, of the
    input, how much hit the prompt cache, where any call said."""
    units: dict[str, list[CallGroup]] = defaultdict(list)
    for call in calls:
        if call.usage_unit is not None:
            units[call.usage_unit].append(call)
    return [
        {
            "unit": unit,
            "input": _sum(c.usage_input for c in billed),
            "output": _sum(c.usage_output for c in billed),
            **({"cached": _sum(c.usage_cached for c in billed)} if llm else {}),
        }
        for unit, billed in sorted(units.items())
    ]


def _sum(values: Iterable[float | None]) -> float | None:
    told = [v for v in values if v is not None]
    return sum(told) if told else None


def turns(records: Sequence[RecordGroup], speaking: Spread) -> dict:
    """The turns: how they turned out, how many on users' own LLMs and in calls, and how
    soon the character began to speak (`speaking`), in the turns where she did."""
    by_result: Counter[str] = Counter()
    for r in records:
        by_result[r.result] += r.count
    return {
        "total": _count(records),
        "by_result": {result: by_result[result] for result in RESULTS if result != "not_connected"},
        "own_keys": _count(r for r in records if r.own_keys),
        "in_calls": _count(r for r in records if r.in_call),
        "time_to_speak_ms": {"count": speaking.known, **_percentiles(speaking, 50, 90)},
    }


CALL_RESULTS = ("success", "failed", "rejected", "not_connected")
"""How a call can turn out: a user leaving a call is hanging it up, not a result."""

def calls(records: Sequence[RecordGroup], tally: Tally) -> dict:
    """The calls: how they turned out, on whose keys, how long those that connected
    lasted, and why each ended (a call refused over the limit never began, so did not)."""
    by_result: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for r in records:
        by_result[r.result] += r.count
        if r.end_reason is not None:
            reasons[r.end_reason] += r.count
    return {
        "total": _count(records),
        "by_result": {result: by_result[result] for result in CALL_RESULTS},
        "own_keys": _count(r for r in records if r.own_keys),
        "duration_s": _percentiles(tally.lasted, 50, 90),
        "durations": [
            {"from_s": low, "to_s": high, "calls": count}
            for (low, high), count in zip(DURATIONS, tally.lasted_within, strict=True)
        ],
        "end_reasons": {reason: reasons[reason] for reason in CALL_END_REASONS},
    }


def stretches(now: datetime, span: Span, zone: ZoneInfo) -> list[datetime]:
    """Where each stretch of the range's trend begins, oldest first: the last one where
    the admin's clock last had a round hour, six hours or day, and as many before it as the
    range holds."""
    local = now.astimezone(zone)
    if span.stretch >= timedelta(days=1):
        last = local.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        hours = int(span.stretch / timedelta(hours=1))
        last = local.replace(hour=local.hour - local.hour % hours, minute=0, second=0,
                             microsecond=0)
    count = int(span.length / span.stretch)
    last = last.astimezone(UTC)
    return [last - span.stretch * (count - 1 - i) for i in range(count)]


def trend(taken: Sequence[Taken], starts: Sequence[datetime]) -> list[dict]:
    """How many turns and calls were `taken`, and by how many users, in each stretch
    beginning at `starts`; the first takes in whatever of the range comes before it."""
    return [
        {"start": start.isoformat(), "turns": t.turns, "calls": t.calls, "active_users": t.users}
        for start, t in zip(starts, taken, strict=True)
    ]


def characters(records: Sequence[RecordGroup], names: dict[str, str]) -> list[dict]:
    """How many turns and calls with each character, most turns first; a character since
    removed from the configuration has no name."""
    counted: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        counted[record.character_id][record.kind] += record.count
    return [
        {"character_id": id, "name": names.get(id), "turns": n["turn"], "calls": n["call"]}
        for id, n in sorted(counted.items(), key=lambda c: (-c[1]["turn"], -c[1]["call"], c[0]))
    ]


JUDGED_TO_TELL_SHARE = 20
"""How many judged turns a character needs for the shares of them that raised and lowered
her affinity to be told."""



def affinity(records: Sequence[RecordGroup], names: dict[str, str], cooled_users: int) -> dict:
    """How the judge model judged the turns recorded (only those, not those failed, refused
    or left), in all and for each character: how many began a cooldown, and for how many
    users (`cooled_users`); how many were out of line, how many tried to dictate her
    affinity; how many at each level. For each character, how many were judged, the shares
    that raised and lowered it (not told for too few, `JUDGED_TO_TELL_SHARE`), how many
    began a cooldown, and how often she hung up a call or ended a conversation, whatever
    for (`she_ended`); the most judged first."""
    judged = [r for r in records if r.kind == "turn" and r.result == "success" and r.judged]
    by_character: dict[str, list[RecordGroup]] = defaultdict(list)
    for record in judged:
        by_character[record.character_id].append(record)
    she_ended: Counter[str] = Counter()
    for r in records:
        if (r.kind == "call" and r.end_reason == "character_hung_up") or (
            r.kind == "turn" and r.ended_conversation
        ):
            she_ended[r.character_id] += r.count
    rows = []
    for id in by_character.keys() | she_ended.keys():
        turns = by_character.get(id, [])
        told = _count(turns) >= JUDGED_TO_TELL_SHARE
        rows.append({
            "character_id": id,
            "name": names.get(id),
            "judged": _count(turns),
            "raised_share": _share(turns, lambda step: step > 0) if told else None,
            "lowered_share": _share(turns, lambda step: step < 0) if told else None,
            "cooldowns": _count(r for r in turns if r.cooled),
            "she_ended": she_ended[id],
        })
    rows.sort(key=lambda r: (-r["judged"], -r["she_ended"], r["character_id"]))
    levels: Counter[int] = Counter()
    for r in judged:
        levels[_judged(r).level] += r.count
    return {
        **_judgements(judged),
        "cooled_users": cooled_users,
        "levels": [levels[level] for level in range(len(LEVEL_STEPS))],
        "characters": rows,
    }


def reach_outs(records: Sequence[RecordGroup]) -> dict:
    """How many times characters reached out, how many times it failed, and the share of
    those reached out that were answered (None of none); not by character."""
    succeeded = [r for r in records if r.result == "success"]
    return {
        "succeeded": _count(succeeded),
        "failed": _count(r for r in records if r.result == "failed"),
        "answered_share": (
            _count(r for r in succeeded if r.answered) / _count(succeeded) if succeeded else None
        ),
    }


def daily_tasks(records: Sequence[RecordGroup]) -> dict[str, int]:
    """How many times each daily task was done, by its id, in the order of `DAILY_TASKS`."""
    return {t.id: _count(r for r in records if r.result == t.id) for t in DAILY_TASKS}


def achievements(records: Sequence[RecordGroup]) -> dict[str, int]:
    """How many achievements' stages were unlocked, the draw chances they gave (stage n gives
    n), and the reset cards characters complete gave (one each)."""
    stages = [r for r in records if r.result != COMPLETE]
    return {
        "stages": _count(stages),
        "draws": sum(stage_of(r.result) * r.count for r in stages),
        "reset_cards": _count(r for r in records if r.result == COMPLETE),
    }


def _share(turns: Sequence[RecordGroup], moved: Callable[[float], bool]) -> float:
    """The share of the judged `turns` whose step (`Judged.step`: out of line lowers it
    whatever the level, trying to dictate it raises it never) `moved` holds of."""
    return _count(r for r in turns if moved(_judged(r).step)) / _count(turns)


def _judgements(records: Sequence[RecordGroup]) -> dict[str, int]:
    """How many of the judged turns began a cooldown, were out of line, or tried to dictate
    her affinity."""
    return {
        "cooldowns": _count(r for r in records if r.cooled),
        "out_of_line": _count(r for r in records if _judged(r).out_of_line),
        "manipulation": _count(r for r in records if _judged(r).manipulation),
    }


def _judged(record: RecordGroup) -> Judged:
    """What the judge model made of turns known to have been judged."""
    assert record.judged is not None
    return record.judged
