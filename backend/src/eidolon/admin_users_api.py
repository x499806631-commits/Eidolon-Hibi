"""The users page of the admin console: listing and searching users, looking one up, and
banning or unbanning them (ADR-0013).

What an admin sees of a user is who they are and how they use the deployment: their
sign-in methods (email included), when they joined and were last active, whether they
are banned and why, and whether they are an admin. Never what they said, what was
remembered of them or their producer profile, nor whether they brought their own keys,
which their browsers keep (ADR-0017): only how much of their use was on them.

Banning here is the same as `eidolon user ban`, both through `Store.set_banned`: the
user is signed out everywhere, their calls are hung up within seconds by the calls'
polling (`call.USER_CHECK_INTERVAL`), and they cannot sign in until unbanned. No admin
can be banned here, oneself included, so that no admin locks the others, or themselves,
out; the deployer can still on the command line.

An admin may also set a user's own limits on turns and calls an hour, each apart, or take
them off; the length of a call is the same for everyone.

How much a user has used the deployment lately is from the activity records (not the
`usage` table, the limit's sliding hour): the list shows how many turns and calls each
took in the last 24 hours, and can be sorted by either before it is cut into pages; a
user's own page shows, for the last 24 hours, 7 days and 30 days, how many turns and
calls they took and how many were rejected, on the deployer's keys and on their own
apart. A turn taken in a call is a turn too; a rejected turn or call counts only as
rejected.
"""

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from eidolon.accounts import signs_in_as_admin
from eidolon.activity import ActivityLog, RecordRow
from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.store import NotFound, SignInMethod, User
from eidolon.usage import UserLimits

PAGE_SIZE = 50
"""How many users a page of the list has unless asked for another number."""

RECENT = timedelta(hours=24)
"""How far back the list counts each user's turns and calls."""
USAGE_RANGES: dict[str, timedelta] = {
    "24h": RECENT, "7d": timedelta(days=7), "30d": timedelta(days=30),
}
"""How far back a user's own page counts their turns, calls and rejections."""

Sort = Literal["newest", "turns", "calls"]
"""How the list is sorted: newest first, or by the turns or the calls taken in the last
24 hours, most first (then newest)."""


class BanInput(BaseModel):
    note: str | None = Field(None, max_length=500)
    """Why, for the admins only; the banned user never sees it."""


Limit = Annotated[int, Field(ge=0, le=100_000)] | Literal["unlimited"] | None


class LimitsInput(BaseModel):
    """A user's own limits an hour: a number (0: on their own keys only), "unlimited", or
    None, the deployment's; at least one of them set."""

    turns_per_hour: Limit = None
    calls_per_hour: Limit = None
    photos_per_hour: Limit = None
    note: str | None = Field(None, max_length=500)
    """Why, for the admins only."""

    @model_validator(mode="after")
    def _any(self) -> "LimitsInput":
        if self.turns_per_hour is None and self.calls_per_hour is None and (
            self.photos_per_hour is None
        ):
            raise ValueError("至少要单独设定回合数、通话次数或照片数中的一项")
        return self


def recent_usage(records: list[RecordRow], since: datetime) -> dict:
    """The turns and calls taken after `since`, and those rejected, each on the deployer's
    keys and on the user's own apart."""
    counts = {
        what: {"deployer": 0, "own_keys": 0} for what in ("turns", "calls", "rejected")
    }
    for record in records:
        if record.started_at > since:
            what = "rejected" if record.result == "rejected" else f"{record.kind}s"
            counts[what]["own_keys" if record.own_keys else "deployer"] += 1
    return counts


