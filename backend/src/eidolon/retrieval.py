"""Retrieval for a conversation turn: whether to look anything up, and what to bring back.

The judge model (`judge`) first judges what the user just said needs: nothing, so small
talk costs no retrieval latency; memories only, when the user talks about their
own life, for which the turn finds the memories of what they have told any of the
characters; references, for which the reference sources (the story corpus, only episodes
the character is cast in; the character's facts; and the work facts all characters share,
when there is a world setting) are searched with the same query vector, the candidates
reranked together, and the best few that are relevant enough go into the prompt, each
under its source's heading, while the turn finds the user's memories alongside; or
recall, for a question about how some past episode went, for which the episodes are
recalled as the recall-story tool would, the lives and releases of the work facts
reranked along with them, and the result goes into the prompt, saving the chat model a
round of calling it. The story not telling how something went does not mean she never
went through it: she may have stood on that stage all the same.

References, memories and recall alike are looked up with what the user said after the
character's reply before it, as people see it (`memory.memory_query`): 「那时候累吗」 alone
finds nothing, her reply says what 「那时候」 is. Embedding and reranking both use it; the
judge model is told what it always was, the latest messages and what the user said.

The judge model takes longest, so the turn does not wait for it to start looking: as it
judges, the query is embedded, once, and the candidates found with the vector, the
nearest of each reference source and of the user's memories. Once it has judged, only
the candidates the turn needs are reranked, so no more often than if the turn had
waited: the references' and the memories', the memories' alone, or the story's with the
work facts' lives and releases to recall. A turn needing nothing drops them, and
whatever is still being looked up. The vector goes on to the writing down, which shows
the background model the memories nearest the same query (`MemoryTurn.write_down`).

Retrieval never fails a turn, nor holds it up for long: embedding and reranking are given
a few seconds and asked again once (`retry`). If the judge model cannot answer, the turn
retrieves anyway; if the query cannot be embedded, it goes on without references or
memories. If reranking fails, the nearest few references by their vectors go into the
prompt instead, unthresholded, as do the nearest episode (and live or release) for recall;
memories do not, as one the reranker never judged might be what the user never said.

Where affinity is on, the judge model is asked what the turn does to it in the same
request (`affinity_judge.QUESTIONS`); failing, it fails both. So too, in a turn that could
have a photo, whether the user asks her for one (`photo_judge`), no if it fails.

The chat model can also look an episode up itself with the recall-story tool, for
general questions like "what happened that time with the photo book" that the judge
model did not catch: the best matching episodes she is cast in come back with where they
sit in her story catalog (card or event, and its other episodes) and what happened in them.
The tool looks only in the story.
"""

import asyncio
import logging
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol

from eidolon.affinity import Judged
from eidolon.affinity_judge import QUESTIONS as AFFINITY_QUESTIONS
from eidolon.affinity_judge import questions_for
from eidolon.affinity_judge import read_judged
from eidolon.characters import Character
from eidolon.corpus import CatalogSource, Episode, StoryCorpus
from eidolon.data_access import Database
from eidolon.embedding import Embedder, EmbeddingError, Reembeddable
from eidolon.facts import CharacterFacts
from eidolon.judge import (
    FALLBACK_FAMILY,
    JUDGE_THRESHOLDS,
    JudgeError,
    JudgeModel,
    JudgeThresholds,
    turn_state,
)
from eidolon.llm import Tool
from eidolon.memory import Memory, MemoryTurn, memory_query
from eidolon.photo_judge import QUESTIONS as PHOTO_QUESTIONS
from eidolon.photo_judge import read_asked
from eidolon.references import Reference, ReferenceKind, ReferenceSource
from eidolon.rerank import Reranker, RerankError
from eidolon.store import Message
from eidolon.work_facts import WORK_RULE, WorkFacts
from eidolon.world import EntryKind, World, WorldEntry

log = logging.getLogger(__name__)

MAX_REFERENCES = 5
"""The most references one turn's prompt carries, after reranking."""
CANDIDATES_PER_SOURCE = 12
"""How many candidates each reference source offers the reranker."""
FALLBACK_REFERENCES = 3
"""How many references a turn's prompt carries if reranking fails: those nearest by their
vectors, of which no threshold can tell the relevant."""

