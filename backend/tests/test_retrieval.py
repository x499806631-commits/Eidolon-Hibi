"""Retrieval in conversation, driven through the text turn API: Jev decides whether a
turn looks anything up; if so, the story corpus (only episodes the character is cast
in) and her character facts are searched, reranked and capped into the prompt. They
are searched while the judge model judges, and reranked only once it has."""

import asyncio
from pathlib import Path

import httpx
import pytest

from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon.retrieval import FALLBACK_REFERENCES, MAX_REFERENCES
from eidolon.story_import import import_csv_directory
from tests.conftest import KOGANE_SHEET, MANO_SHEET, CharacterConfig, ClientFactory
from tests.fakes import FakeBackgroundLLM, FakeEmbedder, FakeJudge, FakeLLM, FakeReranker, context_of, system_of
from tests.test_live_config import until
from tests.test_shared_memory import CAT, memorized
from tests.stories import corpus_in
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]

MANO_FACTS = """\
# 档案
生日是3月4日，喜欢鸟。

# 人物关系
## 風野灯織
illumination STARS 的伙伴，性格认真。
## 八宮めぐる
illumination STARS 的伙伴，总是很有精神。

# 鸟
## ピーちゃん
常和真乃在一起的鸽子。
## 公園
真乃常去公园看鸟。
"""
KOGANE_FACTS = "# 家乡\n来自长崎，最喜欢什锦面。"

PARK_LINE = "公園で鳩と一緒に歌っていました"


@pytest.fixture
def characters(tmp_path: Path) -> CharacterConfig:
    return (
        CharacterConfig(tmp_path / "characters")
        .add("mano", "櫻木真乃", MANO_SHEET, speakers=["真乃"], facts=MANO_FACTS)
        .add("kogane", "月岡恋鐘", KOGANE_SHEET, speakers=["恋鐘"], facts=KOGANE_FACTS)
    )


@pytest.fixture
async def embedder(settings: Settings, story: Path) -> FakeEmbedder:
    """The embedder the sample stories were imported with."""
    embedder = FakeEmbedder()
    async with corpus_in(settings.database_path, embedder) as corpus:
        characters = load_characters(settings.characters_path)
        await import_csv_directory(story, corpus, characters.values())
    return embedder


async def system_prompt(
    client: httpx.AsyncClient, llm: FakeLLM, text: str, character: str = "mano"
) -> str:
    conversation_id = await start_conversation(client, character)
    events = await send_turn(client, conversation_id, text)
    assert events[-1]["type"] == "done"
    return system_of(llm.prompts[-1])


def reference_count(system: str) -> int:
    return system.count("\n## 剧情\n") + system.count("\n## 角色资料\n")


async def test_small_talk_retrieves_nothing(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    jev, reranker, llm = FakeJudge(answer="nothing"), FakeReranker(), FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=jev, embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "早上好")

    assert jev.asked == [("櫻木真乃", "早上好")]
    # The judge model answering at once, nothing was looked up before it did: embedded only
    # to show the background model the memories nearest the turn, after it.
    assert embedder.queries == ["早上好"]
    assert reranker.calls == []
    assert "# 回忆与资料" not in system
    assert PARK_LINE not in system


async def test_a_character_recalls_episodes_she_is_cast_in(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker(PARK_LINE))

    system = await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    assert "# 回忆与资料" in system
    references = system[system.index("# 回忆与资料") :]
    assert references.index("## 剧情\nW.I.N.G.編「公園の歌声」") < references.index("## 角色资料")
    assert PARK_LINE in references


