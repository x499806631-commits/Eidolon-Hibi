"""Reach-outs (GLOSSARY.md「搭话」): what she says first in her current conversation with the
user, the user not talking to her. The deployer turns them on; then, at every tick, each
current conversation the user has had a turn in, last in touch with her within a week, has
one once its time comes: the last contact (her last turn recorded, or a call that connected
ending) and a time drawn from her tier's range, intimate 3–6 hours, close 8–16, trust 20–36
(trust where affinity is off); none in cold or distant, a cooldown, a conversation she has
ended, or after one since the last contact. Generated on the deployer's chat model as text
turns use it, whoever's LLM the user brings, laid out as a text turn's prompt is; recorded in
the conversation as she would reply, in a group of its own, and the conversation list's last
line; with an activity record of its own, which no count of turns or calls takes in.

Her time goes by the producer's day, in the profile's time zone: from the night (22:00–08:00)
to the morning after, 08:00–10:00; no more than the deployer's number a day for each user,
the earliest first, the rest to the next morning; never past a week after the last contact.
What was missed as the backend was down, or failed, is not made up for, but put off by a
time from her tier's range. On a special day (the producer's birthday, her own, or a holiday
of one day, from the calendar), before they are in touch that day, she comes that morning,
sooner than her range. None for a user banned, or let take no turns on the deployer's
keys; none while a text turn is under way or a call; and none recorded that the user spoke
as she was thinking it, its record refused.

Driven through the backend's external API, with the reach-outs' ticks taken by hand
(`ReachOuts.run_due`), the clock moved, chance fixed (`FakeRandom`), and when the last
contact was set in the database directly, as the store writes the time as it is. On the
clock as it is, the producer is in a time zone where it is the afternoon (`afternoon`), so
that what goes by her day comes alike whenever the tests run."""

import asyncio
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from eidolon.assembly import Backend
from eidolon.config import Settings
from eidolon.engine import REACH_OUT_RULES, WANTS_TO_REACH_OUT
from eidolon.llm import LLMError
from tests.browser import Browser
from eidolon.special_days import CALENDAR_FILE
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import (
    DEPLOYER_KEY,
    FAKE_JUDGE,
    FakeBackgroundLLM,
    FakeClock,
    FakeEmbedder,
    FakeJudge,
    FakeIdentityProvider,
    FakeImageService,
    FakeLLM,
    FakeLLMService,
    FakeRandom,
    FakeReranker,
    FakeServiceClients,
    context_of,
    system_of,
)
from tests.test_activity_records import dashboard, health
from tests.test_admin_users import ADMIN, admin_beside, details, find
from tests.test_affinity import keep
from tests.test_affinity_prompt import FONDNESS
from tests.test_conversation_titles import TITLE, listed_titles, naming
from tests.test_delete_account import DELETE
from tests.test_own_llm import LLM as OWN_LLM
from tests.test_photos import IMAGE, SELFIE, WORDS
from tests.test_stats import stats
from tests.test_text_turn import send_turn, start_conversation
from tests.test_user_limits import set_limits

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
REACH_OUT = ["<ja>[happily] プロデューサーさん！</ja><zh>制作人！</zh><ja>レッスン終わったよ</ja><zh>练习结束了哦</zh>"]
REACH_OUTS = "/api/admin/config/reach_outs"
MIDDLE = 500
"""Where in her tier's range the time is drawn: halfway (`reach_outs.PLACES`)."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With reach-outs on in `.env`."""
    return replace(settings, reach_outs=True)


def backend_of(client: httpx.AsyncClient) -> Backend:
    return client._transport.app.state.backend  # pyright: ignore[reportAttributeAccessIssue]


def last_contact(database: Path, conversation: str, at: datetime) -> None:
    """Have the conversation's turns have been recorded at `at`, the last contact."""
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE messages SET created_at = ? WHERE conversation_id = ?",
            (at.isoformat(), conversation),
        )


async def record(client: httpx.AsyncClient, conversation: str) -> list[dict]:
    res = await client.get(f"/api/conversations/{conversation}/messages")
    assert res.status_code == 200
    return res.json()


def reach_outs(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("type") == "reach_out"]


def afternoon(now: datetime) -> str:
    """A time zone it is one in the afternoon in at `now`, give or take an hour, so that her
    time, within hours of it, falls in the producer's day."""
    offset = (13 - now.hour + 12) % 24 - 12
    return f"Etc/GMT{-offset:+d}" if offset else "Etc/GMT"


async def live_in(client: httpx.AsyncClient, timezone: str) -> None:
    res = await client.put("/api/profile", json={"timezone": timezone})
    assert res.status_code == 200, res.text


