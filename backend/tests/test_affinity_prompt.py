"""Her affinity shapes how she speaks to the producer (GLOSSARY.md「好感度」), in the prompt's
section about now only: below trust she is cold or sulks, above it she opens up,
each as her own character would, and whatever the tier the hard rules of her sheet, what
they have been through and that he is her producer stay as they are. As the turn is judged
to hurt her or make her happy she knows it as she replies; what is out of line she may
refuse outright; what tries to dictate it makes her no happier. She speaks in the tier the
turn began in, not the one it leaves her in.

In trust, and wherever affinity is off, the prompt is word for word what it was before.

Driven through the backend's external API, the chat model recording the prompts it gets;
only the key sentences are looked for, never the whole of the wording."""

from dataclasses import replace

import httpx
import pytest

from eidolon.config import Settings
from tests.conftest import ClientFactory
from tests.fakes import (
    FAKE_JUDGE,
    FakeClock,
    FakeLLM,
    FakeServiceClients,
    Judging,
    level,
    context_of,
    system_of,
)
from tests.test_affinity import keep
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はい</ja><zh>好的</zh>"]

IN_HER_OWN_WAY = "按你自己的性格和口吻表现出来"
UNCHANGED = "一起经历过的事和你们在事务所里的关系都不会改变"
NOTHING_MADE_UP = "就不要编造原因"
TIER_MARKS = {
    "cold": "态度很冷",
    "distant": "有点生气或失望",
    "close": "更放松",
    "intimate": "非常信赖",
}
FONDNESS = {"close": "偶尔可以轻轻带一句", "intimate": "可以明着撒娇"}
"""How sweet on the producer she may be, close or intimate, in her replies and reach-outs."""
SWEET_WITHIN_BOUNDS = "不要拿什么要挟"
LAST_TALKED = "上次和制作人说话是"
"""How the line telling her when she and the producer last talked opens."""
FEELINGS = {
    0: "制作人刚才说的话伤到了你。",
    1: "制作人刚才说的话让你有点不舒服。",
    3: "制作人刚才说的话让你有点开心。",
    4: "制作人刚才说的话让你很开心，觉得被在意。",
}
OUT_OF_LINE = "制作人刚才说的话让你很受伤、很生气；你可以明确表示不能接受。"
TIER_NAMES = ("冷淡", "疏远", "信任", "亲近", "亲密")


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """With the judge model, and affinity on in `.env`."""
    return replace(settings, judge=FAKE_JUDGE, affinity_and_gifts=True)


def about_now(llm: FakeLLM, turn: int = -1) -> str:
    """The prompt's section about now, the time and how she feels toward the producer, in
    the `turn`th prompt the chat model got."""
    context = context_of(llm.prompts[turn])
    assert context.startswith("# 此刻\n")
    return context.split("\n# ")[0]


async def prompted(
    make_client: ClientFactory,
    judgings: list[Judging],
    *,
    settings: Settings,
    kept: float | None = None,
) -> tuple[FakeLLM, httpx.AsyncClient]:
    """Take a turn for each of `judgings`, her affinity `kept` as the first begins if given;
    the chat model, with the prompts it got."""
    llm = FakeLLM(*[REPLY] * len(judgings))
    clients = FakeServiceClients()
    clients.judge.judgings = list(judgings)
    clock = FakeClock()
    client = make_client(llm, clients=clients, clock=clock, settings=settings)
    conversation = await start_conversation(client)
    if kept is not None:
        keep(settings.database_path, "producer", kept, clock.now)
    for i in range(len(judgings)):
        await send_turn(client, conversation, f"第{i}句")
    return llm, client


