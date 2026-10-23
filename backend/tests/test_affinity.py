"""Affinity (GLOSSARY.md「好感度」): each user has one with each character, kept apart, which
starts at trust. Below the normal value it recovers as time goes by, 10 a day, up to the
normal value; above it, it never falls back. A cooldown ends once it has recovered to the
thaw line, so when it ends is known in advance. It is read by tier only, never by value,
and only where it is on: the deployment has the judge model and the switch「好感度和礼物」
is on, as `.env` has it (on by default) or the admin console overrides it. Deleting the
user deletes it. Driven through the backend's external API, with what is kept in the
database set directly and the clock moved."""

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from eidolon.config import JevSettings, Settings
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, START, FakeClock, FakeIdentityProvider, FakeLLM
from tests.test_admin_users import admin_beside
from tests.test_delete_account import user_id

AFFINITY = "/api/admin/config/affinity"
UNAVAILABLE = (
    ".env 里没有配置判断模型，好感度要靠它判断，在启动时就关掉了；"
    "要启用，请在 .env 里配置判断模型后重启"
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, and affinity on in `.env`."""
    return replace(
        settings,
        admins=frozenset({("github", "9001")}),
        judge=FAKE_JUDGE,
        affinity_and_gifts=True,
    )


def status(client: httpx.AsyncClient, character: str = "mano"):
    return client.get(f"/api/characters/{character}/affinity")


def keep(
    database: Path,
    user: str,
    value: float,
    at: datetime,
    *,
    character: str = "mano",
    cooling: bool = False,
) -> None:
    """Have the user's affinity with the character be `value` as of `at`, as a turn left it."""
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT OR REPLACE INTO affinities (user_id, character_id, value, updated_at,"
            " cooling) VALUES (?, ?, ?, ?, ?)",
            (user_id(database, user), character, value, at.isoformat(), int(cooling)),
        )


async def test_with_no_turns_yet_she_trusts_the_producer(make_client: ClientFactory):
    client = make_client(FakeLLM())

    res = await status(client)

    assert res.status_code == 200
    assert res.json() == {"tier": "trust", "cooldown_until": None}


async def test_cold_it_recovers_as_time_goes_by_up_to_distant_and_no_further(
    make_client: ClientFactory, settings: Settings
):
    """This fork (GLOSSARY.md「好感度」): cold comes back to distant on its own, no further."""
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    keep(settings.database_path, "producer", 5, START)

    tiers = [(await status(client)).json()["tier"]]
    clock.advance(hours=35)  # 19.6
    tiers.append((await status(client)).json()["tier"])
    clock.advance(hours=1)  # 20
    tiers.append((await status(client)).json()["tier"])
    clock.advance(days=20)  # 20, no higher
    tiers.append((await status(client)).json()["tier"])

    assert tiers == ["cold", "cold", "distant", "distant"]


async def test_above_the_normal_value_it_never_falls_back(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    keep(settings.database_path, "producer", 85, START)
    keep(settings.database_path, "producer", 65, START, character="kogane")

    clock.advance(days=20)

    assert (await status(client)).json()["tier"] == "intimate"
    assert (await status(client, "kogane")).json()["tier"] == "close"


async def test_a_cooldown_ends_once_it_has_recovered_to_the_thaw_line(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    keep(settings.database_path, "producer", 0, START - timedelta(hours=6), cooling=True)

    cooling = (await status(client)).json()
    clock.advance(hours=18)  # 24 hours after it began
    over = (await status(client)).json()
    keep(settings.database_path, "producer", 9.5, clock.now)  # fell again, but not to 0
    fell = (await status(client)).json()

    assert cooling == {
        "tier": "cold", "cooldown_until": (START + timedelta(hours=18)).isoformat()
    }
    assert over == {"tier": "cold", "cooldown_until": None}
    assert fell == {"tier": "cold", "cooldown_until": None}


async def test_each_character_is_apart_and_each_user(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), clock=FakeClock())
    other = make_client(FakeLLM(), "other", clock=FakeClock())
    keep(settings.database_path, "producer", 30, START)

    assert (await status(client)).json()["tier"] == "distant"
    assert (await status(client, "kogane")).json()["tier"] == "trust"
    assert (await status(other)).json()["tier"] == "trust"


async def test_reading_it_needs_a_signed_in_user_and_a_character(make_client: ClientFactory):
    client = make_client(FakeLLM())
    nobody = make_client(FakeLLM(), None)

    assert (await status(client, "nobody")).status_code == 404
    assert (await status(nobody)).status_code == 401


@pytest.mark.parametrize("judge", [FAKE_JUDGE, None])
async def test_unset_in_env_it_is_on_with_the_judge_model_and_off_without(
    make_client: ClientFactory,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    judge: JevSettings | None,
):
    for name in ("EIDOLON_AFFINITY_AND_GIFTS", "JUDGE_PROVIDER", "JEV_API_KEY", "JEV_MODEL",
                 "JEV_BASE_URL", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAT_LLM_BASE_URL", "https://llm.example.com/v1")
    monkeypatch.setenv("CHAT_LLM_API_KEY", "sk-deployer")
    monkeypatch.setenv("CHAT_LLM_MODEL", "deployer-model")
    if judge is not None:
        monkeypatch.setenv("JEV_API_KEY", judge.api_key)
    env = Settings.from_env()
    client = make_client(
        FakeLLM(),
        settings=replace(settings, judge=env.judge, affinity_and_gifts=env.affinity_and_gifts),
    )

    affinity = (await status(client)).json()
    gifts = (await client.get("/api/gifts")).json()

    if judge is None:
        assert (affinity, gifts["open"]) == (None, False)
    else:
        assert (affinity, gifts["open"]) == ({"tier": "trust", "cooldown_until": None}, True)


async def test_off_in_env_there_is_none_until_the_admin_turns_it_on(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = make_client(
        FakeLLM(), settings=replace(settings, affinity_and_gifts=False), clock=clock
    )
    keep(settings.database_path, "producer", 0, START, cooling=True)

    async with admin_beside(client, github) as admin:
        off = await status(client)
        described = (await admin.get("/api/admin/config")).json()["affinity"]
        turned_on = await admin.put(AFFINITY, json={"enabled": True})
        on = await status(client)
        clock.advance(days=2)  # off meanwhile, it recovered all the same
        await admin.put(AFFINITY, json={"enabled": False})
        off_again = await status(client)
        await admin.delete(AFFINITY)
        restored = await status(client)
        assert (await admin.put(AFFINITY, json={"enabled": True})).status_code == 200
        recovered = await status(client)
        refused = await admin.put(AFFINITY, json={"enabled": "yes"})

    assert off.status_code == 200
    assert off.json() is None
    assert described == {
        "status": "env", "env": {"enabled": False}, "effective": {"enabled": False},
        "override": None, "failure": None,
    }
    assert turned_on.json()["affinity"]["override"] == {"enabled": True}
    assert on.json() == {
        "tier": "cold", "cooldown_until": (START + timedelta(days=1)).isoformat()
    }
    assert off_again.json() is None
    assert restored.json() is None
    assert recovered.json() == {"tier": "distant", "cooldown_until": None}
    assert refused.status_code == 422


async def test_without_the_judge_model_there_is_none_and_it_cannot_be_turned_on(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    client = make_client(FakeLLM(), settings=replace(settings, judge=None))

    async with admin_beside(client, github) as admin:
        described = (await admin.get("/api/admin/config")).json()["affinity"]
        refused = await admin.put(AFFINITY, json={"enabled": True})

    assert (await status(client)).json() is None
    assert described == {"unavailable": UNAVAILABLE}
    assert refused.status_code == 409
    assert refused.json()["detail"] == UNAVAILABLE


async def test_deleting_the_user_deletes_it_and_deleting_memories_does_not(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(FakeLLM(), clock=FakeClock())
    keep(settings.database_path, "producer", 30, START)

    assert (await client.delete("/api/memories")).status_code == 204
    kept = (await status(client)).json()["tier"]
    assert (await client.post("/api/account/delete", json={"confirm": True})).status_code == 204

    assert kept == "distant"
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM affinities").fetchone() == (0,)