async def talked(
    make_client: ClientFactory, *replies: Sequence[str | asyncio.Event] | LLMError,
    at: datetime | None = None, **services,
) -> tuple[httpx.AsyncClient, FakeLLM, FakeClock, str]:
    """A client whose user has had a turn with Mano, her chat model replying with `REPLY`
    and then `replies`, on a clock of its own, set to `at`; else to the time it is as the
    store writes it, so that what the store writes and what is set as the last contact come
    in their order, the producer living where it is the afternoon."""
    llm = FakeLLM(REPLY, *replies)
    clock = FakeClock(at or datetime.now(UTC))
    client = make_client(llm, clock=clock, **services)
    if at is None:
        await live_in(client, afternoon(clock.now))
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    return client, llm, clock, conversation


async def test_once_her_time_comes_she_says_something_first_the_lists_last_line_till_a_turn(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT, REPLY, random=FakeRandom(MIDDLE)
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=28))

    await backend_of(client).reach_outs.run_due()

    [said] = reach_outs(await record(client, conversation))
    assert said == {
        "type": "reach_out",
        "id": said["id"],
        "ja": "プロデューサーさん！レッスン終わったよ",
        "zh": "制作人！练习结束了哦",
        "segments": [
            {"ja": "プロデューサーさん！", "zh": "制作人！"},
            {"ja": "レッスン終わったよ", "zh": "练习结束了哦"},
        ],
        "created_at": said["created_at"],
    }
    shown = {
        "role": "character", "text": None, "ja": "プロデューサーさん！レッスン終わったよ",
        "zh": "制作人！练习结束了哦", "created_at": said["created_at"], "reach_out": True,
    }
    [latest] = (await client.get("/api/conversations/latest")).json()
    [listed] = (await client.get("/api/conversations", params={"character_id": "mano"})).json()
    assert latest["last_message"] == shown
    assert listed["last_message"] == shown

    await send_turn(client, conversation, "辛苦了")

    [latest] = (await client.get("/api/conversations/latest")).json()
    assert "reach_out" not in latest["last_message"]
    assert (await record(client, conversation))[-3]["type"] == "reach_out"


async def test_the_closer_she_is_the_sooner_she_comes(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT,
        settings=replace(settings, judge=FAKE_JUDGE), random=FakeRandom(MIDDLE),
    )
    keep(settings.database_path, "producer", 90, clock.now)  # intimate: 3–6 hours, halfway
    last_contact(settings.database_path, conversation, clock.now)
    reach_out = backend_of(client).reach_outs

    clock.advance(hours=4, minutes=29)
    await reach_out.run_due()
    before = reach_outs(await record(client, conversation))
    clock.advance(minutes=1)
    await reach_out.run_due()
    after = reach_outs(await record(client, conversation))

    assert (len(before), len(after)) == (0, 1)


async def test_where_affinity_is_off_she_comes_as_in_trust(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT,
        settings=replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=False),
        random=FakeRandom(0),
    )
    keep(settings.database_path, "producer", 90, clock.now)  # kept, but off
    last_contact(settings.database_path, conversation, clock.now)
    reach_out = backend_of(client).reach_outs

    clock.advance(hours=19, minutes=59)
    await reach_out.run_due()
    before = reach_outs(await record(client, conversation))
    clock.advance(minutes=1)
    await reach_out.run_due()

    assert (len(before), len(reach_outs(await record(client, conversation)))) == (0, 1)


def write_event(database: Path, conversation: str, kind: str, at: datetime) -> None:
    """Record a conversation event at `at`, as the store would: a gift of cake, if a gift."""
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO conversation_events (conversation_id, kind, created_at, gift_id)"
            " VALUES (?, ?, ?, ?)",
            (conversation, kind, at.isoformat(), "cake" if kind == "gift" else None),
        )


def connected_call(database: Path, conversation: str, at: datetime, seconds: float) -> None:
    """Keep a call in the conversation that connected at `at` and lasted `seconds`."""
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO calls (conversation_id, connected_at, duration_s) VALUES (?, ?, ?)",
            (conversation, at.isoformat(), seconds),
        )


