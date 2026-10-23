"""Evaluating memory (`eidolon eval memory`): whether the characters remember right,
against a small labelled set of turns committed with the code (`eval/memory.toml`).

Each case runs through the memories module as a turn would, with the deployer's
background model, embedder, reranker and judge model, on a scratch database of its own
with one made-up user. The memories a case starts with are written down through the
module too, each in a turn with the character it names, by a background model that
writes down just that memory, superseding those the case says it superseded.

An extraction case takes one more turn and looks at what it wrote down: every group of
keywords it must have in some new memory (any keyword of the group will do), none it must
not, the memories it should supersede superseded and those it should keep not, or nothing
written at all. The background model answering differently each time, each case runs
three times, each on a fresh database, and passes if most runs do; a run whose model
failed fails. A recall case takes one turn the way a conversation does (`Retrieval`, with
no references to look up: the judge model decides what the turn needs, and memories are
found if it needs them) and looks at which memories it found, with the judge model's
verdict alongside, to tell a turn never looked up from one looked up in vain.

Each case has a category, and the passes are counted by category: a change aimed at one
must pass more of its cases than the baseline committed beside the set, and may pass at
most one fewer case of any other. The continuity cases are there to decide on later
work, never a bar to a change.

The set holds a made-up user's words and made-up replies, never story text, so it can be
committed (ADR-0003).
"""

import asyncio
import json
import tomllib
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_args
from zoneinfo import ZoneInfo

from eidolon.characters import Character
from eidolon.corpus import StoryCorpus
from eidolon.data_access import Database
from eidolon.embedding import Embedder
from eidolon.judge import JudgeError, JudgeModel, JudgeThresholds
from eidolon.llm import ChatLLM, ChatMessage, Streamed, Tool
from eidolon.memory import SHOWN_MEMORIES, Memories, parse_memories
from eidolon.rerank import Reranker, RerankThresholds
from eidolon.retrieval import ModelRetrievalJudge, Need, ReferenceSources, Retrieval, RetrievalAnswer
from eidolon.store import Conversation, Message, Segment, Store

Category = Literal["supersede", "duplicate", "transient", "pronoun", "proper-noun", "continuity"]

CATEGORY_NAMES: dict[Category, str] = {
    "supersede": "取代",
    "duplicate": "重复",
    "transient": "短期状态",
    "pronoun": "代词",
    "proper-noun": "专有名词",
    "continuity": "前情",
}

RUNS = 3
"""How many times each extraction case runs."""
TIMEZONE = "Asia/Shanghai"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=ZoneInfo(TIMEZONE))
"""When every case is taken, so a case may expect 「明天」 written down as a date."""
WRITE_TIMEOUT = 180.0
"""How long, in seconds, a turn's writing down is waited for before the run fails."""


class MemoryEvalError(RuntimeError):
    pass


@dataclass(frozen=True)
class StartingMemory:
    """A memory a case starts with, told to its source character."""

    key: str
    source: str
    text: str
    superseded_by: str | None = None
    """The key of the starting memory that superseded it, written down after it."""


@dataclass(frozen=True)
class ExtractionCase:
    """A turn, and what it should write down."""

    id: str
    category: Category
    character: str
    memories: tuple[StartingMemory, ...]
    history: tuple[Message, ...]
    said: str
    reply: Message
    must: tuple[tuple[str, ...], ...] = ()
    """Groups of keywords, each group in some new memory, any keyword of it."""
    must_not: tuple[str, ...] = ()
    """Keywords in no new memory."""
    supersedes: tuple[str, ...] = ()
    """The keys of the starting memories it supersedes."""
    keeps: tuple[str, ...] = ()
    """The keys of the starting memories that still hold after it."""
    nothing: bool = False
    """It writes nothing down."""


@dataclass(frozen=True)
class RecallCase:
    """A turn, and which memories it should find."""

    id: str
    category: Category
    character: str
    memories: tuple[StartingMemory, ...]
    history: tuple[Message, ...]
    said: str
    found: tuple[str, ...] = ()
    not_found: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryEval:
    extraction: tuple[ExtractionCase, ...]
    recall: tuple[RecallCase, ...]


