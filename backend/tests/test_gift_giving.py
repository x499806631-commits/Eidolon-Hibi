"""Giving a gift (GLOSSARY.md「礼物」「对话事件」): one at a time, to any character, from the
gifts page. It takes one from the backpack and raises her affinity by its rarity, more if
she likes it, by no more than a little in a cooldown, which enough gifts end sooner than
recovery would; at the top it changes nothing, given all the same. It leaves a gift record
in her current conversation (GLOSSARY.md「当前对话」) if she has not ended it, else in one
begun for it, where she responds to it first, whichever conversation it was given from. Given during a turn under way, the record comes after that turn (tested with her
responding to it); the conversation list's last message is then the record. It is not a turn: no limit counts it, no cooldown refuses it, no activity record is
left. Closed, nothing can be given. Driven through the backend's external API, the random
source giving what is drawn and the clock moved by hand."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace

import httpx
import pytest

from eidolon.config import Settings, UsageLimits
from eidolon.gift_catalog import GIFTS_FILE
from eidolon.llm import ChatMessage, Streamed, Tool
from tests.conftest import CharacterConfig, ClientFactory
from tests.fakes import FAKE_JUDGE, FakeClock, FakeIdentityProvider, FakeLLM, FakeRandom
from tests.test_activity_records import dashboard
from tests.test_admin_users import ADMIN, admin_beside
from tests.test_affinity import keep, status
from tests.test_conversation_events import record, shape
from tests.test_conversation_history import history
from tests.test_gift_backpack import COMMON, FIRST, RARE, SECOND, UNCOMMON
from tests.test_gift_catalog import gift
from tests.test_she_ends_conversation import end
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]

CAKE, TEA, COCOA, PLUSH = (COMMON, FIRST), (COMMON, SECOND), (UNCOMMON, FIRST), (RARE, FIRST)
"""The numbers that draw each gift: cake is common and liked by every test character, tea
common and not, cocoa uncommon, plush rare."""


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With an admin, the judge model, affinity and gifts on in `.env`, and one turn an
    hour."""
    return replace(
        settings,
        admins=frozenset({ADMIN}),
        judge=FAKE_JUDGE,
        affinity_and_gifts=True,
        usage_limits=UsageLimits(turns_per_hour=1),
    )


@pytest.fixture(autouse=True)
def catalog(characters: CharacterConfig) -> None:
    characters.write(
        GIFTS_FILE,
        gift("cake") + gift("tea", category="drink") + gift("cocoa", "uncommon")
        + gift("plush", "rare", "trinket"),
    )


def holding(
    make_client: ClientFactory, clock: FakeClock, *gifts: tuple[int, int], llm: FakeLLM | None = None
) -> httpx.AsyncClient:
    """A client whose draws give `gifts`, in order; `draw_them` draws them."""
    numbers = [n for g in gifts for n in g]
    return make_client(llm or FakeLLM(REPLY), clock=clock, random=FakeRandom(*numbers))


async def draw_them(client: httpx.AsyncClient, clock: FakeClock, count: int) -> None:
    """Draw `count` gifts (up to 7), waiting the days it takes to have the chances."""
    assert (await client.get("/api/gifts")).status_code == 200
    clock.advance(days=count - 1)
    res = await client.post("/api/gifts/draws", json={"all": True})
    assert res.status_code == 200, res.text
    assert len(res.json()["drawn"]) == count


def give(client: httpx.AsyncClient, gift_id: str, character: str = "mano", **body: str):
    """Give her the gift, with whatever else is put in the request `body`."""
    return client.post(f"/api/characters/{character}/gifts", json={"gift_id": gift_id} | body)


async def given(client: httpx.AsyncClient, gift_id: str, character: str = "mano",
                **body: str) -> dict:
    res = await give(client, gift_id, character, **body)
    assert res.status_code == 200, res.text
    return res.json()


async def backpack(client: httpx.AsyncClient) -> dict[str, int]:
    held = (await client.get("/api/gifts")).json()["backpack"]
    return {h["gift_id"]: h["count"] for h in held}


