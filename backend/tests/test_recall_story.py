"""The recall-story tool, driven through the text turn API: the model calls it with a
general question, the engine looks the episode up through the story catalog, and the
model answers from what comes back."""

from pathlib import Path

import pytest

from eidolon.characters import load_characters
from eidolon.config import Settings
from eidolon import engine
from eidolon.engine import MAX_TOOL_ROUNDS
from eidolon.llm import ChatMessage, Reasoning, ToolCall
from eidolon.retrieval import RECALL_STORY_TOOL
from eidolon.story_import import import_csv_directory
from tests.conftest import ClientFactory
from tests.fakes import FakeEmbedder, FakeJudge, FakeLLM, FakeReranker, context_of, system_of
from tests.stories import corpus_in
from tests.test_text_turn import send_turn, start_conversation

PARK_LINE = "公園で鳩と一緒に歌っていました"
ANSWER = ["<ja>公園でピーちゃんたちと歌ってたんです</ja><zh>我在公园和小皮它们唱歌</zh>"]
# Copied, not imported: the wording was verified against the real chat model (ticket 16),
# so an unverified rewording should fail here.
LAST_ROUND_NOTE = (
    "（这一轮不能再调用任何工具了，请现在就回答制作人。上面想起来的内容如果答不上他的问题，"
    "就像一时记不清那样自然地说，不要提到查找、工具、记录或剧情库。）"
)


def recall(query: str, id: str = "call_1") -> ToolCall:
    return ToolCall(id, "recall_story", {"query": query})


@pytest.fixture
async def embedder(settings: Settings, story: Path) -> FakeEmbedder:
    """The embedder the sample stories were imported with."""
    embedder = FakeEmbedder()
    async with corpus_in(settings.database_path, embedder) as corpus:
        characters = load_characters(settings.characters_path)
        await import_csv_directory(story, corpus, characters.values())
    return embedder


def tool_results(prompt: list[ChatMessage]) -> list[str]:
    return [m.content for m in prompt if m.role == "tool"]


async def test_the_reply_is_based_on_the_recalled_episode_and_its_place_in_the_catalog(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM([recall("公園で鳩と歌った日")], ANSWER)
    # Jev sees no need to retrieve; the model asks for the episode itself.
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "那次在公园唱歌是怎么回事？")

    assert [t.name for t in llm.tools[0]] == ["recall_story"]
    first, second = llm.prompts
    assert second[: len(first)] == first
    call, result = second[len(first) :]
    assert (call.role, call.tool_calls) == ("assistant", (recall("公園で鳩と歌った日"),))
    assert (result.role, result.tool_call_id) == ("tool", "call_1")
    # Where the episode sits in her catalog, the other episodes there, and what happened.
    assert "培育剧情 / W.I.N.G.編 /「公園の歌声」" in result.content
    assert "「決勝後コミュ」" in result.content
    assert f"真乃：{PARK_LINE}" in result.content
    assert "歌が好きなのか？" not in result.content  # the producer's choices never were

    assert events[-1]["type"] == "done"
    assert events[-1]["message"]["ja"] == "公園でピーちゃんたちと歌ってたんです"
    messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).json()
    assert [(m["role"], m["text"], m["ja"]) for m in messages] == [
        ("user", "那次在公园唱歌是怎么回事？", None),
        ("character", None, "公園でピーちゃんたちと歌ってたんです"),
    ]


