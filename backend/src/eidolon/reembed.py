"""Embedding everything again after the embedding service, model or dimensions change
(ADR-0005), from the text already in the database and the character configuration: the
story corpus, the character facts, the work facts (songs, releases and live days, given
a world setting; without one, their vectors are forgotten), and every user's memories. Nothing is downloaded or imported again. The
first three are the reference sources, opened as the server opens them
(`retrieval.open_reference_sources`); each of the four is `Reembeddable`.

Only what has no vector from the current model is embedded, in batches that are each
committed, so an interrupted run loses nothing and a rerun picks up where it stopped.
Only once all four are done are the other models' vectors dropped; a failure drops
nothing. The server can keep running meanwhile: its searches find only what is done.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from eidolon.characters import Character
from eidolon.clock import utc_now
from eidolon.corpus import REEMBED_BATCH
from eidolon.data_access import Database
from eidolon.embedding import Embedder, Reembeddable
from eidolon.memory import Memories
from eidolon.retrieval import open_reference_sources
from eidolon.world import World

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddedCount:
    label: str
    """What was embedded: the story corpus, the character facts, the work facts or the
    memories (see `Reembeddable.label`)."""
    unit: str
    """What `count` is counted in."""
    count: int
    """How many were embedded."""


@dataclass(frozen=True)
class ReembedReport:
    embedded: tuple[EmbeddedCount, ...]
    """How much of each was embedded, in the order they were."""
    forgotten: int
    """Vectors of other models dropped (character facts', the work facts' and memories';
    each story passage keeps only one vector, which re-embedding replaces)."""

    def counts(self) -> dict[str, int]:
        """How many of each were embedded, by label."""
        return {e.label: e.count for e in self.embedded}


async def reembed(
    path: Path,
    characters: Iterable[Character],
    embedder: Embedder,
    batch: int = REEMBED_BATCH,
    world: World | None = None,
) -> ReembedReport:
    """Embed with `embedder` whatever in the database at `path` has no vector from it yet,
    the entries of `world` included, then drop the other models' vectors. Raises
    EmbeddingError, having dropped nothing but kept what it finished, if the embedding
    service fails."""
    by_id = {c.id: c for c in characters}
    # Passages missing vectors are what this is for; no need to warn of them.
    db = Database(path)
    memories = Memories(db, by_id, utc_now, embedder=embedder)
    try:
        sources = open_reference_sources(db, embedder, by_id.values(), world, warn=False)
        everything: list[Reembeddable] = [*sources.embedded(), memories]
        embedded = []
        for each in everything:
            count = await each.embed_missing(batch, _progress(each))
            log.info("%s：新向量化 %d %s", each.label, count, each.unit)
            embedded.append(EmbeddedCount(each.label, each.unit, count))
        forgotten = sum([await each.forget_other_models() for each in everything])
    finally:
        await memories.aclose()
        await db.close()
    return ReembedReport(tuple(embedded), forgotten)


def _progress(each: Reembeddable) -> Callable[[int], None]:
    def progress(done: int) -> None:
        log.info("%s：%d %s", each.label, done, each.unit)

    return progress
