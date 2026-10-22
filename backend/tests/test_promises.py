"""Promises (约定): the ones the background model writes down beside the memories, when she
asks about one and when she complains of one kept for a day gone by, and what takes one away —
the gift of its name, or the user saying so; and the calendar that lists them, with the asking
in her turn."""

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import httpx

from eidolon.config import Settings
from eidolon.judge import JudgeError
from eidolon.llm import LLMError
from eidolon.memory import _extraction_prompt  # pyright: ignore[reportPrivateUsage]
from eidolon.promise_judge import KEPT, read_kept
from eidolon.promises import (
    KEEP_AFTER_COMPLAINT,
    NewPromise,
    PromiseChange,
    Promises,
    parse_promise_changes,
    parse_promises,
)
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeJudge, FakeLLM, FakeServiceClients, system_of
from tests.test_affinity import keep
from tests.test_reach_outs import backend_of
from tests.test_data_package import export, import_package, unzipped
from tests.test_delete_account import user_id
from eidolon.store import Message
from tests.test_producer_profile import REPLY, system_prompt
from tests.test_retrieval_services import MANO
from tests.test_text_turn import send_turn, start_conversation

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOON = datetime(2026, 10, 21, 12, 30, tzinfo=SHANGHAI)
"""An afternoon's turn on the day the promise is for."""

ANSWER = """{
  "memories": [{"text": "制作人喜欢布丁", "supersedes": []}],
  "promises": [{"text": "给她一块布丁", "due": "2026-10-21"}]
}"""


@pytest.fixture
def promises(tmp_path: Path):
    database = tmp_path / "promises.db"
    # 约定表的外键指向 users：单测只用这张表，造一个最小的用户表就够
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        for who in ("u", "别人"):
            db.execute("INSERT INTO users (id) VALUES (?)", (who,))
    kept = Promises(database, lambda: NOON, lambda _: 0)  # 总是中午 12 点问
    yield kept
    kept.close()


def seed_promise(
    database: Path, user: str, due: str = "2026-10-21", asked_at: str | None = None
) -> None:
    """A promise for `due` made before it, asked about at noon that day — and already asked
    about, where `asked_at` says when."""
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO promises"
            " (user_id, character_id, text, due_on, made_at, ask_at, asked_at)"
            " VALUES (?, 'mano', '给她一块布丁', ?, ?, ?, ?)",
            (user, due, "2026-10-20T04:00:00+00:00", f"{due}T04:00:00+00:00", asked_at),
        )


async def asked_the_judge(clients: FakeServiceClients) -> None:
    """Wait for the promise judge to be asked about it: that runs after the turn, not in it."""
    for _ in range(200):
        if clients.judge.kept_asked:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the promise judge was never asked")


async def taken_away(client: httpx.AsyncClient) -> None:
    """Wait for the promise to be gone."""
    for _ in range(200):
        if (await client.get("/api/promises")).json() == []:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the promise was never taken away")


def test_the_promises_written_beside_the_memories():
    assert parse_promises(ANSWER) == [NewPromise("给她一块布丁", date(2026, 10, 21))]

    assert parse_promises('{"memories": []}') == []  # 没有约定的答案
    assert parse_promises('{"promises": [{"text": "带她去看海", "due": "明天"}]}') == []  # 不是具体日子
    assert parse_promises('{"promises": [{"text": "带她去看海", "due": "2026-02-30"}]}') == []
    assert parse_promises('{"promises": [{"text": "", "due": "2026-10-21"}]}') == []
    assert parse_promises("没有 JSON") == []


def test_a_promise_for_a_day_already_gone_is_dropped_and_kept_once(promises: Promises):
    promises.record(
        "u",
        "mano",
        [NewPromise("给她一块布丁", date(2026, 10, 21)), NewPromise("带她去看海", date(2026, 10, 20))],
        NOON,
    )
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)  # 又说了一次

    assert [(p.text, p.due) for p in promises.open_for("u")] == [("给她一块布丁", date(2026, 10, 21))]


