"""Her own doings (GLOSSARY.md「她们的经历」): kept one character's own, once each, and
forgotten `KEPT` after nothing recalled them — with what the background model writes down of
them, read on its own from the same answer as the memories and promises."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eidolon.experiences import (
    KEPT,
    SHOWN_BEFORE_WRITING,
    SOON,
    Experiences,
    NewExperience,
    parse_experiences,
)
from eidolon import data_package
from eidolon.config import Settings
from tests.conftest import ClientFactory, database_in
from tests.fakes import FakeEmbedder, FakeJudge, FakeLLM, FakeReranker
from tests.test_delete_account import user_id
from tests.test_producer_profile import REPLY, start_conversation
from tests.test_data_package import export, unzipped
from tests.test_web_search import every_system, turns

FIRST = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
async def experiences(tmp_path: Path) -> AsyncIterator[Experiences]:
    import sqlite3

    database = tmp_path / "experiences.db"
    with sqlite3.connect(database) as db:  # 经历表的外键指向 users：造一个最小的用户表
        db.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO users (id) VALUES ('u')")
        db.execute("INSERT INTO users (id) VALUES ('别人')")
    async with database_in(database) as db:
        yield Experiences(db, lambda: FIRST)


def test_what_the_background_model_wrote_of_her_doings():
    assert parse_experiences([
        {"text": "甜花去看过那个花道展", "summary": "三月，某某美术馆", "url": "https://e/1"},
        "甜花练完舞了",
    ]) == [
        NewExperience("甜花去看过那个花道展", "三月，某某美术馆", "https://e/1"),
        NewExperience("甜花练完舞了", "", None),
    ]

    assert parse_experiences([{"text": "  "}]) == []   # 空的不要
    assert parse_experiences(None) == []
    assert parse_experiences({"text": "不是一个数组"}) == []


def test_at_most_two_are_written_a_turn():
    many = [{"text": f"第 {n} 件事"} for n in range(5)]

    assert [e.text for e in parse_experiences(many)] == ["第 0 件事", "第 1 件事"]


async def test_each_character_keeps_her_own(experiences: Experiences):
    await experiences.record("u", "tenka", [NewExperience("甜花去看过花道展")], FIRST)
    await experiences.record("u", "rinze", [NewExperience("凛世去过和服店")], FIRST)

    assert [e.text for e in await experiences.of("u", "tenka")] == ["甜花去看过花道展"]
    assert [e.text for e in await experiences.of("u", "rinze")] == ["凛世去过和服店"]  # 互相看不到


async def test_the_same_thing_is_kept_once_and_a_fuller_telling_replaces_it(experiences: Experiences):
    await experiences.record("u", "tenka", [NewExperience("甜花去看过花道展", "三月")], FIRST)
    await experiences.record(
        "u", "tenka", [NewExperience("甜花去看过花道展", "三月，在某某美术馆")], FIRST
    )

    [kept] = await experiences.of("u", "tenka")
    assert kept.summary == "三月，在某某美术馆"  # 更完整的覆盖了旧的，不是又多一条


async def test_what_was_shown_before_writing_is_her_own_newest(experiences: Experiences):
    for n in range(SHOWN_BEFORE_WRITING + 3):
        await experiences.record("u", "tenka", [NewExperience(f"第 {n} 件事")], FIRST + timedelta(minutes=n))

    shown = await experiences.recent("u", "tenka")

    assert len(shown) == SHOWN_BEFORE_WRITING
    assert shown[0].text == f"第 {SHOWN_BEFORE_WRITING + 2} 件事"  # 新的在前


async def test_kept_thirty_days_after_it_was_last_recalled_then_forgotten(experiences: Experiences):
    await experiences.record("u", "tenka", [NewExperience("甜花去看过花道展")], FIRST)
    [kept] = await experiences.of("u", "tenka")

    assert kept.left(FIRST) == KEPT
    await experiences.remember_used([kept.id])
    later = FIRST + timedelta(days=29)
    left = kept.left(later)  # 被想起过，从想起那一刻重新算
    assert left is not None and left > timedelta(0)

    await experiences.sweep(FIRST + KEPT - timedelta(minutes=1))
    assert len(await experiences.of("u", "tenka")) == 1  # 还没到三十天

    await experiences.sweep(FIRST + KEPT)
    assert await experiences.of("u", "tenka") == []  # 到了就忘掉


async def test_a_pinned_one_is_never_forgotten(experiences: Experiences):
    await experiences.record("u", "tenka", [NewExperience("甜花去看过花道展")], FIRST)
    [kept] = await experiences.of("u", "tenka")

    assert await experiences.pin("u", kept.id, True) is True
    assert (await experiences.of("u", "tenka"))[0].left(FIRST) is None  # 不计时

    await experiences.sweep(FIRST + KEPT * 3)
    assert len(await experiences.of("u", "tenka")) == 1

    assert await experiences.pin("别人", kept.id, False) is False  # 不是他的
    assert await experiences.delete("u", kept.id) is True  # 手动删，pin 的也删得掉
    assert await experiences.of("u", "tenka") == []


async def test_keeping_one_pushes_it_thirty_days_out(experiences: Experiences):
    await experiences.record("u", "tenka", [NewExperience("甜花去看过花道展")], FIRST)
    [kept] = await experiences.of("u", "tenka")

    assert await experiences.keep("u", kept.id) is True
    [again] = await experiences.of("u", "tenka")
    assert again.last_used_at == FIRST


async def test_what_is_about_to_be_forgotten_across_characters(experiences: Experiences):
    await experiences.record("u", "tenka", [NewExperience("甜花去看过花道展")], FIRST)
    await experiences.record("u", "rinze", [NewExperience("凛世去过和服店")], FIRST)
    soon = FIRST + KEPT - timedelta(days=1)

    assert await experiences.due_soon("u") == []  # 还早
    experience = (await experiences.of("u", "tenka"))[0]
    left = experience.left(soon)
    assert left is not None and left <= SOON
    assert experience.shown(soon)["due_soon"] is True
    assert experience.shown(soon)["days_left"] in (0, 1)




# ---- 进提示词 -------------------------------------------------------------


def seed(database: Path, user: str, character: str = "mano", text: str = "真乃去看过那个花道展") -> None:
    import sqlite3

    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO experiences (user_id, character_id, text, summary, created_at)"
            " VALUES (?, ?, ?, '三月，某某美术馆', ?)",
            (user, character, text, "2026-10-21T04:00:00+00:00"),
        )


async def test_a_turn_brings_up_her_own_and_counts_it_recalled(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, embedder=FakeEmbedder(), reranker=FakeReranker(prefer="花道展"),
        retrieval_judge=FakeJudge(),
    )
    seed(settings.database_path, user_id(settings.database_path, "producer"))
    conversation = await start_conversation(client, "mano")

    await turns(client, conversation, "那个花道展好看吗", web=False)

    told = every_system(llm.prompts[-1])
    assert "你自己经历过的事" in told
    assert "真乃去看过那个花道展" in told
    assert "三月，某某美术馆" in told  # 摘要跟着，回忆时有出处

    import sqlite3

    with sqlite3.connect(settings.database_path) as db:  # 被想起过，到期从这一刻重算
        [(stamp,)] = db.execute("SELECT last_used_at FROM experiences").fetchall()
    assert stamp is not None


async def test_another_characters_doings_are_not_hers(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, embedder=FakeEmbedder(), reranker=FakeReranker(prefer="花道展"),
        retrieval_judge=FakeJudge(),
    )
    seed(settings.database_path, user_id(settings.database_path, "producer"))
    conversation = await start_conversation(client, "kogane")  # 换一个人

    await turns(client, conversation, "那个花道展好看吗", web=False)

    assert "真乃去看过那个花道展" not in every_system(llm.prompts[-1])


# ---- 接口 ------------------------------------------------------------------


async def test_the_page_lists_hers_and_says_when_they_are_forgotten(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, embedder=FakeEmbedder(), reranker=FakeReranker(), retrieval_judge=FakeJudge()
    )
    user = user_id(settings.database_path, "producer")
    seed(settings.database_path, user)
    seed(settings.database_path, user, character="kogane", text="恋鐘去看过那家店")

    hers = (await client.get("/api/experiences", params={"character": "mano"})).json()
    assert [e["text"] for e in hers] == ["真乃去看过那个花道展"]
    assert hers[0]["summary"] == "三月，某某美术馆"
    assert hers[0]["days_left"] is not None and hers[0]["pinned"] is False
    assert (await client.get("/api/experiences", params={"character": "kogane"})).json()[0][
        "text"
    ] == "恋鐘去看过那家店"

    [kept] = hers
    kept_id = kept["id"]
    assert (await client.post(f"/api/experiences/{kept_id}/keep")).status_code == 200
    assert (await client.patch(f"/api/experiences/{kept_id}", json={"pinned": True})).status_code == 200
    again = (await client.get("/api/experiences", params={"character": "mano"})).json()[0]
    assert again["pinned"] is True and again["days_left"] is None  # 永久保留，不计时

    assert (await client.delete(f"/api/experiences/{kept_id}")).status_code == 204
    assert (await client.get("/api/experiences", params={"character": "mano"})).json() == []
    assert (await client.post(f"/api/experiences/{kept_id}/keep")).status_code == 404
    assert (await client.delete(f"/api/experiences/{kept_id}")).status_code == 404


# ---- 数据包 ----------------------------------------------------------------


def seed_aged(database: Path, user: str, text: str, created: str) -> None:
    import sqlite3

    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO experiences (user_id, character_id, text, summary, created_at)"
            " VALUES (?, 'mano', ?, '三月，某某美术馆', ?)",
            (user, text, created),
        )


async def test_a_package_carries_her_doings_that_are_still_kept(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, embedder=FakeEmbedder(), reranker=FakeReranker(), retrieval_judge=FakeJudge()
    )
    user = user_id(settings.database_path, "producer")
    seed_aged(settings.database_path, user, "真乃去看过那个花道展", "2026-10-21T04:00:00+00:00")
    # 四十天没想起过的：已经忘掉了，不该带走
    seed_aged(settings.database_path, user, "真乃很久以前去过海边", "2026-09-01T04:00:00+00:00")

    _, manifest, data = unzipped(await export(client, "memories"))

    assert manifest["format"] == data_package.FORMAT >= 4
    assert [e["text"] for e in data["experiences"]] == ["真乃去看过那个花道展"]
    # 字段就是文档里写的那些
    assert set(data["experiences"][0]) == {
        "character_id", "text", "summary", "url", "pinned", "used_at",
    }
    assert data["experiences"][0]["summary"] == "三月，某某美术馆"
    assert data["promises"] == []  # 约定照旧
    # 约定和经历没有自己的条数（文档里就是这么写的）
    assert "experiences" not in manifest["counts"]
    assert "promises" not in manifest["counts"]