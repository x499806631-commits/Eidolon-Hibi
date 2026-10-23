"""Stats (GLOSSARY.md「统计」): the user's own dealings with the characters, summed up for
them alone under `/api/stats`.

Only what is still in their conversations counts: a turn (the user's message, at the time
of it) or a gift (a `gift` conversation event); a past conversation deleted, what was in
it no longer counts. Dates and hours are where the producer is: the profile's time zone,
else the browser's (`X-Timezone`), else the default, as a turn tells her now
(`effective_timezone`).

What does not change with the range: the days since the user signed up, counting that day
as the first; the days in a row they came, a day they took a turn or gave a gift, the
current run counted back from today, or from yesterday while they have not come yet
today, and the longest; how many of either at each hour of the day, all told; and how many
on each day of the past 52 weeks, from a Monday, and this week up to today.

What does (`range`): the past 30 days or 365, today the last, or all of it. How many turns
and how many of either at each hour of the day; how many calls connected in it, by when
they did, and how long they lasted all told, in seconds, once connected (ADR-0021: only
those since calls were kept); each character's turns and how long her calls lasted, she
among those anything is left with, in the idols' order, and the days since the first thing
left with her, a message, an event or a call, counting that day as the first; and the gifts
given, how many she liked as they were given, and whom the most went to, the first in the
idols' order of those tied. Only numbers: what is shown of them, and how, is the page's to
decide.

And the characters by her affinity with the user (GLOSSARY.md「好感度」), whatever the range:
those in each tier, from cold to intimate, in the idols' order, by tier alone, never by
value, so that none in a tier reads higher than another; one in a cooldown is in cold all
the same. And how many stages of the achievements (GLOSSARY.md「成就」) the user has
unlocked and how many characters are complete, as the achievements keep them, so whatever the
range and a past conversation deleted. Where affinity is off there are no tiers, no gifts and
no achievements, as the user is shown nothing of them elsewhere.
"""

from collections import Counter, defaultdict
from collections.abc import Collection, Iterable
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, Query
from starlette.concurrency import run_in_threadpool

from eidolon.achievements import Tally
from eidolon.affinity import TIERS, Tier
from eidolon.assembly import Backend
from eidolon.characters import in_idol_order
from eidolon.producer import effective_timezone
from eidolon.sessions import UserId
from eidolon.store import Contact, KeptCall

HEATMAP_WEEKS = 52
"""The whole weeks before this one the heatmap goes back."""

Range = Literal["30d", "1y", "all"]

RANGE_DAYS: dict[Range, int | None] = {"30d": 30, "1y": 365, "all": None}
"""The days each range takes in, today the last; all of them for `all`."""


def add_stats_routes(app: FastAPI, backend: Backend) -> None:
    store = backend.store

    @app.get("/api/stats")
    async def stats(
        user_id: UserId,
        span: Annotated[Range, Query(alias="range")] = "all",
        device_timezone: Annotated[str | None, Header(alias="X-Timezone")] = None,
    ) -> dict:
        profile = await store.get_profile(user_id)
        zone = ZoneInfo(effective_timezone(profile, device_timezone))
        signed_up = datetime.fromisoformat((await store.get_user(user_id)).created_at)
        contacts = await store.contacts(user_id)
        first = await store.first_records(user_id)
        calls = await store.kept_calls(user_id)
        idols = in_idol_order(backend.characters.values())
        tiers = (
            await backend.affinities.tiers(user_id, [c.id for c in idols])
            if backend.config.affinity_on else None
        )
        tally = await backend.happenings.tally(user_id)
        # Counting goes over every contact kept: on a thread.
        return await run_in_threadpool(
            summed_up, span, zone, signed_up, contacts, first, calls, tiers, tally
        )

    def summed_up(
        span: Range,
        zone: ZoneInfo,
        signed_up: datetime,
        kept_contacts: list[Contact],
        first: dict[str, datetime],
        calls: list[KeptCall],
        tiers: list[Tier] | None,
        tally: Tally | None,
    ) -> dict:
        """What the stats show, from what was read: `tiers`, each of the idols' in their
        order, None where affinity is off, as `tally` is."""
        today = backend.clock().astimezone(zone).date()
        contacts = [(c, c.at.astimezone(zone)) for c in kept_contacts]
        per_day = Counter(at.date() for _, at in contacts)
        start = today - timedelta(days=today.weekday() + 7 * HEATMAP_WEEKS)

        def days_since(at: datetime) -> int:
            """The days since `at`, counting its day as the first."""
            return max(1, (today - at.astimezone(zone).date()).days + 1)

        days = RANGE_DAYS[span]
        since = None if days is None else today - timedelta(days=days - 1)
        idols = in_idol_order(backend.characters.values())
        return {
            # Since signing up, or since the first thing kept if earlier: conversations imported
            # in a data package from another deployment (GLOSSARY.md「统计」).
            "days_known": days_since(min([signed_up, *first.values()])),
            "visits": visits(per_day.keys(), today),
            "all_hours": by_hour(at for _, at in contacts),
            "heatmap": {
                "from": start.isoformat(),
                "counts": [
                    per_day[start + timedelta(days=i)] for i in range((today - start).days + 1)
                ],
            },
            "affinity": by_tier(
                (c.id, tier) for c, tier in zip(idols, tiers)
            ) if tiers is not None else None,
            "achievements": tally and tally.shown(),
            "range": in_range(
                span,
                [(c, at) for c, at in contacts if since is None or at.date() >= since],
                [
                    call
                    for call in calls
                    if since is None or call.connected_at.astimezone(zone).date() >= since
                ],
                {c.id: days_since(first[c.id]) for c in idols if c.id in first},
                gifts_on=tiers is not None,
            ),
        }