def test_she_asks_about_it_once_its_minute_has_come(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)

    before = datetime(2026, 10, 21, 11, 59, tzinfo=SHANGHAI)
    assert promises.asking("u", "mano", before, "Asia/Shanghai") is None  # 上午还不问

    asked = promises.asking("u", "mano", NOON, "Asia/Shanghai")
    assert asked is not None and asked.text == "给她一块布丁"
    assert promises.asking("u", "mano", NOON, "Asia/Shanghai") is None  # 只问一次
    assert promises.awaiting("u", "mano", NOON, "Asia/Shanghai") != []  # 问过了，等他的回答


@pytest.mark.parametrize(("drawn", "hour", "minute"), [(0, 12, 0), (359, 17, 59), (719, 23, 59)])
def test_the_asking_minute_falls_between_noon_and_midnight(
    tmp_path: Path, drawn: int, hour: int, minute: int
):
    database = tmp_path / "p.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO users (id) VALUES ('u')")
    kept = Promises(database, lambda: NOON, lambda _: drawn)
    try:
        kept.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)
        just_before = datetime(2026, 10, 21, hour, minute, tzinfo=SHANGHAI) - timedelta(minutes=1)
        assert kept.asking("u", "mano", just_before, "Asia/Shanghai") is None
        assert kept.asking(
            "u", "mano", datetime(2026, 10, 21, hour, minute, tzinfo=SHANGHAI), "Asia/Shanghai"
        ) is not None
    finally:
        kept.close()


def test_she_complains_the_next_day_and_only_once_a_day(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)

    next_day = datetime(2026, 10, 22, 9, 0, tzinfo=SHANGHAI)
    first = promises.complaining("u", "mano", next_day, "Asia/Shanghai")
    assert first is not None and first.text == "给她一块布丁"
    assert promises.complaining("u", "mano", next_day, "Asia/Shanghai") is None  # 同一天不再念

    the_day_after = datetime(2026, 10, 23, 9, 0, tzinfo=SHANGHAI)
    assert promises.complaining("u", "mano", the_day_after, "Asia/Shanghai") is not None
    assert promises.complaining("u", "mano", NOON, "Asia/Shanghai") is None  # 当天不抱怨


def test_the_gift_of_its_name_takes_it_away(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)

    assert promises.by_gift("u", "mano", "蛋糕") is False  # 不是这个名字
    assert len(promises.open_for("u")) == 1

    assert promises.by_gift("u", "mano", "布丁") is True
    assert promises.open_for("u") == []


def test_saying_it_was_done_takes_it_away(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)
    [kept] = promises.open_for("u")

    assert promises.fulfil("别人", kept.id) is False  # 不是他的约定
    assert promises.fulfil("u", kept.id) is True
    assert promises.open_for("u") == []
    assert promises.fulfil("u", kept.id) is False  # 已经没了


def test_a_complaint_has_three_days_then_the_promise_is_gone(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)
    complained = datetime(2026, 10, 22, 9, 0, tzinfo=SHANGHAI)
    promises.complaining("u", "mano", complained, "Asia/Shanghai")

    promises.sweep(complained + KEEP_AFTER_COMPLAINT - timedelta(minutes=1))
    assert len(promises.open_for("u")) == 1  # 还没满三天

    promises.sweep(complained + KEEP_AFTER_COMPLAINT)
    assert promises.open_for("u") == []  # 满三天就删掉


async def test_the_calendar_lists_a_promise_and_saying_it_was_done_clears_it(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: datetime(2026, 10, 21, 4, 30, tzinfo=UTC))  # 上海 12:30
    seed_promise(settings.database_path, user_id(settings.database_path, "producer"))

    calendar = (await client.get("/api/calendar", params={"year": 2026})).json()

    assert [(p["character_id"], p["month"], p["day"], p["text"]) for p in calendar["promises"]] == [
        ("mano", 10, 21, "给她一块布丁")
    ]
    [promise] = calendar["promises"]
    assert (await client.post(f"/api/promises/{promise['id']}/fulfil")).status_code == 200
    assert (await client.get("/api/promises")).json() == []
    assert (await client.get("/api/calendar", params={"year": 2026})).json()["promises"] == []


async def test_a_turn_on_the_day_asks_about_it(make_client: ClientFactory, settings: Settings):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: datetime(2026, 10, 21, 4, 30, tzinfo=UTC))  # 上海 12:30
    seed_promise(settings.database_path, user_id(settings.database_path, "producer"))

    prompt = await system_prompt(client, llm)

    assert "制作人答应过「给她一块布丁」，就是今天。" in prompt