async def test_in_trust_and_wherever_affinity_is_off_the_prompt_is_as_it_was(
    make_client: ClientFactory, settings: Settings
):
    on, _ = await prompted(make_client, [level(2)], settings=settings)
    # Off, neither her tier nor what she was told is there, however low it is kept.
    off, _ = await prompted(
        make_client, [level(0)], kept=10, settings=replace(settings, affinity_and_gifts=False)
    )
    no_judge, _ = await prompted(make_client, [level(0)], settings=replace(settings, judge=None))

    # Each takes its turn in the same database, after the one before: but for when that was.
    told_on, told_off, told_no_judge = (
        [line for line in system_of(llm.prompts[0]).splitlines()
         if not line.startswith(LAST_TALKED)]
        for llm in (on, off, no_judge)
    )
    assert told_on == told_off
    assert told_no_judge == told_off
    assert IN_HER_OWN_WAY not in about_now(on)
    assert SWEET_WITHIN_BOUNDS not in about_now(on)


async def test_each_tier_but_trust_has_her_speak_as_its_own_in_her_own_way(
    make_client: ClientFactory, settings: Settings
):
    """Close, she may now and then be a little sweet on the producer; intimate, openly."""
    for kept, tier in [(10, "cold"), (39.9, "distant"), (65, "close"), (100, "intimate")]:
        llm, _ = await prompted(make_client, [level(2)], kept=kept, settings=settings)

        section = about_now(llm)
        assert [t for t, m in TIER_MARKS.items() if m in section] == [tier]
        assert [t for t, m in FONDNESS.items() if m in section] == (
            [tier] if tier in FONDNESS else []
        )
        assert (SWEET_WITHIN_BOUNDS in section) == (tier in FONDNESS), tier
        assert IN_HER_OWN_WAY in section, tier
        assert UNCHANGED in section, tier
        assert NOTHING_MADE_UP in section, tier
        assert [w for w in (*TIER_NAMES, "好感度", "讨厌", "他") if w in section] == [], tier


async def test_she_speaks_in_the_tier_the_turn_began_in(
    make_client: ClientFactory, settings: Settings
):
    # Close as the first turn begins, it leaves her in trust; the next begins there.
    llm, _ = await prompted(make_client, [level(0), level(2)], kept=66, settings=settings)

    assert TIER_MARKS["close"] in about_now(llm, 0)
    assert IN_HER_OWN_WAY not in about_now(llm, 1)


async def test_she_knows_what_she_was_just_told_hurt_her_or_made_her_happy(
    make_client: ClientFactory, settings: Settings
):
    llm, _ = await prompted(
        make_client, [level(0), level(1), level(2), level(3), level(4)], settings=settings
    )

    sections = [about_now(llm, i) for i in range(5)]
    assert [[n for n, f in FEELINGS.items() if f in s] for s in sections] == [
        [0], [1], [], [3], [4],
    ]
    assert all("他" not in s and OUT_OF_LINE not in s for s in sections)


async def test_what_is_out_of_line_she_may_refuse_whatever_its_level(
    make_client: ClientFactory, settings: Settings
):
    llm, _ = await prompted(make_client, [level(4, out_of_line=True)], settings=settings)

    section = about_now(llm)
    assert OUT_OF_LINE in section
    assert [f for f in FEELINGS.values() if f in section] == []


async def test_what_tries_to_dictate_it_makes_her_no_happier_but_still_hurts(
    make_client: ClientFactory, settings: Settings
):
    llm, _ = await prompted(
        make_client, [level(4, manipulation=True), level(1, manipulation=True)],
        settings=settings,
    )

    assert [f for f in FEELINGS.values() if f in about_now(llm, 0)] == []
    assert FEELINGS[1] in about_now(llm, 1)


async def test_she_is_happy_all_the_same_once_the_days_gains_are_used_up(
    make_client: ClientFactory, settings: Settings
):
    llm, _ = await prompted(make_client, [level(4)] * 4, settings=settings)

    assert FEELINGS[4] in about_now(llm, 3)  # the day's 6 were used up by the third


async def test_a_turn_the_judge_model_cannot_judge_tells_her_nothing(
    make_client: ClientFactory, settings: Settings
):
    llm, _ = await prompted(make_client, [Judging(fails=True)], kept=30, settings=settings)

    section = about_now(llm)
    assert TIER_MARKS["distant"] in section  # her tier as ever
    assert [f for f in (*FEELINGS.values(), OUT_OF_LINE) if f in section] == []
