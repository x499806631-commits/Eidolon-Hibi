"""The character facts, as a reference source.

Facts come from the character configuration, not an import: each character's facts are a
scope of the embedded text set, embedded the first time they are searched and kept until
a fact is edited or removed, so an edit to a facts file takes effect after a restart
without any command and costs only the changed entries.
"""

from collections.abc import Callable, Iterable, Sequence

from eidolon.characters import Character, Fact
from eidolon.corpus import REEMBED_BATCH
from eidolon.data_access import Database
from eidolon.embedded_texts import CHARACTER_SCOPES, EmbeddedTexts, character_scope
from eidolon.embedding import Document, Embedder
from eidolon.references import Reference, ReferenceKind


class CharacterFacts:
    kind: ReferenceKind = "fact"
    heading = "角色资料"
    guide = "「角色资料」是关于你自己和身边人物的设定。"
    label = "角色资料"
    unit = "条"

    def __init__(self, db: Database, embedder: Embedder, characters: Iterable[Character]) -> None:
        self._texts = EmbeddedTexts(db, embedder)
        self._characters = list(characters)

    async def search(
        self, query_vector: Sequence[float], character: Character, limit: int
    ) -> list[Reference]:
        facts = _facts(character)
        best = await self._texts.search(
            character_scope(character.id), _documents(facts), query_vector, limit
        )
        return [
            Reference(self.kind, facts[i].text, facts[i].title or facts[i].text, similarity)
            for i, similarity in best
        ]

    async def embed_missing(
        self, batch: int = REEMBED_BATCH, progress: Callable[[int], None] | None = None
    ) -> int:
        """Embed those of the characters' facts that have no vector from this source's
        embedder yet, `batch` at a time; returns how many that was, telling `progress` the
        count so far after each character."""
        embedded = 0
        for character in self._characters:
            embedded += await self._texts.embed_missing(
                character_scope(character.id), _documents(_facts(character)), batch
            )
            if progress is not None:
                progress(embedded)
        return embedded

    async def forget_other_models(self) -> int:
        """Drop every character's vectors not from this source's embedder; returns how many
        there were."""
        return await self._texts.forget_other_models(CHARACTER_SCOPES)


def _facts(character: Character) -> list[Fact]:
    """The character's facts, one for each text."""
    return list({f.text: f for f in character.facts}.values())


def _documents(facts: Sequence[Fact]) -> list[Document]:
    return [Document(f.text, f.title) for f in facts]