async def test_promises_travel_with_the_memories_in_a_data_package(
    make_client: ClientFactory, settings: Settings
):
    here = make_client(FakeLLM())
    seed_promise(settings.database_path, user_id(settings.database_path, "producer"), "2027-03-01")

    package = await export(here, "memories")
    _, manifest, data = unzipped(package)

    assert manifest["categories"] == ["memories"]
    assert data["promises"] == [
        {"character_id": "mano", "text": "给她一块布丁", "due": "2027-03-01", "from_day": None}
    ]

    there = make_client(FakeLLM(), "friend")
    res = await import_package(there, package, "memories")

    assert res.status_code == 200, res.text
    assert res.json()["promises"] == 1
    assert [p["text"] for p in (await there.get("/api/promises")).json()] == ["给她一块布丁"]


def test_the_judge_is_asked_from_the_day_itself_to_three_days_after(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)
    morning = datetime(2026, 10, 21, 9, 0, tzinfo=SHANGHAI)
    day_before = datetime(2026, 10, 20, 12, 0, tzinfo=SHANGHAI)

    assert promises.awaiting("u", "mano", morning, "Asia/Shanghai") != []  # 她还没问也问判断模型
    assert promises.awaiting("u", "mano", day_before, "Asia/Shanghai") == []  # 日子还没到
    complained = promises.complaining("u", "mano", NOON + timedelta(days=1), "Asia/Shanghai")
    assert complained is not None
    three_days = datetime(2026, 10, 24, 12, 0, tzinfo=SHANGHAI)
    assert promises.awaiting("u", "mano", three_days, "Asia/Shanghai") != []  # 抱怨过了也问
    later = datetime(2026, 10, 25, 12, 0, tzinfo=SHANGHAI)
    assert promises.awaiting("u", "mano", later, "Asia/Shanghai") == []


def test_every_promise_of_these_days_is_judged_not_only_the_first(promises: Promises):
    day = date(2026, 10, 21)
    made = [NewPromise(t, day) for t in ("给她一块布丁", "陪她去练习", "带她去看海", "给她打电话")]
    promises.record("u", "mano", made, NOON)

    judged = promises.awaiting("u", "mano", NOON, "Asia/Shanghai")

    assert [p.text for p in judged] == ["给她一块布丁", "陪她去练习", "带她去看海"]  # 最多三个


def test_a_gift_keeps_only_a_promise_to_give_it_for_no_later_than_tomorrow(promises: Promises):
    promises.record("u", "mano", [
        NewPromise("带她去书店", date(2026, 10, 21)),
        NewPromise("给她买一本书", date(2026, 10, 21)),
        NewPromise("下周送她一本书", date(2026, 10, 28)),
    ], NOON)

    assert promises.by_gift("u", "mano", "书") is True

    assert [p.text for p in promises.open_for("u")] == ["带她去书店", "下周送她一本书"]


def test_a_promise_put_off_is_asked_about_on_its_new_day_and_one_called_off_is_gone(
    promises: Promises,
):
    promises.record("u", "mano", [
        NewPromise("给她一块布丁", date(2026, 10, 21)),
        NewPromise("带她去看海", date(2026, 10, 22)),
    ], NOON)
    standing = promises.standing("u", "mano")
    answer = (
        '{"promise_changes": [{"id": 1, "due": "2026-10-24"}, {"id": 2, "cancel": true},'
        ' {"id": 9, "cancel": true}, {"id": 1, "due": "不知道"}]}'
    )

    changes = parse_promise_changes(answer, standing)
    promises.change("u", changes, NOON)

    [left] = promises.open_for("u")
    assert (left.text, left.due) == ("给她一块布丁", date(2026, 10, 24))
    assert promises.to_ask("u", "mano", NOON, "Asia/Shanghai") is None  # 不是今天了
    assert promises.to_complain("u", "mano", NOON + timedelta(days=1), "Asia/Shanghai") is None
    assert parse_promise_changes("没有 JSON", standing) == []


def test_reading_whether_the_judge_found_it_kept():
    assert read_kept({KEPT: {"noul": 0.8}}) is True  # 到 0.8 才算
    assert read_kept({KEPT: {"noul": 0.79}}) is False
    assert read_kept({KEPT: {"noul": 1.0}}) is True
    for unreadable in ({}, {KEPT: {}}, {"别的": {"noul": 1.0}}):
        with pytest.raises(JudgeError):
            read_kept(unreadable)


