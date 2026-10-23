"""Letting a turn or call begin and settling it once it is over, through `Admissions`
alone: whose keys it runs on, its activity record, its use of the limit and the deployer's
lease, each of them looked at as the admin and the limiter see them.

All of it is real, on a fresh database, but the deployer, which counts its leases, and the
speech services the user's own keys are tried on; the user is allowed one turn and one
call an hour.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pytest

from eidolon.activity import ActivityLog, Result
from eidolon.admission import Admissions
from eidolon.data_access import Database
from eidolon.config import EXEMPTING_SERVICES, ChatModelSettings, LLMSettings, SttKey, UsageLimits
from eidolon.own_keys import (
    BroughtKeys,
    CallsUnavailable,
    DeployerKeys,
    DeployerLease,
    OwnKeys,
)
from eidolon.usage import OverLimit, UsageKind, UsageLimiter
from tests.conftest import users_in
from tests.fakes import FakeBackgroundLLM, FakeClock, FakeLLM, FakeLLMService, FakeSpeech

MY_LLM = ChatModelSettings(
    LLMSettings("https://llm.example.com/v1", "sk-my-own-llm-cccc", "my-model")
)
ALL_OWN = BroughtKeys(MY_LLM, "my-own-fish", SttKey("soniox", "my-own-soniox"))
"""The user's own LLM and speech keys, which their calls are exempt on."""


class CountingDeployer:
    """The deployer's keys, counting how many times they were leased and let go."""

    def __init__(self) -> None:
        self.keys = DeployerKeys(
            FakeLLM(), FakeBackgroundLLM(), SttKey("soniox", "deployer-soniox"), "deployer-fish"
        )
        self.leased = 0
        self.released = 0

    def lease(self) -> DeployerLease:
        self.leased += 1
        return DeployerLease(self.keys, self._release)

    def _release(self) -> None:
        self.released += 1


async def not_cooling(*_: str) -> None:
    """No character is in a cooldown with anyone."""


class Deployment:
    """A user allowed one turn and one call an hour, and everything letting them begin."""

    def __init__(self, path: Path, user: str) -> None:
        self.clock = FakeClock()
        self.user = user
        self.db = Database(path)
        self.usage = UsageLimiter(
            self.db, UsageLimits(turns_per_hour=1, calls_per_hour=1), self.clock
        )
        self.activity = ActivityLog(self.db, self.clock)
        self.deployer = CountingDeployer()
        self.llm_service = FakeLLMService()
        self.speech = FakeSpeech()

    def admissions(self) -> Admissions:
        return Admissions(
            self.usage, self.activity, self.own_keys(), cooldown_until=not_cooling
        )

    def own_keys(self) -> OwnKeys:
        return OwnKeys(
            self.llm_service, self.deployer, self.activity, self.speech,
            exempting=lambda: frozenset(EXEMPTING_SERVICES),
        )

    async def counted(self, kind: UsageKind = "turn") -> bool:
        """Whether this hour's turn, or call, is used up, that is, the last one counted."""
        try:
            await (await self.usage.take(self.user, kind)).give_back()
        except OverLimit:
            return True
        return False

    async def turns_taken(self) -> int:
        """Uses of the limit kept for the admin, counting or not."""
        return (await self.usage.last_day(self.user)).total.turns

    async def calls_taken(self) -> int:
        return (await self.usage.last_day(self.user)).total.calls

    async def records(self) -> list[tuple[Result, bool]]:
        """Each of the user's records: how it turned out, and whether on their own keys."""
        return [
            (r.result, r.own_keys)
            for r in await self.activity.of_user(self.user, self.clock() - timedelta(hours=1))
        ]


@pytest.fixture
async def deployment(tmp_path: Path) -> AsyncIterator[Deployment]:
    path = tmp_path / "eidolon.db"
    [user] = await users_in(path, "producer")
    deployment = Deployment(path, user)
    yield deployment
    await deployment.db.close()