RECALL_EPISODES = 2
"""The most episodes one call of the recall-story tool brings back."""
RECALL_ENTRIES = 3
"""The most lives and releases of the work facts recalling brings back along with the
episodes."""
RECALL_KINDS: tuple[EntryKind, ...] = ("live", "release")
"""What of the work facts recalling looks in: how a live or a release went, not a song."""
RECALL_EPISODE_CHARS = 3000
"""How much of an episode's dialogue the recall-story tool brings back before cutting it short."""

RECALL_STORY_TOOL = Tool(
    name="recall_story",
    description="回顾剧情：在你亲身经历过的原作剧情中查找和某件事有关的剧情，返回它在你的剧情目录中的位置"
    "（哪张卡或哪个活动）和剧情内容。当制作人问起「那次……是怎么回事」这类过去的事、"
    "而你想不起具体经过时使用；闲聊时不需要。",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要回顾的事，写成一句概括，例如「拍写真集的那次」「W.I.N.G. 决赛」",
            }
        },
        "required": ["query"],
    },
)


_CANON_RULE = (
    "剧情里没有记录制作人当时选了哪个选项，不要断言制作人当时说了什么或选了什么。"
)
_NOT_IN_STORY = (
    "剧情库里没有记录这段经过，但这不等于这件事没有发生过。"
    "不要编造经过，也不要说没有这回事；可以坦率地说细节记不太清了"
)
_CANNOT_RECALL = "暂时想不起来，现在查不到剧情。不要编造经过，可以坦率地说记不太清了。"


Need = Literal["nothing", "memories", "references", "recall"]
"""What a turn needs looked up: nothing, the user's memories only, references from the
reference sources along with the user's memories, or the recall of a past episode."""


class RetrievalJudge(Protocol):
    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = False,
        photo: bool = False,
    ) -> tuple[Need, Judged | None, bool]:
        """What answering `text` needs looked up; asked about `affinity` too, what saying it
        does to her affinity (None if that alone cannot be told); and asked about `photo`,
        whether it asks her for a photo (no if that alone cannot be told)."""
        ...


class StoryReferences:
    """The story corpus as a reference source: the passages of the episodes the character
    is cast in."""

    kind: ReferenceKind = "episode"
    heading = "剧情"
    guide = "「剧情」是你亲身经历过的原作剧情，可以像回忆往事一样自然地提起，不要照搬台词。" + _CANON_RULE
    label = "剧情库"
    unit = "段"

    def __init__(self, corpus: StoryCorpus) -> None:
        self.corpus = corpus

    async def search(
        self, query_vector: Sequence[float], character: Character, limit: int
    ) -> list[Reference]:
        passages = await self.corpus.search_by_vector(query_vector, character.id, limit)
        return [Reference(self.kind, p.text, p.episode_id, p.score) for p in passages]

    async def embed_missing(self, batch: int, progress: Callable[[int], None]) -> int:
        return await self.corpus.embed_missing(batch, progress)

    async def forget_other_models(self) -> int:
        """Nothing: each passage keeps only one vector, which embedding it again replaces."""
        return 0