def happier(tier: str) -> dict:
    return {"up": True, "tier_changed": False, "tier": tier, "cooldown": False, "thawed": False}


def closer(tier: str) -> dict:
    return {"up": True, "tier_changed": True, "tier": tier, "cooldown": False, "thawed": False}


THAWED = {"up": True, "tier_changed": False, "tier": "cold", "cooldown": False, "thawed": True}


@pytest.mark.parametrize(
    ("drawn", "gift_id", "change"),
    [
        (TEA, "tea", happier("trust")),  # 59 + 3
        (COCOA, "cocoa", happier("trust")),  # 59 + 5
        # 59 + 3 + 3, as she likes it, and 59 + 7: held at close's door, her scene there not
        # played yet (GLOSSARY.md「重要的一幕」, test_scenes)
        (CAKE, "cake", happier("trust")),
        (PLUSH, "plush", happier("trust")),
    ],
)
async def test_a_gift_raises_her_affinity_by_its_rarity_and_more_if_she_likes_it(
    make_client: ClientFactory, settings: Settings, drawn: tuple[int, int], gift_id: str,
    change: dict,
):
    clock = FakeClock()
    client = holding(make_client, clock, drawn, drawn)
    await draw_them(client, clock, 2)
    keep(settings.database_path, "producer", 59, clock.now)

    result = await given(client, gift_id)

    assert result["change"] == change
    assert result["affinity"] == {"tier": change["tier"], "cooling": False, "cooldown_until": None}
    assert result["left"] == 1
    assert await backpack(client) == {gift_id: 1}
    assert (await status(client)).json()["tier"] == change["tier"]


async def test_in_a_cooldown_each_gift_counts_for_little_and_the_fourth_ends_it(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE, CAKE, CAKE, CAKE)
    await draw_them(client, clock, 4)
    keep(settings.database_path, "producer", 0, clock.now, cooling=True)
    until = (await status(client)).json()["cooldown_until"]

    results = [await given(client, "cake") for _ in range(4)]

    # She likes cake, which would end it in two were it not held to 3 a gift.
    assert [r["change"] for r in results] == [None, None, None, THAWED]
    untils = [until, *(r["affinity"]["cooldown_until"] for r in results[:3])]
    assert untils == sorted(untils, reverse=True) and len(set(untils)) == 4
    assert results[3]["affinity"] == {"tier": "cold", "cooling": False, "cooldown_until": None}
    assert (await status(client)).json() == {"tier": "cold", "cooldown_until": None}


