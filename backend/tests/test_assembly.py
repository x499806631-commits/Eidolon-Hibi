"""The assembly root: what `build` turns on, given which outside services are at hand.

Retrieval needs the judge model, the embedder and the reranker; memory is on only with
retrieval, since only retrieval finds memories again. Turns are taken through the built
engine.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import asdict, replace
from datetime import UTC, datetime

import httpx
import pytest

from eidolon.assembly import Backend
from eidolon.config import (
    CloudflareJudgeSettings,
    JevSettings,
    JudgeSettings,
    LLMSettings,
    Settings,
)
from eidolon.emotion import SaidSegment, SegmentToJudge
from eidolon.judge import JudgeModel, open_judge
from eidolon.own_keys import BroughtKeys
from eidolon.retrieval import RECALL_STORY_TOOL
from tests.fakes import (
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeServiceClients,
    build_faked,
)
from tests.test_cloudflare_judge import URL, envelope
from tests.test_retrieval_services import Handler, client

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
REPLY = ["<ja>はいっ</ja><zh>好的</zh>"]
CAT = "制作人家里养了一只叫小白的猫"
ALL = ("retrieval_judge", "embedder", "reranker")


class Services:
    """Fakes for every outside service retrieval and memory need."""

    def __init__(self) -> None:
        self.chat = FakeLLM(REPLY)
        self.background = FakeBackgroundLLM(json.dumps({"memories": [CAT]}, ensure_ascii=False))
        self.retrieval_judge = FakeJudge()
        self.embedder = FakeEmbedder()
        self.reranker = FakeReranker("制作人")


@pytest.fixture
async def backends() -> AsyncIterator[list[Backend]]:
    made: list[Backend] = []
    yield made
    for backend in made:
        await backend.aclose()


async def tell_mano(backend: Backend, text: str) -> tuple[str, list[dict]]:
    """Take one turn in a new conversation with Mano; returns its user and events."""
    user = await backend.store.create_user("producer")
    conversation = await backend.store.create_conversation(user.id, "mano")
    llms = backend.own_keys.for_turn(BroughtKeys()).llms
    return user.id, [asdict(e) async for e in backend.engine.turn(conversation, text, llms)]


async def test_with_every_service_turns_retrieve_and_remember(
    settings: Settings, backends: list[Backend]
):
    services = Services()
    backend = build_faked(
        settings, services.chat, background_llm=services.background,
        retrieval_judge=services.retrieval_judge, embedder=services.embedder, reranker=services.reranker,
        clock=lambda: NOW,
    )
    backends.append(backend)

    user_id, events = await tell_mano(backend, "我家的猫叫小白哦")

    assert events[-1]["type"] == "done"
    assert services.retrieval_judge.asked == [("櫻木真乃", "我家的猫叫小白哦")]
    assert services.chat.tools == [[RECALL_STORY_TOOL]]
    assert [m.text for m in await backend.memories.entries(user_id)] == [CAT]


@pytest.mark.parametrize("missing", ALL)
async def test_without_any_one_of_them_retrieval_and_memory_are_off(
    settings: Settings, backends: list[Backend], missing: str
):
    services = Services()
    given = {name: getattr(services, name) for name in ALL if name != missing}
    backend = build_faked(
        settings, services.chat, background_llm=services.background,
        clock=lambda: NOW, **given,
    )
    backends.append(backend)

    user_id, events = await tell_mano(backend, "我家的猫叫小白哦")

    assert events[-1]["type"] == "done"
    assert services.retrieval_judge.asked == []
    assert services.chat.tools == [[]]  # nothing to recall episodes with
    assert await backend.memories.entries(user_id) == []  # listing still works
    assert services.background.prompts == []  # nothing was written down


def answering_every_question(requests: list[dict]) -> Handler:
    """Workers AI answering whatever it is asked: no to each yes-or-no question, the first
    option of each choice."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append({"url": str(request.url), **body})
        answers = {}
        for name, question in body["questions"].items():
            if question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.1}
            else:
                first, *others = question["criteria"]
                answers[name] = {"type": "choice", "choice": first, "probabilities": {
                    first: 0.9, **{o: 0.1 / len(others) for o in others}
                }}
        return httpx.Response(200, json=envelope(answers))

    return handle


async def test_cloudflare_chosen_judges_both_what_turns_need_and_her_emotions(
    settings: Settings, backends: list[Backend]
):
    requests: list[dict] = []

    class CloudflareClients(FakeServiceClients):
        def judge_model(self, settings: JudgeSettings) -> JudgeModel:
            return open_judge(settings, client(answering_every_question(requests)))

    services = Services()
    backend = build_faked(
        replace(
            settings, judge=CloudflareJudgeSettings("cf-token", "acc-id"), affinity_and_gifts=False
        ),
        services.chat, background_llm=services.background, clients=CloudflareClients(),
        embedder=services.embedder, reranker=services.reranker, clock=lambda: NOW,
    )
    backends.append(backend)

    await tell_mano(backend, "早上好")
    lease = backend.config.lease()  # as a call takes it, connecting
    assert lease.keys.emotions is not None
    said = SegmentToJudge(backend.characters["mano"], "早上好", [], SaidSegment("おはよう！", "早上好！"))
    await lease.keys.emotions.message(1, 0, said)
    lease.release()

    assert [(r["url"], set(r["questions"])) for r in requests] == [
        (URL, {"needs_retrieval", "recall", "about_producer"}), (URL, {"emotion", "nod"})
    ]


async def test_the_judge_model_made_judges_both_on_the_thresholds_in_effect(
    settings: Settings, backends: list[Backend]
):
    services = Services()
    clients = FakeServiceClients(FakeLLM(REPLY, REPLY))
    clients.judge.retrieval = {"needs_retrieval": 0.1, "recall": 0.6, "about_producer": 0.1}
    clients.judge.emotion = {"开心": 0.6, "平静": 0.4}
    backend = build_faked(
        replace(settings, judge=JevSettings("jev-key")), clients=clients,
        embedder=services.embedder, reranker=services.reranker, clock=lambda: NOW,
    )
    backends.append(backend)

    async def felt() -> str:
        """The emotion the next call hears in a segment."""
        lease = backend.config.lease()
        assert lease.keys.emotions is not None
        said = SegmentToJudge(backend.characters["mano"], "早上好", [], SaidSegment("わーい！", "耶！"))
        message = await lease.keys.emotions.message(1, 0, said)
        lease.release()
        assert message is not None
        return message["emotion"]

    await tell_mano(backend, "那次比赛怎么样")  # Jev's 0.5: recalled
    before = await felt()
    await backend.config.save("judge_thresholds", {"recall": 0.7, "emotion": 0.7})
    await tell_mano(backend, "那次比赛怎么样")  # 0.7: not recalled
    after = await felt()

    # A turn that recalled forbids the tool its model is offered all the same.
    assert clients.llm.forbidden == [True, False]
    assert (before, after) == ("happy", "calm")
    assert clients.judge.asked == [
        ("jev-key", "turn"), ("jev-key", "emotion"), ("jev-key", "turn"), ("jev-key", "emotion")
    ]


def test_the_background_model_is_the_chat_model_unless_configured_apart(settings: Settings):
    chat = LLMSettings("https://chat.example/v1", "chat-key", "chat-model")
    background = LLMSettings("https://chat.example/v1", "chat-key", "cheap-model")

    assert Settings(settings.database_path, settings.characters_path, chat).background_llm == chat
    assert (
        Settings(settings.database_path, settings.characters_path, chat, background).background_llm
        == background
    )
