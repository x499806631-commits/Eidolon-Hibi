"""`eidolon reembed`: after the embedding service, model or dimensions change, the story
corpus, character facts, the work facts (songs, releases and lives) and every user's
memories are embedded again from the text already in the database (and the data file), and
only then are the old model's vectors dropped."""

import asyncio
import logging
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from eidolon.characters import Character, load_characters
from eidolon.corpus import Episode, Line
from eidolon.data_access import Database
from eidolon.embedding import Document, EmbeddingError
from eidolon.facts import CharacterFacts
from eidolon.memory import Memories
from eidolon.reembed import reembed
from eidolon.config import LLMSettings, Settings
from tests.conftest import Admin, CharacterConfig, store_in
from tests.fakes import (
    FakeBackgroundLLM,
    FakeEmbedder,
    FakeJudge,
    FakeLLM,
    FakeReranker,
    FakeSpeech,
    build_faked,
)
from tests.stories import corpus_in
from tests.test_memories import NOW, World, tell, writing_with
from tests.test_shared_memory import CAT, TRIP, memorized
from tests.test_world_retrieval import ENTRIES as WORK_FACTS
from tests.test_world_setting import write_world

OLD, NEW = "old-model", "new-model@768"
FACTS = "# 档案\n喜欢鸟。\n\n# 人物关系\n## 風野灯織\n同组合的伙伴。\n"
EPISODES = [
    Episode(
        f"produce_events/10010010{i}", "培育剧情", "W.I.N.G.編", f"第{i}話", frozenset({"mano"}),
        (Line("真乃", "公園の鳩" if i == 1 else f"台詞 {i}"),),
    )
    for i in range(1, 6)
]


class FailingEmbedder(FakeEmbedder):
    """Goes down after embedding `batches` batches of documents."""

    def __init__(self, model: str, batches: int) -> None:
        super().__init__(model)
        self._batches = batches

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
        if self._batches == 0:
            raise EmbeddingError("fake embedding service went down")
        self._batches -= 1
        return await super().embed_documents(documents)


class Deployment:
    """A database whose story corpus, character facts and memories were all embedded with
    the old model."""

    def __init__(self, tmp_path: Path) -> None:
        self.database = tmp_path / "eidolon.db"
        config = CharacterConfig(tmp_path / "characters")
        config.add("mano", "櫻木真乃", "sheet", speakers=["真乃"], facts=FACTS)
        config.add("kogane", "月岡恋鐘", "sheet", speakers=["恋鐘"])
        self.characters_path = config.path
        self.characters = load_characters(config.path)

    async def embed_everything_with(self, model: str) -> None:
        embedder = FakeEmbedder(model)
        async with corpus_in(self.database, embedder) as corpus:
            await corpus.save(EPISODES)
        db = Database(self.database)
        await CharacterFacts(db, embedder, [self.mano]).embed_missing()
        await db.close()
        db = Database(self.database)
        try:
            world = await World.made(db, self.characters)
            memories = writing_with(
                Memories(
                    db, self.characters, lambda: NOW, embedder=embedder,
                    reranker=FakeReranker(),
                ),
                FakeBackgroundLLM(memorized(CAT, TRIP)),
            )
            await tell(memories, world, await world.conversation("mano"), "我家的猫叫小白，下个月去北海道")
            await memories.entries(world.producer)  # written down, and embedded
            await memories.aclose()
        finally:
            await db.close()

    def settings(self) -> Settings:
        return Settings(
            database_path=self.database,
            characters_path=self.characters_path,
            chat_llm=LLMSettings("http://llm.invalid/v1", "k", "m"),
        )

    @property
    def mano(self) -> Character:
        return self.characters["mano"]

    def vectors(self, table: str, scope: str | None = None) -> dict[str, int]:
        """How many vectors `table` holds from each model (of the scopes matching `scope`,
        a LIKE pattern, if given)."""
        where, params = ("WHERE scope LIKE ?", (scope,)) if scope is not None else ("", ())
        with sqlite3.connect(self.database) as db:
            rows = db.execute(
                f"SELECT model, count(*) FROM {table} {where} GROUP BY model", params
            ).fetchall()
        db.close()
        return dict(rows)