async def test_she_waits_for_the_user_to_answer_before_she_comes_again(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock, conversation = await talked(
        make_client, REACH_OUT, REPLY, REACH_OUT, REACH_OUT,
        random=FakeRandom(0, 0, 0),
    )
    database = settings.database_path
    last_contact(database, conversation, clock.now - timedelta(hours=20))
    reach_out = backend_of(client).reach_outs
    await reach_out.run_due()

    clock.advance(hours=30)  # unanswered, and a gift is no answer
    write_event(database, conversation, "gift", clock.now)
    await reach_out.run_due()
    unanswered = len(reach_outs(await record(client, conversation)))

    await send_turn(client, conversation, "我回来了")  # answered by a turn
    last_contact(database, conversation, clock.now - timedelta(hours=20))
    await reach_out.run_due()
    after_a_turn = len(reach_outs(await record(client, conversation)))

    connected_call(database, conversation, clock.now - timedelta(hours=21), 3600)  # by a call
    await reach_out.run_due()
    after_a_call = len(reach_outs(await record(client, conversation)))

    assert (unanswered, after_a_turn, after_a_call) == (1, 2, 3)
    assert len(llm.prompts) == 5  # the turns' two, and three reach-outs


async def test_she_reaches_out_only_in_her_current_conversation_once_it_has_a_turn(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock, conversation = await talked(make_client, REACH_OUT, random=FakeRandom(0))
    last_contact(settings.database_path, conversation, clock.now - timedelta(days=2))
    newer = await start_conversation(client)  # begun since, still without a turn
    connected_call(settings.database_path, newer, clock.now - timedelta(days=2), 600)

    await backend_of(client).reach_outs.run_due()

    assert reach_outs(await record(client, conversation)) == []
    assert reach_outs(await record(client, newer)) == []


@pytest.mark.parametrize("why", ["off", "ended", "a week ago", "cold", "cooldown"])
async def test_she_does_not_reach_out(make_client: ClientFactory, settings: Settings, why: str):
    on = replace(settings, judge=FAKE_JUDGE, reach_outs=why != "off")
    client, _, clock, conversation = await talked(make_client, settings=on, random=FakeRandom())
    database = settings.database_path
    ago = timedelta(days=7, minutes=1) if why == "a week ago" else timedelta(days=2)
    last_contact(database, conversation, clock.now - ago)
    if why == "ended":
        write_event(database, conversation, "ended", clock.now - ago)
    if why in ("cold", "cooldown"):
        keep(database, "producer", {"cold": 15, "cooldown": 5}[why], clock.now,
             cooling=why == "cooldown")

    await backend_of(client).reach_outs.run_due()

    assert reach_outs(await record(client, conversation)) == []


async def test_her_tier_now_is_what_she_goes_by(make_client: ClientFactory, settings: Settings):
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT,
        settings=replace(settings, judge=FAKE_JUDGE), random=FakeRandom(0),
    )
    database = settings.database_path
    last_contact(database, conversation, clock.now - timedelta(hours=20))
    keep(database, "producer", 39, clock.now)  # distant: no
    reach_out = backend_of(client).reach_outs
    await reach_out.run_due()
    distant = reach_outs(await record(client, conversation))

    clock.advance(hours=3)  # recovered to trust, 20 hours and more since the last contact
    await reach_out.run_due()

    assert (len(distant), len(reach_outs(await record(client, conversation)))) == (0, 1)


async def test_she_is_asked_as_in_a_text_turn_but_told_they_are_not_talking(
    make_client: ClientFactory, settings: Settings
):
    client, llm, clock, conversation = await talked(
        make_client, REPLY, REACH_OUT,
        settings=replace(settings, judge=FAKE_JUDGE), random=FakeRandom(0),
    )
    database = settings.database_path
    keep(database, "producer", 90, clock.now)
    await send_turn(client, conversation, "今天好累")
    last_contact(database, conversation, clock.now - timedelta(hours=3))
    write_event(database, conversation, "gift", clock.now - timedelta(hours=1))

    await backend_of(client).reach_outs.run_due()

    turn, reaching_out = llm.prompts[-2], llm.prompts[-1]
    assert system_of(reaching_out) == system_of(turn)
    assert [(m.role, m.content) for m in reaching_out if m.role != "system"] == [
        ("user", "我去练习了"), ("assistant", "<ja>はい</ja><zh>好的</zh>"),
        ("user", "今天好累"), ("assistant", "<ja>はい</ja><zh>好的</zh>"),
        ("user", f"（制作人送给你一份蛋糕。）\n{WANTS_TO_REACH_OUT}"),
    ]
    told = context_of(reaching_out)
    assert "你们这会儿没在说话" in told and "正在用文字和你聊天" not in told
    assert "现在是" in told and "非常信赖" in told and FONDNESS["intimate"] in told
    assert REACH_OUT_RULES in told
    assert (llm.tools[-1], llm.forbidden[-1]) == ([], False)


async def test_she_knows_when_they_last_talked_and_how_long_ago(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, REACH_OUT)
    clock = FakeClock(datetime(2026, 10, 19, 9, tzinfo=UTC))  # 17:00 in Shanghai
    client = make_client(llm, clock=clock, random=FakeRandom(0))
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    last_contact(settings.database_path, conversation, datetime(2026, 10, 18, 13, tzinfo=UTC))
    clock.advance(hours=3)

    await backend_of(client).reach_outs.run_due()

    assert len(reach_outs(await record(client, conversation))) == 1
    assert "上次和制作人说话是 10月18日 21:00，隔了 23 小时。" in context_of(llm.prompts[-1])


async def test_on_his_birthday_she_wishes_him_happy_birthday_once(
    make_client: ClientFactory, settings: Settings
):
    llm = FakeLLM(REPLY, REACH_OUT)
    clock = FakeClock(datetime(2026, 10, 19, 9, tzinfo=UTC))  # 17:00 in Shanghai
    client = make_client(llm, clock=clock, random=FakeRandom(0))
    await client.put("/api/profile", json={"birthday": {"month": 10, "day": 19}})
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "我去练习了")
    last_contact(settings.database_path, conversation, datetime(2026, 10, 18, 13, tzinfo=UTC))
    clock.advance(hours=3)

    await backend_of(client).reach_outs.run_due()

    [birthday] = [
        line for line in context_of(llm.prompts[-1]).splitlines() if "今天是制作人的生日" in line
    ]
    assert "祝福一次" in birthday and "回应制作人说的话" not in birthday