def load_memory_eval(path: Path, characters: Mapping[str, Character]) -> MemoryEval:
    """The set at `path`, its characters checked against `characters`."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MemoryEvalError(f"找不到记忆评测集 {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise MemoryEvalError(f"记忆评测集 {path} 格式有误：{e}") from None
    ids: set[str] = set()
    extraction = []
    for entry in _list(data, "extraction", "记忆评测集"):
        case = _Case(entry, _EXTRACTION_FIELDS, ids, characters)
        must = _groups(entry, case.id)
        nothing = entry.get("nothing", False)
        if not isinstance(nothing, bool):
            raise MemoryEvalError(f"用例 {case.id} 的 nothing 应为 true 或 false")
        supersedes, keeps = case.memory_keys(entry, "supersedes"), case.memory_keys(entry, "keeps")
        must_not = _keywords(entry, "must_not", case.id)
        if nothing and (must or supersedes):
            raise MemoryEvalError(f"用例 {case.id} 写了 nothing = true，就不能再写 must 或 supersedes")
        if not (must or must_not or supersedes or keeps or nothing):
            raise MemoryEvalError(
                f"用例 {case.id} 没有任何检查项（must、must_not、supersedes、keeps、nothing）"
            )
        if both := set(supersedes) & set(keeps):
            raise MemoryEvalError(
                f"用例 {case.id} 的记忆 {'、'.join(sorted(both))} 同时写在 supersedes 和 keeps 里"
            )
        extraction.append(ExtractionCase(
            case.id, case.category, case.character, case.memories, case.history, case.said,
            _reply(entry, case.id, len(case.history)), must, must_not, supersedes, keeps, nothing,
        ))
    recall = []
    for entry in _list(data, "recall", "记忆评测集"):
        case = _Case(entry, _RECALL_FIELDS, ids, characters)
        if (not case.history and case.category != "continuity") or (
            case.history and case.history[-1].role != "character"
        ):
            raise MemoryEvalError(
                f"用例 {case.id} 的 history 最后一条应为角色的上一条回复"
                "（只有前情题可以没有上文，即对话刚开始）"
            )
        found, not_found = case.memory_keys(entry, "found"), case.memory_keys(entry, "not_found")
        if not (found or not_found):
            raise MemoryEvalError(f"用例 {case.id} 没有任何检查项（found、not_found）")
        if both := set(found) & set(not_found):
            raise MemoryEvalError(
                f"用例 {case.id} 的记忆 {'、'.join(sorted(both))} 同时写在 found 和 not_found 里"
            )
        recall.append(RecallCase(
            case.id, case.category, case.character, case.memories, case.history, case.said,
            found, not_found,
        ))
    return MemoryEval(tuple(extraction), tuple(recall))


_CASE_FIELDS = {"id", "category", "character", "memories", "history", "said", "note", "reviewed"}
_EXTRACTION_FIELDS = _CASE_FIELDS | {"reply", "must", "must_not", "supersedes", "keeps", "nothing"}
_RECALL_FIELDS = _CASE_FIELDS | {"found", "not_found"}
_MEMORY_FIELDS = {"key", "source", "text", "superseded_by"}


class _Case:
    """What extraction and recall cases have in common, read and checked."""

    def __init__(
        self, entry: Any, fields: set[str], ids: set[str], characters: Mapping[str, Character]
    ) -> None:
        """`entry` may have only `fields`, so a misspelt check is not quietly dropped."""
        if not isinstance(entry, dict):
            raise MemoryEvalError("记忆评测集的每个用例应为一张表")
        self.id = _text(entry, "id", "记忆评测集的用例")
        if self.id in ids:
            raise MemoryEvalError(f"用例 {self.id} 重复")
        ids.add(self.id)
        if unknown := entry.keys() - fields:
            raise MemoryEvalError(f"用例 {self.id} 里有不认识的项 {'、'.join(sorted(unknown))}")
        category = entry.get("category")
        if category not in get_args(Category):
            raise MemoryEvalError(
                f"用例 {self.id} 的 category 只能是 {'、'.join(get_args(Category))}，而不是 {category}"
            )
        self.category: Category = category  # pyright: ignore[reportAttributeAccessIssue]  # checked above
        self.character = _text(entry, "character", f"用例 {self.id}")
        if self.character not in characters:
            raise MemoryEvalError(f"用例 {self.id} 的角色 {self.character} 不在角色配置里")
        self.memories = _starting(entry, self.id, characters)
        self.history = _history(entry.get("history", []), self.id)
        self.said = _text(entry, "said", f"用例 {self.id}")

    def memory_keys(self, entry: dict, field: str) -> tuple[str, ...]:
        """The starting memories' keys `field` lists."""
        keys = _keywords(entry, field, self.id)
        if unknown := set(keys) - {m.key for m in self.memories}:
            raise MemoryEvalError(
                f"用例 {self.id} 的 {field} 引用了不存在的记忆 {'、'.join(sorted(unknown))}"
            )
        return keys


