"""Housekeeping: what the backend deletes as it ages, everyone's at once, every `TICK` from
when it starts: uses of the limits a day old (`usage.KEPT`), and activity records and
service calls 90 days old (`activity.KEPT`), with their rollups. Not as each turn begins,
which would keep it waiting on deleting.

Each is deleted in batches (`in_batches`), each a write task of its own (ADR-0028), so that
other writes are let in between: what piled up while the backend was down, a day's at once,
holds none of them up for long."""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TypeVarTuple, Unpack

from eidolon.data_access import Database

log = logging.getLogger(__name__)

A = TypeVarTuple("A")

TICK = timedelta(minutes=10)

BATCH = 1000
"""How many rows a batch deletes at most: measured to take some 40 ms at most, a day's
backlog of everything aged out some 350 ms in all, batch by batch (250 ms at once)."""


async def in_batches(
    db: Database,
    delete: Callable[[sqlite3.Connection, Unpack[A], int], int],
    *args: Unpack[A],
) -> None:
    """Delete with `delete` until it deletes fewer than `BATCH`: each time as a write task of
    its own, given the connection, `args` and `BATCH`, and saying how many it deleted."""
    while await db.write(delete, *args, BATCH) >= BATCH:
        pass


async def tidy(*forgetting: Callable[[], Awaitable[None]]) -> None:
    """Delete what has aged out with each of `forgetting`; one failing keeps none of the
    others from it."""
    for forget in forgetting:
        try:
            await forget()
        except Exception:
            log.exception("tidying up failed")


async def keep_tidy(*forgetting: Callable[[], Awaitable[None]]) -> None:
    """`tidy` now and at every `TICK`, until cancelled; a batch under way as it is, is
    written all the same."""
    while True:
        await tidy(*forgetting)
        await asyncio.sleep(TICK.total_seconds())