async def test_a_character_cannot_recall_episodes_she_is_not_in(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(REPLY, REPLY), FakeReranker()
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    # Mano's park episode is the closest match, but Kogane is not in it.
    kogane = await system_prompt(client, llm, "公園の鳩のこと覚えてる？", "kogane")
    candidates = reranker.calls[-1][1]
    assert not any(PARK_LINE in c for c in candidates)
    assert PARK_LINE not in kogane
    assert "ちゃんぽん" in kogane  # her own card is still there

    # Hiori's event is about a photo shoot in the park; no configured character is in it.
    mano = await system_prompt(client, llm, "公園で写真を撮ったこと", "mano")
    assert "撮影、頑張ります" not in mano
    assert not any("撮影、頑張ります" in c for _, docs in reranker.calls for c in docs)


async def test_character_facts_are_retrieved_for_their_own_character(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker("風野灯織"))

    system = await system_prompt(client, llm, "灯織ちゃんは元気？")

    references = system[system.index("# 回忆与资料") :]
    assert references.index("## 角色资料\n人物关系 / 風野灯織\nillumination STARS 的伙伴，性格认真。") < (
        references.index("## 剧情")
    )
    assert "来自长崎" not in system  # Kogane's facts are hers alone


async def test_references_are_reranked_then_capped(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    reranker, llm = FakeReranker("ピーちゃん"), FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "鳩の話をしよう")

    [(query, candidates)] = reranker.calls
    assert query == "鳩の話をしよう"
    # Mano's three episodes and five facts all go to the reranker...
    assert len(candidates) == 8 > MAX_REFERENCES
    # ...but only the best few reach the prompt, in the reranker's order.
    assert reference_count(system) == MAX_REFERENCES
    references = system[system.index("# 回忆与资料") :]
    assert references.index("ピーちゃん") < references.index("## 剧情")


PARK_REPLY = ["<ja>[happy] 公園で鳩さんと歌った日ですね</ja><zh>[happily] 是和鸽子一起在公园唱歌的那天吧</zh>"]


async def test_references_are_looked_up_with_her_previous_reply_as_people_see_it(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    """「那时候累吗」: her reply before it says what 「那时候」 is."""
    jev, reranker = FakeJudge(answer="nothing"), FakeReranker(PARK_LINE)
    llm = FakeLLM(PARK_REPLY, REPLY)
    client = make_client(llm, retrieval_judge=jev, embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "还记得那天吗？")
    jev.answer = "references"

    events = await send_turn(client, conversation_id, "那时候累吗？")

    assert events[-1]["type"] == "done"
    query = "是和鸽子一起在公园唱歌的那天吧\n那时候累吗？"
    [(reranked, candidates)] = reranker.calls
    assert reranked == query
    assert any(PARK_LINE in c for c in candidates)
    assert query in embedder.queries
    assert "那时候累吗？" not in embedder.queries  # nor are memories found without it
    # The judge model is told what it was before: the latest messages and what was said.
    assert jev.asked[-1] == ("櫻木真乃", "那时候累吗？")
    assert PARK_LINE in context_of(llm.prompts[-1])


async def test_the_first_line_of_a_conversation_is_looked_up_alone(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    reranker, llm = FakeReranker(PARK_LINE), FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    assert [query for query, _ in reranker.calls] == ["公園の鳩のこと覚えてる？"]
    assert set(embedder.queries) == {"公園の鳩のこと覚えてる？"}


async def test_irrelevant_references_are_left_out(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(REPLY, REPLY), FakeReranker(PARK_LINE, others=0.01)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=reranker)

    system = await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    # Only the park episode is relevant enough; the other candidates are not filler.
    assert reference_count(system) == 1
    assert PARK_LINE in system

    # With nothing relevant, the prompt carries no references section at all.
    reranker.prefer = "（どこにもない）"
    system = await system_prompt(client, llm, "長崎のちゃんぽん、好き？")

    assert "# 回忆与资料" not in system


async def test_edited_facts_take_effect_on_restart(
    make_client: ClientFactory, embedder: FakeEmbedder, characters: CharacterConfig
):
    characters.write("facts/mano.md", "# 鸟\n## ピーちゃん\n最近学会了说「ほわっ」。")
    llm = FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker())

    system = await system_prompt(client, llm, "ピーちゃんは元気？")

    assert "最近学会了说「ほわっ」。" in system
    assert "常和真乃在一起的鸽子。" not in system


async def test_retrieval_goes_ahead_when_jev_is_down(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(answer=None), embedder=embedder, reranker=FakeReranker())

    system = await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    assert PARK_LINE in system


async def test_a_turn_still_gets_a_reply_when_retrieval_fails(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    embedder.down = True
    llm = FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=FakeJudge(), embedder=embedder, reranker=FakeReranker())

    system = await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    assert "# 回忆与资料" not in system


async def test_with_the_reranker_down_the_nearest_references_are_brought_but_no_memories(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    judge, reranker, llm = FakeJudge(answer="nothing"), FakeReranker(), FakeLLM(REPLY, REPLY)
    client = make_client(
        llm, retrieval_judge=judge, embedder=embedder, reranker=reranker,
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    await tell_of_the_cat(client)
    judge.answer, reranker.down = "references", True

    system = await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    # The three nearest of all the candidates, whatever the threshold: the park episode
    # and the park among her facts, and one of those sharing no word with the question.
    assert sorted(len(docs) for _, docs in reranker.calls) == [1, 8]  # it was asked
    assert reference_count(system) == FALLBACK_REFERENCES == 3
    references = system[system.index("# 回忆与资料") :]
    assert PARK_LINE in references and "真乃常去公园看鸟。" in references
    # A memory the reranker never judged might be what the user never said.
    assert "# 关于制作人的记忆" not in system and CAT not in system


# While the judge model judges, the turn's query is embedded, once, and its candidates
# found; only those the turn turns out to need are reranked.


async def tell_of_the_cat(client: httpx.AsyncClient) -> None:
    """A turn in which the user tells Mano of their cat, which is written down."""
    await send_turn(client, await start_conversation(client), "我家的猫叫小白哦")
    memories = (await client.get("/api/memories")).json()  # once written down
    assert [m["text"] for m in memories] == [CAT]


async def test_a_turn_needing_references_embeds_its_query_once_for_them_and_memories(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    judge, reranker, llm = FakeJudge(answer="nothing"), FakeReranker(PARK_LINE), FakeLLM(REPLY, REPLY)
    client = make_client(
        llm, retrieval_judge=judge, embedder=embedder, reranker=reranker,
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    await tell_of_the_cat(client)
    judge.answer = "references"
    before = len(embedder.queries)

    system = await system_prompt(client, llm, "公園の鳩のこと覚えてる？")

    # Nor is it embedded again to write the turn down.
    assert embedder.queries[before:] == ["公園の鳩のこと覚えてる？"]
    assert PARK_LINE in system[system.index("# 回忆与资料") :]
    assert CAT in system[system.index("# 关于制作人的记忆") :]
    # Reranked as they always were: the references together, the memories apart.
    assert sorted(len(docs) for _, docs in reranker.calls) == [1, 8]
    assert [CAT] in [docs for _, docs in reranker.calls]


async def test_the_query_is_embedded_while_the_judge_model_judges(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    judge, reranker, llm = FakeJudge(), FakeReranker(PARK_LINE), FakeLLM(REPLY)
    client = make_client(llm, retrieval_judge=judge, embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client)
    judge.hold()

    turn = asyncio.create_task(send_turn(client, conversation_id, "公園の鳩のこと覚えてる？"))
    await asyncio.wait_for(judge.holding.wait(), 5)
    await until(lambda: embedder.queries)

    assert embedder.queries == ["公園の鳩のこと覚えてる？"]
    assert reranker.calls == []  # not until the judge model has answered
    judge.let_go()
    events = await turn
    assert events[-1]["type"] == "done"
    assert PARK_LINE in system_of(llm.prompts[-1])


async def test_a_turn_needing_nothing_drops_what_it_found_while_the_judge_model_judged(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    judge, reranker, llm = FakeJudge(answer="nothing"), FakeReranker("猫"), FakeLLM(REPLY, REPLY)
    client = make_client(
        llm, retrieval_judge=judge, embedder=embedder, reranker=reranker,
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    await tell_of_the_cat(client)
    conversation_id = await start_conversation(client)
    judge.hold()

    turn = asyncio.create_task(send_turn(client, conversation_id, "公園の鳩と猫、かわいいね"))
    await asyncio.wait_for(judge.holding.wait(), 5)
    await until(lambda: "公園の鳩と猫、かわいいね" in embedder.queries)
    judge.let_go()
    events = await turn

    assert events[-1]["type"] == "done"
    assert reranker.calls == []
    system = system_of(llm.prompts[-1])
    assert "# 回忆与资料" not in system and "# 关于制作人的记忆" not in system
    await client.get("/api/memories")  # once written down, with the query embedded already
    assert embedder.queries.count("公園の鳩と猫、かわいいね") == 1


async def test_a_recall_question_is_recalled_though_her_facts_cannot_be_embedded(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    """Her facts are embedded the first time they are searched, which a turn does as it
    begins; recalling never needed them."""
    embedder.documents_down = True
    llm = FakeLLM(REPLY)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder,
        reranker=FakeReranker(PARK_LINE),
    )

    system = await system_prompt(client, llm, "那次在公园唱歌是怎么回事？")

    assert "培育剧情 / W.I.N.G.編 /「公園の歌声」" in system
    assert f"真乃：{PARK_LINE}" in system
