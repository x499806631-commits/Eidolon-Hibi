"""Activity records (GLOSSARY.md「运行记录」): one for each turn, each call and each
reach-out (GLOSSARY.md「搭话」), saying how it turned out, and one for each call to an
outside service, saying which service, whose key, whether it failed and how, how long it
took and what it was billed for; an LLM's, also how much of its input hit the provider's
prompt cache, how soon its first word came and the reasoning effort it was asked for. They
hold nothing anyone said. Only the admin console reads them: its dashboard (`dashboard`), and its users page, for each user's
recent usage (`admin_users_api`).

A turn's record is open while the turn runs (`ActivityLog.begin`, then `current`), and the
service calls made meanwhile, in the turn's context, are recorded under it; its outcome
is told it as the turn's use of the limit is (`admission`), and it is written once it
has one. A turn recorded keeps what the judge model made of it for affinity, if it judged
it, and whether it began a cooldown (`Recording.judged`); never what was judged. A service call made outside any turn, as writing down memories after one is
(`detached`), is recorded on its own.

A call's record is told its outcome as the call's use of the limit is (`call_outcome`),
with how long it lasted and why it ended. The turns taken in it each have a record of
their own under the call's (`Recording.turn`), written with it; its speech recognition,
synthesis and emotion judging are recorded under the call (`Recording.add`), not a turn.

A reach-out's record says whether the user answered it: had a turn, or a call that connected,
in its conversation after it and before the next. The store marks it so as it records the
turn or keeps the call (`Store.add_turn`, `Store.keep_call`), by the record its reach-out
names; or as that is kept, should they have been in touch since (`Store.reach_out_recorded_as`).

A photo she sends (`photos`) is generated in the context of the turn it was begun in, and
often comes out after the turn is over: its request to the image service is recorded under
the turn all the same, with the turn's record if that is still to be written, under it once
written (`ActivityLog.record`). It is billed one picture where the service answered with
one, of the size asked for or not, and none where it failed or took too long. It says how
many tags were stripped from her photo tag for crossing the line, and which part of it each
crossed (`photo_prompt.Crossing`); never the tags.

The services are timed by thin wrappers around their clients (`metered_llm`,
`MeteredJudge`, `MeteredEmbedder`, `MeteredReranker`), which also open a `Billing` for
the client to tell what the request is billed for. A call abandoned part-way, as when the
user leaves, is not a failure; an attempt the client made again (`retry`) is a call of
its own, failed.

Records are kept for 90 days (`KEPT`); older ones are deleted whenever a turn begins, as
uses of the limit are. A deleted user's records stay, for the numbers not to change, but
no longer say whose they were (`database.ANONYMIZED_USER_DATA`).
"""

import asyncio
import json
import logging
import re
import secrets
import sqlite3
import threading
from collections.abc import AsyncIterator, Awaitable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal, TypeVar
from urllib.parse import urlsplit

import httpx

from eidolon.affinity import EndsFor, Judged
from eidolon.billing import Billing, billing
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.config import (
    BailianJudgeSettings,
    CloudflareJudgeSettings,
    ImageSettings,
    JudgeSettings,
    LLMSettings,
    SERVICE_NAMES,
    ReasoningEffort,
    ServiceName,
    Settings,
)
from eidolon.database import connect
from eidolon.embedding import Document, Embedder
from eidolon.judge import CLOUDFLARE_API_URL
from eidolon.llm import ChatLLM, ChatMessage, Reasoning, Streamed, Tool
from eidolon.photo_prompt import CROSSINGS, Crossing
from eidolon.rerank import Reranker
from eidolon.retry import ServiceError, on_retry
if TYPE_CHECKING:  # retrieval finds memories, which write down detached from turns
    from eidolon.affinity_judge import AffinityJudge
    from eidolon.ambience import AmbienceJudge, AmbienceKind, AmbienceMoment
    from eidolon.emotion import EmotionAnswer, EmotionJudge, SegmentToJudge
    from eidolon.promise_judge import PromiseJudge
    from eidolon.search_judge import SearchJudge
    from eidolon.web_search import Found, Search
    from eidolon.retrieval import Need, RetrievalJudge
from eidolon.store import Message

log = logging.getLogger(__name__)

SERVICES: tuple[ServiceName, ...] = SERVICE_NAMES
LLM_SERVICES: tuple[ServiceName, ...] = ("chat_llm", "memory_llm")
"""The LLMs among them (`llm_origin`): a user's own is recorded as `OWN`, and each call
says how soon its first word came and at which reasoning effort."""

RecordKind = Literal["turn", "call", "reach_out", "letter", "diary"]
"""A reach-out is neither a turn nor a call, nor counted as one anywhere; it is always on the
deployer's keys."""
Result = Literal["success", "failed", "rejected", "left", "not_connected"]
"""How a turn or call turned out (GLOSSARY.md「回合」「接通」): a turn succeeded, failed on
a service, was rejected (over the limit, or its input), or the user left before it was
done; a call also may never have connected. A reach-out succeeds or fails."""
RESULTS: tuple[Result, ...] = ("success", "failed", "rejected", "left", "not_connected")

ErrorKind = Literal[
    "timeout", "connection", "rate_limited", "unauthorized", "refused", "server_error",
    "unreadable", "other",
]
"""What kind of failure a service call was, never what the service said: that may quote
what it was sent."""

TAKEN = "result != 'rejected'"
"""Which records, in SQL over `activity_records`, are of a turn or call the user took: all
but those rejected, which were refused rather than taken. The admin's users page counts
these, and sorts the users by them (`Store.find_users`)."""

TURNS_AND_CALLS = "kind IN ('turn', 'call')"
"""Which records, in SQL over `activity_records`, are of turns and calls: not of reach-outs,
which the user did not take."""

JUDGEMENT_COLUMNS = ("affinity_level", "out_of_line", "manipulation", "cooled")
"""What a turn's record keeps of how the judge model judged it, as its columns are named."""

KEPT = timedelta(days=90)
"""How long activity records are kept."""

