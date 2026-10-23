"""Whose keys a turn or call runs on, answered once, through the own keys module, from the
user's own keys it brings: theirs where it brought them, the deployer's otherwise, never
falling back from a key of theirs that does not work; and whether it counts towards the
user's limit, by one rule, also asked before a call from the services it will bring keys
for."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from eidolon.activity import OWN, ActivityLog
from eidolon.data_access import Database
from eidolon.config import EXEMPTING_SERVICES, ChatModelSettings, LLMSettings, SttKey
from eidolon.llm import ChatLLM, ChatMessage
from eidolon.own_keys import (
    BroughtKeys,
    CallsUnavailable,
    DeployerKeys,
    DeployerLease,
    OwnKeys,
    SERVICES,
    Service,
)
from tests.fakes import (
    DEPLOYER_FISH_KEY,
    DEPLOYER_SONIOX_KEY,
    FakeBackgroundLLM,
    FakeClock,
    FakeLLM,
    FakeLLMService,
    FakeSpeech,
)


class CountingDeployer:
    """The deployer's keys, counting how many times they were leased and let go."""

    def __init__(self, keys: DeployerKeys) -> None:
        self.keys = keys
        self.leased = 0
        self.released = 0

    def lease(self) -> DeployerLease:
        self.leased += 1
        return DeployerLease(self.keys, self._release)

    def _release(self) -> None:
        self.released += 1


class Deployment:
    """The deployer's keys, and where service calls are recorded."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.activity = ActivityLog(db, FakeClock())
        self.chat = FakeLLM()
        self.memory = FakeBackgroundLLM()
        self.llm_service = FakeLLMService()
        self.speech = FakeSpeech()

    def own_keys(
        self,
        *,
        stt: SttKey | None = SttKey("soniox", DEPLOYER_SONIOX_KEY),
        tts: str | None = DEPLOYER_FISH_KEY,
    ) -> OwnKeys:
        """The own keys module on the deployer's keys, leased through `deployer`, which
        counts them, from the last call on."""
        self.deployer = CountingDeployer(DeployerKeys(self.chat, self.memory, stt, tts))
        return OwnKeys(
            self.llm_service, self.deployer, self.activity, self.speech,
            exempting=lambda: frozenset(EXEMPTING_SERVICES),
        )


MY_LLM = ChatModelSettings(
    LLMSettings("https://llm.example.com/v1", "sk-my-own-llm-cccc", "my-model")
)
MY_FISH = "fish-my-own-key-aaaa"
MY_SONIOX = SttKey("soniox", "soniox-my-own-key-bbbb")
MY_DASHSCOPE = SttKey("bailian", "sk-my-own-dashscope-dddd", "singapore")


def brought(*services: Service, stt: SttKey = MY_SONIOX) -> BroughtKeys:
    """The user's own keys for `services`, as a turn or call brings them."""
    return BroughtKeys(
        llm=MY_LLM if "llm" in services else None,
        tts=MY_FISH if "tts" in services else None,
        stt=stt if "stt" in services else None,
    )


async def ask(llm: ChatLLM) -> None:
    async for _ in llm.stream([ChatMessage("user", "在吗")]):
        pass


@pytest.fixture
async def deployment(tmp_path: Path) -> AsyncIterator[Deployment]:
    deployment = Deployment(Database(tmp_path / "eidolon.db"))
    yield deployment
    await deployment.db.close()


async def test_a_turn_that_brings_an_llm_runs_on_it_for_both_and_does_not_count(
    deployment: Deployment,
):
    deployment.llm_service.llm = FakeLLM(["好的"], ["好的"])

    keys = deployment.own_keys().for_turn(brought("llm"))
    await ask(keys.llms.chat)
    await ask(keys.llms.background)

    assert deployment.llm_service.llm.keys == [MY_LLM.text.api_key, MY_LLM.text.api_key]
    assert deployment.chat.keys == []
    assert deployment.memory.prompts == []
    assert keys.exempt is True and keys.own_llm is True
    # Each request is recorded as a service call on their own key, never saying which
    # host or model theirs is.
    _, calls = await deployment.activity.since(datetime.min.replace(tzinfo=UTC))
    assert sorted((c.service, c.provider, c.model, c.own_key) for c in calls) == [
        ("chat_llm", OWN, OWN, True),
        ("memory_llm", OWN, OWN, True),
    ]


