"""Exempting keys (GLOSSARY.md「免限额密钥」): the deployer chooses which of the LLM, speech
synthesis and recognition a call must bring the user's own keys for, all of them, to
count towards no limit; the rest it runs on the deployer's. `.env` sets them
(`EIDOLON_EXEMPTING_LLM`, `_TTS`, `_STT`: unset or 1 on, 0 off, never all off), the admin
console may override them beside the limits, and they hold from the next turn and call
on, while a call in progress keeps the rule it began with. Text turns go by the LLM alone,
whichever are chosen. Asking before a call, the settings page's question and
`/api/account` answer as calls are counted. All three chosen, as by default, is the rule
the rest of the tests count by (test_own_speech_keys, test_call_records, test_exemption).
Driven through the backend's external API, with a browser calling in over a real WebRTC
connection and fake outside services."""

import asyncio
from dataclasses import replace

import httpx
import pytest

from eidolon.config import Settings, UsageLimits
from tests.browser import TIMEOUT, Browser, open_browser
from tests.conftest import DEPLOYER_FISH_KEY, DEPLOYER_SONIOX_KEY, ClientFactory
from tests.fakes import FakeClock, FakeIdentityProvider, FakeLLM, FakeLLMService, FakeSpeech
from tests.test_activity_records import dashboard
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_exemption import exemption
from tests.test_live_config import start
from tests.test_own_llm import LLM
from tests.test_own_speech_keys import MY_FISH, ask_before_calling, own_keys
from tests.test_rate_limit import post_turn
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]
LIMITS = "/api/admin/config/limits"
ENV_LIMITS = {
    "turns_per_hour": 1, "calls_per_hour": 1, "photos_per_hour": 5, "call_minutes": 10,
    "turns_per_call": 40, "concurrent_calls": 10,
    "history_turns": 20,
}
"""The limits as `.env` has them in these tests, but for the exempting keys."""
ALL_ON = {"exempting_llm": True, "exempting_tts": True, "exempting_stt": True}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, and a turn and a call an hour."""
    return replace(
        settings,
        admins=frozenset({ADMIN}),
        usage_limits=UsageLimits(turns_per_hour=1, calls_per_hour=1, call_minutes=10),
    )


async def choose(admin: httpx.AsyncClient, *services: object) -> httpx.Response:
    """Have the admin choose `services` as the exempting keys in the admin console."""
    switches = {f"exempting_{s}": s in services for s in ("llm", "tts", "stt")}
    return await admin.put(LIMITS, json=ENV_LIMITS | switches)


async def answers(client: httpx.AsyncClient, *services: str) -> tuple[bool, bool]:
    """Whether a call bringing the user's own keys for `services` would count towards
    nothing, as asked before calling and as the settings page asks."""
    before = await ask_before_calling(client, *services)
    assert before.status_code == 200, before.text
    return before.json()["callExempt"], (await exemption(client, *services)).json()["calls"]


async def calls_recorded(admin: httpx.AsyncClient, total: int) -> dict:
    """The dashboard's calls, once there are `total` of them."""
    async with asyncio.timeout(TIMEOUT):
        while (board := await dashboard(admin))["calls"]["total"] < total:
            await asyncio.sleep(0.05)
    return board["calls"]


async def test_with_the_llm_alone_chosen_a_call_bringing_it_counts_towards_nothing_and_is_voiced_on_the_deployers_keys(
    make_client: ClientFactory,
    settings: Settings,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    own_llm: FakeLLMService,
):
    own_llm.llm = FakeLLM(REPLY)
    deployers = FakeLLM()
    client = make_client(
        deployers, settings=replace(settings, exempting_keys=frozenset({"llm"})), clock=FakeClock()
    )
    conversation_id = await start_conversation(client)

    asked = {own: await answers(client, *own) for own in [("llm",), ("llm", "tts", "stt"), ()]}
    async with admin_beside(client, github) as admin:
        async with open_browser() as on_own_llm:
            placed = await on_own_llm.call(
                client, conversation_id=conversation_id, own_keys=own_keys("llm")
            )
            await on_own_llm.connect()
            await speech.stts[-1].hear("你好")
            turn = await on_own_llm.turn()
            await on_own_llm.hang_up()
        async with open_browser() as on_none:
            none = await on_none.call(client, conversation_id=conversation_id)
            await on_none.connect()
            await on_none.hang_up()
        async with open_browser() as again:
            over = await again.call(client, conversation_id=conversation_id)
        calls = await calls_recorded(admin, 3)

    assert asked == {
        ("llm",): (True, True), ("llm", "tts", "stt"): (True, True), (): (False, False)
    }
    assert [placed.status_code, none.status_code] == [200, 200]
    assert turn[-1]["type"] == "done"
    assert deployers.prompts == []  # she answered on the user's own LLM
    assert speech.stts[0].api_key == DEPLOYER_SONIOX_KEY
    assert speech.ttss[0].api_key == DEPLOYER_FISH_KEY
    assert over.status_code == 429  # only the call bringing nothing counted
    assert calls["own_keys"] == 1  # the call on the user's own LLM, recorded as not counted