_SCHEMA = """
-- Times are epoch seconds, so that a range is a plain range query. A record's user is
-- NULL once the user is deleted, and `anonymous_id` then tells their records apart from
-- other users' without saying whose they were. `call_id`, `duration_s` and `end_reason`
-- are for calls: a call's turns name its record in `call_id`, and a call has the other two.
-- `own_keys` is, for a turn, whether its LLM was the user's own; for a call, whether all
-- of its LLM, speech recognition and synthesis were (GLOSSARY.md「运行记录」). The last four
-- are what the judge model made of a turn recorded (GLOSSARY.md「好感度」): its level, 0–4,
-- whether out of line, whether it tried to dictate her affinity, and whether the turn began
-- a cooldown; all NULL for a turn not judged, or not recorded. `ended_conversation` is why
-- she ended the conversation after a typed turn (`affinity.EndsFor`), NULL if she did not.
-- `answered` is whether a reach-out was answered (GLOSSARY.md「搭话」): the user had a turn,
-- or a call connected, in its conversation after it and before the next; the store marks it
-- so, by the record its reach-out keeps (`Store.add_turn`, `Store.keep_call`,
-- `Store.reach_out_recorded_as`). 0 for others.
CREATE TABLE IF NOT EXISTS activity_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    user_id TEXT REFERENCES users(id),
    anonymous_id TEXT,
    character_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    result TEXT NOT NULL,
    own_keys INTEGER NOT NULL DEFAULT 0,
    time_to_speak_ms REAL,
    call_id INTEGER,
    duration_s REAL,
    end_reason TEXT,
    affinity_level INTEGER,
    out_of_line INTEGER,
    manipulation INTEGER,
    cooled INTEGER,
    ended_conversation TEXT,
    answered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS activity_records_by_time ON activity_records(started_at);
CREATE INDEX IF NOT EXISTS activity_records_by_user ON activity_records(user_id, started_at);
-- Each call to an outside service; `record_id` is the turn or call it was made in, NULL
-- for one made outside any. `error` is NULL when it succeeded; the usage columns are NULL
-- when the service did not say; `latency_ms` is NULL for speech recognition, a stream as
-- long as the call, and for speech synthesis that never answered. `first_token_ms`,
-- `reasoning_effort` and `usage_cached` are an LLM's alone: how soon its first text or tool
-- call came (NULL if none did), the effort it was asked to think at (NULL for none asked),
-- and how much of `usage_input` the provider read from its prompt cache (NULL if it did
-- not say). `stripped_tags` is image generation's alone: how many tags were stripped from
-- her photo tag for crossing the line, by which part of it, as a JSON object
-- (`{"nudity": 1}`); NULL where none were.
CREATE TABLE IF NOT EXISTS service_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER,
    at REAL NOT NULL,
    service TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    own_key INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    latency_ms REAL,
    usage_unit TEXT,
    usage_input REAL,
    usage_output REAL,
    first_token_ms REAL,
    reasoning_effort TEXT,
    usage_cached REAL,
    stripped_tags TEXT
);
CREATE INDEX IF NOT EXISTS service_calls_by_time ON service_calls(at);
CREATE INDEX IF NOT EXISTS service_calls_by_record ON service_calls(record_id);
"""


@dataclass(frozen=True)
class Origin:
    """Which service a call is to: the host it is at (`provider`), the model, and whether
    on the user's own key. A user's own LLM is named neither by host nor by model (`OWN`);
    an LLM's calls say the effort it was asked to think at, if any."""

    service: ServiceName
    provider: str
    model: str
    own_key: bool = False
    effort: ReasoningEffort | None = None


OWN = "own"
"""What a call to a user's own LLM is recorded as being to, as its provider and its model:
the server keeps no note of which host or model a user brings (ADR-0017)."""


def host(url: str | None) -> str:
    """The provider a service at `url` is: its host."""
    return (urlsplit(url).hostname or "unknown") if url else "unknown"


def llm_origin(service: ServiceName, settings: LLMSettings, own_key: bool = False) -> Origin:
    """The LLM `settings` describe, as its service calls are recorded: a user's own (with
    `own_key`) only as `OWN`, whichever host and model it is; either at its effort."""
    effort = settings.reasoning_effort
    if own_key:
        return Origin(service, OWN, OWN, own_key=True, effort=effort)
    return Origin(service, host(settings.base_url), settings.model, effort=effort)


@dataclass(frozen=True)
class ServiceCall:
    origin: Origin
    at: datetime
    error: ErrorKind | None
    latency_ms: float | None
    billed: Billing
    first_token_ms: float | None = None
    """For an LLM, how soon its first text or tool call came; None if none did."""
    stripped: dict[Crossing, int] | None = None
    """For image generation, how many tags were stripped from her photo tag, by which part
    of the line each crossed; None if none were."""


CallEndReason = Literal[
    "hung_up", "left", "duration_limit", "turn_limit", "character_hung_up", "banned",
    "deleted", "failed", "shutdown",
]
"""Why a call ended: the user hung up, or left the conversation page (or lost the
connection) without hanging up; it reached its time or its turn limit; she hung up, for
whichever reason; the user was banned, or deleted; a service, or the connection, failed;
the server shut down."""
CALL_END_REASONS: tuple[CallEndReason, ...] = (
    "hung_up", "left", "duration_limit", "turn_limit", "character_hung_up", "banned",
    "deleted", "failed", "shutdown",
)


@dataclass(frozen=True)
class RecordRow:
    """An activity record as read back one by one (`ActivityLog.since`,
    `ActivityLog.of_user`): whose (any stable id; the same for one user's records, deleted or
    not), which character, when, how it turned out."""

    kind: RecordKind
    who: str | None
    character_id: str
    started_at: datetime
    result: Result
    own_llm: bool | None
    """For a turn (one in a call too), whether the character's LLM was the user's own;
    None for a call (GLOSSARY.md「运行记录」)."""
    exempt: bool | None
    """For a call, whether it brought the user's own keys for every one of the exempting
    keys (GLOSSARY.md「免限额密钥」), so that it counted towards no limit; None for a turn
    (in a call or not), whose record keeps only `own_llm`."""
    time_to_speak_ms: float | None
    duration_s: float | None = None
    """How long a call lasted once connected; None for a turn, or a call never connected."""
    end_reason: CallEndReason | None = None
    """Why a call ended; None for a turn, or a call refused."""
    in_call: bool = False
    """Whether a turn was taken in a call."""
    judged: Judged | None = None
    """What the judge model made of a turn recorded; None if it did not judge it, or the turn
    was not recorded."""
    cooled: bool = False
    """Whether a turn began a cooldown."""
    ended_conversation: EndsFor | None = None
    """Why she ended the conversation after a typed turn; None if she did not."""
    answered: bool = False
    """Whether a reach-out was answered: the user had a turn, or a call connected, in its
    conversation after it and before the next."""

    @property
    def own_keys(self) -> bool:
        """Whether the record counts as on the user's own keys, as the dashboard and the
        admin's users page show it: a turn's own LLM, a call's being exempt."""
        return bool(self.own_llm if self.kind == "turn" else self.exempt)