async def test_the_judge_takes_the_promise_away_when_he_said_he_did_it(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    clients = FakeServiceClients()
    clients.judge.kept = 1.0
    client = make_client(
        llm,
        clock=lambda: datetime(2026, 10, 21, 4, 30, tzinfo=UTC),
        clients=clients,
        retrieval_judge=FakeJudge(),  # 没有判断模型就没有这条路
    )
    seed_promise(
        settings.database_path,
        user_id(settings.database_path, "producer"),
        asked_at="2026-10-21T04:00:00+00:00",
    )

    await system_prompt(client, llm)
    await asked_the_judge(clients)
    await taken_away(client)

    assert (await client.get("/api/promises")).json() == []


async def test_the_promise_stands_when_the_judge_does_not_find_it_kept(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    clients = FakeServiceClients()
    clients.judge.kept = 0.0
    client = make_client(
        llm,
        clock=lambda: datetime(2026, 10, 21, 4, 30, tzinfo=UTC),
        clients=clients,
        retrieval_judge=FakeJudge(),
    )
    seed_promise(
        settings.database_path,
        user_id(settings.database_path, "producer"),
        asked_at="2026-10-21T04:00:00+00:00",
    )

    await system_prompt(client, llm)
    await asked_the_judge(clients)

    assert len((await client.get("/api/promises")).json()) == 1  # 还在，到了日子她会抱怨


def asked_at(database: Path) -> str | None:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT asked_at FROM promises").fetchone()[0]


def test_looking_for_what_she_would_ask_or_complain_of_changes_nothing(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)
    next_day = NOON + timedelta(days=1)

    assert promises.to_ask("u", "mano", NOON, "Asia/Shanghai") is not None
    assert promises.to_ask("u", "mano", NOON, "Asia/Shanghai") is not None
    assert promises.to_complain("u", "mano", next_day, "Asia/Shanghai") is not None
    assert promises.to_complain("u", "mano", next_day, "Asia/Shanghai") is not None


async def test_a_turn_that_fails_does_not_use_up_her_asking(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(LLMError("服务挂了"), REPLY)
    client = make_client(llm, clock=lambda: datetime(2026, 10, 21, 4, 30, tzinfo=UTC))  # 上海 12:30
    seed_promise(settings.database_path, user_id(settings.database_path, "producer"))
    conversation = await start_conversation(client)

    await send_turn(client, conversation, "在吗")
    assert asked_at(settings.database_path) is None
    await send_turn(client, conversation, "在吗")

    assert "制作人答应过「给她一块布丁」，就是今天。" in system_of(llm.prompts[-1])
    assert asked_at(settings.database_path) is not None


def test_the_background_model_is_shown_the_promises_already_kept_numbered(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 21))], NOON)

    prompt = _extraction_prompt(
        MANO, [], "布丁明天再给你", Message(1, "character", None, "うん", "嗯", NOON.isoformat()),
        [], NOON, promised=promises.standing("u", "mano"),
    )

    told = prompt[-1].content
    instructions = prompt[0].content
    assert "# 已经记着的约定\n1. 2026-10-21：给她一块布丁" in told
    assert "同一件事不要再写进 promises" in instructions and "promise_changes" in instructions


def test_a_promise_for_a_stretch_of_days_is_asked_about_on_its_last_and_kept_within_them(
    promises: Promises,
):
    [weekend] = parse_promises(
        '{"promises": [{"text": "这周末陪她去看海", "from": "2026-10-24", "due": "2026-10-25"}]}'
    )
    promises.record("u", "mano", [weekend], NOON)
    saturday = datetime(2026, 10, 24, 12, 30, tzinfo=SHANGHAI)
    sunday = datetime(2026, 10, 25, 12, 30, tzinfo=SHANGHAI)

    assert weekend == NewPromise("这周末陪她去看海", date(2026, 10, 25), date(2026, 10, 24))
    assert promises.awaiting("u", "mano", NOON, "Asia/Shanghai") == []  # not yet its days
    assert promises.awaiting("u", "mano", saturday, "Asia/Shanghai") != []
    assert promises.to_ask("u", "mano", saturday, "Asia/Shanghai") is None  # asked on the last
    assert promises.to_ask("u", "mano", sunday, "Asia/Shanghai") is not None
    assert promises.to_complain("u", "mano", sunday, "Asia/Shanghai") is None