def _starting(entry: dict, case_id: str, characters: Mapping[str, Character]) -> tuple[StartingMemory, ...]:
    memories: list[StartingMemory] = []
    where = f"用例 {case_id} 的预置记忆"
    for m in _list(entry, "memories", f"用例 {case_id}"):
        if not isinstance(m, dict):
            raise MemoryEvalError(
                f"{where}每一条应为 {{ key = \"…\", source = \"…\", text = \"…\" }}"
            )
        if unknown := m.keys() - _MEMORY_FIELDS:
            raise MemoryEvalError(f"{where}里有不认识的项 {'、'.join(sorted(unknown))}")
        memory = StartingMemory(
            _text(m, "key", where), _text(m, "source", where), _text(m, "text", where),
            m.get("superseded_by"),
        )
        if memory.source not in characters:
            raise MemoryEvalError(
                f"{where} {memory.key} 的来源角色 {memory.source} 不在角色配置里"
            )
        if any(earlier.key == memory.key for earlier in memories):
            raise MemoryEvalError(f"{where}的 key {memory.key} 重复")
        if any(earlier.text == memory.text for earlier in memories):
            raise MemoryEvalError(f"{where}里有两条都是「{memory.text}」")
        memories.append(memory)
    for i, memory in enumerate(memories):
        later = memory.superseded_by
        if later is None:
            continue
        if not isinstance(later, str) or later not in {m.key for m in memories}:
            raise MemoryEvalError(f"{where} {memory.key} 被不存在的记忆 {later} 取代")
        if later not in {m.key for m in memories[i + 1 :]}:
            raise MemoryEvalError(
                f"{where} {memory.key} 被 {later} 取代，{later} 要写在它后面"
            )
    return tuple(memories)