class ReferenceSources:
    """The reference sources a turn looks references up in, all searched with the same
    query vector and reranked together; and the story corpus and the work facts, which
    recalling searches itself."""

    def __init__(
        self,
        embedder: Embedder,
        sources: Sequence[ReferenceSource],
        corpus: StoryCorpus,
        work: WorkFacts | None,
        unsearched: Sequence[ReferenceSource] = (),
    ) -> None:
        """`corpus` and `work` are among `sources`; `unsearched` are sources with nothing
        to search, kept only to be embedded again."""
        self.embedder = embedder
        self.corpus = corpus
        self.work = work
        self._sources = list(sources)
        self._unsearched = list(unsearched)

    async def candidates(
        self,
        query_vector: Sequence[float],
        character: Character,
        kinds: Collection[ReferenceKind] | None = None,
    ) -> list[Reference]:
        """The candidate references for a query embedded with the sources' embedder: as many as
        `CANDIDATES_PER_SOURCE` of each source's (of `kinds`, or any) nearest it. Raises
        EmbeddingError if a source's texts not embedded yet cannot be."""
        found = await asyncio.gather(*(
            s.search(query_vector, character, CANDIDATES_PER_SOURCE)
            for s in self._sources
            if kinds is None or s.kind in kinds
        ))
        return [r for references in found for r in references]

    async def rerank(
        self, reranker: Reranker, query: str, candidates: Sequence[Reference]
    ) -> list[tuple[Reference, float]]:
        """The `candidates` for `query`, with the reranker's score, best first, reranked
        together. Raises RerankError."""
        if not candidates:
            return []
        best = await reranker.rerank(query, [c.text for c in candidates], len(candidates))
        return [(candidates[i], score) for i, score in best]

    async def rank(
        self, reranker: Reranker, character: Character, text: str
    ) -> list[tuple[Reference, float]]:
        """Every candidate reference for `text`, with the reranker's score, best first, as a
        turn needing references finds and reranks them. Raises EmbeddingError or
        RerankError if either service fails."""
        query_vector = await self.embedder.embed_query(text)
        return await self.rerank(reranker, text, await self.candidates(query_vector, character))

    def embedded(self) -> list[Reembeddable]:
        """Every source, for `eidolon reembed`, the unsearched among them."""
        return [*self._sources, *self._unsearched]

    def prompt(self, references: Sequence[Reference]) -> str:
        """The prompt section carrying a turn's references, each under its source's heading."""
        headings = {s.kind: s.heading for s in self._sources}
        lines = [
            "# 回忆与资料",
            "下面是和制作人这句话可能有关的内容，按相关程度排列，和话题无关的可以忽略。",
            *(f"- {s.guide}" for s in self._sources),
        ]
        for reference in references:
            lines += ["", f"## {headings[reference.kind]}", reference.text]
        return "\n".join(lines)


def open_reference_sources(
    db: Database,
    embedder: Embedder,
    characters: Iterable[Character],
    world: World | None,
    *,
    warn: bool = True,
) -> ReferenceSources:
    """The reference sources in the database `db` reads and writes: the story, the characters' facts
    and, given a world setting, the work facts; without one, the work facts are there only
    to be embedded again, which forgets whatever vectors they had. Warns (unless not to
    `warn`) if story passages have no vector from `embedder`'s model, as they cannot be
    found."""
    corpus = StoryCorpus(db, embedder)
    if warn and (missing := corpus.missing_vectors_at_open):
        log.warning(
            "%d story passages have no vectors from the embedding model %s and cannot be"
            " found: run `eidolon reembed` after changing the embedding service, model"
            " or dimensions",
            missing, embedder.model,
        )
    sources: list[ReferenceSource] = [
        StoryReferences(corpus), CharacterFacts(db, embedder, characters)
    ]
    work = WorkFacts(db, embedder, world)
    if world is None:
        return ReferenceSources(embedder, sources, corpus, None, [work])
    sources.append(work)
    return ReferenceSources(embedder, sources, corpus, work)


@dataclass(frozen=True)
class Retrieved:
    """What a turn looked up."""

    prompt: str | None
    """The prompt section carrying the references or recalled episodes, if any."""
    memories_prompt: str | None = None
    """The prompt section carrying the user's memories relevant to the turn, if any."""
    experiences_prompt: str | None = None
    """The prompt section carrying her own doings the turn brings up (GLOSSARY.md「她们的经历」),
    if any."""
    recalled: bool = False
    """Whether the recall-story tool already ran, so the chat model need not call it."""
    judged: Judged | None = None
    """What the turn does to her affinity, asked about it; None if not or if it could not
    be told."""
    photo: bool = False
    """Whether the user asks her for a photo, asked about it; no if it could not be told."""
    query_vector: list[float] | None = None
    """The turn's query embedded, for writing down (`MemoryTurn.write_down`); None if it
    was not by the time the turn knew what it needed, or could not be."""