async def test_the_last_tool_results_the_model_reads_tell_it_no_more_tools_are_coming(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM([recall("公園で鳩と歌った日")], ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "那次在公园唱歌是怎么回事？")

    [result] = tool_results(llm.prompts[-1])
    episode, note = result.rsplit("\n\n", 1)
    assert note == LAST_ROUND_NOTE
    assert f"真乃：{PARK_LINE}" in episode
    assert LAST_ROUND_NOTE not in episode


async def test_what_the_model_thought_goes_back_with_its_calls_within_the_turn_alone(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    thought = Reasoning({"reasoning_content": "制作人问的是公园那次"})
    llm = FakeLLM([thought, "<ja>えっと</ja><zh>嗯</zh>", recall("公園")], ANSWER, ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "公园那次呢？")
    await send_turn(client, conversation_id, "后来呢？")

    call = llm.prompts[1][-2]
    assert (call.content, call.tool_calls, call.reasoning) == (
        "<ja>えっと</ja><zh>嗯</zh>", (recall("公園"),), (thought,)
    )
    assert events[-1]["message"]["segments"][0] == {"ja": "えっと", "zh": "嗯"}
    # The next turn reads the conversation as recorded, which has her words alone.
    assert not any(m.reasoning for m in llm.prompts[-1])
    messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).text
    assert "制作人问的是公园那次" not in messages


async def test_a_character_only_recalls_episodes_she_is_cast_in(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM([recall("公園の鳩")], ANSWER), FakeReranker(PARK_LINE, others=0.01)
    client = make_client(llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client, "kogane")

    await send_turn(client, conversation_id, "那次在公园和鸽子唱歌是怎么回事？")

    # Mano's park episode is never a candidate for Kogane, and nothing of hers is about it.
    assert not any(PARK_LINE in doc for _, docs in reranker.calls for doc in docs)
    [result] = tool_results(llm.prompts[-1])
    assert PARK_LINE not in result
    assert "没有想起" in result


async def test_the_tool_is_not_offered_without_retrieval(make_client: ClientFactory):
    llm = FakeLLM(ANSWER)
    client = make_client(llm)
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "那次在公园唱歌是怎么回事？")

    assert llm.tools == [[]]


async def test_what_she_says_before_calling_the_tool_is_part_of_the_reply(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(["<ja>えっと……</ja><zh>嗯……</zh>", recall("公園")], ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "公园那次呢？")

    assert llm.prompts[1][-2].content == "<ja>えっと……</ja><zh>嗯……</zh>"
    assert events[-1]["message"]["segments"] == [
        {"ja": "えっと……", "zh": "嗯……"},
        {"ja": "公園でピーちゃんたちと歌ってたんです", "zh": "我在公园和小皮它们唱歌"},
    ]


async def test_tool_calls_stop_after_a_few_rounds_and_a_call_made_then_goes_unanswered(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    rounds = [[recall("公園", f"call_{i}")] for i in range(MAX_TOOL_ROUNDS)]
    llm = FakeLLM(*rounds, [*ANSWER, recall("公園", "call_last")])
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "公园那次呢？")

    # The last round offers the same tools, as some models drop what they thought once the
    # tools change (Claude 5.5), but forbids calling them, so the model has to answer; a
    # call it makes all the same goes unanswered.
    assert all(tools == llm.tools[0] for tools in llm.tools)
    assert [len(tools) for tools in llm.tools] == [1] * (MAX_TOOL_ROUNDS + 1)
    assert llm.forbidden == [False] * MAX_TOOL_ROUNDS + [True]
    assert len(llm.prompts) == MAX_TOOL_ROUNDS + 1
    assert events[-1]["type"] == "done"
    assert events[-1]["message"]["ja"] == "公園でピーちゃんたちと歌ってたんです"


async def test_a_failed_or_malformed_call_is_answered_and_the_turn_goes_on(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(
        [
            ToolCall("call_1", "recall_story", None),
            ToolCall("call_2", "take_selfie", {}),
            recall("公園", "call_3"),
        ],
        ANSWER,
    )
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)
    embedder.down = True

    events = await send_turn(client, conversation_id, "公园那次呢？")

    malformed, unknown, failed = tool_results(llm.prompts[-1])
    assert "query" in malformed
    assert "take_selfie" in unknown
    assert "暂时想不起来" in failed
    assert events[-1]["type"] == "done"
    # Every result of the last round says no more tools are coming, the failed ones too.
    assert all(r.endswith(LAST_ROUND_NOTE) for r in (malformed, unknown, failed))


async def test_the_note_is_never_recorded_or_seen_again_in_later_turns(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM([recall("公園")], ANSWER, ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "公园那次呢？")
    await send_turn(client, conversation_id, "后来呢？")

    assert events[-1]["message"]["segments"] == [
        {"ja": "公園でピーちゃんたちと歌ってたんです", "zh": "我在公园和小皮它们唱歌"}
    ]
    # The later turn, without tool calls, asks the model once, with no note.
    assert len(llm.prompts) == 3
    assert not any(LAST_ROUND_NOTE in m.content for m in llm.prompts[-1])
    messages = (await client.get(f"/api/conversations/{conversation_id}/messages")).text
    assert "不能再调用任何工具" not in messages


async def test_with_more_tool_rounds_only_the_last_results_carry_the_note(
    make_client: ClientFactory, embedder: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(engine, "MAX_TOOL_ROUNDS", 2)
    llm = FakeLLM([recall("公園", "call_1")], [recall("公園の鳩", "call_2")], ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=FakeReranker(PARK_LINE)
    )
    conversation_id = await start_conversation(client)

    await send_turn(client, conversation_id, "公园那次呢？")

    assert [len(tools) for tools in llm.tools] == [1, 1, 1]
    assert llm.forbidden == [False, False, True]
    earlier, last = tool_results(llm.prompts[-1])
    assert LAST_ROUND_NOTE not in earlier
    assert last.endswith(LAST_ROUND_NOTE)


async def test_when_jev_sees_a_recall_question_the_episode_is_recalled_before_the_model_answers(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm, reranker = FakeLLM(ANSWER), FakeReranker(PARK_LINE)
    client = make_client(llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "那次在公园唱歌是怎么回事？")

    # One model call: the episode is already in the prompt, and the tool, offered as on every
    # turn so the prompt opens the same, is not to be called again.
    [prompt] = llm.prompts
    assert llm.tools == [[RECALL_STORY_TOOL]]
    assert llm.forbidden == [True]
    system = system_of(prompt)
    assert "# 回顾剧情：那次在公园唱歌是怎么回事？" in system
    assert "培育剧情 / W.I.N.G.編 /「公園の歌声」" in system
    assert f"真乃：{PARK_LINE}" in system
    # Recalling takes the place of the turn's general retrieval.
    assert "# 回忆与资料" not in system
    assert [query for query, _ in reranker.calls] == ["那次在公园唱歌是怎么回事？"]
    assert "（W.I.N.G.編中你出场的剧情依次是：" in system and "「決勝後コミュ」" in system
    # Embedded once, as the turn began, for recalling and writing down alike.
    assert embedder.queries == ["那次在公园唱歌是怎么回事？"]
    assert events[-1]["type"] == "done"


async def test_with_the_reranker_down_the_nearest_episode_is_recalled(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    """By the judge model's recall and the recall-story tool alike, with its place in the
    catalog."""
    reranker = FakeReranker()
    reranker.down = True
    llm = FakeLLM(ANSWER, [recall("公園で鳩と歌った日")], ANSWER)
    judge = FakeJudge(answer="recall")
    client = make_client(llm, retrieval_judge=judge, embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client)

    events = await send_turn(client, conversation_id, "公園の鳩と歌ったの、覚えてる？")
    judged = system_of(llm.prompts[-1])
    judge.answer = "nothing"
    await send_turn(client, conversation_id, "那次是怎么回事？")
    [called] = tool_results(llm.prompts[-1])

    assert events[-1]["type"] == "done"
    assert len(reranker.calls) == 2  # each asked, each failing
    for recalled in [judged[judged.index("# 回顾剧情") :], called]:
        assert recalled.count("\n## ") == 1  # the nearest episode alone
        assert "培育剧情 / W.I.N.G.編 /「公園の歌声」" in recalled
        assert "（W.I.N.G.編中你出场的剧情依次是：" in recalled and "「決勝後コミュ」" in recalled
        assert f"真乃：{PARK_LINE}" in recalled


PARK_REPLY = ["<ja>[happy] 公園で鳩さんと歌った日ですね</ja><zh>[happily] 是和鸽子一起在公园唱歌的那天吧</zh>"]
AFTER_PARK = "是和鸽子一起在公园唱歌的那天吧\n那次后来怎么样了？"


async def test_a_recall_question_after_her_reply_is_recalled_with_both(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    """「那次后来怎么样了」: her reply before it says which time 「那次」 is."""
    jev, reranker = FakeJudge(answer="nothing"), FakeReranker(PARK_LINE)
    llm = FakeLLM(PARK_REPLY, ANSWER)
    client = make_client(llm, retrieval_judge=jev, embedder=embedder, reranker=reranker)
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "还记得那天吗？")
    jev.answer = "recall"

    await send_turn(client, conversation_id, "那次后来怎么样了？")

    assert [query for query, _ in reranker.calls] == [AFTER_PARK]
    assert AFTER_PARK in embedder.queries
    assert "那次后来怎么样了？" not in embedder.queries
    system = context_of(llm.prompts[-1])
    # She is reminded of what the user asked; her reply is right there in the conversation.
    assert "# 回顾剧情：那次后来怎么样了？\n" in system
    assert f"真乃：{PARK_LINE}" in system


async def test_the_tool_looks_up_what_the_model_wrote_alone(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    reranker = FakeReranker(PARK_LINE)
    llm = FakeLLM(PARK_REPLY, [recall("公園で鳩と歌った日")], ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=reranker
    )
    conversation_id = await start_conversation(client)
    await send_turn(client, conversation_id, "还记得那天吗？")

    await send_turn(client, conversation_id, "那次后来怎么样了？")

    assert [query for query, _ in reranker.calls] == ["公園で鳩と歌った日"]
    assert "公園で鳩と歌った日" in embedder.queries
    [result] = tool_results(llm.prompts[-1])
    assert f"真乃：{PARK_LINE}" in result


async def test_a_recall_question_about_someone_elses_episode_recalls_nothing(
    make_client: ClientFactory, embedder: FakeEmbedder
):
    llm = FakeLLM(ANSWER)
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="recall"), embedder=embedder,
        reranker=FakeReranker(PARK_LINE, others=0.01),
    )
    conversation_id = await start_conversation(client, "kogane")

    await send_turn(client, conversation_id, "那次你在公园和鸽子一起唱歌是怎么回事？")

    system = system_of(llm.prompts[0])
    assert PARK_LINE not in system
    assert "没有想起" in system
