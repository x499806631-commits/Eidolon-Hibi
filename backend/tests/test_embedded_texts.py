"""The contract of the embedded text set, which keeps the vectors of the character facts
and of the work facts. A scope is only a name to it but in forgetting other models'
vectors and moving the old tables' over, which are run once for a character's scope and
once for the work's; the rest is run for a character's."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from eidolon.data_access import Database
from eidolon.embedding import Document, to_blob
from eidolon.embedded_texts import CHARACTER_SCOPES, WORK_SCOPE, EmbeddedTexts, character_scope
from tests.fakes import FakeEmbedder

PARK = Document("公園で鳩にパンをあげた。", "公園")
WING = Document("WING で優勝した。", "WING")
DOME = Document("ドームのステージに立った。", "ドーム")
DOCUMENTS = [PARK, WING, DOME]

SCOPE = character_scope("mano")
SCOPES = [SCOPE, WORK_SCOPE]
FAMILIES = {character_scope("mano"): CHARACTER_SCOPES, WORK_SCOPE: WORK_SCOPE}
"""What each scope's index forgets other models' vectors of."""


@pytest.fixture(params=SCOPES)
def scope(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "eidolon.db"


@pytest.fixture
async def db(database: Path) -> AsyncIterator[Database]:
    """The data access layer the sets are opened on; a set opened on it again is as the
    server restarted."""
    db = Database(database)
    yield db
    await db.close()


def stored(database: Path) -> set[tuple[str, str, str]]:
    """The scope, model and text of every vector the database holds."""
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT scope, model, text FROM embedded_texts").fetchall()
    db.close()
    return set(rows)


async def nearest_text(
    texts: EmbeddedTexts, embedder: FakeEmbedder, scope: str, documents: list[Document], query: str
) -> str:
    [(best, _)] = await texts.search(scope, documents, await embedder.embed_query(query), 1)
    return documents[best].text


async def test_the_texts_are_embedded_the_first_time_they_are_searched(db: Database):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    assert embedder.documents == []

    found = await nearest_text(texts, embedder, SCOPE, DOCUMENTS, "鳩")

    assert found == PARK.text
    assert embedder.documents == [d.text for d in DOCUMENTS]
    assert embedder.titles == [d.title for d in DOCUMENTS]


async def test_searching_again_embeds_nothing_even_after_a_restart(db: Database):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    await nearest_text(texts, embedder, SCOPE, DOCUMENTS, "鳩")
    embedded = len(embedder.documents)

    again = await nearest_text(texts, embedder, SCOPE, DOCUMENTS, "優勝")
    restarted = EmbeddedTexts(db, embedder)
    after_restart = await nearest_text(restarted, embedder, SCOPE, DOCUMENTS, "ドーム")

    assert (again, after_restart) == (WING.text, DOME.text)
    assert len(embedder.documents) == embedded


async def test_searches_at_the_same_time_embed_each_text_once(db: Database):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    query = await embedder.embed_query("鳩")

    results = await asyncio.gather(*(texts.search(SCOPE, DOCUMENTS, query, 1) for _ in range(3)))

    assert [[i for i, _ in found] for found in results] == [[0]] * 3
    assert embedder.documents == [d.text for d in DOCUMENTS]


async def test_a_search_can_look_among_only_some_of_the_texts(db: Database, database: Path):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    query = await embedder.embed_query("鳩")

    best = await texts.search(SCOPE, DOCUMENTS, query, 2, among=[1, 2])

    assert sorted(i for i, _ in best) == [1, 2]
    assert stored(database) == {(SCOPE, embedder.model, d.text) for d in DOCUMENTS}


async def test_after_an_edit_a_restart_embeds_only_the_edited_text(db: Database):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    await nearest_text(texts, embedder, SCOPE, DOCUMENTS, "鳩")
    edited = Document("北海道のドームのステージに立った。", "ドーム")
    before = len(embedder.documents)

    restarted = EmbeddedTexts(db, embedder)
    found = await nearest_text(restarted, embedder, SCOPE, [PARK, WING, edited], "北海道")

    assert found == edited.text
    assert embedder.documents[before:] == [edited.text]


async def test_a_removed_text_is_forgotten(db: Database, database: Path):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    await nearest_text(texts, embedder, SCOPE, DOCUMENTS, "鳩")

    restarted = EmbeddedTexts(db, embedder)
    found = await nearest_text(restarted, embedder, SCOPE, [PARK, DOME], "優勝")

    assert found != WING.text
    assert stored(database) == {(SCOPE, embedder.model, d.text) for d in [PARK, DOME]}


async def test_other_scopes_are_left_alone(db: Database, database: Path):
    other = next(s for s in [*SCOPES, character_scope("kogane")] if s != SCOPE)
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)
    await nearest_text(texts, embedder, other, [WING], "優勝")
    before = len(embedder.documents)

    found = await nearest_text(texts, embedder, SCOPE, [PARK, DOME], "優勝")

    assert found != WING.text
    assert embedder.documents[before:] == [PARK.text, DOME.text]
    assert stored(database) == {
        (other, embedder.model, WING.text),
        *((SCOPE, embedder.model, d.text) for d in [PARK, DOME]),
    }


async def test_embedding_what_is_missing_reports_how_many_it_embedded(db: Database):
    embedder = FakeEmbedder()
    texts = EmbeddedTexts(db, embedder)

    first = await texts.embed_missing(SCOPE, DOCUMENTS)
    second = await texts.embed_missing(SCOPE, DOCUMENTS)

    assert (first, second) == (3, 0)
    assert embedder.documents == [d.text for d in DOCUMENTS]


async def test_forgetting_other_models_keeps_only_the_current_models_vectors(
    db: Database, database: Path, scope: str
):
    old = EmbeddedTexts(db, FakeEmbedder("old-model"))
    await old.embed_missing(scope, DOCUMENTS)
    embedder = FakeEmbedder("new-model")
    texts = EmbeddedTexts(db, embedder)
    await texts.embed_missing(scope, [PARK, WING])

    forgotten = await texts.forget_other_models(FAMILIES[scope])
    found = await nearest_text(texts, embedder, scope, [PARK, WING], "優勝")

    assert forgotten == 3
    assert found == WING.text
    assert stored(database) == {(scope, "new-model", d.text) for d in [PARK, WING]}


async def test_forgetting_other_models_leaves_other_scopes_alone(
    db: Database, database: Path, scope: str
):
    other = WORK_SCOPE if scope != WORK_SCOPE else character_scope("kogane")
    old = EmbeddedTexts(db, FakeEmbedder("old-model"))
    await old.embed_missing(scope, [PARK])
    await old.embed_missing(other, [WING])
    texts = EmbeddedTexts(db, FakeEmbedder("new-model"))

    forgotten = await texts.forget_other_models(FAMILIES[scope])

    assert forgotten == 1
    assert stored(database) == {(other, "old-model", WING.text)}


async def test_forgetting_other_models_of_the_characters_covers_every_character(
    db: Database, database: Path
):
    old = EmbeddedTexts(db, FakeEmbedder("old-model"))
    for scope in [character_scope("mano"), character_scope("kogane"), WORK_SCOPE]:
        await old.embed_missing(scope, [PARK])
    texts = EmbeddedTexts(db, FakeEmbedder("new-model"))

    forgotten = await texts.forget_other_models(CHARACTER_SCOPES)

    assert forgotten == 2
    assert stored(database) == {(WORK_SCOPE, "old-model", PARK.text)}


async def write_old_table(database: Path, scope: str, documents: list[Document]) -> None:
    """Store the vectors of `documents` as the index of `scope` did before the embedded text
    set: character facts in `character_facts`, the work facts in `world_entries`."""
    vectors = [to_blob(v) for v in await FakeEmbedder().embed_documents(documents)]
    with sqlite3.connect(database) as db:
        if scope == WORK_SCOPE:
            db.execute(
                "CREATE TABLE world_entries (text TEXT NOT NULL, model TEXT NOT NULL,"
                " embedding BLOB NOT NULL, PRIMARY KEY (model, text))"
            )
            db.executemany(
                "INSERT INTO world_entries VALUES (?, ?, ?)",
                [(d.text, "fake-embedding", v) for d, v in zip(documents, vectors)],
            )
        else:
            db.execute(
                "CREATE TABLE character_facts (character_id TEXT NOT NULL, text TEXT NOT NULL,"
                " model TEXT NOT NULL, embedding BLOB NOT NULL,"
                " PRIMARY KEY (character_id, model, text))"
            )
            db.executemany(
                "INSERT INTO character_facts VALUES (?, ?, ?, ?)",
                [
                    (scope.removeprefix(CHARACTER_SCOPES + ":"), d.text, "fake-embedding", v)
                    for d, v in zip(documents, vectors)
                ],
            )
    db.close()


def tables(database: Path) -> set[str]:
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    db.close()
    return {name for (name,) in rows}


async def test_the_vectors_of_the_old_tables_are_moved_over_without_embedding_again(
    db: Database, database: Path, scope: str
):
    await write_old_table(database, scope, DOCUMENTS)
    embedder = FakeEmbedder()

    texts = EmbeddedTexts(db, embedder)
    found = await nearest_text(texts, embedder, scope, DOCUMENTS, "ドーム")

    assert found == DOME.text
    assert embedder.documents == []
    assert stored(database) == {(scope, embedder.model, d.text) for d in DOCUMENTS}
    assert not tables(database) & {"character_facts", "world_entries"}