def remember(
    database: Path, text: str, at: datetime, told: str = "mano", superseded_by: int | None = None
) -> int:
    """Have the producer have told `told` `text` at `at`, as a turn writes it down: its id."""
    with sqlite3.connect(database) as db:
        cursor = db.execute(
            "INSERT INTO memories (user_id, source_character_id, text, created_at, superseded_by)"
            " VALUES ((SELECT id FROM users WHERE name = 'producer'), ?, ?, ?, ?)",
            (told, text, at.isoformat(), superseded_by),
        )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


async def test_she_finds_what_he_told_her_of_their_last_turn_and_his_last_two_weeks(
    make_client: ClientFactory, settings: Settings
):
    """Only what he told her herself, and still holds: what is relevant to their last turn,
    as a turn finds it, and what he told her lately, each once."""
    embedder, reranker = FakeEmbedder(), FakeReranker("猫", others=0.0)
    client, llm, clock, conversation = await talked(
        make_client, ["<ja>猫ちゃん？</ja><zh>小猫怎么了？</zh>"], REACH_OUT,
        retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=reranker,
        random=FakeRandom(0),
    )
    await send_turn(client, conversation, "我家的猫又捣乱了")
    database, now = settings.database_path, clock.now
    remember(database, "制作人家里养了一只叫小白的猫", now - timedelta(days=40))
    remember(database, "制作人家的猫怕打雷", now - timedelta(days=40), told="kogane")
    remember(database, "制作人10月3日去北海道出差", now - timedelta(days=15))
    exam = remember(database, "制作人10月25日要考资格证", now - timedelta(days=3))
    remember(database, "制作人10月20日要考资格证", now - timedelta(days=5), superseded_by=exam)
    remember(database, "制作人最近在学吉他", now - timedelta(days=1), told="kogane")
    remember(database, "制作人的猫这两天感冒了", now - timedelta(days=2))
    last_contact(database, conversation, now - timedelta(hours=20))

    await backend_of(client).reach_outs.run_due()

    assert len(reach_outs(await record(client, conversation))) == 1
    told = context_of(llm.prompts[-1])
    found = [line for line in told.splitlines() if line.startswith("- 制作人")]
    assert sorted(line.split("（")[0] for line in found) == sorted([
        "- 制作人家里养了一只叫小白的猫",
        "- 制作人的猫这两天感冒了",
        "- 制作人10月25日要考资格证",
    ])
    assert all(line.endswith("制作人对你说的）") for line in found)
    assert told.index("# 关于制作人的记忆") < told.index(REACH_OUT_RULES)
    assert reranker.calls[-1][0] == "我家的猫又捣乱了\n小猫怎么了？"


async def test_she_reaches_out_on_the_deployers_chat_model_whatever_the_user_brings(
    make_client: ClientFactory, settings: Settings, own_llm: FakeLLMService
):
    llm = FakeLLM(REACH_OUT)
    own_llm.llm = FakeLLM(REPLY)
    clients = FakeServiceClients(llm)
    client = make_client(llm, clock=(clock := FakeClock(datetime.now(UTC))),
                         clients=clients, random=FakeRandom(0))
    await live_in(client, afternoon(clock.now))
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "在吗", own_llm=OWN_LLM)
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))

    await backend_of(client).reach_outs.run_due()

    assert llm.keys == [DEPLOYER_KEY]
    assert clients.efforts == [settings.chat_llm.reasoning_effort]  # as text turns, not calls
    assert len(reach_outs(await record(client, conversation))) == 1