@dataclass(frozen=True)
class CallRow:
    """A service call as read back one by one (`ActivityLog.since`)."""

    service: ServiceName
    provider: str
    model: str
    own_key: bool
    at: datetime
    error: ErrorKind | None
    latency_ms: float | None
    usage_unit: str | None
    usage_input: float | None
    usage_output: float | None
    first_token_ms: float | None = None
    """For an LLM, how soon its first text or tool call came; None if none did."""
    effort: ReasoningEffort | None = None
    """For an LLM, the effort it was asked to think at; None for none asked."""
    usage_cached: float | None = None
    """For an LLM, how much of its input the provider read from its prompt cache; None if
    it did not say."""
    stripped: dict[Crossing, int] | None = None
    """For image generation, how many tags were stripped from her photo tag, by which part
    of the line each crossed; None if none were."""


PERCENTILES = (50, 90, 95)
"""The percentiles a `Spread` has."""


@dataclass(frozen=True)
class Spread:
    """How many of some values were known, and their nearest-rank `PERCENTILES`; none of
    none."""

    known: int
    percentiles: dict[int, float]


@dataclass(frozen=True)
class RecordGroup:
    """Activity records alike in all the dashboard counts them by, and how many there are."""

    kind: RecordKind
    character_id: str
    result: Result
    own_keys: bool
    """`RecordRow.own_keys`: for a turn, its own LLM; for a call, its being exempt."""
    in_call: bool
    end_reason: CallEndReason | None
    judged: Judged | None
    cooled: bool
    ended_conversation: bool
    """Whether she ended the conversation after a typed turn, whatever for."""
    answered: bool
    count: int


@dataclass(frozen=True)
class CallGroup:
    """Service calls alike in all the dashboard counts them by, summed up: made in one
    stretch of its range, to one service, provider and model on one key, failed one way or
    not, billed in one unit, saying or not what for, with the same tags stripped."""

    stretch: int
    """Which stretch of the range they were made in, as an index into its starts."""
    service: ServiceName
    provider: str
    model: str
    own_key: bool
    error: ErrorKind | None
    usage_unit: str | None
    input_told: bool
    """Whether the service said what their input was billed for."""
    output_told: bool
    """Whether it said what their output was."""
    stripped: dict[Crossing, int] | None
    """For image generation, how many tags were stripped from each one's photo tag, by
    which part of the line each crossed; None if none were."""
    calls: int
    usage_input: float | None
    """What their input was billed for, summed; None where none said, as the next two."""
    usage_output: float | None
    usage_cached: float | None

    @property
    def billed(self) -> bool:
        """Whether the service said what they were billed for."""
        return self.usage_unit is not None and (self.input_told or self.output_told)


@dataclass(frozen=True)
class Taken:
    """How many turns and calls were taken in a stretch of the dashboard's range, and by
    how many users."""

    turns: int
    calls: int
    users: int


@dataclass(frozen=True)
class Tally:
    """The activity records and service calls after a moment, summed up for the dashboard
    (`ActivityLog.tally`)."""

    records: list[RecordGroup]
    calls: list[CallGroup]
    """In the order their first calls were made."""
    users: int
    """How many users took any turn or call."""
    cooled_users: int
    """How many of them a judged turn began a cooldown for."""
    stretches: list[Taken]
    """What was taken in each stretch of the range."""
    latency: dict[tuple[ServiceName, bool], Spread]
    """How long each service took, on the deployer's keys and on users' own apart."""
    first_token: dict[tuple[ServiceName, bool], Spread]
    """How soon each LLM's first word came, so too."""
    time_to_speak: Spread
    """How soon the character began to speak, in the turns where she did."""
    lasted: Spread
    """How long the calls that connected lasted, in seconds."""
    lasted_within: list[int]
    """How many of them lasted within each of the bounds asked for."""


_current: ContextVar["Recording | None"] = ContextVar("activity_record", default=None)


def detached() -> Context:
    """A context for work started in a turn that is not part of it, as writing down
    memories: its service calls are recorded on their own."""
    context = copy_context()
    context.run(_current.set, None)
    return context


def recording() -> "Recording | None":
    """The turn's or call's record that service calls made now, in this context, are
    recorded under; None outside any."""
    return _current.get()


class Recording:
    """One turn's or call's activity record while it runs: the service calls made in it,
    and, for a turn, when the character began to speak (GLOSSARY.md「开口」); for a call,
    the turns taken in it. Written once finished; a turn in a call, with the call."""

    def __init__(
        self,
        log: "ActivityLog",
        kind: RecordKind,
        user_id: str,
        character_id: str,
        started_at: datetime,
        own_llm: bool,
        exempt: bool | None,
        call: "Recording | None" = None,
    ) -> None:
        self._log = log
        self.kind: RecordKind = kind
        self.user_id = user_id
        self.character_id = character_id
        self.started_at = started_at
        self.own_llm = own_llm
        """Whether the character's LLM is the user's own: what a turn's record keeps of
        whose keys it ran on (GLOSSARY.md「运行记录」)."""
        self.exempt = exempt
        """Whether it counts towards no limit, on the user's own keys (GLOSSARY.md「免限额密钥」):
        what a call's record keeps of whose keys it ran on. A turn begun on its own has it too,
        though its record keeps `own_llm` instead; a turn in a call has None."""
        self.time_to_speak_ms: float | None = None
        self.calls: list[ServiceCall] = []
        self.finished = False
        self.result: Result | None = None
        self.duration_s: float | None = None
        self.end_reason: CallEndReason | None = None
        self.judged_as: Judged | None = None
        self.cooled = False
        self.ended_conversation: EndsFor | None = None
        self.call = call
        """The call this turn was taken in, if any."""
        self.turns: list[Recording] = []
        """For a call, the turns taken in it."""
        self.row_id: int | None = None  # once written
        self._started = perf_counter()

    @contextmanager
    def current(self) -> Iterator[None]:
        """Record the service calls made meanwhile, in this context, under this record."""
        previous = _current.get()
        _current.set(self)
        try:
            yield
        finally:
            _current.set(previous)

    def spoke(self) -> None:
        """The character began to speak (only the first time counts)."""
        if self.time_to_speak_ms is None:
            self.time_to_speak_ms = (perf_counter() - self._started) * 1000

    def judged(self, judged: Judged, cooled: bool, ended: EndsFor | None = None) -> None:
        """What the judge model made of the turn, now recorded, whether it began a cooldown,
        and why she ended the conversation after it, if she did."""
        self.judged_as, self.cooled, self.ended_conversation = judged, cooled, ended

    def turn(self, own_llm: bool) -> "Recording":
        """Open the record of a turn taken in this call, on the user's own LLM or not."""
        turn = Recording(
            self._log, "turn", self.user_id, self.character_id, self._log.now(), own_llm,
            None, call=self,
        )
        self.turns.append(turn)
        return turn

    def add(
        self, origin: Origin, error: ErrorKind | None, latency_ms: float | None, billed: Billing
    ) -> None:
        """A service call made in this call (or turn) has ended, having taken `latency_ms`."""
        self._log.record(ServiceCall(origin, self._log.now(), error, latency_ms, billed), self)

    def finish(
        self,
        result: Result,
        duration_s: float | None = None,
        end_reason: CallEndReason | None = None,
    ) -> None:
        """Write the record, as `result` (a call's with how long it lasted and why it
        ended), with its service calls; only the first time. A turn in a call is written
        with the call, or at once if the call's record has been written already."""
        if self.finished:
            return
        self.finished = True
        self.result, self.duration_s, self.end_reason = result, duration_s, end_reason
        if self.call is None or self.call.finished:
            self._log._write(self)


