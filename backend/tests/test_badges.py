"""Titles (GLOSSARY.md「称号」): earned once what each asks is found done, and kept; seen once
looked at; ten of them give a second encounter and draw chance a day, thirty a third."""

import sqlite3
from pathlib import Path

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeLLM
from tests.test_delete_account import user_id
from tests.test_gift_draws import draws, settings  # noqa: F401 判断模型、好感度和礼物开着
from tests.test_reach_outs import backend_of


def write(database: Path, sql: str, *args: object) -> None:
    with sqlite3.connect(database) as db:
        db.execute(sql, args)


def letter(database: Path, me: str) -> None:
    write(
        database,
        "INSERT INTO letters (user_id, character_id, occasion, ja, zh, segments, created_at)"
        " VALUES (?, 'mano', 'just', '', '', '[]', '2026-10-01T00:00:00+00:00')",
        me,
    )


def earned(shown: dict) -> dict[str, str]:
    return {b["id"]: b["name"] for b in shown["badges"] if b["earned_at"]}


async def test_earned_as_found_done_kept_after_and_seen_once_looked_at(
    make_client: ClientFactory, settings: Settings  # noqa: F811
):
    client = make_client(FakeLLM(), clock=FakeClock())
    before = (await client.get("/api/badges")).json()
    me = user_id(settings.database_path, "producer")
    letter(settings.database_path, me)
    write(settings.database_path, "UPDATE affinities SET value = 100 WHERE user_id = ? AND character_id = 'mano'", me)
    write(
        settings.database_path,
        "INSERT OR REPLACE INTO affinities (user_id, character_id, value, updated_at)"
        " VALUES (?, 'mano', 100, '2026-10-01T00:00:00+00:00')",
        me,
    )

    unseen = (await client.get("/api/badges/unseen")).json()
    write(settings.database_path, "DELETE FROM letters")
    after = (await client.get("/api/badges")).json()
    await client.post("/api/badges/seen")
    seen = (await client.get("/api/badges/unseen")).json()

    assert earned(before) == {}
    assert earned(after)["letters_1"] == "信箱有回音了"  # 信删了也还在
    assert "hers_mano" in earned(after) and "intimate_1" in earned(after)
    assert next(b for b in after["badges"] if b["id"] == "letters_10")["how"] == "收到第 10 封来信"
    assert unseen["unseen"] == len(earned(after)) and seen["unseen"] == 0
    assert after["perk"] == 1 and after["next"] == 10


async def test_ten_give_a_second_encounter_and_draw_chance_a_day_thirty_a_third(
    make_client: ClientFactory, settings: Settings  # noqa: F811
):
    clock = FakeClock()
    client = make_client(FakeLLM(), clock=clock)
    await draws(client)
    me = user_id(settings.database_path, "producer")
    outings = backend_of(client).outings
    for n in range(10):
        write(
            settings.database_path,
            "INSERT INTO badges (user_id, badge, earned_at) VALUES (?, ?, '2026-10-01')", me, f"x{n}",
        )

    clock.advance(days=1)
    ten = (await draws(client), await outings.encounters_a_day(me))
    for n in range(10, 30):
        write(
            settings.database_path,
            "INSERT INTO badges (user_id, badge, earned_at) VALUES (?, ?, '2026-10-01')", me, f"x{n}",
        )
    clock.advance(days=1)
    thirty = (await draws(client), await outings.encounters_a_day(me))

    assert ten == (1 + 2, 2)
    assert thirty == (1 + 2 + 3, 3)
