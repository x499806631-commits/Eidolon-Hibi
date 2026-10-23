"""The calendar over HTTP (日历), for users who agreed to the terms: `/api/calendar?year=`
gives the idols' birthdays and the holidays that year has, as the calendar page shows them,
both empty without the calendar file; and the user's own anniversaries (纪念日), the hundredth
days and the whole years since they first spoke with each idol, which are theirs alone. The
producer's own birthday is in their profile."""

from fastapi import FastAPI, Query

from eidolon import anniversaries
from eidolon.assembly import Backend
from eidolon.producer import effective_timezone
from eidolon.sessions import UserId


def add_calendar_routes(app: FastAPI, backend: Backend) -> None:
    days = backend.special_days
    store = backend.store

    @app.get("/api/calendar")
    async def read_calendar(user_id: UserId, year: int = Query(ge=1, le=9999)) -> dict:
        profile = await store.get_profile(user_id)
        return {
            **(days.described(year) if days is not None else {"birthdays": [], "holidays": []}),
            "anniversaries": anniversaries.described(
                await store.first_records(user_id), year, effective_timezone(profile, None)
            ),
            "promises": [
                promise.shown()
                for promise in (await backend.promises.open_for(user_id) if backend.promises else [])
                if promise.due.year == year
            ],
        }
