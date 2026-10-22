"""Her diary (GLOSSARY.md「日记」): only days that mattered are written of, once each day is
over, one entry for each, on the chat model; how much of an entry the producer reads goes by
her tier as they read it, the page she did not mean them to see only when intimate; and what
makes a day matter — her affinity first reaching close, a liked gift, a promise kept or
broken, what the background model finds mattered to her in a turn.

The producer's time zone is UTC (`Etc/GMT`), and the clock is set to times of today's date,
as the store writes when turns were recorded at the time it is."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from eidolon.affinity import Change
from eidolon.config import Settings
from eidolon.diary import RETRY_AFTER, TRIES, split_secret
from eidolon.engine import WANTS_TO_WRITE_DIARY, diary_rules
from eidolon.llm import LLMError
from eidolon.memory import parse_moment
from eidolon.promises import NewPromise
from eidolon.reply_format import Segment
from tests.conftest import ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeLLM, FakeServiceClients, context_of, level
from tests.test_affinity import keep
from tests.test_delete_account import user_id
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
DIARY = [
    "<ja>今日は雨だった。</ja><zh>今天下雨了。</zh>",
    "<ja>[softly] プロデューサーさんと話せてよかった。</ja><zh>能和制作人说上话真好。</zh>",
    "<ja>※ずっと一緒にいたいな。</ja><zh>※想一直在一起啊。</zh>",
]
MOMENT = "真乃第一次听制作人说想一直看着她唱歌"


def today_at(hour: int, minute: int = 0, days: int = 0) -> datetime:
    now = datetime.now(UTC) + timedelta(days=days)
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


async def talked(
    make_client: ClientFactory, settings: Settings, *replies, at: datetime | None = None,
) -> tuple[httpx.AsyncClient, FakeLLM, FakeClock]:
    llm = FakeLLM(REPLY, *replies)
    clock = FakeClock(at or today_at(10))
    client = make_client(llm, clock=clock, settings=replace(settings, diary=True))
    res = await client.put("/api/profile", json={"timezone": "Etc/GMT"})
    assert res.status_code == 200, res.text
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    return client, llm, clock


def moments(database: Path) -> list[tuple[str, str]]:
    with sqlite3.connect(database) as db:
        return db.execute("SELECT kind, what FROM diary_moments ORDER BY id").fetchall()


async def test_a_day_that_mattered_is_written_of_once_it_is_over_and_once_only(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock = await talked(make_client, settings, DIARY)
    backend = backend_of(client)
    me = user_id(settings.database_path, "producer")
    backend.diary.moment(me, "mano", MOMENT, clock.now)

    clock.now = today_at(23, 50)
    await backend.diary.run_due()
    before = len(llm.prompts)
    clock.now = today_at(0, 30, days=1)
    await backend.diary.run_due()
    await backend.diary.run_due()

    assert before == 1  # the turn's only: the day is not over yet
    assert len(llm.prompts) == 2  # written once
    prompt = llm.prompts[-1]
    assert diary_rules([MOMENT]) in context_of(prompt)
    assert [(m.role, m.content) for m in prompt if m.role != "system"][-1] == (
        "user", WANTS_TO_WRITE_DIARY,
    )
    entries = (await client.get("/api/diary")).json()
    assert [(e["character_id"], e["day"]) for e in entries] == [
        ("mano", today_at(10).date().isoformat())
    ]
    assert [s["zh"] for s in entries[0]["segments"]] == ["今天下雨了。", "能和制作人说上话真好。"]
    assert "[softly]" not in entries[0]["segments"][1]["ja"]  # read, not voiced


async def test_a_day_with_nothing_that_mattered_is_not_written_of(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock = await talked(make_client, settings)
    clock.now = today_at(0, 30, days=1)

    await backend_of(client).diary.run_due()

    assert len(llm.prompts) == 1
    assert (await client.get("/api/diary")).json() == []


async def test_off_nothing_is_noted_or_written(make_client: ClientFactory, settings: Settings):
    llm = FakeLLM(REPLY)
    clock = FakeClock(today_at(10))
    client = make_client(llm, clock=clock, settings=settings)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    backend = backend_of(client)
    backend.diary.moment(user_id(settings.database_path, "producer"), "mano", MOMENT, clock.now)
    clock.now = today_at(0, 30, days=1)

    await backend.diary.run_due()

    assert moments(settings.database_path) == []
    assert len(llm.prompts) == 1


async def test_a_failed_entry_is_tried_again_later_then_let_go(
    make_client: ClientFactory, settings: Settings
):
    failing = [LLMError("挂了")] * TRIES
    client, llm, clock = await talked(make_client, settings, *failing)
    backend = backend_of(client)
    backend.diary.moment(user_id(settings.database_path, "producer"), "mano", MOMENT, clock.now)
    clock.now = today_at(0, 30, days=1)

    await backend.diary.run_due()
    await backend.diary.run_due()  # too soon to try again
    tried_once = len(llm.prompts)
    for _ in range(TRIES):
        clock.advance(seconds=RETRY_AFTER.total_seconds())
        await backend.diary.run_due()

    assert tried_once == 2
    assert len(llm.prompts) == 1 + TRIES  # let go after that
    assert (await client.get("/api/diary")).json() == []


async def written_now(
    make_client: ClientFactory, settings: Settings, *, affinity: bool = True
) -> tuple[httpx.AsyncClient, FakeClock, int]:
    llm = FakeLLM(DIARY)
    clock = FakeClock(today_at(10))
    if affinity:
        settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)
    client = make_client(llm, clock=clock, settings=replace(settings, diary=True))
    await start_conversation(client)
    res = await client.post("/api/diary/write-now", json={"character_id": "mano", "what": MOMENT})
    assert res.status_code == 201, res.text
    return client, clock, res.json()["id"]


async def shown(client: httpx.AsyncClient) -> dict:
    (entry,) = (await client.get("/api/diary")).json()
    return entry


async def test_how_much_the_producer_reads_goes_by_her_tier_as_they_read_it(
    make_client: ClientFactory, settings: Settings
):
    client, clock, _ = await written_now(make_client, settings)
    database = settings.database_path

    keep(database, "producer", 10, clock.now)
    cold = await shown(client)
    keep(database, "producer", 50, clock.now)
    trust = await shown(client)
    keep(database, "producer", 70, clock.now)
    close = await shown(client)
    keep(database, "producer", 90, clock.now)
    intimate = await shown(client)

    assert (cold["shown"], cold["segments"], cold["secret"]) == ("closed", [], [])
    assert (trust["shown"], [s["zh"] for s in trust["segments"]], trust["more"]) == (
        "opening", ["今天下雨了。"], True,
    )
    assert (close["shown"], len(close["segments"]), close["more"]) == ("full", 2, False)
    assert (close["secret"], close["secret_hidden"]) == ([], True)
    assert intimate["shown"] == "secret"
    assert intimate["secret"] == [{"ja": "ずっと一緒にいたいな。", "zh": "想一直在一起啊。"}]


async def test_where_affinity_is_off_all_of_it_but_that_page_is_read(
    make_client: ClientFactory, settings: Settings
):
    client, _, _ = await written_now(make_client, settings, affinity=False)

    entry = await shown(client)

    assert (entry["shown"], len(entry["segments"]), entry["secret"]) == ("full", 2, [])
    assert entry["secret_hidden"] is False


async def test_only_what_may_be_read_is_unread_and_opening_it_marks_it_read(
    make_client: ClientFactory, settings: Settings
):
    client, clock, entry_id = await written_now(make_client, settings)
    database = settings.database_path
    with sqlite3.connect(database) as db:  # write-now opens it; written by the schedule, it is not
        db.execute("UPDATE diary_entries SET read_at = NULL")

    keep(database, "producer", 10, clock.now)
    closed = (await client.get("/api/diary/unread")).json()
    opened_closed = (await client.post(f"/api/diary/{entry_id}/read")).json()
    keep(database, "producer", 50, clock.now)
    opening = (await client.get("/api/diary/unread")).json()
    await client.post(f"/api/diary/{entry_id}/read")

    assert closed == {"unread": 0}
    assert opened_closed["read"] is False
    assert opening == {"unread": 1}
    assert (await client.get("/api/diary/unread")).json() == {"unread": 0}
    assert (await client.post("/api/diary/999/read")).status_code == 404


async def test_another_users_diary_is_not_theirs(make_client: ClientFactory, settings: Settings):
    _, _, entry_id = await written_now(make_client, settings)
    other = make_client(FakeLLM(), "other", settings=replace(settings, diary=True))

    assert (await other.get("/api/diary")).json() == []
    assert (await other.post(f"/api/diary/{entry_id}/read")).status_code == 404


async def test_her_affinity_first_reaching_close_or_intimate_is_a_moment_once(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings)
    diary = backend_of(client).diary
    me = user_id(settings.database_path, "producer")
    closer = Change(up=True, tier_changed=True, tier="close", cooldown=False)

    diary.affinity_changed(me, "mano", closer, clock.now)
    diary.affinity_changed(me, "mano", closer, clock.now + timedelta(days=3))
    diary.affinity_changed(me, "mano", replace(closer, tier="trust"), clock.now)
    diary.affinity_changed(me, "mano", replace(closer, tier_changed=False), clock.now)

    assert moments(settings.database_path) == [("closer", "和制作人的关系第一次变成了「亲近」")]


async def test_a_liked_gift_and_promises_kept_or_broken_are_moments(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings)
    diary = backend_of(client).diary
    me = user_id(settings.database_path, "producer")

    diary.gift_given(me, "mano", "布丁", False, None, clock.now)
    diary.gift_given(me, "mano", "花束", True, None, clock.now)
    diary.promise_kept(me, "mano", "给她一块布丁", clock.now)
    diary.promise_broken(me, "mano", "带她去看海", clock.now)

    assert moments(settings.database_path) == [
        ("gift", "制作人送了自己喜欢的花束"),
        ("promise_kept", "制作人做到了答应过的事：给她一块布丁"),
        ("promise_broken", "制作人答应过的事没有做到：带她去看海"),
    ]


async def test_promises_tell_of_being_kept_and_of_her_first_complaint(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock = await talked(make_client, settings)
    backend = backend_of(client)
    promises = backend.promises
    assert promises is not None
    me = user_id(settings.database_path, "producer")
    today = clock.now.date()

    promises.record(me, "mano", [NewPromise("给她一块布丁", today)], clock.now)
    promises.record(me, "mano", [NewPromise("带她去看海", today)], clock.now)
    pudding, sea = sorted(promises.open_for(me), key=lambda p: p.text)
    promises.fulfil(me, pudding.id)
    promises.complained(sea.id, clock.now, "Etc/GMT")
    promises.complained(sea.id, clock.now + timedelta(days=1), "Etc/GMT")

    assert [kind for kind, _ in moments(settings.database_path)] == [
        "promise_kept", "promise_broken",
    ]


def test_what_mattered_in_a_turn_is_read_beside_the_memories():
    assert parse_moment('{"memories": [], "moment": "  真乃哭了  "}') == "真乃哭了"
    assert parse_moment('{"memories": [], "moment": null}') is None
    assert parse_moment('{"memories": []}') is None
    assert parse_moment("没有 JSON") is None


def test_the_page_she_did_not_mean_them_to_see_is_kept_apart():
    kept, secret = split_secret([
        Segment("今日は", "今天"), Segment(" ※内緒", "※秘密"), Segment("おやすみ", "晚安"),
    ])

    assert kept == [Segment("今日は", "今天"), Segment("おやすみ", "晚安")]
    assert secret == [Segment("内緒", "秘密")]


async def test_a_turn_that_brings_her_close_for_the_first_time_is_a_moment(
    make_client: ClientFactory, settings: Settings
):
    clients = FakeServiceClients()
    clients.judge.judgings = [level(4)]
    clock = FakeClock(today_at(10))
    settings = replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True, diary=True)
    client = make_client(FakeLLM(REPLY), clients=clients, clock=clock, settings=settings)
    conversation = await start_conversation(client)
    keep(settings.database_path, "producer", 64, clock.now)

    await send_turn(client, conversation, "你今天唱得真好")

    assert moments(settings.database_path) == [("closer", "和制作人的关系第一次变成了「亲近」")]