def records(database: Path) -> list[tuple[str, str, str, int]]:
    """Every activity record, oldest first: its kind, character, result and whether on the
    user's own keys."""
    with sqlite3.connect(database) as db:
        return db.execute(
            "SELECT kind, character_id, result, own_keys FROM activity_records ORDER BY id"
        ).fetchall()


async def test_each_has_an_activity_record_of_its_own_which_no_turn_or_call_counts_take_in(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    settings = replace(settings, admins=frozenset({ADMIN}))
    llm = FakeLLM(REPLY, REPLY, REACH_OUT, LLMError("模型返回 503: overloaded"), tokens=(1000, 200))
    client = make_client(
        llm, settings=settings, clock=(clock := FakeClock(datetime.now(UTC))),
        random=FakeRandom(0, 0),
    )
    database = settings.database_path
    await live_in(client, afternoon(clock.now + timedelta(hours=25)))
    mano, kogane = await start_conversation(client), await start_conversation(client, "kogane")
    for conversation in (mano, kogane):
        await send_turn(client, conversation, "在吗")
    clock.advance(hours=25)  # the turns out of the dashboard's last 24 hours
    for conversation in (mano, kogane):
        last_contact(database, conversation, clock.now - timedelta(hours=20))

    await backend_of(client).reach_outs.run_due()

    assert records(database)[2:] == [
        ("reach_out", "mano", "success", 0), ("reach_out", "kogane", "failed", 0),
    ]
    assert len(reach_outs(await record(client, mano))) == 1
    assert reach_outs(await record(client, kogane)) == []
    async with admin_beside(client, github) as admin:
        board = await dashboard(admin)
        week = await dashboard(admin, "7d")
        user = await find(admin, "producer")
        shown = await details(admin, user["id"])
    assert (board["turns"]["total"], board["calls"]["total"], board["active_users"]) == (0, 0, 0)
    assert sum(point["turns"] + point["calls"] + point["active_users"]
               for point in board["trend"]) == 0
    assert board["characters"] == []
    assert {(c["character_id"], c["turns"], c["calls"]) for c in week["characters"]} == {
        ("mano", 1, 0), ("kogane", 1, 0),
    }
    chat = health(board, "chat_llm")
    assert chat is not None and (chat["calls"], chat["errors"]) == (2, 1)
    [cost] = [r for r in board["cost"]["rows"] if r["service"] == "chat_llm"]
    assert (cost["provider"], cost["calls"], cost["input"], cost["output"]) == (
        "llm.invalid", 2, 1000, 200,
    )
    assert user["usage_24h"] == {"turns": 0, "calls": 0}
    assert shown["usage"]["7d"]["turns"] == {"deployer": 2, "own_keys": 0}
    assert sum(sum(counted.values()) for counted in shown["usage"]["7d"].values()) == 2


async def test_one_that_fails_is_put_off_by_a_time_from_her_range_and_counts_for_no_day(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    """She could say nothing; not tried again until a time drawn anew from her tier's range
    has passed, the day's one reach-out still hers."""
    on = replace(settings, judge=FAKE_JUDGE, admins=frozenset({ADMIN}))
    client, llm, clock, conversation = await talked(
        make_client, ["<photo>selfie</photo>"], REACH_OUT, settings=on,
        at=datetime(2026, 10, 19, 2, tzinfo=UTC),  # 10:00 in Shanghai
        random=FakeRandom(0, 0),
    )
    database = settings.database_path
    await allow_a_day(client, github, 1)
    keep(database, "producer", 90, clock.now)  # intimate: 3–6 hours
    last_contact(database, conversation, clock.now - timedelta(hours=3))
    reach_out = backend_of(client).reach_outs

    await reach_out.run_due()  # fails
    clock.advance(hours=2, minutes=59)
    await reach_out.run_due()
    asked = len(llm.prompts)
    clock.advance(minutes=1)
    await reach_out.run_due()

    assert asked == 2
    assert len(reach_outs(await record(client, conversation))) == 1
    assert [r for r in records(database) if r[0] == "reach_out"] == [
        ("reach_out", "mano", "failed", 0), ("reach_out", "mano", "success", 0),
    ]


async def test_she_sends_no_photo_her_tag_dropped_and_her_words_recorded(
    make_client: ClientFactory, settings: Settings
):
    image = FakeImageService()
    client, _, clock, conversation = await talked(
        make_client, [f"<photo>{SELFIE}</photo>", WORDS],
        settings=replace(settings, image=IMAGE, judge=FAKE_JUDGE, affinity_and_gifts=False),
        image_transport=image.transport(), random=FakeRandom(0),
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))

    await backend_of(client).reach_outs.run_due()

    [said] = reach_outs(await record(client, conversation))
    assert (said["ja"], said["zh"]) == ("はい、どうぞ", "给，你看")
    assert "photo" not in said
    assert image.requests == []


async def test_titles_and_stats_take_no_account_of_her_reach_outs(
    make_client: ClientFactory, settings: Settings
):
    background = FakeBackgroundLLM(TITLE)
    client, _, clock, conversation = await talked(
        make_client, REPLY, REACH_OUT, REPLY, background_llm=background, random=FakeRandom(0)
    )
    await send_turn(client, conversation, "第二句")
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))
    before = await stats(client)
    await backend_of(client).reach_outs.run_due()
    after = await stats(client)

    await send_turn(client, conversation, "第三句")

    assert await listed_titles(client) == [TITLE]
    [prompt] = naming(background.prompts)
    assert "练习结束了哦" not in prompt and "第三句" in prompt
    assert after == before