@dataclass(frozen=True)
class _Found:
    """What a turn finds before it knows what it needs: its query embedded, the candidate
    references and the candidate memories."""

    query_vector: list[float]
    references: list[Reference] | None
    """None if searching the reference sources failed."""
    memories: list[Memory]


class Retrieval:
    def __init__(
        self,
        judge: RetrievalJudge,
        sources: ReferenceSources,
        reranker: Reranker,
        min_relevance: Callable[[], float],
    ) -> None:
        """`min_relevance` says, whenever asked, the reranker's score below which a
        reference or recalled episode is left out (see `rerank.KNOWN_THRESHOLDS`); the
        admin console may change it while the backend runs."""
        self._judge = judge
        self._sources = sources
        self._reranker = reranker
        self._min_relevance = min_relevance

    async def retrieve(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        memory: MemoryTurn | None = None,
        affinity: bool = False,
        photo: bool = False,
    ) -> Retrieved:
        """Look up what this turn needs, finding the user's memories through `memory`; and
        judge what it does to her affinity, and whether it asks her for a photo, if asked
        to."""
        query = memory_query(history, text)
        embedding = asyncio.ensure_future(self._embed(query))
        finding = asyncio.ensure_future(self._find(character, embedding, memory))
        looked_up = False
        try:
            judged, asked = None, False
            try:
                need, judged, asked = await self._judge.judge(
                    character, history, text, affinity, photo
                )
            except JudgeError:
                log.warning("retrieval judge failed; retrieving anyway", exc_info=True)
                need = "references"
            if need == "nothing":
                retrieved = Retrieved(None)
            else:
                looked_up = True
                retrieved = await self._look_up(character, text, query, await finding, memory, need)
        finally:
            for task in (finding, embedding):  # needing nothing, or no longer anything at all
                task.cancel()
                if not looked_up:  # nor will anyone else see what it failed with
                    task.add_done_callback(_log_failure)
        return replace(
            retrieved, judged=judged, photo=asked, query_vector=_query_vector(embedding)
        )

    async def _embed(self, query: str) -> list[float] | None:
        """The turn's query embedded; None if it cannot be."""
        try:
            return await self._sources.embedder.embed_query(query)
        except EmbeddingError:
            log.warning("embedding the turn failed; replying without references or memories",
                        exc_info=True)
            return None

    async def _find(
        self,
        character: Character,
        embedding: "asyncio.Future[list[float] | None]",
        memory: MemoryTurn | None,
    ) -> _Found | None:
        """The candidates for the turn's query, once `embedding` it is done; None if it
        cannot be embedded."""
        if (query_vector := await embedding) is None:
            return None
        references, memories = await asyncio.gather(
            self._candidates(character, query_vector), _memory_candidates(memory, query_vector)
        )
        return _Found(query_vector, references, memories)

    async def _candidates(
        self, character: Character, query_vector: Sequence[float]
    ) -> list[Reference] | None:
        try:
            return await self._sources.candidates(query_vector, character)
        except EmbeddingError:
            log.warning("searching the references failed; replying without them", exc_info=True)
            return None

    async def _look_up(
        self,
        character: Character,
        text: str,
        query: str,
        found: _Found | None,
        memory: MemoryTurn | None,
        need: Need,
    ) -> Retrieved:
        """What the turn `need`s (something) of what it found for `query`, `text` and her
        reply before it."""
        if need == "recall":
            return Retrieved(await self._recall_turn(character, text, query, found), recalled=True)
        if found is None:
            return Retrieved(None)
        if need == "memories":
            memories = await _find_memories(memory, query, found.memories)
            return Retrieved(
                None, memories, experiences_prompt=await _find_experiences(memory, query, found)
            )
        references, memories, own = await asyncio.gather(
            self._references(query, found.references or []),
            _find_memories(memory, query, found.memories),
            _find_experiences(memory, query, found),
        )
        return Retrieved(
            self._sources.prompt(references) if references else None, memories,
            experiences_prompt=own,
        )

    async def _references(self, query: str, candidates: Sequence[Reference]) -> list[Reference]:
        """Those of the `candidates` relevant to `query`, most relevant first."""
        try:
            ranked = await self._sources.rerank(self._reranker, query, candidates)
        except RerankError:
            log.warning("reranking the references failed; replying with the nearest",
                        exc_info=True)
            return sorted(candidates, key=lambda r: -r.similarity)[:FALLBACK_REFERENCES]
        min_relevance = self._min_relevance()
        return [r for r, score in ranked if score >= min_relevance][:MAX_REFERENCES]

    async def recall_story(self, character: Character, query: str) -> str:
        """The recall-story tool: the episodes `character` is cast in that best match
        `query`, for the chat model to answer from: what the model wrote, looked up alone."""
        try:
            query_vector = await self._sources.embedder.embed_query(query)
            story = await self._sources.candidates(query_vector, character, ("episode",))
        except EmbeddingError:
            log.warning("recall failed", exc_info=True)
            return _CANNOT_RECALL
        return await self._recall(character, query, query, story, [])

    async def _recall_turn(
        self, character: Character, text: str, query: str, found: _Found | None
    ) -> str:
        """The recall the judge model sees `text` ask for: the story's candidates for the
        turn's `query`, and the lives and releases of the work facts nearest it."""
        if found is None:
            return _CANNOT_RECALL
        try:
            story = (
                [r for r in found.references if r.kind == "episode"]
                if found.references is not None
                # Another source failed: the story's are still to be had.
                else await self._sources.candidates(found.query_vector, character, ("episode",))
            )
            entries = await _search_work(self._sources.work, found.query_vector)
        except EmbeddingError:
            log.warning("recall failed", exc_info=True)
            return _CANNOT_RECALL
        return await self._recall(character, text, query, story, entries)

    async def _recall(
        self,
        character: Character,
        asked: str,
        query: str,
        story: Sequence[Reference],
        entries: Sequence[WorldEntry],
    ) -> str:
        """The episodes of the `story` candidates (passages of episodes `character` is cast
        in) and the lives and releases of `entries` (nearest first) that best match
        `query`, reranked together; recalled as what was `asked`. If reranking fails, the
        episode of the nearest candidate and the nearest of `entries`."""
        documents = [r.text for r in story] + [e.text for e in entries]
        try:
            best = (
                await self._reranker.rerank(query, documents, len(documents)) if documents else []
            )
        except RerankError:
            log.warning("reranking the recall failed; recalling the nearest", exc_info=True)
            nearest = max(story, key=lambda r: r.similarity, default=None)
            episode_ids = {nearest.origin: None} if nearest else {}
            found = list(entries[:1])
        else:
            episode_ids, found = self._relevant(best, story, entries)
        corpus = self._sources.corpus
        episodes = await corpus.episodes(episode_ids) if episode_ids else []
        if not episodes and not found:
            return f"没有想起和「{asked}」有关的剧情：{_NOT_IN_STORY}，或者问问制作人。"
        catalog = await corpus.catalog(character.id) if episodes else []
        return recall_prompt(asked, episodes, catalog, found)

    def _relevant(
        self,
        best: Sequence[tuple[int, float]],
        story: Sequence[Reference],
        entries: Sequence[WorldEntry],
    ) -> tuple[dict[str, None], list[WorldEntry]]:
        """The episodes (their identifiers, in order) and the work facts to recall, of the
        `story` candidates and `entries` reranked together, `best` first."""
        episode_ids: dict[str, None] = {}
        found: list[WorldEntry] = []
        min_relevance = self._min_relevance()
        for i, score in best:
            if score < min_relevance:
                continue
            if i < len(story):
                if len(episode_ids) < RECALL_EPISODES:
                    episode_ids.setdefault(story[i].origin)
            elif len(found) < RECALL_ENTRIES:
                found.append(entries[i - len(story)])
        return episode_ids, found