async def test_with_speech_synthesis_alone_chosen_a_call_bringing_it_alone_counts_towards_nothing_and_one_on_the_llm_counts(
    make_client: ClientFactory, settings: Settings, speech: FakeSpeech
):
    client = make_client(
        FakeLLM(), settings=replace(settings, exempting_keys=frozenset({"tts"})), clock=FakeClock()
    )
    conversation_id = await start_conversation(client)

    asked = {own: await answers(client, *own) for own in [("tts",), ("llm",)]}
    async with open_browser() as on_own_tts:
        placed = await on_own_tts.call(
            client, conversation_id=conversation_id, own_keys=own_keys("tts")
        )
        await on_own_tts.connect()
        await on_own_tts.hang_up()
    async with open_browser() as on_own_llm:
        on_llm = await on_own_llm.call(
            client, conversation_id=conversation_id, own_keys=own_keys("llm")
        )
        await on_own_llm.connect()
        await on_own_llm.hang_up()
    async with open_browser() as again:
        over = await again.call(client, conversation_id=conversation_id, own_keys=own_keys("llm"))

    assert asked == {("tts",): (True, True), ("llm",): (False, False)}
    assert [placed.status_code, on_llm.status_code] == [200, 200]
    assert speech.ttss[0].api_key == MY_FISH
    assert speech.stts[0].api_key == DEPLOYER_SONIOX_KEY
    assert over.status_code == 429  # the call on the user's own LLM counted


async def test_a_text_turn_on_the_users_own_llm_counts_towards_nothing_whichever_are_chosen(
    make_client: ClientFactory, settings: Settings, own_llm: FakeLLMService
):
    own_llm.llm = FakeLLM(REPLY)
    client = make_client(
        FakeLLM(REPLY),
        settings=replace(settings, exempting_keys=frozenset({"tts", "stt"})),
        clock=FakeClock(),
    )
    conversation_id = await start_conversation(client)

    asked = [(await exemption(client, *own)).json()["turns"] for own in [("llm",), ()]]
    on_own_llm = await send_turn(client, conversation_id, "在吗", own_llm=LLM)
    on_deployers = await send_turn(client, conversation_id, "还在吗")
    over = await post_turn(client, conversation_id)

    assert asked == [True, False]
    assert on_own_llm[-1]["type"] == "done"
    assert on_deployers[-1]["type"] == "done"
    assert over.status_code == 429  # only the turn on the deployer's LLM counted


async def test_the_exempting_keys_saved_hold_from_the_next_call_while_one_in_progress_keeps_its_rule(
    make_client: ClientFactory,
    github: FakeIdentityProvider,
    speech: FakeSpeech,
    own_llm: FakeLLMService,
    browser: Browser,
):
    own_llm.llm = FakeLLM(REPLY)
    client = make_client(FakeLLM(), clock=FakeClock())
    conversation_id = await start_conversation(client)

    async with admin_beside(client, github) as admin:
        asked_before = await answers(client, "llm")
        await browser.call(client, conversation_id=conversation_id, own_keys=own_keys("llm"))
        await browser.connect()
        saved = await choose(admin, "llm")
        asked_after = await answers(client, "llm")
        await speech.stts[-1].hear("你好")
        turn = await browser.turn()
        await browser.hang_up()
        calls = await calls_recorded(admin, 1)
        async with open_browser() as on_none:
            refused = await on_none.call(client, conversation_id=conversation_id)
        async with open_browser() as on_own_llm:
            allowed = await on_own_llm.call(
                client, conversation_id=conversation_id, own_keys=own_keys("llm")
            )
        assert (await admin.delete(LIMITS)).status_code == 200
        asked_restored = await answers(client, "llm")

    assert saved.status_code == 200, saved.text
    assert (asked_before, asked_after, asked_restored) == ((False, False), (True, True), (False, False))
    assert turn[-1]["type"] == "done"
    assert calls["own_keys"] == 0  # begun before the change, it counted
    assert refused.status_code == 429
    assert allowed.status_code == 200, allowed.text