async def test_off_by_default_the_admin_turns_them_on_with_how_many_a_day(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    off = replace(settings, reach_outs=False, admins=frozenset({ADMIN}))
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT, settings=off, random=FakeRandom(0)
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))
    reach_out = backend_of(client).reach_outs

    async with admin_beside(client, github) as admin:
        described = (await admin.get("/api/admin/config")).json()["reach_outs"]
        await reach_out.run_due()
        before = reach_outs(await record(client, conversation))
        refused = [
            await admin.put(REACH_OUTS, json=body)
            for body in (
                {"enabled": "yes", "per_day": 3}, {"enabled": True, "per_day": 0},
                {"enabled": True, "per_day": True}, {"enabled": True, "per_day": 1.5},
                {"enabled": True},
            )
        ]
        turned_on = await admin.put(REACH_OUTS, json={"enabled": True, "per_day": 5})
        await reach_out.run_due()

    assert described == {
        "status": "env", "env": {"enabled": False, "per_day": 3},
        "effective": {"enabled": False, "per_day": 3}, "override": None, "failure": None,
    }
    assert before == []
    assert [r.status_code for r in refused] == [422] * 5
    assert turned_on.status_code == 200
    assert turned_on.json()["reach_outs"]["effective"] == {"enabled": True, "per_day": 5}
    assert len(reach_outs(await record(client, conversation))) == 1


async def test_deleting_the_user_deletes_them_when_she_was_to_come_and_their_switch(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock, conversation = await talked(make_client, REACH_OUT, random=FakeRandom(0))
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))
    await backend_of(client).reach_outs.run_due()
    switched_off = await client.put("/api/account/reach-outs", json={"on": False})

    assert switched_off.status_code == 204
    assert (await client.post("/api/account/delete", json=DELETE)).status_code == 204

    with sqlite3.connect(settings.database_path) as db:
        left = [db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("reach_outs", "reach_out_schedules", "reach_outs_off")]
    assert left == [0, 0, 0]


SPECIAL = {
    "his-birthday": None,
    "her-birthday": '[[birthdays]]\ncharacter = "mano"\ndate = "10-20"\n',
    "a-holiday": '[[holidays]]\nname = "测试节"\ndate = "10-20"\n',
    "another-idols-birthday": '[[birthdays]]\ncharacter = "kogane"\ndate = "10-20"\n',
    "a-season": '[[holidays]]\nname = "测试季"\ndate = "10-15"\nend = "10-25"\n',
}


@pytest.mark.parametrize(
    ("day", "talked_today", "comes", "told"),
    [
        ("his-birthday", False, True, "今天是制作人的生日"),
        ("her-birthday", False, True, "今天是你自己的生日"),
        ("a-holiday", False, True, "今天是测试节"),
        ("his-birthday", True, False, None),
        ("another-idols-birthday", False, False, None),
        ("a-season", False, False, None),
    ],
    ids=["his-birthday", "her-birthday", "a-holiday", "talked-today", "another-idols-birthday", "a-season"],
)
async def test_on_a_special_day_she_comes_that_morning_sooner_than_her_range(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig,
    day: str, talked_today: bool, comes: bool, told: str | None,
):
    """In Shanghai, last in touch at 21:00 the evening before (or at 00:30 that day), her time
    from the range 28 hours on; brought forward to 8:00, the start of the morning."""
    if (calendar := SPECIAL[day]) is not None:
        characters.write(CALENDAR_FILE, calendar)
    client, llm, clock, conversation = await talked(
        make_client, REACH_OUT,
        at=datetime(2026, 10, 19, 13, tzinfo=UTC),  # 21:00 on the 19th in Shanghai
        random=FakeRandom(MIDDLE, 0),
    )
    if day == "his-birthday":
        await client.put("/api/profile", json={"birthday": {"month": 10, "day": 20}})
    contact = datetime(2026, 10, 19, 16, 30, tzinfo=UTC) if talked_today else clock.now
    last_contact(settings.database_path, conversation, contact)
    reach_out = backend_of(client).reach_outs

    clock.now = datetime(2026, 10, 19, 23, 50, tzinfo=UTC)  # 7:50 on the 20th: still night
    await reach_out.run_due()
    at_night = len(reach_outs(await record(client, conversation)))
    clock.now = datetime(2026, 10, 20, 0, 30, tzinfo=UTC)  # 8:30
    await reach_out.run_due()
    in_the_morning = len(reach_outs(await record(client, conversation)))

    assert (at_night, in_the_morning) == (0, 1 if comes else 0)
    if told is not None:
        assert told in context_of(llm.prompts[-1])