class ActivityLog:
    """Every turn's activity record and every service call's, kept in the database."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self._db = connect(path)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            _let_latency_be_unknown(self._db)
            _add_llm_columns(self._db)
            _add_stripped_tags_column(self._db)
            _add_judgement_columns(self._db)
            _add_answered_column(self._db)
            _forget_own_llms(self._db)
        # Summing up a long range takes a while: on a connection of its own, records are
        # written meanwhile, not kept waiting.
        self._reader = connect(path)
        self._reader.execute("PRAGMA query_only = ON")
        self._reading = threading.Lock()
        self._clock = clock

    def now(self) -> datetime:
        return self._clock()

    def begin(
        self, kind: RecordKind, user_id: str, character_id: str, *, own_llm: bool, exempt: bool
    ) -> Recording:
        """Open the record of a turn or call the user begins with `character_id`, on their
        own LLM or not and exempt from the limit or not: a turn's record keeps the
        first, a call's the second (GLOSSARY.md「运行记录」). Forgets every record older
        than `KEPT`."""
        now = self._clock()
        self._forget_before(now - KEPT)
        return Recording(self, kind, user_id, character_id, now, own_llm, exempt)

    def call(
        self,
        origin: Origin,
        started: float,
        error: ErrorKind | None,
        billed: Billing,
        first_token_at: float | None = None,
        stripped: Sequence[Crossing] = (),
    ) -> None:
        """A service call, begun at `started` (`perf_counter`), has ended: recorded under
        the turn it was made in, if it is still open, or on its own. An LLM's says when its
        first text or tool call came (`first_token_at`, also `perf_counter`), if one did;
        image generation's, which part of the line each tag stripped from her photo tag
        crossed."""
        call = ServiceCall(
            origin, self._clock(), error, (perf_counter() - started) * 1000, billed,
            (first_token_at - started) * 1000 if first_token_at is not None else None,
            _counted(stripped),
        )
        self.record(call, _current.get())

    def record(self, call: ServiceCall, into: Recording | None) -> None:
        """A service call, recorded under `into`: with it until it is written (a turn in a
        call, with the call), under it once it is; on its own without one."""
        if into is not None and into.row_id is None:
            into.calls.append(call)
        else:
            with self._lock, self._db:
                self._insert_call(call, into.row_id if into is not None else None)

    def since(self, moment: datetime) -> tuple[list[RecordRow], list[CallRow]]:
        """Every record and service call after `moment`, oldest first."""
        at = moment.timestamp()
        with self._lock:
            records = self._db.execute(
                "SELECT kind, COALESCE(user_id, anonymous_id), character_id, started_at, result,"
                " own_keys, time_to_speak_ms, duration_s, end_reason, call_id IS NOT NULL,"
                f" {', '.join(JUDGEMENT_COLUMNS)}, ended_conversation, answered"
                " FROM activity_records WHERE started_at > ? ORDER BY started_at",
                (at,),
            ).fetchall()
            calls = self._db.execute(
                "SELECT service, provider, model, own_key, at, error, latency_ms, usage_unit,"
                " usage_input, usage_output, first_token_ms, reasoning_effort, usage_cached,"
                " stripped_tags FROM service_calls WHERE at > ? ORDER BY at",
                (at,),
            ).fetchall()
        return (
            [
                RecordRow(
                    kind, who, character, _time(started), result,
                    *_reading_own_keys(kind, own), speak, duration, reason, bool(in_call),
                    *_reading_judgement(level, out_of_line, manipulation, cooled), ended,
                    bool(answered),
                )
                for (
                    kind, who, character, started, result, own, speak, duration, reason, in_call,
                    level, out_of_line, manipulation, cooled, ended, answered,
                ) in records
            ],
            [
                CallRow(service, provider, model, bool(own), _time(at), error, latency,
                        unit, used_in, used_out, first, effort, cached,
                        json.loads(stripped) if stripped is not None else None)
                for (
                    service, provider, model, own, at, error, latency, unit, used_in, used_out,
                    first, effort, cached, stripped,
                ) in calls
            ],
        )

    def tally(
        self,
        moment: datetime,
        starts: Sequence[datetime],
        durations: Sequence[tuple[int, int | None]],
    ) -> Tally:
        """The records and service calls after `moment`, summed up in the database rather
        than read one by one, for there may be millions: their trend in stretches beginning
        at `starts` (the first taking in whatever comes before it), and how many calls lasted
        within each of `durations`, from so many seconds to before so many (None: however
        long)."""
        at = moment.timestamp()
        with self._reading:
            db = self._reader
            db.execute("BEGIN")  # all of it as of one moment
            try:
                return _tally(db, at, starts, durations)
            finally:
                db.rollback()

    def billed_models(self, moment: datetime) -> dict[tuple[str, str, str], ServiceName]:
        """What the deployer's service calls after `moment` were billed for, by provider,
        model and unit, each with the service its latest call was to."""
        with self._reading:
            rows = self._reader.execute(
                # The bare `service` is of the row whose `at` is the greatest.
                "SELECT provider, model, usage_unit, service, MAX(at) FROM service_calls"
                " WHERE at > ? AND own_key = 0 AND usage_unit IS NOT NULL"
                " AND (usage_input IS NOT NULL OR usage_output IS NOT NULL)"
                " GROUP BY provider, model, usage_unit",
                (moment.timestamp(),),
            ).fetchall()
        return {(provider, model, unit): service for provider, model, unit, service, _ in rows}

    def deployer_calls_since(self, moment: datetime) -> dict[ServiceName, tuple[int, int]]:
        """How many calls to each service after `moment` were on the deployer's keys, and
        how many of them failed; only those with any."""
        with self._lock:
            rows = self._db.execute(
                "SELECT service, COUNT(*), SUM(error IS NOT NULL) FROM service_calls"
                " WHERE at > ? AND own_key = 0 GROUP BY service",
                (moment.timestamp(),),
            ).fetchall()
        return {service: (calls, failed) for service, calls, failed in rows}

    def of_user(self, user_id: str, moment: datetime) -> list[RecordRow]:
        """The user's records of turns and calls after `moment`, oldest first, not of
        reach-outs; only how each turned out and on whose keys are of use to the admin's users
        page."""
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, character_id, started_at, result, own_keys,"
                " call_id IS NOT NULL FROM activity_records"
                f" WHERE user_id = ? AND started_at > ? AND {TURNS_AND_CALLS}"
                " ORDER BY started_at",
                (user_id, moment.timestamp()),
            ).fetchall()
        return [
            RecordRow(kind, user_id, character, _time(started), result,
                      *_reading_own_keys(kind, own), None, in_call=bool(in_call))
            for kind, character, started, result, own, in_call in rows
        ]

    def reached_out_since(self, user_id: str, moment: datetime) -> int:
        """How many times characters reached out to the user since `moment`, as of when each
        began; only those recorded count, not those that failed or were refused."""
        with self._lock:
            [count] = self._db.execute(
                "SELECT COUNT(*) FROM activity_records WHERE user_id = ? AND started_at >= ?"
                " AND kind = 'reach_out' AND result = 'success'",
                (user_id, moment.timestamp()),
            ).fetchone()
        return count

    def taken_since(self, user_ids: Sequence[str], moment: datetime) -> dict[str, dict[str, int]]:
        """How many turns and calls (by `RecordKind`) each of the users took after
        `moment`, not counting those rejected (`TAKEN`); a user who took none is not in
        it."""
        if not user_ids:
            return {}
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id, kind, COUNT(*) FROM activity_records"
                f" WHERE user_id IN ({','.join('?' * len(user_ids))}) AND started_at > ?"
                f" AND {TAKEN} AND {TURNS_AND_CALLS} GROUP BY user_id, kind",
                [*user_ids, moment.timestamp()],
            ).fetchall()
        taken: dict[str, dict[str, int]] = {}
        for user_id, kind, count in rows:
            taken.setdefault(user_id, {})[kind] = count
        return taken

    @staticmethod
    def ranking(kind: RecordKind, moment: datetime) -> tuple[str, list[Any]]:
        """How many turns, or calls, a user took after `moment`, as an SQL expression over
        a row of `users` with its parameters, for `Store.find_users` to sort by."""
        return (
            "(SELECT COUNT(*) FROM activity_records a WHERE a.user_id = users.id"
            f" AND a.kind = ? AND a.started_at > ? AND a.{TAKEN})",
            [kind, moment.timestamp()],
        )

    def close(self) -> None:
        self._db.close()
        self._reader.close()

    def _write(self, recording: Recording) -> None:
        """Write a finished record with its service calls and, for a call, its turns."""
        # Should the user have been deleted meanwhile, the records are theirs anonymously
        # (`database.ANONYMIZED_USER_DATA`), as one more deleted user.
        anonymous = secrets.token_hex(16)
        with self._lock, self._db:
            self._insert(recording, anonymous)
            for turn in recording.turns:
                self._insert(turn, anonymous)

    def _insert(self, recording: Recording, anonymous: str) -> None:
        assert recording.result is not None
        row = (
            recording.kind, recording.character_id, recording.started_at.timestamp(),
            recording.result, int(_writing_own_keys(recording)), recording.time_to_speak_ms,
            recording.call.row_id if recording.call is not None else None,
            recording.duration_s, recording.end_reason, *_writing_judgement(recording),
            recording.ended_conversation,
        )
        insert = (
            "INSERT INTO activity_records (user_id, anonymous_id, kind, character_id,"
            " started_at, result, own_keys, time_to_speak_ms, call_id, duration_s, end_reason,"
            f" {', '.join(JUDGEMENT_COLUMNS)}, ended_conversation)"
            f" VALUES ({', '.join('?' * (12 + len(JUDGEMENT_COLUMNS)))})"
        )
        try:
            cursor = self._db.execute(insert, (recording.user_id, None, *row))
        except sqlite3.IntegrityError:  # the user was deleted meanwhile
            cursor = self._db.execute(insert, (None, anonymous, *row))
        recording.row_id = cursor.lastrowid
        for call in recording.calls:
            self._insert_call(call, recording.row_id)

    def _insert_call(self, call: ServiceCall, record_id: int | None) -> None:
        origin, billed = call.origin, call.billed
        self._db.execute(
            "INSERT INTO service_calls (record_id, at, service, provider, model, own_key,"
            " error, latency_ms, usage_unit, usage_input, usage_output, first_token_ms,"
            " reasoning_effort, usage_cached, stripped_tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_id, call.at.timestamp(), origin.service, origin.provider, origin.model,
                int(origin.own_key), call.error, call.latency_ms, billed.unit, billed.input,
                billed.output, call.first_token_ms, origin.effort, billed.cached,
                json.dumps(call.stripped) if call.stripped is not None else None,
            ),
        )

    def _forget_before(self, moment: datetime) -> None:
        at = moment.timestamp()
        with self._lock, self._db:
            self._db.execute("DELETE FROM service_calls WHERE at <= ?", (at,))
            self._db.execute("DELETE FROM activity_records WHERE started_at <= ?", (at,))


def _tally(
    db: sqlite3.Connection,
    at: float,
    starts: Sequence[datetime],
    durations: Sequence[tuple[int, int | None]],
) -> Tally:
    who = "COALESCE(user_id, anonymous_id)"
    records = []
    for (
        kind, character, result, own, in_call, reason, level, out_of_line, manipulation,
        cooled, ended, answered, count,
    ) in db.execute(
        "SELECT kind, character_id, result, own_keys != 0, call_id IS NOT NULL, end_reason,"
        f" {', '.join(JUDGEMENT_COLUMNS)}, ended_conversation IS NOT NULL, answered != 0,"
        " COUNT(*) FROM activity_records WHERE started_at > ?"
        " GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12",
        (at,),
    ):
        judged, was_cooled = _reading_judgement(level, out_of_line, manipulation, cooled)
        records.append(RecordGroup(
            kind=kind, character_id=character, result=result, own_keys=bool(own),
            in_call=bool(in_call), end_reason=reason, judged=judged, cooled=was_cooled,
            ended_conversation=bool(ended), answered=bool(answered), count=count,
        ))
    stretch, stretch_params = _stretch("at", starts)
    calls = [
        CallGroup(
            stretch=index, service=service, provider=provider, model=model,
            own_key=bool(own), error=error, usage_unit=unit, input_told=bool(input_told),
            output_told=bool(output_told),
            stripped=json.loads(stripped) if stripped is not None else None, calls=count,
            usage_input=used_in, usage_output=used_out, usage_cached=cached,
        )
        for (
            index, service, provider, model, own, error, unit, input_told, output_told,
            stripped, count, used_in, used_out, cached,
        ) in db.execute(
            f"SELECT {stretch}, service, provider, model, own_key != 0, error, usage_unit,"
            " usage_input IS NOT NULL, usage_output IS NOT NULL, stripped_tags, COUNT(*),"
            " SUM(usage_input), SUM(usage_output), SUM(usage_cached) FROM service_calls"
            " WHERE at > ? GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 ORDER BY MIN(at)",
            (*stretch_params, at),
        )
    ]
    users, cooled_users = db.execute(
        f"SELECT COUNT(DISTINCT {who}), COUNT(DISTINCT CASE WHEN kind = 'turn'"
        " AND result = 'success' AND affinity_level IS NOT NULL AND cooled"
        f" THEN {who} END) FROM activity_records WHERE started_at > ? AND {TURNS_AND_CALLS}",
        (at,),
    ).fetchone()
    stretch, stretch_params = _stretch("started_at", starts)
    taken = {
        index: Taken(turns, calls_taken, takers)
        for index, turns, calls_taken, takers in db.execute(
            f"SELECT {stretch}, SUM(kind = 'turn'), SUM(kind = 'call'), COUNT(DISTINCT {who})"
            f" FROM activity_records WHERE started_at > ? AND {TURNS_AND_CALLS} GROUP BY 1",
            (*stretch_params, at),
        )
    }
    bounds = [
        "SUM(duration_s >= ?" + (" AND duration_s < ?)" if high is not None else ")")
        for _, high in durations
    ]
    within = db.execute(
        f"SELECT {', '.join(bounds) or 'NULL'} FROM activity_records"
        " WHERE started_at > ? AND kind = 'call' AND duration_s IS NOT NULL",
        (*(b for low, high in durations for b in (low, high) if b is not None), at),
    ).fetchone()
    by_service = ["service", "own_key != 0"]
    latency, first_token = (
        {
            (service, bool(own)): spread
            for (service, own), spread in _spreads(
                db, "service_calls", by_service, measure, "at > ?", (at,)
            ).items()
        }
        for measure in ("latency_ms", "first_token_ms")
    )
    speaking, lasted = (
        _spreads(db, "activity_records", ["kind"], measure, "started_at > ? AND kind = ?",
                 (at, kind)).get((kind,), Spread(0, {}))
        for kind, measure in (("turn", "time_to_speak_ms"), ("call", "duration_s"))
    )
    return Tally(
        records=records,
        calls=calls,
        users=users,
        cooled_users=cooled_users,
        stretches=[taken.get(i, Taken(0, 0, 0)) for i in range(len(starts))],
        latency=latency,
        first_token=first_token,
        time_to_speak=speaking,
        lasted=lasted,
        lasted_within=[n or 0 for n in within] if durations else [],
    )


def _stretch(column: str, starts: Sequence[datetime]) -> tuple[str, list[float]]:
    """Which of the stretches beginning at `starts` a row's time `column` is in, as an SQL
    expression with its parameters: the first takes in whatever comes before it, the last
    whatever after. Times are taken to the microsecond, as `_time` reads them."""
    if len(starts) < 2:
        return "0", []
    first, length = starts[0].timestamp(), (starts[1] - starts[0]).total_seconds()
    return (
        f"MAX(0, MIN(?, CAST(ROUND({column} - ?, 6) / ? AS INTEGER)))",
        [len(starts) - 1, first, length],
    )


def _spreads(
    db: sqlite3.Connection,
    table: str,
    group: Sequence[str],
    measure: str,
    where: str,
    params: Sequence[object],
) -> dict[tuple, Spread]:
    """The `Spread` of the `measure` column, where known, of the rows of `table` that are
    `where`, for each `group` of them; sorted in the database, which reads back only the
    rows at the percentiles."""
    keys = ", ".join(f"{column} AS g{i}" for i, column in enumerate(group))
    named = ", ".join(f"g{i}" for i in range(len(group)))
    # The nearest rank of the pth percentile of n: ceil(p * n / 100), and at least the first.
    ranks = " OR ".join(f"rank = MAX(1, ({p} * known + 99) / 100)" for p in PERCENTILES)
    spreads: dict[tuple, Spread] = {}
    for *key, known, rank, value in db.execute(
        f"SELECT {named}, known, rank, value FROM (SELECT {keys}, {measure} AS value,"
        " COUNT(*) OVER whole AS known, ROW_NUMBER() OVER (whole ORDER BY"
        f" {measure}) AS rank FROM {table} WHERE {where} AND {measure} IS NOT NULL"
        f" WINDOW whole AS (PARTITION BY {', '.join(group)})) WHERE {ranks}",
        params,
    ):
        spread = spreads.setdefault(tuple(key), Spread(known, {}))
        for p in PERCENTILES:
            if rank == max(1, (p * known + 99) // 100):
                spread.percentiles[p] = value
    return spreads


def _time(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def _writing_own_keys(recording: Recording) -> bool:
    """What is written to the `own_keys` column of a record, by its kind: a turn's own
    LLM, a call's being exempt."""
    if recording.kind == "turn":
        return recording.own_llm
    assert recording.exempt is not None
    return recording.exempt