def _query_vector(embedding: "asyncio.Future[list[float] | None]") -> list[float] | None:
    """The turn's query embedded, if it was by the time `embedding` was let go of."""
    if not embedding.done() or embedding.cancelled() or embedding.exception() is not None:
        return None
    return embedding.result()


def _log_failure(task: "asyncio.Future[Any]") -> None:
    """Log what looking a turn up failed with that no one awaited."""
    if not task.cancelled() and (error := task.exception()) is not None:
        log.warning("looking the turn up failed", exc_info=error)


async def _memory_candidates(
    memory: MemoryTurn | None, query_vector: Sequence[float]
) -> list[Memory]:
    return await memory.candidates(query_vector) if memory else []


async def _find_memories(
    memory: MemoryTurn | None, query: str, candidates: Sequence[Memory]
) -> str | None:
    return await memory.find(query, candidates) if memory else None


async def _find_experiences(
    memory: MemoryTurn | None, query: str, found: "_Found | None"
) -> str | None:
    """Her own doings the turn brings up, as `her_own` finds them; None where nothing does."""
    if memory is None or found is None:
        return None
    return await memory.her_own(query, found.query_vector)


async def _search_work(work: WorkFacts | None, query_vector: Sequence[float]) -> list[WorldEntry]:
    if work is None:
        return []
    return await work.search_entries(query_vector, CANDIDATES_PER_SOURCE, RECALL_KINDS)


