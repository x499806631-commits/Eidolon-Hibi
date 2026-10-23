"""How one turn turns out (GLOSSARY.md「回合」「开口」), a text turn or a turn of a call
alike: its result, the one it is settled with, and when the character began to speak.

`TurnOutcome` is told each event of the turn as the browser is: the turn done (success),
rejected for its input, or failed; when the turn is replied to afresh, the last reply's
result is the turn's. It is told too what the judge model made of the turn, which the
browser is not, for the record to keep. It is told when the character began to speak, by whoever knows
(`began_speaking`), and once the turn is over, whether a failure cut it short: a turn
over without a result failed if so, and was left by the user otherwise. It is settled
as it turned out once, by `settle`: a text turn's `Admission`, which keeps the use or
gives it back and writes its record, or the record of a turn in a call.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from eidolon.activity import Recording, Result
from eidolon.engine import TurnDone, TurnEvent, TurnFailed, TurnJudged, TurnRejected


class TurnOutcome:
    """One turn, recorded in `recording`, until it is over and settled by `settle`."""

    def __init__(self, recording: Recording, settle: Callable[[Result], Awaitable[None]]) -> None:
        self._recording = recording
        self._settle = settle
        self._result: Result | None = None
        self._over = False

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """While a text turn runs: its service calls are recorded under it, and once it
        is over, however it ended, so is the turn."""
        try:
            with self.current():
                yield
        finally:
            await self.over(cut_short_by_failure=False)

    @contextmanager
    def current(self) -> Iterator[None]:
        """Record the service calls made meanwhile, in this context, under the turn."""
        with self._recording.current():
            yield

    def see(self, event: TurnEvent) -> None:
        """An event of the turn, as the browser is told it."""
        if isinstance(event, TurnDone):
            self._result = "success"
        elif isinstance(event, TurnRejected):
            self._result = "rejected"
        elif isinstance(event, TurnFailed):
            self._result = "failed"
        elif isinstance(event, TurnJudged):
            self._recording.judged(event.judged, event.cooled, event.ended)

    def began_speaking(self) -> None:
        """The character began to speak (only the first time counts)."""
        self._recording.spoke()

    async def over(self, cut_short_by_failure: bool) -> None:
        """The turn is over: settled as it turned out, once. One over without a result
        failed if a failure cut it short, and was left by the user otherwise, as is one
        the user left before it began, which never ran. Its record is handed in to be
        written with the service calls made in it so far; those made after, as a photo's
        that comes out later, are written under it apart."""
        if self._over:
            return
        self._over = True
        await self._settle(self._result or ("failed" if cut_short_by_failure else "left"))