async def test_the_admin_console_shows_saves_and_restores_them_and_refuses_all_off_or_not_true_or_false(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    client = make_client(FakeLLM())

    async with admin_beside(client, github) as admin:
        before = (await admin.get("/api/admin/config")).json()["limits"]
        all_off = await choose(admin)
        not_a_switch = await admin.put(LIMITS, json=ENV_LIMITS | ALL_ON | {"exempting_tts": 1})
        unchanged = (await admin.get("/api/admin/config")).json()["limits"]
        saved = await choose(admin, "llm", "stt")
        account_saved = (await client.get("/api/account")).json()["exempting_keys"]
        restored = await admin.delete(LIMITS)
        account_restored = (await client.get("/api/account")).json()["exempting_keys"]

    assert before["env"] | ALL_ON == before["env"]
    assert all_off.status_code == 422
    assert "至少" in all_off.json()["detail"] and "不限" in all_off.json()["detail"]
    assert not_a_switch.status_code == 422
    assert "语音合成" in not_a_switch.json()["detail"]
    assert "true 或 false" in not_a_switch.json()["detail"]
    assert unchanged == before
    assert saved.status_code == 200, saved.text
    assert saved.json()["limits"]["override"] == {"exempting_tts": False}
    assert account_saved == ["llm", "stt"]
    assert restored.json()["limits"] == before
    assert account_restored == ["llm", "tts", "stt"]


async def test_an_override_left_with_none_on_by_env_since_falls_back_to_env_as_the_server_starts(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    async with admin_beside(make_client(FakeLLM()), github) as admin:
        assert (await choose(admin, "tts", "stt")).status_code == 200

    # `.env` has since chosen the LLM alone, which the admin console turned off.
    restarted = make_client(FakeLLM(), settings=replace(settings, exempting_keys=frozenset({"llm"})))
    await start(restarted)
    async with admin_beside(restarted, github) as admin:
        limits = (await admin.get("/api/admin/config")).json()["limits"]

    assert limits["status"] == "failed" and "至少" in limits["failure"]
    assert (await restarted.get("/api/account")).json()["exempting_keys"] == ["llm"]
    assert (await answers(restarted, "llm")) == (True, True)  # as `.env` has it


def test_env_has_them_all_on_unless_turned_off_and_refuses_all_off_or_what_it_cannot_read(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("CHAT_LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("CHAT_LLM_API_KEY", "sk-deployer")
    monkeypatch.setenv("CHAT_LLM_MODEL", "deployer-model")
    for service in ("LLM", "TTS", "STT"):
        monkeypatch.delenv(f"EIDOLON_EXEMPTING_{service}", raising=False)
    assert Settings.from_env().exempting_keys == {"llm", "tts", "stt"}

    monkeypatch.setenv("EIDOLON_EXEMPTING_LLM", "1")
    monkeypatch.setenv("EIDOLON_EXEMPTING_TTS", "0")
    monkeypatch.setenv("EIDOLON_EXEMPTING_STT", "0")
    assert Settings.from_env().exempting_keys == {"llm"}

    monkeypatch.setenv("EIDOLON_EXEMPTING_LLM", "0")
    with pytest.raises(RuntimeError, match="至少") as all_off:
        Settings.from_env()
    assert "不限" in str(all_off.value)

    monkeypatch.setenv("EIDOLON_EXEMPTING_LLM", "yes")
    with pytest.raises(RuntimeError, match="EIDOLON_EXEMPTING_LLM.*0 或 1"):
        Settings.from_env()