@pytest.mark.parametrize(
    ("result", "counted"),
    [("success", True), ("failed", False), ("rejected", False), ("left", True)],
)
async def test_a_turn_counts_when_it_succeeded_or_was_left_and_is_given_back_otherwise(
    deployment: Deployment, result: Result, counted: bool
):
    admission = await deployment.admissions().turn(deployment.user, "mano", BroughtKeys())

    await admission.settle(result)

    assert await deployment.counted() is counted
    assert admission.counts(result) is counted
    assert await deployment.records() == [(result, False)]
    assert deployment.deployer.released == 1


async def test_a_turn_over_the_limit_is_refused_recorded_rejected_and_lets_go_of_its_lease(
    deployment: Deployment,
):
    admissions = deployment.admissions()
    await (await admissions.turn(deployment.user, "mano", BroughtKeys())).settle("success")

    with pytest.raises(OverLimit):
        await admissions.turn(deployment.user, "mano", BroughtKeys())

    assert await deployment.turns_taken() == 1
    assert await deployment.records() == [("success", False), ("rejected", False)]
    assert deployment.deployer.released == 2


async def test_a_turn_bringing_an_own_llm_does_not_count_and_leases_nothing(deployment: Deployment):
    admissions = deployment.admissions()

    for _ in range(2):  # over the limit, were they counted
        admission = await admissions.turn(deployment.user, "mano", BroughtKeys(llm=MY_LLM))
        assert admission.exempt
        assert not any(admission.counts(r) for r in ("success", "failed", "rejected", "left"))
        await admission.settle("success")

    assert not await deployment.counted()
    assert await deployment.turns_taken() == 2  # kept for the admin all the same
    assert await deployment.records() == [("success", True), ("success", True)]
    assert deployment.deployer.leased == 0


async def test_a_turn_settled_twice_is_settled_once(deployment: Deployment):
    admission = await deployment.admissions().turn(deployment.user, "mano", BroughtKeys())

    await admission.settle("failed")
    await admission.settle("success")

    assert not await deployment.counted()
    assert await deployment.records() == [("failed", False)]
    assert deployment.deployer.released == 1


@pytest.mark.parametrize(
    ("result", "counted"), [("not_connected", False), ("success", True), ("failed", True)]
)
async def test_a_call_counts_once_it_connected_and_is_given_back_otherwise(
    deployment: Deployment, result: Result, counted: bool
):
    admission = await deployment.admissions().call(deployment.user, "mano", BroughtKeys())

    await admission.settle(result, None if result == "not_connected" else 12.5, "hung_up")

    assert await deployment.counted("call") is counted
    assert admission.counts(result) is counted
    assert await deployment.records() == [(result, False)]
    assert deployment.deployer.released == 1


async def test_a_call_over_the_limit_is_refused_recorded_rejected_and_lets_go_of_its_lease(
    deployment: Deployment,
):
    admissions = deployment.admissions()
    admission = await admissions.call(deployment.user, "mano", BroughtKeys())
    await admission.settle("success", 3.0, "hung_up")

    with pytest.raises(OverLimit):
        await admissions.call(deployment.user, "mano", BroughtKeys())

    assert await deployment.calls_taken() == 1
    assert await deployment.records() == [("success", False), ("rejected", False)]
    assert deployment.deployer.released == 2


async def test_a_call_without_keys_for_speech_is_unavailable_and_not_recorded(
    deployment: Deployment,
):
    deployment.deployer.keys = DeployerKeys(FakeLLM(), FakeBackgroundLLM())

    with pytest.raises(CallsUnavailable):
        await deployment.admissions().call(deployment.user, "mano", BroughtKeys())

    assert await deployment.calls_taken() == 0
    assert await deployment.records() == []
    assert deployment.deployer.leased == deployment.deployer.released


async def test_a_call_on_the_users_own_keys_alone_does_not_count(deployment: Deployment):
    admissions = deployment.admissions()

    for _ in range(2):  # over the limit, were they counted
        admission = await admissions.call(deployment.user, "mano", ALL_OWN)
        assert admission.exempt
        assert not any(admission.counts(r) for r in ("success", "failed", "not_connected"))
        await admission.settle("success", 3.0, "hung_up")

    assert not await deployment.counted("call")
    assert await deployment.calls_taken() == 2  # kept for the admin all the same
    assert await deployment.records() == [("success", True), ("success", True)]