def _reading_own_keys(kind: RecordKind, kept: int) -> tuple[bool | None, bool | None]:
    """`RecordRow.own_llm` and `RecordRow.exempt`, from what is read of the `own_keys`
    column of a record of `kind`."""
    return (bool(kept), None) if kind == "turn" else (None, bool(kept))


def _writing_judgement(recording: Recording) -> tuple[int | None, ...]:
    """What is written to the judgement's columns of a record: all NULL for none."""
    judged = recording.judged_as
    if judged is None:
        return (None, None, None, None)
    return (
        judged.level, int(judged.out_of_line), int(judged.manipulation), int(recording.cooled)
    )


def _reading_judgement(
    level: int | None, out_of_line: int | None, manipulation: int | None, cooled: int | None
) -> tuple[Judged | None, bool]:
    """`RecordRow.judged` and `RecordRow.cooled`, from what is read of a record's columns."""
    if level is None:
        return None, False
    return Judged(level, bool(out_of_line), bool(manipulation)), bool(cooled)


def _add_judgement_columns(db: sqlite3.Connection) -> None:
    """Records written before turns' judgements were kept have no columns for them, nor for
    why she ended the conversation."""
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(activity_records)")}
    for column in JUDGEMENT_COLUMNS:
        if column not in columns:
            db.execute(f"ALTER TABLE activity_records ADD COLUMN {column} INTEGER")
    if "ended_conversation" not in columns:
        db.execute("ALTER TABLE activity_records ADD COLUMN ended_conversation TEXT")


