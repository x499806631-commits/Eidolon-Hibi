"""She hangs up from elsewhere (GLOSSARY.md「通话」「冷却」): once a typed turn in another
conversation has her fall into a cooldown with the user, she hangs up the user's call with
her there and then, without waiting for the user to speak.
Idle, the call ends straight away; a reply she has started speaking goes no further, to be
recorded with what was complete of it, and one she has not is dropped; nothing said after
that is taken up. The browser is told `ended` as for a cooldown, with when it ends, the
「挂断了电话」event is written into the call's conversation, and the call is a success,
ended by her (「角色挂断」). A user has one call at a time, so no other call of
theirs, with her or anyone, is there to go on or end with it.

Driven through the backend's external API, with browsers calling in over real WebRTC
connections and the judge model faked turn by turn; what needs its timing held exactly, at
the Pipecat stage that takes the call's turns (see `test_call_turns.py`)."""

from dataclasses import replace
from datetime import timedelta

import pytest
from pipecat.tests.utils import SleepFrame

from eidolon.config import Settings
from tests.browser import open_browser
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    START,
    FakeClock,
    FakeIdentityProvider,
    FakeLLM,
    FakeServiceClients,
    level,
)
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import keep
from tests.test_call_records import call_recorded
from tests.test_call_turns import (  # noqa: F401
    INTERRUPTED_REPLY,
    INTERRUPTIBLE,
    NOW,
    SLOW_REPLY,
    CallUnderTest,
    kinds,
    mano_call,
    spoken,
    without_emotions,
)
from tests.test_conversation_events import record, shape
from tests.test_she_hangs_up import record_of
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
UNTIL = START + timedelta(days=1)
"""When a cooldown begun at 0 as the tests begin ends."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity on in `.env`."""
    return replace(
        settings, admins=frozenset({ADMIN}), judge=FAKE_JUDGE, affinity_and_gifts=True
    )


async def test_a_cooldown_begun_elsewhere_has_her_hang_up_an_idle_call(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
):
    clock = FakeClock()
    clients = FakeServiceClients()
    clients.judge.judgings = [level(0)]
    client = make_client(FakeLLM(REPLY), clients=clients, clock=clock)
    in_call = await start_conversation(client)
    elsewhere = await start_conversation(client)
    keep(settings.database_path, "producer", 5, clock.now)

    async with admin_beside(client, github) as admin, open_browser() as mano:
        await mano.call(client, conversation_id=in_call)
        await mano.connect()
        await send_turn(client, elsewhere, "随便你")
        ended = await mano.next_server_message("ended")
        await mano.hung_up()
        board = await call_recorded(admin)
    recorded = await record(client, in_call)

    assert ended["reason"] == "cooldown"
    assert ended["cooldown_until"] == UNTIL.isoformat()
    assert ended["counts"] is True
    assert shape(recorded) == ["hung_up"]
    assert ended["event"] == recorded[-1]
    calls = board["calls"]
    assert {r: n for r, n in calls["by_result"].items() if n} == {"success": 1}
    assert {r: n for r, n in calls["end_reasons"].items() if n} == {"character_hung_up": 1}


async def test_a_reply_she_has_started_speaking_goes_no_further_and_nothing_after_is_taken_up(
    mano_call: CallUnderTest,
):
    _, messages = await mano_call.run(
        FakeLLM(INTERRUPTED_REPLY, REPLY, delay=0.3),
        [spoken("早上好"), *INTERRUPTIBLE, SleepFrame(0.3), spoken("还在吗"), SleepFrame(0.5)],
        affinity=[level(2), level(2)],
        cooled=(0.5, UNTIL),
    )

    told = without_emotions(messages)
    assert kinds(told) == ["user", "done", "ended"]
    assert (told[-2]["message"]["ja"], told[-2]["message"]["zh"]) == ("ほわっ", "哇")
    assert messages[-1]["reason"] == "cooldown"
    assert messages[-1]["cooldown_until"] == UNTIL.isoformat()
    assert shape(await record_of(mano_call)) == ["user:早上好", "character:哇", "hung_up"]


async def test_a_reply_she_has_not_started_speaking_is_dropped_turn_and_all(
    mano_call: CallUnderTest,
):
    _, messages = await mano_call.run(
        FakeLLM(SLOW_REPLY, delay=0.3),
        [spoken("早上好"), SleepFrame(0.5)],
        affinity=[level(2)],
        cooled=(0.2, UNTIL),
    )

    assert kinds(without_emotions(messages)) == ["user", "ended"]
    assert messages[-1]["reason"] == "cooldown"
    assert shape(await record_of(mano_call)) == ["hung_up"]
