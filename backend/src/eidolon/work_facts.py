"""The work facts (every song, release and live day, shared by every character), as a
reference source.

They come from the world setting's data file (`World.entries`), not an import: they are
the work's scope of the embedded text set, embedded the first time they are searched and
kept until an entry is edited or removed, so a new data file takes effect after a restart
without any command and costs only the changed entries.

As a reference source they are searched whatever they are about; recalling how something
went searches them itself (`search_entries`), for the lives and releases only.
"""

from collections.abc import Callable, Collection, Sequence

from eidolon.characters import Character
from eidolon.corpus import REEMBED_BATCH
from eidolon.data_access import Database
from eidolon.embedded_texts import WORK_SCOPE, EmbeddedTexts
from eidolon.embedding import Document, Embedder
from eidolon.references import Reference, ReferenceKind
from eidolon.world import EntryKind, World, WorldEntry

WORK_RULE = "作品资料里只有名称、日期、会场、出演者这类事实，没有台上或当时具体发生了什么，不要编造。"
"""What the work facts do not tell, for every prompt that carries them."""


class WorkFacts:
    kind: ReferenceKind = "work"
    heading = "作品资料"
    guide = (
        "「作品资料」是关于歌曲、唱片和演出的事实，可以照着回答；"
        "日期在今天之后的，是还没发售或还没举办的。" + WORK_RULE
    )
    label = "作品资料"
    unit = "条"

    def __init__(self, db: Database, embedder: Embedder, world: World | None) -> None:
        """Without `world` (no data file), there are no work facts: any vectors stored
        for the embedder's model are forgotten the first time they are searched."""
        self._texts = EmbeddedTexts(db, embedder)
        self._entries = list({e.text: e for e in (world.entries() if world else [])}.values())
        self._documents = [Document(e.text, e.title) for e in self._entries]

    async def search(
        self, query_vector: Sequence[float], character: Character, limit: int
    ) -> list[Reference]:
        return [
            Reference(self.kind, e.text, e.title, similarity)
            for e, similarity in await self._nearest(query_vector, limit)
        ]

    async def search_entries(
        self,
        query_vector: Sequence[float],
        limit: int,
        kinds: Collection[EntryKind] | None = None,
    ) -> list[WorldEntry]:
        """The entries (of `kinds`, or any) most similar to a query embedded with this
        source's embedder, best first."""
        return [e for e, _ in await self._nearest(query_vector, limit, kinds)]

    async def _nearest(
        self,
        query_vector: Sequence[float],
        limit: int,
        kinds: Collection[EntryKind] | None = None,
    ) -> list[tuple[WorldEntry, float]]:
        """As `search_entries`, with their similarity."""
        among = [i for i, e in enumerate(self._entries) if kinds is None or e.kind in kinds]
        best = await self._texts.search(WORK_SCOPE, self._documents, query_vector, limit, among)
        return [(self._entries[i], similarity) for i, similarity in best]

    async def embed_missing(
        self, batch: int = REEMBED_BATCH, progress: Callable[[int], None] | None = None
    ) -> int:
        """Embed the entries that have no vector from this source's embedder yet, `batch`
        at a time; returns how many that was, telling `progress` once done."""
        embedded = await self._texts.embed_missing(WORK_SCOPE, self._documents, batch)
        if progress is not None:
            progress(embedded)
        return embedded

    async def forget_other_models(self) -> int:
        """Drop every vector not from this source's embedder; returns how many there were."""
        return await self._texts.forget_other_models(WORK_SCOPE)