def _add_answered_column(db: sqlite3.Connection) -> None:
    """Records written before reach-outs were marked answered have no column for it: none
    were."""
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(activity_records)")}
    if "answered" not in columns:
        db.execute(
            "ALTER TABLE activity_records ADD COLUMN answered INTEGER NOT NULL DEFAULT 0"
        )


def _add_llm_columns(db: sqlite3.Connection) -> None:
    """Service calls written before LLMs' first words were timed have no columns for that,
    nor for the effort they were asked to think at, nor for how much of their input hit the
    prompt cache: none of them say."""
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(service_calls)")}
    for column, kind in [
        ("first_token_ms", "REAL"), ("reasoning_effort", "TEXT"), ("usage_cached", "REAL")
    ]:
        if column not in columns:
            db.execute(f"ALTER TABLE service_calls ADD COLUMN {column} {kind}")


def _add_stripped_tags_column(db: sqlite3.Connection) -> None:
    """Service calls written before tags were stripped from her photo tags have no column
    for it: none were."""
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(service_calls)")}
    if "stripped_tags" not in columns:
        db.execute("ALTER TABLE service_calls ADD COLUMN stripped_tags TEXT")


def _counted(stripped: Sequence[Crossing]) -> dict[Crossing, int] | None:
    """How many of `stripped` crossed each part of the line, in the order of `CROSSINGS`;
    None for none."""
    counted: dict[Crossing, int] = {c: n for c in CROSSINGS if (n := stripped.count(c))}
    return counted or None