def test_the_day_before_she_reminds_the_producer_once(promises: Promises):
    promises.record("u", "mano", [NewPromise("给她一块布丁", date(2026, 10, 22))], NOON)

    reminding = promises.to_remind("u", "mano", NOON, "Asia/Shanghai")
    assert reminding is not None and reminding.text == "给她一块布丁"
    promises.reminded(reminding.id, NOON, "Asia/Shanghai")

    assert promises.to_remind("u", "mano", NOON, "Asia/Shanghai") is None
    assert promises.to_remind("u", "mano", NOON - timedelta(days=1), "Asia/Shanghai") is None


def test_a_promise_to_call_her_is_kept_by_a_call_and_one_to_come_and_talk_by_any_turn(
    promises: Promises,
):
    day = date(2026, 10, 21)
    promises.record("u", "mano", [
        NewPromise("晚上给她打电话", day), NewPromise("来找她聊聊天", day),
        NewPromise("给她一块布丁", day),
    ], NOON)

    assert promises.by_action("u", "mano", False, NOON, "Asia/Shanghai") is True
    assert [p.text for p in promises.open_for("u")] == ["晚上给她打电话", "给她一块布丁"]
    assert promises.by_action("u", "mano", True, NOON, "Asia/Shanghai") is True
    assert [p.text for p in promises.open_for("u")] == ["给她一块布丁"]


def test_how_each_promise_ends_up_is_told(promises: Promises):
    told: list[tuple[str, str]] = []
    promises.on_settled(lambda promise, settled, _: told.append((promise.text, settled)))
    day = date(2026, 10, 21)
    promises.record("u", "mano", [
        NewPromise(t, day) for t in ("给她一块布丁", "给她一个蛋糕", "带她去看海", "带她去拉面店")
    ], NOON)
    pudding, cake, sea, ramen = promises.standing("u", "mano")
    day_before = NOON - timedelta(days=1)

    promises.fulfil("u", pudding.id)
    promises.complaining("u", "mano", NOON + timedelta(days=1), "Asia/Shanghai")
    promises.fulfil("u", cake.id)
    promises.change("u", [PromiseChange(sea.id, date(2026, 10, 30))], day_before)
    promises.change("u", [PromiseChange(sea.id, date(2026, 11, 1))], day_before)
    promises.change("u", [PromiseChange(ramen.id, None)], NOON)

    assert told == [
        ("给她一块布丁", "kept"),
        ("给她一个蛋糕", "broken"),
        ("给她一个蛋糕", "kept_late"),
        ("带她去看海", "put_off"),
        ("带她去看海", "put_off_late"),  # once more
        ("带她去拉面店", "called_off_late"),  # on the day itself
    ]


async def test_her_affinity_goes_by_how_a_promise_ends_up(
    make_client: ClientFactory, settings: Settings
):
    client = make_client(
        FakeLLM(), clock=lambda: NOON, settings=replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True),
    )
    await client.get("/api/account")
    affinities = backend_of(client).affinities
    me = user_id(settings.database_path, "producer")

    keep(settings.database_path, "producer", 50, NOON)
    affinities.promise_settled(me, "mano", "kept")
    kept = affinity_value(settings.database_path)
    keep(settings.database_path, "producer", 11, NOON)
    affinities.promise_settled(me, "mano", "broken")
    floored = affinity_value(settings.database_path)
    keep(settings.database_path, "producer", 30, NOON)
    affinities.promise_settled(me, "mano", "put_off")

    assert kept == pytest.approx(52)
    assert floored == pytest.approx(10)  # never into a cooldown
    assert affinity_value(settings.database_path) == pytest.approx(30)


def affinity_value(database: Path) -> float:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT value FROM affinities").fetchone()[0]


async def test_the_day_before_her_turn_reminds_the_producer(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, clock=lambda: datetime(2026, 10, 20, 4, 30, tzinfo=UTC))  # 上海 12:30
    seed_promise(settings.database_path, user_id(settings.database_path, "producer"))

    prompt = await system_prompt(client, llm)

    assert "制作人答应过「给她一块布丁」，明天就是那天了" in prompt