def in_range(
    span: Range,
    within: list[tuple[Contact, datetime]],
    calls: list[KeptCall],
    known_in_idol_order: dict[str, int],
    *,
    gifts_on: bool,
) -> dict:
    """What changes with the range `span`, of the turns and gifts `within` it, each at its
    time where the user is, and the `calls` that connected in it, and the characters
    anything is left with, in the idols' order, and the days each has been known; whom the
    most gifts went to, tied, is the first of them. No gifts unless `gifts_on`."""
    turns = Counter(c.character_id for c, _ in within if c.kind == "turn")
    called: defaultdict[str, float] = defaultdict(float)
    for call in calls:
        called[call.character_id] += call.duration_s
    gifts = [c for c, _ in within if c.kind == "gift"]
    given = Counter(c.character_id for c in gifts)
    most = max(given.values(), default=0)
    return {
        "span": span,
        "turns": turns.total(),
        "by_hour": by_hour(at for _, at in within),
        "characters": [
            {
                "character_id": character, "turns": turns[character], "days_known": days,
                "call_seconds": called[character],
            }
            for character, days in known_in_idol_order.items()
        ],
        "calls": {"count": len(calls), "seconds": sum(c.duration_s for c in calls)},
        "gifts": {
            "given": len(gifts),
            "liked": sum(c.liked for c in gifts),
            "most": next(
                (
                    {"character_id": character, "given": most}
                    for character in known_in_idol_order
                    if most and given[character] == most
                ),
                None,
            ),
        } if gifts_on else None,
    }


def by_tier(tiers_of: Iterable[tuple[str, Tier]]) -> list[dict]:
    """Each tier, from cold to intimate, and the characters in it as `tiers_of` has them,
    in the order given."""
    tiers: dict[Tier, list[str]] = {tier: [] for tier, _ in TIERS}
    for character, tier in tiers_of:
        tiers[tier].append(character)
    return [{"tier": tier, "characters": characters} for tier, characters in tiers.items()]


def by_hour(times: Iterable[datetime]) -> list[int]:
    """How many of `times` at each hour of the day, 0–23."""
    per_hour = Counter(at.hour for at in times)
    return [per_hour[hour] for hour in range(24)]


def visits(days: Collection[date], today: date) -> dict:
    """The days in a row the user came, of `days`, the days they came: the run up to today,
    or up to yesterday while today is not one of them yet, and the longest."""
    longest = run = 0
    previous: date | None = None
    for day in sorted(d for d in days if d <= today):
        run = run + 1 if previous == day - timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    current = 0
    day = today if today in days else today - timedelta(days=1)
    while day in days:
        current += 1
        day -= timedelta(days=1)
    return {"current": current, "longest": longest}
