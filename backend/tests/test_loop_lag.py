"""How far behind the event loop runs, logged once it is late by more than is tolerated."""

import asyncio
import logging
import time

import pytest

import eidolon.loop_lag
from eidolon.loop_lag import watch_the_loop


async def test_an_event_loop_running_late_is_logged_with_the_calls_then(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(eidolon.loop_lag, "INTERVAL", 0.02)
    monkeypatch.setattr(eidolon.loop_lag, "TOLERATED", 0.1)
    watching = asyncio.create_task(watch_the_loop(lambda: 3))
    try:
        with caplog.at_level(logging.WARNING, logger="eidolon.loop_lag"):
            await asyncio.sleep(0.1)  # on time: nothing logged
            on_time = list(caplog.messages)
            time.sleep(0.3)  # blocking the loop
            await asyncio.sleep(0.05)
    finally:
        watching.cancel()

    assert on_time == []
    [late] = caplog.messages
    assert "ms late, 3 calls in progress" in late