class ClosableEmbedder(FakeEmbedder):
    async def aclose(self) -> None:
        pass


@pytest.fixture
def deployment(tmp_path: Path) -> Deployment:
    return Deployment(tmp_path)


async def test_everything_is_embedded_again_and_then_the_old_vectors_dropped(
    deployment: Deployment,
):
    await deployment.embed_everything_with(OLD)
    embedder = FakeEmbedder(NEW)

    report = await reembed(deployment.database, deployment.characters.values(), embedder)

    assert report.counts() == {"剧情库": 5, "角色资料": 2, "作品资料": 0, "记忆": 2}
    assert report.forgotten == 4  # 2 facts and 2 memories; each passage kept only one
    assert deployment.vectors("episode_passages") == {NEW: 5}
    assert deployment.vectors("embedded_texts", "character:%") == {NEW: 2}
    assert deployment.vectors("memory_vectors") == {NEW: 2}
    # Each passage is re-embedded from its text, under its title.
    assert "W.I.N.G.編「第1話」" in embedder.titles
    async with corpus_in(deployment.database, embedder) as corpus:
        [park] = await corpus.search("鳩", "mano", 1)
    assert park.episode_id == "produce_events/100100101"


async def test_a_second_run_has_nothing_to_do(deployment: Deployment):
    await deployment.embed_everything_with(OLD)
    await reembed(deployment.database, deployment.characters.values(), FakeEmbedder(NEW))
    embedder = FakeEmbedder(NEW)

    report = await reembed(deployment.database, deployment.characters.values(), embedder)

    assert report.counts() == {"剧情库": 0, "角色资料": 0, "作品资料": 0, "记忆": 0}
    assert report.forgotten == 0
    assert embedder.documents == []


async def test_an_interrupted_run_keeps_what_it_finished_and_drops_nothing(
    deployment: Deployment,
):
    await deployment.embed_everything_with(OLD)
    failing = FailingEmbedder(NEW, batches=2)

    with pytest.raises(EmbeddingError):
        await reembed(deployment.database, deployment.characters.values(), failing, batch=2)

    assert deployment.vectors("episode_passages") == {NEW: 4, OLD: 1}
    assert deployment.vectors("embedded_texts", "character:%") == {OLD: 2}  # not reached, nothing dropped
    assert deployment.vectors("memory_vectors") == {OLD: 2}

    embedder = FakeEmbedder(NEW)
    report = await reembed(deployment.database, deployment.characters.values(), embedder, batch=2)

    assert report.counts()["剧情库"] == 1  # only what the first run did not finish
    assert len([t for t in embedder.titles if t is not None and "「" in t]) == 1
    assert deployment.vectors("episode_passages") == {NEW: 5}
    assert deployment.vectors("embedded_texts", "character:%") == {NEW: 2}
    assert deployment.vectors("memory_vectors") == {NEW: 2}


async def test_deleted_memories_are_not_embedded(deployment: Deployment):
    await deployment.embed_everything_with(OLD)
    async with store_in(deployment.database) as store:
        producer = await store.first_user_named("producer")
    assert producer is not None
    db = Database(deployment.database)
    memories = Memories(db, deployment.characters, lambda: NOW)
    [trip, cat] = await memories.entries(producer.id)
    await memories.delete(cat.user_id, cat.id)
    await memories.aclose()
    await db.close()
    embedder = FakeEmbedder(NEW)

    report = await reembed(deployment.database, deployment.characters.values(), embedder)

    assert report.counts()["记忆"] == 1
    assert CAT not in embedder.documents and TRIP in embedder.documents
    assert deployment.vectors("memory_vectors") == {NEW: 1}