def recall_prompt(
    query: str,
    episodes: Sequence[Episode],
    catalog: Sequence[CatalogSource],
    entries: Sequence[WorldEntry] = (),
) -> str:
    """What recalling brings back: each episode under its place in the story catalog, then
    the lives and releases of the work facts."""
    lines = [f"# 回顾剧情：{query}"]
    if episodes:
        lines.append(
            "下面是你经历过的剧情中和这件事最相关的，按相关程度排列。用自己的话回忆经过，不要照搬台词。"
            + _CANON_RULE
        )
    else:
        lines.append(f"{_NOT_IN_STORY}。")
    if entries:
        lines.append(f"「{WorkFacts.heading}」是和这件事有关的演出或唱片的事实。" + WORK_RULE)
    for episode in episodes:
        source = next(
            (s for s in catalog if any(e.episode_id == episode.id for e in s.episodes)), None
        )
        lines += ["", f"## {episode.category} / {episode.source} /「{episode.title}」"]
        if source is not None and len(source.episodes) > 1:
            titles = "".join(f"「{e.title}」" for e in source.episodes)
            lines.append(f"（{episode.source}中你出场的剧情依次是：{titles}）")
        lines.append(_transcript(episode))
    for entry in entries:
        lines += ["", f"## {WorkFacts.heading}", entry.text]
    return "\n".join(lines)


def _transcript(episode: Episode) -> str:
    text = ""
    for line in episode.lines:
        said = f"{line.speaker}：{line.ja}" if line.speaker else line.ja
        if line.zh:
            said += f"（{line.zh}）"
        if len(text) + len(said) > RECALL_EPISODE_CHARS:
            return (text or said[:RECALL_EPISODE_CHARS]) + "……（后略）"
        text += said + "\n"
    return text.rstrip("\n")


_JUDGE_QUESTION = {
    "type": "noul",
    "instructions": "角色扮演对话中，角色是《偶像大师 闪耀色彩》283 事务所的偶像（或事务员），正在和制作人对话。"
    "可检索的内容有三类：剧情，即她亲身经历过的原作剧情；角色资料，即关于她自己和身边人物的设定"
    "（档案、人物关系等）；作品资料，即作品里的歌曲、唱片和演出（演唱者、发售日、曲目、"
    "演出的日期、会场和出演者）。回答制作人最新的一句话，是否需要检索这些内容？",
    "criteria": {
        "true": "需要回忆过去的具体经历、提到具体的人物或事件、需要确认角色设定细节，"
        "或问到具体的歌曲、唱片、演出",
        "false": "只是寒暄、闲聊、情绪回应，或只凭上文就能回答",
    },
}
_RECALL_QUESTION = {
    "type": "noul",
    "instructions": "制作人最新的一句话，是不是在问角色过去某一次具体经历的来龙去脉"
    "（例如「那次……是怎么回事」「还记得……那次吗」），需要回想那段经历的完整经过才能回答？",
    "criteria": {
        "true": "问的是过去某一次具体的事情、活动、工作或比赛的经过和当时的心情",
        "false": "寒暄闲聊、问喜好或设定、问身边人物近况，或者只是顺口提到过去",
    },
}
_ABOUT_PRODUCER_QUESTION = {
    "type": "noul",
    "instructions": "角色扮演对话中，角色是《偶像大师 闪耀色彩》283 事务所的偶像（或事务员），正在和制作人对话。"
    "制作人最新的一句话，是否说起或问起制作人自己的事（生活、近况、工作安排、计划、家人、宠物、喜好、"
    "身体状况等），而制作人以前对偶像们说过的相关的事可能有助于回应？",
    "criteria": {
        "true": "说起或问起制作人自己的生活、近况、计划、家人宠物、喜好，或提到以前对偶像说过的事",
        "false": "只是打招呼、说偶像自己的事、问原作剧情或角色设定，与制作人自己的生活无关",
    },
}
_QUESTIONS = {
    "needs_retrieval": _JUDGE_QUESTION,
    "recall": _RECALL_QUESTION,
    "about_producer": _ABOUT_PRODUCER_QUESTION,
}


