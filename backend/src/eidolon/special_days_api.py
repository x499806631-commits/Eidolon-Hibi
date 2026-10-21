"""The calendar over HTTP (日历), for users who agreed to the terms: `/api/calendar?year=`
gives the idols' birthdays and the holidays that year has, as the calendar page shows them;
both empty without the calendar file. The producer's own birthday is in their profile."""

from fastapi import FastAPI, Query

from eidolon.assembly import Backend
from eidolon.sessions import UserId


def add_calendar_routes(app: FastAPI, backend: Backend) -> None:
    days = backend.special_days

    @app.get("/api/calendar")
    def read_calendar(_: UserId, year: int = Query(ge=1, le=9999)) -> dict:
        if days is None:
            return {"birthdays": [], "holidays": []}
        return days.described(year)
