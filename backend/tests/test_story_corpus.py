"""Searching the story corpus: its vectors are kept in memory, read once, and read anew
once another process (a story sync or import, `eidolon reembed`) has changed the corpus;
searches go on with the vectors held meanwhile."""

import asyncio
import logging
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from eidolon.corpus import Episode, Line, StoryCorpus
from eidolon.data_access import Database
from eidolon.embedding import nearest
from tests.fakes import FakeEmbedder
from tests.stories import corpus_in

PARK = Episode(
    "produce_events/100100101", "produce", "W.I.N.G.編", "第1話", frozenset({"mano"}),
    (Line("真乃", "公園で鳩と一緒に歌っていました"),),
)
WON = Episode(
    "produce_events/100100106", "produce", "W.I.N.G.編", "第6話", frozenset({"mano"}),
    (Line("真乃", "優勝、したんですねっ"),),
)
NAGASAKI = Episode(
    "produce_events/201100401", "produce", "恋鐘のカード", "第1話", frozenset({"kogane", "mano"}),
    (Line("恋鐘", "長崎のちゃんぽん、ばりうまかよ！写真ば撮ろう、公園で"),),
)
HIORI = Episode(
    "game_event_communications/400100401", "event", "イベント", "第1話", frozenset({"hiori"}),
    (Line("灯織", "公園で写真の撮影、頑張ります"),),
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "eidolon.db"


@pytest.fixture
def loads(caplog: pytest.LogCaptureFixture) -> Callable[[], int]:
    """How many times the vectors have been loaded so far."""
    caplog.set_level(logging.INFO, logger="eidolon.corpus")
    return lambda: sum("story vectors loaded" in r.getMessage() for r in caplog.records)


@pytest.fixture
async def db(database: Path) -> AsyncIterator[Database]:
    """The server's data access layer."""
    db = Database(database)
    yield db
    await db.close()


@pytest.fixture
async def corpus(db: Database) -> AsyncIterator[StoryCorpus]:
    """The corpus as the server searches it."""
    corpus = StoryCorpus(db, FakeEmbedder())
    yield corpus
    await corpus.aclose()


async def saved(database: Path, *episodes: Episode, embedder: FakeEmbedder | None = None) -> None:
    """`episodes` saved by another process, as a story sync saves them."""
    async with corpus_in(database, embedder or FakeEmbedder()) as other:
        await other.save(list(episodes))


async def found(corpus: StoryCorpus, query: str, character_id: str, limit: int = 5) -> list[str]:
    return [p.episode_id for p in await corpus.search(query, character_id, limit)]


async def until(done: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not done():
            await asyncio.sleep(0.01)


async def test_a_character_finds_only_the_passages_of_the_episodes_she_is_cast_in(
    database: Path, corpus: StoryCorpus
):
    await saved(database, PARK, WON, NAGASAKI, HIORI)

    assert await found(corpus, "公園の鳩", "mano", 1) == [PARK.id]
    assert set(await found(corpus, "公園で写真", "mano")) == {PARK.id, WON.id, NAGASAKI.id}
    assert await found(corpus, "公園で写真", "kogane") == [NAGASAKI.id]
    assert await found(corpus, "公園", "hazuki") == []
    [passage] = await corpus.search("優勝", "mano", 1)
    assert passage.text == "W.I.N.G.編「第6話」\n真乃：優勝、したんですねっ"


async def test_the_scores_are_the_cosine_similarity_of_the_stored_vectors(
    database: Path, corpus: StoryCorpus
):
    await saved(database, PARK, WON, NAGASAKI, HIORI)
    query = await FakeEmbedder().embed_query("公園で鳩の写真")
    with sqlite3.connect(database) as stored:
        rows = stored.execute(
            "SELECT p.episode_id, p.embedding FROM episode_passages p"
            " JOIN episode_cast c ON c.episode_id = p.episode_id WHERE c.character_id = 'mano'"
        ).fetchall()
    stored.close()
    expected = [(rows[i][0], score) for i, score in nearest([r[1] for r in rows], query, 3)]

    best = await corpus.search_by_vector(query, "mano", 3)

    assert [p.episode_id for p in best] == [episode_id for episode_id, _ in expected]
    assert [p.score for p in best] == pytest.approx([score for _, score in expected], abs=1e-6)


async def test_the_vectors_are_loaded_once_for_every_search(
    database: Path, corpus: StoryCorpus, loads: Callable[[], int]
):
    await saved(database, PARK, NAGASAKI)

    await asyncio.gather(*(found(corpus, "鳩", "mano") for _ in range(3)))
    await found(corpus, "長崎", "kogane")

    assert loads() == 1


async def test_loading_them_as_the_server_starts_spares_the_first_search(
    database: Path, corpus: StoryCorpus, loads: Callable[[], int]
):
    await saved(database, PARK)

    await corpus.load()
    assert loads() == 1
    assert await found(corpus, "鳩", "mano") == [PARK.id]
    assert loads() == 1


async def test_episodes_another_process_saves_are_found_once_a_search_has_noticed(
    database: Path, corpus: StoryCorpus, loads: Callable[[], int]
):
    await saved(database, PARK)
    assert await found(corpus, "優勝", "mano") == [PARK.id]

    await saved(database, WON)
    # The search that notices goes on with the vectors held; those after it find the episode.
    assert await found(corpus, "優勝", "mano", 1) == [PARK.id]
    await until(lambda: loads() == 2)

    assert await found(corpus, "優勝", "mano", 1) == [WON.id]


async def test_an_episode_another_process_removes_is_never_found_again(
    database: Path, corpus: StoryCorpus, loads: Callable[[], int]
):
    await saved(database, PARK, WON)
    assert await found(corpus, "鳩", "mano", 1) == [PARK.id]

    async with corpus_in(database, FakeEmbedder()) as other:
        assert await other.remove(PARK.id)

    # Not even by the search that notices, which still holds its vectors.
    assert await found(corpus, "鳩", "mano") == [WON.id]
    await until(lambda: loads() == 2)
    assert await found(corpus, "鳩", "mano") == [WON.id]


async def test_removing_an_episode_not_in_the_corpus_changes_nothing(
    database: Path, corpus: StoryCorpus, loads: Callable[[], int]
):
    await saved(database, PARK)
    await found(corpus, "鳩", "mano")

    async with corpus_in(database, FakeEmbedder()) as other:
        assert not await other.remove("produce_events/999999999")
    await found(corpus, "鳩", "mano")
    await asyncio.sleep(0.05)

    assert loads() == 1


async def test_passages_another_process_embeds_again_are_found_once_noticed(
    database: Path, corpus: StoryCorpus, loads: Callable[[], int]
):
    # Embedded with another model, as before the embedding model changed.
    await saved(database, PARK, embedder=FakeEmbedder("old-model"))
    assert await found(corpus, "鳩", "mano") == []

    async with corpus_in(database, FakeEmbedder()) as other:
        assert await other.embed_missing() == 1
    await found(corpus, "鳩", "mano")
    await until(lambda: loads() == 2)

    assert await found(corpus, "鳩", "mano") == [PARK.id]


async def test_searches_go_on_while_the_vectors_are_loaded_anew(
    database: Path, db: Database, corpus: StoryCorpus, monkeypatch: pytest.MonkeyPatch,
    loads: Callable[[], int],
):
    await saved(database, PARK)
    await found(corpus, "優勝", "mano")
    held_up = asyncio.Event()
    read = cast(Callable[..., Awaitable[Any]], db.read)

    async def slow_read(body: Callable[..., Any], *args: Any) -> Any:
        if body.__name__ == "_vectors":
            await held_up.wait()
        return await read(body, *args)

    monkeypatch.setattr(db, "read", slow_read)
    await saved(database, WON)

    async with asyncio.timeout(5):
        assert await found(corpus, "優勝", "mano", 1) == [PARK.id]
        assert await found(corpus, "優勝", "mano", 1) == [PARK.id]
    assert loads() == 1

    held_up.set()
    await until(lambda: loads() == 2)
    assert await found(corpus, "優勝", "mano", 1) == [WON.id]


async def test_a_load_that_fails_is_logged_and_tried_again_by_the_next_search(
    database: Path, db: Database, corpus: StoryCorpus, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    await saved(database, PARK)
    read = cast(Callable[..., Awaitable[Any]], db.read)
    failures = [sqlite3.OperationalError("disk I/O error")]

    async def failing_read(body: Callable[..., Any], *args: Any) -> Any:
        if body.__name__ == "_vectors" and failures:
            raise failures.pop()
        return await read(body, *args)

    monkeypatch.setattr(db, "read", failing_read)

    with pytest.raises(sqlite3.OperationalError):
        await found(corpus, "鳩", "mano")
    assert await found(corpus, "鳩", "mano") == [PARK.id]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [
        "loading the story vectors failed"
    ]


async def test_closing_stops_a_load_under_way(
    database: Path, db: Database, monkeypatch: pytest.MonkeyPatch
):
    await saved(database, PARK)
    corpus = StoryCorpus(db, FakeEmbedder())
    started = asyncio.Event()
    read = cast(Callable[..., Awaitable[Any]], db.read)

    async def endless_read(body: Callable[..., Any], *args: Any) -> Any:
        if body.__name__ == "_vectors":
            started.set()
            await asyncio.Event().wait()
        return await read(body, *args)

    monkeypatch.setattr(db, "read", endless_read)
    loading = asyncio.create_task(corpus.load())
    await started.wait()

    async with asyncio.timeout(5):
        await corpus.aclose()
        with pytest.raises(asyncio.CancelledError):
            await loading
