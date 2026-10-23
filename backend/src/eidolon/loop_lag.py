"""How far behind the event loop runs: every call's audio and every request are served on
the one loop, and anything run on it that blocks holds all of them up. Measured every
`INTERVAL` from when the server starts, and logged as a warning once it is more than
`TOLERATED` late, with how many calls there are then; only logged, to know where to begin."""

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

INTERVAL = 1.0
"""How often, in seconds, the loop is measured."""

TOLERATED = 0.2
"""How late, in seconds, the loop may wake before it is logged."""


async def watch_the_loop(calls_in_progress: Callable[[], int]) -> None:
    """Sleep for `INTERVAL` over and over, until cancelled, logging each wake later than
    `TOLERATED` by how much, with `calls_in_progress` then."""
    loop = asyncio.get_running_loop()
    while True:
        asleep = loop.time()
        await asyncio.sleep(INTERVAL)
        late = loop.time() - asleep - INTERVAL
        if late > TOLERATED:
            log.warning(
                "the event loop ran %d ms late, %d calls in progress",
                round(late * 1000), calls_in_progress(),
            )