def add_admin_user_routes(app: FastAPI, backend: Backend) -> None:
    store, settings, clock = backend.store, backend.settings, backend.clock
    limiter, activity = backend.usage, backend.activity

    async def described(users: list[User]) -> list[dict]:
        """What the list shows of each user."""
        ids = [u.id for u in users]
        active = await store.last_active(ids) if users else {}
        methods = await store.sign_in_methods_of(ids)
        taken = await activity.taken_since(ids, clock() - RECENT)
        own_limits = await limiter.limits_of(ids)
        return [
            summary(user, methods[user.id], active.get(user.id), taken.get(user.id, {}),
                    asdict(own) if (own := own_limits[user.id]) else None)
            for user in users
        ]

    def summary(
        user: User, methods: list[SignInMethod], last_active: datetime | None,
        taken: dict[str, int], own_limits: dict | None,
    ) -> dict:
        return {
            "id": user.id,
            "name": user.name,
            "sign_in_methods": [
                {"provider": m.provider, "subject": m.subject,
                 "display_name": m.display_name, "email": m.email}
                for m in methods
            ],
            "created_at": user.created_at,
            "last_active_at": last_active.isoformat() if last_active else None,
            "banned": user.banned_at is not None,
            "is_admin": signs_in_as_admin(methods, settings),
            "limits": own_limits,
            "usage_24h": {"turns": taken.get("turn", 0), "calls": taken.get("call", 0)},
        }

    async def details(user_id: str) -> dict:
        """What a user's own page shows of them; 404 for no such user."""
        try:
            user = await store.get_user(user_id)
        except NotFound:
            raise HTTPException(404, "没有这位用户") from None
        [shown] = await described([user])
        now = clock()
        records = await activity.of_user(user_id, now - max(USAGE_RANGES.values()))
        return shown | {
            "banned_at": user.banned_at,
            "ban_note": user.ban_note,
            "usage": {
                name: recent_usage(records, now - span) for name, span in USAGE_RANGES.items()
            },
        }

    @app.get("/api/admin/users")
    async def list_users(
        _: Admin,
        q: str = "",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = PAGE_SIZE,
        sort: Sort = "newest",
    ) -> dict:
        """A page of the users, or of those `q` finds by name, email or user id, sorted
        by `sort`; with how many there are in all."""
        ranking = None if sort == "newest" else ActivityLog.ranking(
            "turn" if sort == "turns" else "call", clock() - RECENT
        )
        users, total = await store.find_users(q, offset, limit, ranking)
        return {"users": await described(users), "total": total}

    @app.get("/api/admin/users/{user_id}")
    async def user_details(user_id: str, _: Admin) -> dict:
        """A user, with when and why they were banned, and their recent usage."""
        return await details(user_id)

    @app.post("/api/admin/users/{user_id}/ban")
    async def ban(user_id: str, body: BanInput, _: Admin) -> dict:
        """Ban the user, with a note for the admins; 409 for an admin."""
        found = await details(user_id)
        if found["is_admin"]:
            raise HTTPException(409, "不能在后台封禁管理者")
        await store.set_banned(user_id, clock(), (body.note or "").strip() or None)
        return await details(user_id)

    @app.delete("/api/admin/users/{user_id}", status_code=204)
    async def delete_user(user_id: str, _: Admin) -> None:
        """Delete the user and all their data, as they would themselves; 409 for an admin."""
        if (await details(user_id))["is_admin"]:
            raise HTTPException(409, "不能在后台删除管理者")
        await backend.accounts.delete(user_id)

    @app.delete("/api/admin/users/{user_id}/ban")
    async def unban(user_id: str, _: Admin) -> dict:
        """Lift the user's ban, and its note; they may sign in again."""
        await details(user_id)
        await store.set_banned(user_id, None)
        return await details(user_id)

    @app.put("/api/admin/users/{user_id}/limits")
    async def set_limits(user_id: str, body: LimitsInput, _: Admin) -> dict:
        """Set the user's own limits, replacing those they had; from their next turn or
        call on."""
        await details(user_id)
        await limiter.set_user_limits(user_id, UserLimits(
            body.turns_per_hour, body.calls_per_hour, (body.note or "").strip() or None,
            photos_per_hour=body.photos_per_hour,
        ))
        return await details(user_id)

    @app.delete("/api/admin/users/{user_id}/limits")
    async def take_off_limits(user_id: str, _: Admin) -> dict:
        """Take off the user's own limits; the deployment's hold for them again."""
        await details(user_id)
        await limiter.set_user_limits(user_id, None)
        return await details(user_id)
