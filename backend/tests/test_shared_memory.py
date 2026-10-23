"""Shared memory in text turns, driven through the text turn API: what the user tells one
character reaches another's prompt as hearsay, and Jev's judgement decides whether a
turn finds memories at all. The memory rules themselves are tested on the memories
module (test_memories)."""

import json

import httpx
import pytest

from eidolon.config import LLMSettings, Settings
from tests.conftest import ClientFactory, everyone_hears
from tests.fakes import (
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    context_of,
)
from tests.test_text_turn import send_turn, start_conversation

REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]
CAT = "制作人家里养了一只叫小白的猫"
TRIP = "制作人10月3日要去北海道出差"


def memorized(*texts: str) -> str:
    return json.dumps({"memories": list(texts)}, ensure_ascii=False)


def memories_section(system: str) -> str:
    return system[system.index("# 关于制作人的记忆") :] if "# 关于制作人的记忆" in system else ""


async def turn(client: httpx.AsyncClient, conversation_id: str, text: str) -> None:
    events = await send_turn(client, conversation_id, text)
    assert events[-1]["type"] == "done", events[-1]


async def test_what_the_user_told_mano_reaches_kogane_as_hearsay(make_client: ClientFactory):
    llm, background = FakeLLM(REPLY, REPLY), FakeBackgroundLLM(memorized(CAT))
    client = make_client(
        llm, retrieval_judge=FakeJudge(), embedder=FakeEmbedder(), reranker=FakeReranker("猫"),
        background_llm=background,
    )
    await everyone_hears(client)

    await turn(client, await start_conversation(client, "mano"), "我家的猫叫小白哦")
    await turn(client, await start_conversation(client, "kogane"), "你知道我家的猫吗？")

    kogane = llm.prompts[-1]
    memories = memories_section(context_of(kogane))
    assert CAT in memories
    line = next(line for line in memories.splitlines() if CAT in line)
    assert "櫻木真乃" in line and "转述" in line
    # A new conversation carries only what memory retrieves, not the earlier conversation.
    assert [m.role for m in kogane] == ["system", "system", "system", "user"]


async def test_talk_of_the_users_own_life_retrieves_memories_only(
    make_client: ClientFactory,
):
    llm, reranker = FakeLLM(REPLY, REPLY), FakeReranker("猫")
    jev = FakeJudge(answer="memories")
    client = make_client(
        llm, retrieval_judge=jev, embedder=FakeEmbedder(), reranker=reranker,
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    await everyone_hears(client)

    await turn(client, await start_conversation(client, "mano"), "我家的猫叫小白哦")
    await turn(client, await start_conversation(client, "kogane"), "我家的猫最近总是半夜叫")

    context = context_of(llm.prompts[-1])
    assert CAT in memories_section(context)
    assert "# 回忆与资料" not in context
    # The story corpus and facts searched while the judge model judged are not reranked.
    assert [docs for _, docs in reranker.calls] == [[CAT]]


async def test_small_talk_retrieves_no_memories(make_client: ClientFactory):
    llm, embedder, reranker = FakeLLM(REPLY, REPLY), FakeEmbedder(), FakeReranker()
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="nothing"), embedder=embedder, reranker=reranker,
        background_llm=FakeBackgroundLLM(memorized(CAT)),
    )

    await turn(client, await start_conversation(client, "mano"), "我家的猫叫小白哦")
    await turn(client, await start_conversation(client, "kogane"), "早上好")

    assert memories_section(context_of(llm.prompts[-1])) == ""
    assert reranker.calls == []
    # The judge model answering at once, nothing was looked up before it did: each turn is
    # embedded only to show the background model the memories nearest it.
    assert embedder.queries == ["我家的猫叫小白哦", "早上好"]
    assert embedder.documents == [CAT]  # embedded as it was written down, not when searched


async def test_memories_are_found_with_what_the_character_said_just_before(
    make_client: ClientFactory,
):
    llm = FakeLLM(["<ja>猫ちゃん、元気？</ja><zh>小猫还好吗？</zh>"], REPLY)
    embedder, reranker = FakeEmbedder(), FakeReranker("猫")
    client = make_client(
        llm, retrieval_judge=FakeJudge(answer="memories"), embedder=embedder,
        reranker=reranker, background_llm=FakeBackgroundLLM(memorized(CAT)),
    )
    conversation_id = await start_conversation(client, "mano")

    await turn(client, conversation_id, "我家的猫叫小白哦")
    await turn(client, conversation_id, "它昨晚又叫了一夜")

    assert embedder.queries[-1] == "小猫还好吗？\n它昨晚又叫了一夜"
    assert reranker.calls[-1][0] == "小猫还好吗？\n它昨晚又叫了一夜"


def test_the_background_model_is_configured_apart_from_the_chat_model(
    monkeypatch: pytest.MonkeyPatch,
):
    for name in ("BASE_URL", "API_KEY", "MODEL", "PROTOCOL", "MAX_TOKENS"):
        monkeypatch.delenv(f"CHAT_LLM_{name}", raising=False)
        monkeypatch.delenv(f"BACKGROUND_LLM_{name}", raising=False)
    monkeypatch.setenv("CHAT_LLM_BASE_URL", "https://chat.example/v1")
    monkeypatch.setenv("CHAT_LLM_API_KEY", "chat-key")
    monkeypatch.setenv("CHAT_LLM_MODEL", "chat-model")

    # Left unset, it is the chat model...
    settings = Settings.from_env()
    assert settings.background_llm == LLMSettings(
        "https://chat.example/v1", "chat-key", "chat-model"
    )

    # ...and each setting can be changed on its own, say a cheaper model on the same service.
    monkeypatch.setenv("BACKGROUND_LLM_MODEL", "cheap-model")
    settings = Settings.from_env()
    assert settings.background_llm == LLMSettings(
        "https://chat.example/v1", "chat-key", "cheap-model"
    )
    assert settings.chat_llm.model == "chat-model"