async def test_a_turn_that_brings_nothing_runs_on_the_deployers_models(
    deployment: Deployment,
):
    keys = deployment.own_keys().for_turn(BroughtKeys())

    assert keys.llms.chat is deployment.chat
    assert keys.llms.background is deployment.memory
    assert keys.own_llm is False


async def test_a_call_that_brings_speech_keys_hears_and_speaks_on_them_and_the_rest_on_the_deployers(
    deployment: Deployment,
):
    keys = deployment.own_keys().for_call(brought("tts"))

    assert (keys.stt, keys.tts) == (SttKey("soniox", DEPLOYER_SONIOX_KEY), MY_FISH)
    assert keys.own == {"tts"}
    assert keys.llms.chat is deployment.chat
    assert keys.llms.background is deployment.memory
    assert keys.own_llm is False


async def test_a_call_that_brings_all_three_runs_on_them_alone(deployment: Deployment):
    deployment.llm_service.llm = FakeLLM(["好的"])

    keys = deployment.own_keys().for_call(brought("llm", "tts", "stt"))
    await ask(keys.llms.chat)

    assert (keys.stt, keys.tts, keys.own) == (MY_SONIOX, MY_FISH, {"stt", "tts"})
    assert keys.own_llm is True
    assert deployment.llm_service.llm.keys == [MY_LLM.text.api_key]
    assert deployment.chat.keys == []


@pytest.mark.parametrize(
    ("services", "turns", "calls"),
    [
        ((), False, False),
        (("llm",), True, False),
        (("tts", "stt"), False, False),
        (("llm", "tts"), True, False),
        (("llm", "tts", "stt"), True, True),
    ],
)
async def test_turns_bringing_an_own_llm_and_calls_bringing_all_three_do_not_count(
    deployment: Deployment, services: tuple[Service, ...], turns: bool, calls: bool
):
    own_keys = deployment.own_keys()

    assert own_keys.exempt("turn", services) is turns
    assert own_keys.exempt("call", services) is calls
    assert own_keys.for_turn(brought(*services)).exempt is turns
    assert own_keys.for_call(brought(*services)).exempt is calls


@pytest.mark.parametrize(
    ("deployers", "mine"),
    [
        (SttKey("bailian", "deployer-dashscope-key", "beijing"), MY_SONIOX),
        (SttKey("soniox", DEPLOYER_SONIOX_KEY), MY_DASHSCOPE),
    ],
)
async def test_a_users_own_speech_recognition_is_the_service_they_chose_whichever_the_deployer_did(
    deployment: Deployment, deployers: SttKey, mine: SttKey
):
    own_keys = deployment.own_keys(stt=deployers)

    assert own_keys.for_call(brought("stt", stt=mine)).stt == mine
    assert own_keys.for_call(BroughtKeys()).stt == deployers


async def test_a_call_can_bring_a_key_for_what_the_deployer_has_none_for(
    deployment: Deployment,
):
    without_stt = deployment.own_keys(stt=None)
    without_tts = deployment.own_keys(tts=None)

    assert without_stt.for_call(brought("stt")).stt == MY_SONIOX
    with pytest.raises(CallsUnavailable):
        without_stt.for_call(BroughtKeys())
    with pytest.raises(CallsUnavailable):
        without_tts.for_call(brought("stt"))


async def test_asking_before_a_call_answers_by_the_rule_for_calls_and_lets_go_of_the_lease(
    deployment: Deployment,
):
    assert deployment.own_keys().call_exempt(SERVICES) is True
    assert deployment.deployer.leased == deployment.deployer.released == 1


async def test_asking_before_a_call_without_keys_for_speech_says_calls_are_unavailable(
    deployment: Deployment,
):
    without_speech = deployment.own_keys(stt=None, tts=None)

    with pytest.raises(CallsUnavailable):
        without_speech.call_exempt(("llm", "tts"))
    assert deployment.deployer.leased == deployment.deployer.released == 1
    assert without_speech.call_exempt(("tts", "stt")) is False
