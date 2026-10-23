"""What a reference source is: one of the places a turn looks references up in (the
story, the character facts, the work facts), searched with the same query vector and
reranked together.

Each source carries its kind, the heading its references go under in the prompt, the
line telling the character how to use them, and its search; and, being embedded, it can
be embedded again (`embedding.Reembeddable`). So adding one is writing it and registering
it where the sources are opened (`retrieval.open_reference_sources`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from eidolon.characters import Character
from eidolon.embedding import Reembeddable

ReferenceKind = Literal["episode", "fact", "work"]


@dataclass(frozen=True)
class Reference:
    kind: ReferenceKind
    text: str
    origin: str
    """The episode's identifier, the fact's title (its text, if it has none), or the work
    fact's title."""
    similarity: float
    """Its vector's cosine similarity to the query's: as every source embeds with the same
    embedder, comparable across sources."""


class ReferenceSource(Reembeddable, Protocol):
    @property
    def kind(self) -> ReferenceKind: ...

    @property
    def heading(self) -> str:
        """The heading each of its references goes under in the prompt."""
        ...

    @property
    def guide(self) -> str:
        """The line telling the character what its references are and how to use them."""
        ...

    async def search(
        self, query_vector: Sequence[float], character: Character, limit: int
    ) -> list[Reference]:
        """Its references for `character` most similar to a query embedded with its
        embedder, as many as `limit`, best first."""
        ...
