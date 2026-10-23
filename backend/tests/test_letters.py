"""Letters she writes the user (GLOSSARY.md「来信」): how she is asked to write one, on the
chat model, laid out as a reach-out's prompt; and how, once written, she knows of it as she
talks with the user, for two weeks."""

from datetime import UTC, datetime, timedelta

import pytest

from eidolon.engine import LETTER_OCCASIONS, WANTS_TO_WRITE, EmptyReply, letter_rules
from eidolon.reply_format import Segment
from tests.conftest import ClientFactory
from tests.fakes import FakeClock, FakeLLM, context_of
from tests.test_reach_outs import backend_of
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]
LETTER = [
    "<ja>プロデューサーさんへ</ja><zh>致制作人</zh>",
    "<ja>[softly] 最近、寒くなってきましたね。</ja><zh>最近变冷了呢。</zh>",
    "<ja>真乃より</ja><zh>真乃</zh>",
]


async def writing(make_client: ClientFactory, *replies, now: datetime | None = None):
    llm = FakeLLM(REPLY, *replies)
    clock = FakeClock(now or datetime.now(UTC))
    client = make_client(llm, clock=clock)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "我去练习了")
    backend = backend_of(client)
    conversation = await backend.store.get_conversation(conversation_id)
    assert conversation is not None
    return client, llm, clock, backend, conversation


@pytest.mark.parametrize("occasion", list(LETTER_OCCASIONS))
async def test_she_writes_a_letter_told_what_it_is_for(
    make_client: ClientFactory, occasion
):
    _, llm, _, backend, conversation = await writing(make_client, LETTER)

    said = await backend.engine.write_letter(conversation, llm, occasion)

    assert [s.zh for s in said] == ["致制作人", "最近变冷了呢。", "真乃"]
    prompt = llm.prompts[-1]
    assert [(m.role, m.content) for m in prompt if m.role != "system"][-1] == (
        "user", WANTS_TO_WRITE,
    )
    told = context_of(prompt)
    assert letter_rules(occasion) in told
    assert "你们这会儿没在说话" in told
    assert ("user", "我去练习了") in [(m.role, m.content) for m in prompt]


async def test_a_letter_with_nothing_to_send_is_an_empty_reply(make_client: ClientFactory):
    _, llm, _, backend, conversation = await writing(make_client, [])

    with pytest.raises(EmptyReply):
        await backend.engine.write_letter(conversation, llm, "ordinary")


async def test_she_knows_of_her_letters_for_two_weeks_and_whether_he_read_them(
    make_client: ClientFactory,
):
    client, llm, clock, backend, conversation = await writing(
        make_client, REPLY, REPLY, REPLY,
    )
    letter = await backend.store.add_letter(
        conversation.user_id, "mano", "ordinary",
        [Segment("プロデューサーさんへ", "致制作人"), Segment("[softly] 寒いね", "好冷呀")],
        clock.now,
    )
    await backend.store.add_letter(
        conversation.user_id, "kogane", "ordinary", [Segment("あ", "啊")], clock.now
    )
    await backend.store.add_letter(  # written ahead: not come yet
        conversation.user_id, "mano", "holiday", [Segment("x", "x"), Segment("x", "还没到的信")],
        clock.now + timedelta(days=30),
    )

    await send_turn(client, conversation.id, "收到你的信了")
    unread = context_of(llm.prompts[-1])
    await backend.store.read_letter(conversation.user_id, letter.id, clock.now)
    await send_turn(client, conversation.id, "又看了一遍")
    read = context_of(llm.prompts[-1])

    assert "一封平时的信，正文开头是「好冷呀」；他还没看" in unread
    assert "「啊」" not in unread  # Kogane's, not hers
    assert "还没到的信" not in unread
    assert "他已经看过了" in read

    clock.advance(days=15)
    await send_turn(client, conversation.id, "好久不见")
    assert "写过一封" not in context_of(llm.prompts[-1])


async def test_a_holiday_letter_tells_which_holiday_from_the_calendar_and_how_she_spent_it(
    make_client: ClientFactory,
):
    _, llm, _, backend, conversation = await writing(make_client, LETTER)

    await backend.engine.write_letter(
        conversation, llm, "holiday", about="七夕。牛郎织女相会的日子。"
    )

    told = context_of(llm.prompts[-1])
    assert "今天是七夕。牛郎织女相会的日子。现在是晚上" in told
    assert "做了什么、吃了什么、和谁一起" in told
    assert LETTER_OCCASIONS["holiday"][2] in told  # she may tell of her day with others