async def test_brought_forward_to_a_special_day_she_comes_however_late_the_backend_starts(
    make_client: ClientFactory, settings: Settings, characters: CharacterConfig,
):
    """The backend starting at noon on her birthday, past the morning: she comes all the same,
    not put off as missed."""
    characters.write(CALENDAR_FILE, SPECIAL["her-birthday"])
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT,
        at=datetime(2026, 10, 20, 4, tzinfo=UTC),  # 12:00 on the 20th in Shanghai
        random=FakeRandom(MIDDLE, 0),
    )
    last_contact(settings.database_path, conversation, datetime(2026, 10, 19, 13, tzinfo=UTC))

    await backend_of(client).reach_outs.run_due()

    assert len(reach_outs(await record(client, conversation))) == 1


async def allow_a_day(client: httpx.AsyncClient, github: FakeIdentityProvider, per_day: int) -> None:
    """Have the admin let each user be reached out to `per_day` times a day."""
    async with admin_beside(client, github) as admin:
        res = await admin.put(REACH_OUTS, json={"enabled": True, "per_day": per_day})
    assert res.status_code == 200, res.text


def reached_out(database: Path) -> int:
    """How many reach-outs are recorded, in every conversation."""
    with sqlite3.connect(database) as db:
        return db.execute("SELECT COUNT(*) FROM reach_outs").fetchone()[0]


async def until(done) -> None:
    async with asyncio.timeout(5):
        while not done():
            await asyncio.sleep(0.01)


async def test_at_night_she_does_not_come_but_in_the_morning_after_between_8_and_10(
    make_client: ClientFactory, settings: Settings
):
    client, _, clock, conversation = await talked(
        make_client, REACH_OUT,
        at=datetime(2026, 10, 19, 12, tzinfo=UTC),  # 20:00 in Shanghai
        random=FakeRandom(0, MIDDLE),
    )
    # 22:00 in Shanghai, 20 hours on; to 9:00, halfway through the morning
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=18))
    reach_out = backend_of(client).reach_outs
    came = []
    for to in (timedelta(hours=2), timedelta(hours=10, minutes=59), timedelta(minutes=1)):
        clock.advance(seconds=to.total_seconds())
        await reach_out.run_due()
        came.append(len(reach_outs(await record(client, conversation))))

    assert came == [0, 0, 1]