async def test_the_server_warns_of_passages_the_current_model_cannot_find(
    deployment: Deployment, caplog: pytest.LogCaptureFixture
):
    await deployment.embed_everything_with(OLD)

    with caplog.at_level(logging.WARNING, logger="eidolon.assembly"):
        backend = build_faked(
            deployment.settings(), FakeLLM([]), background_llm=FakeBackgroundLLM(),
            retrieval_judge=FakeJudge(), embedder=FakeEmbedder(NEW), reranker=FakeReranker(),
            speech=FakeSpeech(),
        )
    await backend.aclose()

    assert "5" in caplog.text and "eidolon reembed" in caplog.text


async def test_the_server_does_not_warn_when_every_passage_is_found(
    deployment: Deployment, caplog: pytest.LogCaptureFixture
):
    await deployment.embed_everything_with(OLD)

    with caplog.at_level(logging.WARNING, logger="eidolon.assembly"):
        backend = build_faked(
            deployment.settings(), FakeLLM([]), background_llm=FakeBackgroundLLM(),
            retrieval_judge=FakeJudge(), embedder=FakeEmbedder(OLD), reranker=FakeReranker(),
            speech=FakeSpeech(),
        )
    await backend.aclose()

    assert "eidolon reembed" not in caplog.text


def test_the_command_needs_the_embedding_service(
    admin: Admin, characters: CharacterConfig, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    err = admin.fails("reembed", "--characters", str(characters.path))

    assert "SILICONFLOW_API_KEY" in err


def test_the_command_reports_what_it_did(
    deployment: Deployment, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    asyncio.run(deployment.embed_everything_with(OLD))  # the command runs its own event loop
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr("eidolon.embedding.open_embedder", lambda *_: ClosableEmbedder(NEW))
    admin = Admin(deployment.database, capsys)

    out = admin.run("reembed", "--characters", str(deployment.characters_path))

    assert "剧情库：新向量化 5 段" in out
    assert "角色资料：新向量化 2 条" in out
    assert "作品资料：新向量化 0 条" in out  # no world setting
    assert "记忆：新向量化 2 条" in out
    assert "删除了其他模型的 4 个向量" in out


def test_the_command_fails_without_dropping_anything(
    deployment: Deployment, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    asyncio.run(deployment.embed_everything_with(OLD))  # the command runs its own event loop
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    down = ClosableEmbedder(NEW)
    down.down = True
    monkeypatch.setattr("eidolon.embedding.open_embedder", lambda *_: down)
    admin = Admin(deployment.database, capsys)

    err = admin.fails("reembed", "--characters", str(deployment.characters_path))

    assert "重新运行即可继续" in err
    assert deployment.vectors("embedded_texts", "character:%") == {OLD: 2}


async def test_an_import_meanwhile_is_not_overwritten(deployment: Deployment):
    await deployment.embed_everything_with(OLD)
    rewritten = Episode(
        EPISODES[0].id, "培育剧情", "W.I.N.G.編", "第1話", frozenset({"mano"}),
        (Line("真乃", "優勝しました"),),
    )

    class ImportingMeanwhile(FakeEmbedder):
        """An import of a rewritten episode lands while the first batch is being embedded."""

        async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
            if not self.documents:
                async with corpus_in(deployment.database, FakeEmbedder(NEW)) as importer:
                    await importer.save([rewritten])
            return await super().embed_documents(documents)

    embedder = ImportingMeanwhile(NEW)
    await reembed(deployment.database, deployment.characters.values(), embedder)

    async with corpus_in(deployment.database, embedder) as corpus:
        [found] = await corpus.search("優勝", "mano", 1)
    assert found.episode_id == EPISODES[0].id and "優勝しました" in found.text
    assert deployment.vectors("episode_passages") == {NEW: 5}


def test_the_command_reembeds_the_work_facts_beside_the_characters(
    deployment: Deployment,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    write_world(CharacterConfig(tmp_path / "characters"))
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr("eidolon.embedding.open_embedder", lambda *_: ClosableEmbedder(NEW))
    admin = Admin(deployment.database, capsys)

    out = admin.run("reembed", "--characters", str(deployment.characters_path))

    assert f"作品资料：新向量化 {WORK_FACTS} 条" in out