async def test_at_the_top_it_is_given_all_the_same_changing_nothing(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = holding(make_client, clock, PLUSH)
    await draw_them(client, clock, 1)
    keep(settings.database_path, "producer", 100, clock.now)

    result = await given(client, "plush")

    assert (result["change"], result["affinity"]["tier"], result["left"]) == (None, "intimate", 0)


async def test_what_cannot_be_given_is_refused_changing_nothing(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    client = holding(make_client, clock, TEA)
    await draw_them(client, clock, 1)
    await given(client, "tea")

    none_left = await give(client, "tea")
    no_such_gift = await give(client, "rock")
    no_one = await give(client, "cake", character="nobody")

    assert [none_left.status_code, no_such_gift.status_code, no_one.status_code] == [409, 409, 404]
    assert none_left.json()["detail"] == "已经没有了"
    assert (await status(client)).json()["tier"] == "trust"
    assert await backpack(client) == {}


async def test_two_giving_the_last_one_at_once_give_it_once(make_client: ClientFactory):
    clock = FakeClock()
    client = holding(make_client, clock, TEA)
    await draw_them(client, clock, 1)

    results = await asyncio.gather(give(client, "tea"), give(client, "tea", "kogane"))

    assert sorted(r.status_code for r in results) == [200, 409]


async def test_it_is_no_turn_no_limit_counts_it_and_no_activity_record_is_left(
    make_client: ClientFactory, settings: Settings, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = holding(make_client, clock, TEA, TEA)
    await draw_them(client, clock, 2)
    conversation = await start_conversation(client)

    await given(client, "tea")
    await given(client, "tea")
    turn = await send_turn(client, conversation, "在吗")

    assert turn[-1]["type"] == "done"  # the hour's one turn was left
    async with admin_beside(client, github) as admin:
        board = await dashboard(admin)
    assert board["turns"]["total"] == 1


async def test_the_record_goes_into_her_newest_even_when_the_request_names_another(
    make_client: ClientFactory,
):
    """Requests from before the gifts page stopped naming the conversation it was come to
    from name one: it changes nothing."""
    clock = FakeClock()
    client = holding(make_client, clock, TEA)
    await draw_them(client, clock, 1)
    older = await start_conversation(client)
    newest = await start_conversation(client)

    result = await given(client, "tea", conversation_id=older)

    assert result["conversation_id"] == newest


async def test_with_no_conversation_with_her_one_is_begun_for_it_and_listed_by_it(
    make_client: ClientFactory,
):
    clock = FakeClock()
    client = holding(make_client, clock, CAKE)
    await draw_them(client, clock, 1)

    result = await given(client, "cake")

    record = (await client.get(f"/api/conversations/{result['conversation_id']}/messages")).json()
    assert record == [
        {
            "type": "event",
            "id": record[0]["id"],
            "kind": "gift",
            "created_at": record[0]["created_at"],
            "affinity": happier("trust"),
            "gift_id": "cake",
            "gift_name": "cake",
        }
    ]
    [latest] = (await client.get("/api/conversations/latest")).json()
    assert latest["conversation_id"] == result["conversation_id"]
    assert latest["last_message"] == {
        "type": "event", "kind": "gift", "created_at": record[0]["created_at"],
        "gift_id": "cake", "gift_name": "cake",
    }


async def test_with_her_newest_ended_it_goes_to_one_begun_for_it_not_an_older_one_she_has_not(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    llm = FakeLLM(REPLY)
    client = holding(make_client, clock, CAKE, TEA, llm=llm)
    await draw_them(client, clock, 2)
    older = await start_conversation(client)
    ended = await start_conversation(client)
    end(settings.database_path, ended)

    result = await given(client, "cake")
    begun = result["conversation_id"]
    again = (await given(client, "tea"))["conversation_id"]
    await send_turn(client, begun, "尝尝看")

    assert begun not in (older, ended)
    assert again == begun
    assert shape(await record(client, ended)) == ["ended"]
    assert shape(await record(client, older)) == []
    assert shape(await record(client, begun)) == ["gift", "gift", "user:尝尝看", "character:好的"]
    assert result["change"] == happier("trust")
    assert "（制作人送给你一份cake。这是你喜欢的东西。）" in history(llm)[0][1]
    [latest] = (await client.get("/api/conversations/latest")).json()
    assert latest["conversation_id"] == begun


class HoldsReply(FakeLLM):
    """Stops after the reply's first chunk until `release` is set, having set `replying`."""

    def __init__(self, *replies: Sequence[str]) -> None:
        super().__init__(*replies)
        self.replying = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_with(
        self,
        key: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        async for chunk in super().stream_with(key, messages, tools, forbid_calls=forbid_calls):
            yield chunk
            if not self.release.is_set():
                self.replying.set()
                await self.release.wait()


async def test_closed_nothing_can_be_given(
    make_client: ClientFactory, github: FakeIdentityProvider
):
    clock = FakeClock()
    client = holding(make_client, clock, TEA)
    await draw_them(client, clock, 1)

    async with admin_beside(client, github) as admin:
        assert (await admin.put("/api/admin/config/affinity", json={"enabled": False})).status_code == 200
        res = await give(client, "tea")

    assert res.status_code == 409
    assert res.json()["gifts_closed"] is True
    assert await backpack(client) == {"tea": 1}