def _text(entry: dict, field: str, where: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value:
        raise MemoryEvalError(f"{where}缺少 {field}")
    return value


def _list(entry: dict, field: str, where: str) -> list:
    value = entry.get(field, [])
    if not isinstance(value, list):
        raise MemoryEvalError(f"{where}的 {field} 应为列表")
    return value


def _keywords(entry: dict, field: str, case_id: str) -> tuple[str, ...]:
    value = entry.get(field, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise MemoryEvalError(f"用例 {case_id} 的 {field} 应为字符串列表")
    return tuple(value)


def _groups(entry: dict, case_id: str) -> tuple[tuple[str, ...], ...]:
    groups = []
    for group in _list(entry, "must", f"用例 {case_id}"):
        if isinstance(group, str) and group:
            groups.append((group,))
        elif isinstance(group, list) and group and all(isinstance(k, str) and k for k in group):
            groups.append(tuple(group))
        else:
            raise MemoryEvalError(f"用例 {case_id} 的 must 每一项应为一个关键词，或几个关键词的列表")
    return tuple(groups)


def _history(entries: Any, case_id: str) -> tuple[Message, ...]:
    if not isinstance(entries, list):
        raise MemoryEvalError(f"用例 {case_id} 的 history 应为列表")
    messages = []
    for i, entry in enumerate(entries):
        if isinstance(entry, dict) and entry.keys() == {"user"} and isinstance(entry["user"], str):
            messages.append(Message(i, "user", entry["user"], None, None, ""))
        elif (said := _said(entry)) is not None:
            messages.append(_reply_message(i, *said))
        else:
            raise MemoryEvalError(
                f"用例 {case_id} 的 history 每一条应为 {{ user = \"…\" }} 或 {{ ja = \"…\", zh = \"…\" }}"
            )
    return tuple(messages)


def _reply(entry: dict, case_id: str, index: int) -> Message:
    said = _said(entry.get("reply"))
    if said is None:
        raise MemoryEvalError(f"用例 {case_id} 缺少 reply（{{ ja = \"…\", zh = \"…\" }}）")
    return _reply_message(index, *said)


def _said(entry: Any) -> tuple[str, str] | None:
    """A reply's Japanese and subtitle, if `entry` is one."""
    if not isinstance(entry, dict) or entry.keys() != {"ja", "zh"}:
        return None
    ja, zh = entry["ja"], entry["zh"]
    return (ja, zh) if isinstance(ja, str) and ja and isinstance(zh, str) and zh else None


def _reply_message(index: int, ja: str, zh: str) -> Message:
    return Message(index, "character", None, ja, zh, "", [Segment(ja, zh)])


@dataclass(frozen=True)
class MemoryServices:
    """What memory runs on, the deployer's."""

    background_llm: ChatLLM
    embedder: Embedder
    reranker: Reranker
    rerank_thresholds: RerankThresholds
    judge_model: JudgeModel
    judge_thresholds: JudgeThresholds


@dataclass(frozen=True)
class ExtractionRun:
    """What one run of an extraction case wrote down."""

    written: tuple[str, ...]
    """The new memories, oldest first."""
    superseded: tuple[str, ...]
    """The keys of the starting memories superseded after it."""
    problems: tuple[str, ...] = ()
    error: str | None = None
    """Why the background model wrote nothing down, if it failed."""

    @property
    def passed(self) -> bool:
        return self.error is None and not self.problems


@dataclass(frozen=True)
class ExtractionResult:
    case: ExtractionCase
    runs: tuple[ExtractionRun, ...]

    @property
    def passed(self) -> bool:
        """Whether most runs passed."""
        return sum(r.passed for r in self.runs) * 2 > len(self.runs)


@dataclass(frozen=True)
class Judged:
    """What the judge model made of a turn."""

    need: Need | None
    """None if it failed, and the turn looked everything up."""
    answer: RetrievalAnswer | None
    error: str | None = None


@dataclass(frozen=True)
class RecallResult:
    case: RecallCase
    judged: Judged
    found: tuple[str, ...]
    """The keys of the memories found, most relevant first."""
    problems: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class MemoryEvalResults:
    extraction: list[ExtractionResult]
    recall: list[RecallResult]

    def to_json(self, summary: dict) -> dict:
        """The summary and every case's outcome, to commit as the baseline."""
        return {
            "summary": summary,
            "extraction": [
                {
                    "id": r.case.id,
                    "category": r.case.category,
                    "passed": r.passed,
                    "runs": [
                        {
                            "passed": run.passed,
                            "written": list(run.written),
                            "superseded": list(run.superseded),
                            "problems": list(run.problems),
                            "error": run.error,
                        }
                        for run in r.runs
                    ],
                }
                for r in self.extraction
            ],
            "recall": [
                {
                    "id": r.case.id,
                    "category": r.case.category,
                    "passed": r.passed,
                    "found": list(r.found),
                    "problems": list(r.problems),
                    "judge": {
                        "need": r.judged.need,
                        "probabilities": None if r.judged.answer is None else {
                            "needs_retrieval": r.judged.answer.needs_retrieval,
                            "recall": r.judged.answer.recall,
                            "about_producer": r.judged.answer.about_producer,
                        },
                        "error": r.judged.error,
                    },
                }
                for r in self.recall
            ],
        }


async def evaluate(
    memory_eval: MemoryEval,
    characters: Mapping[str, Character],
    services: MemoryServices,
    workdir: Path,
    progress: Callable[[str], None] | None = None,
) -> MemoryEvalResults:
    """Take every case's turns, one case at a time, each run on a scratch database in
    `workdir`, telling `progress` which case is next."""
    workdir.mkdir(parents=True, exist_ok=True)
    # No references to look up: a turn needing them finds only memories.
    references = Database(workdir / "references.db")
    total = len(memory_eval.extraction) + len(memory_eval.recall)
    try:
        sources = ReferenceSources(
            services.embedder, [], StoryCorpus(references, services.embedder), None
        )
        extraction = []
        for i, case in enumerate(memory_eval.extraction):
            if progress is not None:
                progress(f"[{i + 1}/{total}] {case.id}")
            extraction.append(ExtractionResult(case, tuple([
                await _extract(case, workdir / f"extraction-{i}-{run}.db", characters, services)
                for run in range(RUNS)
            ])))
        recall = []
        for i, case in enumerate(memory_eval.recall):
            if progress is not None:
                progress(f"[{len(extraction) + i + 1}/{total}] {case.id}")
            recall.append(
                await _recall(case, workdir / f"recall-{i}.db", characters, services, sources)
            )
    finally:
        await references.close()
    return MemoryEvalResults(extraction, recall)


async def _extract(
    case: ExtractionCase, path: Path, characters: Mapping[str, Character],
    services: MemoryServices,
) -> ExtractionRun:
    scratch = _Scratch(path, characters, services)
    try:
        keys = await scratch.start_with(case.memories)
        model = _RecordedBackground(services.background_llm)
        error = None
        try:
            await scratch.take_turn(case.character, model, case.history, case.said, case.reply)
        except TimeoutError:
            error = f"背景模型 {WRITE_TIMEOUT:g} 秒内没有写完"
        if model.error is not None:
            error = f"背景模型出错：{model.error}"
        elif error is None and parse_memories(model.answer) is None:
            error = f"背景模型没有给出记忆的 JSON：{model.answer[:200]}"
        remembered = await scratch.memories.entries(scratch.user)
    finally:
        await scratch.aclose()
    starting = set(keys.values())
    written = tuple(m.text for m in reversed(remembered) if m.id not in starting)
    by_id = {m.id: m for m in remembered}
    superseded = tuple(
        key for key, id in keys.items()
        if (m := by_id.get(id)) is not None and m.superseded_by is not None
    )
    if error is not None:
        return ExtractionRun(written, superseded, error=error)
    return ExtractionRun(
        written, superseded, tuple(check_extraction(case, written, set(superseded)))
    )


def check_extraction(
    case: ExtractionCase, written: Sequence[str], superseded: set[str]
) -> list[str]:
    """What is wrong with `case`'s turn having written down `written`, the starting
    memories of the keys `superseded` superseded after it."""
    problems = [
        f"没有写下{'或'.join(f'「{k}」' for k in group)}"
        for group in case.must
        if not any(k in text for k in group for text in written)
    ]
    problems += [f"写下了「{k}」" for k in case.must_not if any(k in text for text in written)]
    problems += [f"{key} 没有被取代" for key in case.supersedes if key not in superseded]
    problems += [f"{key} 被取代了" for key in case.keeps if key in superseded]
    if case.nothing and written:
        problems.append("不该写下任何记忆")
    return problems


async def _recall(
    case: RecallCase, path: Path, characters: Mapping[str, Character],
    services: MemoryServices, sources: ReferenceSources,
) -> RecallResult:
    scratch = _Scratch(path, characters, services)
    judge = _RecordedJudge(ModelRetrievalJudge(services.judge_model, services.judge_thresholds))
    retrieval = Retrieval(
        judge, sources, services.reranker, lambda: services.rerank_thresholds.references
    )
    try:
        await scratch.start_with(case.memories)
        turn = await scratch.memories.begin(
            await scratch.conversation(case.character), TIMEZONE, _ScriptedBackground(None)
        )
        retrieved = await retrieval.retrieve(
            characters[case.character], case.history, case.said, turn
        )
    finally:
        await scratch.aclose()
    found = _found(retrieved.memories_prompt, case.memories)
    problems = [f"没有想起 {key}" for key in case.found if key not in found]
    problems += [f"想起了不该想起的 {key}" for key in case.not_found if key in found]
    return RecallResult(case, judge.judged, found, tuple(problems))


def _found(prompt: str | None, memories: Sequence[StartingMemory]) -> tuple[str, ...]:
    """The keys of the memories the prompt section carries, in its order, each on a line
    of its own followed by who it was told to (see `memory._memories_prompt`). Raises
    MemoryEvalError if it carries memories but none can be told apart, as its format has
    changed, rather than have every case fail as finding nothing."""
    found = tuple(
        m.key
        for line in (prompt or "").splitlines()
        for m in memories
        if line.startswith(f"- {m.text}（")
    )
    if prompt and not found:
        raise MemoryEvalError(
            "想起的记忆认不出是哪几条：记忆在提示词里的写法变了，要跟着改 memory_eval._found"
        )
    return found


class _Scratch:
    """A scratch database with one made-up user, and the memories module on it."""

    def __init__(
        self, path: Path, characters: Mapping[str, Character], services: MemoryServices
    ) -> None:
        self._database = Database(path)
        self._store = Store(self._database)
        self.user = ""
        """The made-up user, once `start_with` has made them."""
        self.memories = Memories(
            self._database, characters, lambda: NOW, embedder=services.embedder,
            reranker=services.reranker, min_relevance=lambda: services.rerank_thresholds.memories,
        )
        self._conversations: dict[str, Conversation] = {}

    async def conversation(self, character_id: str) -> Conversation:
        if character_id not in self._conversations:
            self._conversations[character_id] = await self._store.create_conversation(
                self.user, character_id
            )
        return self._conversations[character_id]

    async def start_with(self, memories: Sequence[StartingMemory]) -> dict[str, int]:
        """Make the user, then write `memories` down, each in a turn with its source
        character, in order, superseding those it superseded; returns each one's id by
        key."""
        self.user = (await self._store.create_user("评测用的制作人")).id
        for memory in memories:
            superseded = [m.text for m in memories if m.superseded_by == memory.key]
            model = _ScriptedBackground(memory.text, superseded)
            await self.take_turn(
                memory.source, model, (), memory.text, _reply_message(0, "はい", "好的"),
            )
            if model.unseen:
                raise MemoryEvalError(
                    f"预置的记忆 {memory.key} 要取代的记忆没有交给背景模型（只交给最相关的"
                    f" {SHOWN_MEMORIES} 条）：{'、'.join(model.unseen)}"
                )
        ids = {m.text: m.id for m in await self.memories.entries(self.user)}
        if missing := [m.text for m in memories if m.text not in ids]:
            raise MemoryEvalError(f"预置的记忆没有写进去：{'、'.join(missing)}")
        return {m.key: ids[m.text] for m in memories}

    async def take_turn(
        self, character_id: str, llm: ChatLLM, history: Sequence[Message], said: str,
        reply: Message,
    ) -> None:
        """Take a turn with the character, writing down with `llm`, and wait until it is
        written down. Raises TimeoutError if that takes over `WRITE_TIMEOUT`."""
        written = asyncio.Event()
        turn = await self.memories.begin(
            await self.conversation(character_id), TIMEZONE, llm, lambda: written.set
        )
        turn.write_down(history, said, reply)
        await asyncio.wait_for(written.wait(), WRITE_TIMEOUT)

    async def aclose(self) -> None:
        await self.memories.aclose()
        await self._database.close()


class _ScriptedBackground:
    """A background model that writes down just `text` (or nothing), superseding the memories
    `superseded` by the numbers it is shown them under; those it is not shown are listed
    in `unseen`."""

    def __init__(self, text: str | None, superseded: Sequence[str] = ()) -> None:
        self._text = text
        self._superseded = superseded
        self.unseen: list[str] = []

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        # As `memory._extraction_prompt` numbers them: 「1. 制作人……」 under 「# 已经记住的事」.
        section = messages[-1].content.split("# 已经记住的事")[-1].split("\n# ")[0]
        shown = {
            text: int(number)
            for number, _, text in (line.partition(". ") for line in section.splitlines())
            if number.isdigit()
        }
        self.unseen = [t for t in self._superseded if t not in shown]
        supersedes = [shown[t] for t in self._superseded if t in shown]
        written = [{"text": self._text, "supersedes": supersedes}] if self._text else []
        yield json.dumps({"memories": written}, ensure_ascii=False)

    async def aclose(self) -> None:
        pass


class _RecordedBackground:
    """A background model, keeping its answer, or why it failed."""

    def __init__(self, llm: ChatLLM) -> None:
        self._llm = llm
        self.answer = ""
        self.error: str | None = None

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        try:
            async for chunk in self._llm.stream(messages, tools, forbid_calls=forbid_calls):
                if isinstance(chunk, str):
                    self.answer += chunk
                yield chunk
        except Exception as e:
            self.error = str(e) or type(e).__name__
            raise

    async def aclose(self) -> None:
        pass


class _RecordedJudge:
    """The judge model as a turn asks it, keeping what it made of the latest turn."""

    def __init__(self, judge: ModelRetrievalJudge) -> None:
        self._judge = judge
        self.judged = Judged(None, None)

    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = False,
        photo: bool = False,
    ) -> tuple[Need, None, bool]:
        """Never about affinity nor photos, which the memory eval has nothing to do with."""
        try:
            answer = await self._judge.ask(character, history, text)
        except JudgeError as e:
            self.judged = Judged(None, None, str(e))
            raise
        need = self._judge.decide(answer)
        self.judged = Judged(need, answer)
        return need, None, False


def summarize(results: MemoryEvalResults, models: Mapping[str, str]) -> dict:
    """The passes by category, with the models they were had on, as JSON."""
    categories = {}
    for category in get_args(Category):
        extraction = [r for r in results.extraction if r.case.category == category]
        recall = [r for r in results.recall if r.case.category == category]
        both: list[ExtractionResult | RecallResult] = [*extraction, *recall]
        categories[category] = {
            "name": CATEGORY_NAMES[category],
            "cases": len(both),
            "passed": sum(r.passed for r in both),
            "extraction": _tally(extraction),
            "recall": _tally(recall),
            "failed": [r.case.id for r in both if not r.passed],
        }
    every: list[ExtractionResult | RecallResult] = [*results.extraction, *results.recall]
    return {
        "models": dict(models),
        "runs": RUNS,
        "categories": categories,
        "total": _tally(every),
    }


def _tally(results: Sequence[ExtractionResult | RecallResult]) -> dict:
    return {"cases": len(results), "passed": sum(r.passed for r in results)}


def report(results: MemoryEvalResults, summary: dict) -> str:
    """The passes by category, then every case: each extraction's runs that failed, and
    each recall's judge model's verdict and the memories found."""
    models = summary["models"]
    lines = [
        f"背景模型：{models.get('background', '?')}　判断模型：{models.get('judge', '?')}"
        f"　向量化：{models.get('embedding', '?')}　重排：{models.get('rerank', '?')}",
        f"提炼题每题跑 {summary['runs']} 次，过半通过才算通过",
        "",
        "## 各题型通过数（提炼、召回、合计）",
    ]
    for category, s in summary["categories"].items():
        e, r = s["extraction"], s["recall"]
        note = "（只作参考，不作为门槛）" if category == "continuity" else ""
        lines.append(
            f"- {s['name']}：提炼 {e['passed']}/{e['cases']}，召回 {r['passed']}/{r['cases']}，"
            f"合计 {s['passed']}/{s['cases']}{note}"
        )
    total = summary["total"]
    lines.append(f"- 全部：{total['passed']}/{total['cases']}")
    lines += ["", "## 逐条提炼（✓ 通过，✗ 未通过，! 出错）"]
    for result in results.extraction:
        passes = sum(r.passed for r in result.runs)
        lines.append(
            f"{'✓' if result.passed else '✗'} {result.case.id}［{CATEGORY_NAMES[result.case.category]}］"
            f"{passes}/{len(result.runs)} 次通过"
        )
        for i, run in enumerate(result.runs):
            if run.passed:
                continue
            what = run.error if run.error is not None else "；".join(run.problems)
            written = "、".join(f"「{t}」" for t in run.written) or "（没有）"
            lines.append(
                f"    第 {i + 1} 次 {'!' if run.error is not None else '✗'} {what}　写下了：{written}"
            )
    lines += [
        "",
        "## 逐条召回（判断：nothing 不找记忆，memories 只找记忆，references 查资料也找记忆，"
        "recall 回忆剧情不找记忆）",
    ]
    for result in results.recall:
        judged = result.judged
        if judged.answer is None:
            verdict = f"判断失败（照常查资料和记忆）：{judged.error}"
        else:
            verdict = (
                f"判断 {judged.need}（needs_retrieval {judged.answer.needs_retrieval:.3f}"
                f" recall {judged.answer.recall:.3f}"
                f" about_producer {judged.answer.about_producer:.3f}）"
            )
        found = "、".join(result.found) or "（没有）"
        problems = f"　{'；'.join(result.problems)}" if result.problems else ""
        lines.append(
            f"{'✓' if result.passed else '✗'} {result.case.id}［{CATEGORY_NAMES[result.case.category]}］"
            f"{verdict}　想起了：{found}{problems}"
        )
    return "\n".join(lines)