def _let_latency_be_unknown(db: sqlite3.Connection) -> None:
    """Service calls written before calls were recorded always had a latency; a database
    of theirs is copied into the table as it is now."""
    columns = db.execute("PRAGMA table_info(service_calls)").fetchall()
    if not any(name == "latency_ms" and not_null for _, name, _, not_null, *_ in columns):
        return
    db.execute("ALTER TABLE service_calls RENAME TO service_calls_before")
    db.executescript(_SCHEMA)
    kept = ", ".join(name for _, name, *_ in columns)
    db.execute(f"INSERT INTO service_calls ({kept}) SELECT {kept} FROM service_calls_before")
    db.execute("DROP TABLE service_calls_before")
    db.executescript(_SCHEMA)  # the indexes went with the old table


def _forget_own_llms(db: sqlite3.Connection) -> None:
    """Calls to users' own LLMs were recorded with their hosts and models; a database of
    theirs is left saying only `OWN`."""
    db.execute(
        "UPDATE service_calls SET provider = ?, model = ?"
        f" WHERE own_key = 1 AND service IN ({', '.join('?' * len(LLM_SERVICES))})"
        " AND (provider != ? OR model != ?)",
        (OWN, OWN, *LLM_SERVICES, OWN, OWN),
    )


def error_kind(error: BaseException) -> ErrorKind:
    """What kind of failure `error`, or what it was raised from, is."""
    seen: set[int] = set()
    cause: BaseException | None = error
    statuses: list[int] = []
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, httpx.TimeoutException | TimeoutError):
            return "timeout"
        if isinstance(cause, httpx.TransportError):
            return "connection"
        statuses += _statuses(str(cause))
        cause = cause.__cause__ or cause.__context__
    for status in statuses:
        if status == 429:
            return "rate_limited"
        if status in (401, 402, 403):
            return "unauthorized"
        if 400 <= status < 500:
            return "refused"
        if status >= 500:
            return "server_error"
    if "无法解析" in str(error):
        return "unreadable"
    return "other"


def _statuses(message: str) -> list[int]:
    """The HTTP statuses a service's error message names: "返回 503", or "402 Payment
    Required" at its start."""
    found = [int(s) for s in re.findall(r"返回 (\d{3})\b", message)]
    if (lead := re.search(r"(?:^|：)(\d{3}) [A-Z]", message)) is not None:
        found.append(int(lead.group(1)))
    return found


T = TypeVar("T")


async def timed(
    log: ActivityLog, origin: Origin, work: Awaitable[T], stripped: Sequence[Crossing] = ()
) -> T:
    """What `work`, a request to the service `origin` names, comes to, recorded as a
    service call; each attempt of it that failed and was made again, as one of its own. A
    photo's says which part of the line each tag `stripped` from her photo tag crossed (its
    last attempt alone, for them to be counted once)."""
    started = perf_counter()
    billed = Billing()
    error: ErrorKind | None = None

    def record_failed_attempt(failure: ServiceError) -> None:
        nonlocal started
        log.call(origin, started, error_kind(failure), billed.take())
        started = perf_counter()

    try:
        with billing(billed), on_retry(record_failed_attempt):
            return await work
    except asyncio.CancelledError:
        raise  # abandoned, not failed
    except Exception as e:
        error = error_kind(e)
        raise
    finally:
        log.call(origin, started, error, billed, stripped=stripped)


class _MeteredLLM:
    """An LLM whose every request is recorded as a service call."""

    def __init__(self, llm: ChatLLM, log: ActivityLog, origin: Origin) -> None:
        self._llm = llm
        self._log = log
        self._origin = origin

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        started = perf_counter()
        # When its first text or tool call came; thinking is neither. A tool call comes only
        # once its arguments are whole, so a reply that is one comes about when it ends.
        first_token_at: float | None = None
        billed = Billing()
        error: ErrorKind | None = None
        stream = aiter(self._llm.stream(messages, tools, forbid_calls=forbid_calls))
        try:
            while True:
                # Opened and closed around each step alone: a step may be taken in a task
                # of its own (see `engine._unless_stopped`).
                with billing(billed):
                    try:
                        item = await anext(stream)
                    except StopAsyncIteration:
                        break
                if first_token_at is None and item and not isinstance(item, Reasoning):
                    first_token_at = perf_counter()
                yield item
        except (GeneratorExit, asyncio.CancelledError):
            raise  # abandoned, not failed
        except Exception as e:
            error = error_kind(e)
            raise
        finally:
            if (close := getattr(stream, "aclose", None)) is not None:
                await close()
            self._log.call(self._origin, started, error, billed, first_token_at)

    async def aclose(self) -> None:
        pass  # whoever opened the model closes it


