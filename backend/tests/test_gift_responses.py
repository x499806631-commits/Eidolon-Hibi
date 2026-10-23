"""She responds to a gift (GLOSSARY.md「礼物」「对话事件」): her prompt tells her of it in a line
before the user's next words, saying so if she liked it as it was given; until she has
replied after it, it also tells her, once however many came, to answer the user first and
the gift in her own way, once. A turn that fails is no reply. A gift given while she
replies is not this turn's but the next's; in a call, the next turn of it. Memory and the
judge model never see it, nor any other conversation event.

Driven through the backend's external API, the random source giving what is drawn; the
call with a browser calling in over a real WebRTC connection."""

import asyncio
from dataclasses import replace

import pytest

from eidolon.config import Settings
from tests.browser import open_browser
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeBackgroundLLM,
    FakeClock,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeRandom,
    FakeReranker,
    FakeServiceClients,
    FakeSpeech,
)
from tests.test_conversation_events import HUNG_UP, hang_up
from tests.test_conversation_history import history, told
from tests.test_gift_giving import (  # noqa: F401
    CAKE,
    TEA,
    HoldsReply,
    catalog,
    draw_them,
    given,
    holding,
)
from tests.test_she_hangs_up import say
from tests.test_text_turn import send_turn, start_conversation

SAID = "<ja>はい</ja><zh>好的</zh>"
REPLY = [SAID]
GIVEN_CAKE = "（制作人送给你一份cake。这是你喜欢的东西。）"
GIVEN_TEA = "（制作人送给你一份tea。）"
RESPOND = "（先回应制作人说的话，再在合适的时候用你自己的方式回应收到的礼物，一次就够。）"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model, and affinity and gifts on in `.env`."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


async def test_her_next_turn_tells_her_of_it_whether_she_likes_it_and_to_respond_once(
    make_client: ClientFactory,
):
    clock = FakeClock()
    llm = FakeLLM(REPLY, REPLY)
    client = holding(make_client, clock, CAKE, TEA, llm=llm)
    await draw_them(client, clock, 2)
    conversation = await start_conversation(client)

    await given(client, "cake")
    await send_turn(client, conversation, "尝尝看")
    await given(client, "tea")
    await send_turn(client, conversation, "还有这个")

    assert history(llm) == [
        ("user", f"{GIVEN_CAKE}\n尝尝看"),
        ("assistant", SAID),
        ("user", f"{GIVEN_TEA}\n{RESPOND}\n还有这个"),
    ]


async def test_several_given_at_once_are_each_told_and_she_is_told_to_respond_once(
    make_client: ClientFactory,
):
    clock = FakeClock()
    llm = FakeLLM(REPLY)
    client = holding(make_client, clock, TEA, CAKE, TEA, llm=llm)
    await draw_them(client, clock, 3)
    conversation = await start_conversation(client)

    for gift_id in ("tea", "cake", "tea"):
        await given(client, gift_id)
    await send_turn(client, conversation, "都给你")

    assert history(llm) == [("user", f"{GIVEN_TEA}\n{GIVEN_CAKE}\n{GIVEN_TEA}\n{RESPOND}\n都给你")]


async def test_a_turn_that_fails_is_no_reply_to_it(make_client: ClientFactory):
    clock = FakeClock()
    llm = FakeLLM(["  "], REPLY)
    client = holding(make_client, clock, TEA, llm=llm)
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)
    await given(client, "tea")

    failed = await send_turn(client, conversation, "送你的")
    await send_turn(client, conversation, "送你的")

    assert failed[-1]["type"] == "error"
    assert history(llm) == [("user", f"{GIVEN_TEA}\n{RESPOND}\n送你的")]


async def test_one_given_while_she_replies_is_the_next_turns_to_respond_to(
    make_client: ClientFactory,
):
    clock = FakeClock()
    llm = HoldsReply(["<ja>は", "い</ja><zh>好的</zh>"], REPLY)
    client = holding(make_client, clock, TEA, llm=llm)
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)

    turn = asyncio.create_task(send_turn(client, conversation, "在吗"))
    await llm.replying.wait()
    await given(client, "tea")
    llm.release.set()
    await turn
    record = (await client.get(f"/api/conversations/{conversation}/messages")).json()
    assert [e.get("kind") or e["role"] for e in record] == ["user", "character", "gift"]
    assert history(llm) == [("user", "在吗")]

    await send_turn(client, conversation, "第二句")
    assert history(llm)[-1] == ("user", f"{GIVEN_TEA}\n{RESPOND}\n第二句")


async def test_in_a_call_the_next_turn_responds_to_one_given_from_elsewhere(
    make_client: ClientFactory, speech: FakeSpeech
):
    clock = FakeClock()
    llm = FakeLLM(REPLY, REPLY)
    client = holding(make_client, clock, CAKE, llm=llm)
    await draw_them(client, clock, 1)
    conversation = await start_conversation(client)

    async with open_browser() as browser:
        await browser.call(client, conversation_id=conversation)
        await browser.connect()
        await say(speech, browser, "在吗")
        await given(client, "cake")  # from the gifts page, on another device
        turn = await say(speech, browser, "怎么了")
        await browser.hang_up()

    assert turn[-1]["type"] == "done"
    assert history(llm)[-1] == ("user", f"{GIVEN_CAKE}\n{RESPOND}\n怎么了")


async def test_memory_and_the_judge_model_never_see_it_nor_her_hanging_up(
    make_client: ClientFactory, settings: Settings
):
    clock = FakeClock()
    clients = FakeServiceClients()
    memory = FakeBackgroundLLM()
    llm = FakeLLM(REPLY, REPLY, REPLY)
    client = make_client(
        llm, clock=clock, random=FakeRandom(*CAKE, *TEA), clients=clients,
        retrieval_judge=FakeJudge(answer="memories"), embedder=FakeEmbedder(),
        reranker=FakeReranker(), background_llm=memory,
    )
    await draw_them(client, clock, 2)
    conversation = await start_conversation(client)
    await send_turn(client, conversation, "第一句")
    await given(client, "cake")
    await send_turn(client, conversation, "送你的")
    await given(client, "tea")
    hang_up(settings.database_path, conversation)

    await send_turn(client, conversation, "还有这个")
    await client.get("/api/memories")  # once what the turns write down is written

    assert HUNG_UP in told(llm) and RESPOND in told(llm)  # she is told of them all the same
    # Naming the conversation after its third turn reads events too, and may or may not
    # have begun by now; only what memory was asked counts here.
    memories = [prompt for prompt in memory.prompts if "起标题" not in prompt[0].content]
    seen = [*(str(state) for state in clients.judge.states),
            *(m.content for prompt in memories for m in prompt)]
    assert len(clients.judge.states) == 3 and len(memories) == 3
    assert not [s for s in seen if "制作人送给你" in s or "回应收到的礼物" in s or "挂断" in s]
    assert clients.judge.affinity_asked == ["第一句", "送你的", "还有这个"]