def judge_questions(
    affinity: bool = False, photo: bool = False, character: Character | None = None
) -> dict[str, dict]:
    """What the judge model is asked of a turn: retrieval's three questions, affinity's in
    the same request if `affinity` (weighed by `character`'s heart, if given and she has one),
    and whether it asks for a photo if `photo`."""
    affinity_questions = AFFINITY_QUESTIONS if character is None else questions_for(character)
    return (
        _QUESTIONS
        | (affinity_questions if affinity else {})
        | (PHOTO_QUESTIONS if photo else {})
    )


@dataclass(frozen=True)
class RetrievalAnswer:
    """The judge model's probability of yes to each of its three questions."""

    needs_retrieval: float
    recall: float
    about_producer: float


class ModelRetrievalJudge:
    """The judge model, asked three yes-or-no questions (`noul`) about the turn in one
    request: whether it needs looking anything up, whether it asks how a past episode
    went, and whether it is about the user's own life. Each answer is yes at or above the
    model's threshold for it."""

    def __init__(
        self,
        judge_model: JudgeModel,
        thresholds: JudgeThresholds = JUDGE_THRESHOLDS[FALLBACK_FAMILY],
    ) -> None:
        self._judge_model = judge_model
        self.thresholds = thresholds

    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = False,
        photo: bool = False,
    ) -> tuple[Need, Judged | None, bool]:
        """As `RetrievalJudge.judge`, affinity's questions and the photo's asked in the same
        request."""
        answers = await self._judge_model.ask(
            turn_state(character, history, text), judge_questions(affinity, photo, character)
        )
        need = self.decide(_answer(answers))
        judged = None
        if affinity:
            try:
                judged = read_judged(answers, self.thresholds)
            except JudgeError:
                log.warning("affinity judge's answers could not be read", exc_info=True)
        asked = False
        if photo:
            try:
                asked = read_asked(answers, self.thresholds)
            except JudgeError:
                log.warning("the photo question's answer could not be read", exc_info=True)
        return need, judged, asked

    async def ask(
        self, character: Character, history: Sequence[Message], text: str, affinity: bool = False
    ) -> RetrievalAnswer:
        """How likely the judge model finds yes to each question about `text`, affinity's
        questions asked in the same request if `affinity`."""
        return _answer(await self._judge_model.ask(
            turn_state(character, history, text), judge_questions(affinity)
        ))

    def decide(self, answer: RetrievalAnswer) -> Need:
        """What a turn so answered needs: recall first, then references, then memories."""
        if answer.recall >= self.thresholds.recall:
            return "recall"
        if answer.needs_retrieval >= self.thresholds.needs_retrieval:
            return "references"
        if answer.about_producer >= self.thresholds.about_producer:
            return "memories"
        return "nothing"


def _answer(answers: dict) -> RetrievalAnswer:
    """The probabilities of yes `answers` give each of the three questions."""
    try:
        return RetrievalAnswer(
            float(answers["needs_retrieval"]["noul"]),
            float(answers["recall"]["noul"]),
            float(answers["about_producer"]["noul"]),
        )
    except (ValueError, KeyError, TypeError) as e:
        raise JudgeError("判断模型的返回无法解析（缺少回答）") from e