def metered_llm(llm: ChatLLM, log: ActivityLog, origin: Origin) -> ChatLLM:
    return _MeteredLLM(llm, log, origin)


def metered_retrieval(
    log: ActivityLog, settings: Settings, embedder: Embedder | None, reranker: Reranker | None
) -> tuple[Embedder | None, Reranker | None]:
    """The embedder and the reranker turns retrieve with, whichever are at hand, each
    request recorded as a service call to the service `settings` name. The judge model is
    the live configuration's, recorded there (`live_config.LiveRetrievalJudge`)."""
    if embedder is not None:
        e = settings.embedding
        origin = Origin("embedding", host(e and e.base_url), embedder.model)
        embedder = MeteredEmbedder(embedder, log, origin)
    if reranker is not None:
        r = settings.rerank
        reranker = MeteredReranker(
            reranker, log, Origin("rerank", host(r and r.base_url), r.model if r else "unknown")
        )
    return embedder, reranker


def image_origin(settings: ImageSettings) -> Origin:
    """The deployer's image generation `settings` describe, as its service calls are
    recorded."""
    return Origin("image", host(settings.base_url), settings.model)


OWN_IMAGE_ORIGIN = Origin("image", OWN, OWN, own_key=True)
"""A user's own image generation, as its service calls are recorded: only as `OWN`, as
their LLMs are, for its address may be a relay of their own."""


def judge_origin(settings: JudgeSettings | None) -> Origin:
    """The judge model `settings` choose, as its service calls are recorded."""
    if isinstance(settings, CloudflareJudgeSettings):
        return Origin("judge", host(CLOUDFLARE_API_URL), settings.model)
    if isinstance(settings, BailianJudgeSettings):  # the region's, not the workspace's
        return Origin("judge", settings.host, settings.model)
    return Origin(
        "judge", host(settings and settings.base_url), settings.model if settings else "unknown"
    )


class MeteredEmotionJudge:
    """The judge model as calls ask it each segment's emotion, each question recorded as a
    service call under the call it is asked in. A question the call stops waiting for is
    abandoned, not failed."""

    def __init__(self, judge: "EmotionJudge", log: ActivityLog, origin: Origin) -> None:
        self._judge = judge
        self._log = log
        self._origin = origin

    async def judge_emotion(self, segment: "SegmentToJudge") -> "EmotionAnswer":
        return await timed(self._log, self._origin, self._judge.judge_emotion(segment))


class MeteredAmbienceJudge:
    """The judge model as calls ask it which ambience fits, each question recorded as a
    service call under the call it is asked in."""

    def __init__(self, judge: "AmbienceJudge", log: ActivityLog, origin: Origin) -> None:
        self._judge = judge
        self._log = log
        self._origin = origin

    async def judge_ambience(self, moment: "AmbienceMoment") -> "Mapping[AmbienceKind, float]":
        return await timed(self._log, self._origin, self._judge.judge_ambience(moment))


class MeteredJudge:
    """The judge model as retrieval asks it, each question recorded as a service call."""

    def __init__(self, judge: "RetrievalJudge", log: ActivityLog, origin: Origin) -> None:
        self._judge = judge
        self._log = log
        self._origin = origin

    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = False,
        photo: bool = False,
    ) -> "tuple[Need, Judged | None, bool]":
        return await timed(
            self._log, self._origin, self._judge.judge(character, history, text, affinity, photo)
        )


class MeteredWebSearch:
    """The search, each request of it recorded as a service call."""

    def __init__(self, search: "Search", log: ActivityLog, origin: Origin) -> None:
        self._search = search
        self._log = log
        self._origin = origin

    async def find(self, query: str) -> "list[Found]":
        return await timed(self._log, self._origin, self._search.find(query))

    async def aclose(self) -> None:
        await self._search.aclose()


class MeteredSearchJudge:
    """The judge model as a turn asks it whether he asked her to look something up, recorded
    as a service call."""

    def __init__(self, judge: "SearchJudge", log: ActivityLog, origin: Origin) -> None:
        self._judge = judge
        self._log = log
        self._origin = origin

    async def asked(
        self, character: Character, history: Sequence[Message], text: str
    ) -> bool:
        return await timed(
            self._log, self._origin, self._judge.asked(character, history, text)
        )


class MeteredPromiseJudge:
    """The judge model as a turn asks it whether a promise was kept, each question recorded as
    a service call."""

    def __init__(self, judge: "PromiseJudge", log: ActivityLog, origin: Origin) -> None:
        self._judge = judge
        self._log = log
        self._origin = origin

    async def kept(
        self, character: Character, history: Sequence[Message], text: str, promised: str
    ) -> bool:
        return await timed(
            self._log, self._origin, self._judge.kept(character, history, text, promised)
        )


class MeteredAffinityJudge:
    """The judge model as a turn asks it about affinity or a photo apart, where retrieval is
    off, each question recorded as a service call."""

    def __init__(self, judge: "AffinityJudge", log: ActivityLog, origin: Origin) -> None:
        self._judge = judge
        self._log = log
        self._origin = origin

    async def judge(
        self,
        character: Character,
        history: Sequence[Message],
        text: str,
        affinity: bool = True,
        photo: bool = False,
    ) -> "tuple[Judged | None, bool]":
        return await timed(
            self._log, self._origin, self._judge.judge(character, history, text, affinity, photo)
        )


class MeteredEmbedder:
    """An embedder whose every request is recorded as a service call."""

    def __init__(self, embedder: Embedder, log: ActivityLog, origin: Origin) -> None:
        self.model = embedder.model
        self._embedder = embedder
        self._log = log
        self._origin = origin

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
        return await timed(self._log, self._origin, self._embedder.embed_documents(documents))

    async def embed_query(self, text: str) -> list[float]:
        return await timed(self._log, self._origin, self._embedder.embed_query(text))


class MeteredReranker:
    """A reranker whose every request is recorded as a service call."""

    def __init__(self, reranker: Reranker, log: ActivityLog, origin: Origin) -> None:
        self._reranker = reranker
        self._log = log
        self._origin = origin

    async def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        return await timed(
            self._log, self._origin, self._reranker.rerank(query, documents, top_n)
        )