async def test_a_day_the_earliest_come_first_the_rest_the_next_morning_by_his_time_zone(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    """The day goes by the producer's time zone, New York's: Mano, put off from 21:00 there,
    comes at 9:00 the next morning, though it is the same day still in UTC."""
    client, _, clock, mano = await talked(
        make_client, REPLY, REACH_OUT, REACH_OUT,
        settings=replace(settings, admins=frozenset({ADMIN})),
        at=datetime(2026, 10, 20, 0, tzinfo=UTC),  # 20:00 in New York
        random=FakeRandom(0, 0, MIDDLE),
    )
    await live_in(client, "America/New_York")
    await allow_a_day(client, github, 1)
    kogane = await start_conversation(client, "kogane")
    await send_turn(client, kogane, "在吗")
    database = settings.database_path
    at = clock.now + timedelta(hours=1)  # 21:00 in New York
    last_contact(database, mano, at - timedelta(hours=20))
    last_contact(database, kogane, at - timedelta(hours=20, minutes=1))  # a minute before
    reach_out = backend_of(client).reach_outs

    came = []
    for to in (timedelta(hours=1), timedelta(hours=11, minutes=59), timedelta(minutes=1)):
        clock.advance(seconds=to.total_seconds())
        await reach_out.run_due()
        came.append((
            len(reach_outs(await record(client, kogane))),
            len(reach_outs(await record(client, mano))),
        ))

    assert came == [(1, 0), (1, 0), (1, 1)]


async def test_one_over_the_day_put_off_past_the_week_does_not_come(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    """Mano, missed as the backend was down six days on, put off to the next morning in the
    week; over the day by then, and put off past it."""
    client, _, clock, mano = await talked(
        make_client, REPLY, REACH_OUT, REACH_OUT,
        settings=replace(settings, admins=frozenset({ADMIN})),
        at=datetime(2026, 10, 19, 6, tzinfo=UTC),  # 14:00 in Shanghai
        random=FakeRandom(0, 0, 0, 0),
    )
    await allow_a_day(client, github, 1)
    kogane = await start_conversation(client, "kogane")
    await send_turn(client, kogane, "在吗")
    database = settings.database_path
    last_contact(database, mano, clock.now - timedelta(days=6))  # put off to 20 hours on
    last_contact(database, kogane, clock.now - timedelta(minutes=1))  # due a minute before
    reach_out = backend_of(client).reach_outs

    await reach_out.run_due()
    clock.advance(hours=20)
    await reach_out.run_due()
    clock.advance(days=1)
    await reach_out.run_due()

    assert len(reach_outs(await record(client, kogane))) == 1
    assert reach_outs(await record(client, mano)) == []


async def test_what_came_as_the_backend_was_down_is_not_made_up_for_but_put_off(
    make_client: ClientFactory, settings: Settings
):
    """Put off by a time from her range, from as it starts: 28 hours, halfway."""
    client, llm, clock, conversation = await talked(
        make_client, REACH_OUT,
        at=datetime(2026, 10, 19, 4, tzinfo=UTC),  # 12:00 in Shanghai
        random=FakeRandom(0, MIDDLE),
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=22))  # 10:00
    reach_out = backend_of(client).reach_outs

    came = []
    for to in (timedelta(0), timedelta(hours=27, minutes=59), timedelta(minutes=1)):
        clock.advance(seconds=to.total_seconds())
        await reach_out.run_due()
        came.append(len(reach_outs(await record(client, conversation))))

    assert came == [0, 0, 1]
    assert len(llm.prompts) == 2  # the turn's, and hers


@pytest.mark.parametrize("why", ["no turns on the deployer's keys", "banned"])
async def test_she_does_not_reach_out_to_a_user_the_admin(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider, why: str
):
    client, _, clock, conversation = await talked(
        make_client, settings=replace(settings, admins=frozenset({ADMIN})), random=FakeRandom()
    )
    last_contact(settings.database_path, conversation, clock.now - timedelta(days=2))
    async with admin_beside(client, github) as admin:
        producer = (await find(admin, "producer"))["id"]
        if why == "banned":
            assert (await admin.post(f"/api/admin/users/{producer}/ban", json={})).status_code == 200
        else:
            await set_limits(admin, producer, turns_per_hour=0)

    await backend_of(client).reach_outs.run_due()

    assert reached_out(settings.database_path) == 0


@pytest.mark.parametrize("taking", ["a text turn", "a call"])
async def test_she_does_not_reach_out_while_they_are_talking(
    make_client: ClientFactory, settings: Settings, browser: Browser, taking: str
):
    thinking = asyncio.Event()
    replies = [[thinking, *REPLY], REACH_OUT] if taking == "a text turn" else [REACH_OUT]
    client, llm, clock, conversation = await talked(make_client, *replies, random=FakeRandom(0))
    last_contact(settings.database_path, conversation, clock.now - timedelta(hours=20))
    turn = None
    if taking == "a text turn":
        turn = asyncio.create_task(send_turn(client, conversation, "在吗"))
        await until(lambda: len(llm.prompts) == 2)
    else:
        assert (await browser.call(client, conversation_id=conversation)).status_code == 200

    try:
        await backend_of(client).reach_outs.run_due()
        asked = len(llm.prompts)
    finally:
        thinking.set()
        if turn is not None:
            await turn

    assert asked == (2 if taking == "a text turn" else 1)  # not asked to reach out
    assert reached_out(settings.database_path) == 0


async def test_what_she_thought_as_the_user_spoke_is_not_recorded_its_record_refused(
    make_client: ClientFactory, settings: Settings
):
    thinking = asyncio.Event()
    client, llm, clock, conversation = await talked(
        make_client, [thinking, *REACH_OUT], REPLY, random=FakeRandom(0)
    )
    database = settings.database_path
    last_contact(database, conversation, clock.now - timedelta(hours=20))
    reaching_out = asyncio.create_task(backend_of(client).reach_outs.run_due())
    await until(lambda: len(llm.prompts) == 2)

    try:
        await send_turn(client, conversation, "在吗")
    finally:
        thinking.set()
        await reaching_out

    assert reach_outs(await record(client, conversation)) == []
    assert records(database) == [
        ("turn", "mano", "success", 0), ("turn", "mano", "success", 0),
        ("reach_out", "mano", "rejected", 0),
    ]
