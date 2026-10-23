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
turn or keeps the call (`store.add_turn`, `store.keep_call`), by the record its reach-out
names; or as that is kept, should they have been in touch since (`Store.reach_out_recorded_as`).

A daily task done (GLOSSARY.md「每日任务」) has a record of its own, with no character and
which task in its result (`ActivityLog.daily_task_done`): no turn nor call, nor counted as
one anywhere. So has an achievement's stage unlocked (GLOSSARY.md「成就」), with the
character it is hers of (none for those of all the characters together) and which stage of
which achievement in its result, and a character complete, with `COMPLETE` in its result
(`ActivityLog.achievement_unlocked`, `ActivityLog.character_complete`).

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

Records are kept for 90 days (`KEPT`); older ones are deleted as the backend tidies up
(`housekeeping`), as uses of the limit are. A deleted user's records stay, for the numbers not to change, but
no longer say whose they were (`database.ANONYMIZED_USER_DATA`).

As each record and service call is written, it is also rolled up, in the same transaction,
into the slot of `SLOT` seconds it began in, and what the dashboard sums up only over a whole
range into its UTC day too (`_roll_up_records`, `_roll_up_calls`): the dashboard sums up
these (`ActivityLog.tally`), however many records a range holds. A range so begins where the
slot its first moment falls in does (`range_start`). Counts, usage and users are as many as
the records say; how long things took, only to a bucket of the spread (`_bucket`). A
reach-out marked answered moves in the rollups too (a trigger: the store marks it). The
rollups are made from the records when there are none (`_roll_up_all`): a database from
before them, or one whose rollups were dropped, as they are to be by hand after running an
earlier version that wrote records without rolling them up.
"""

import asyncio
import json
import logging
import math
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator, Awaitable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from eidolon.data_access import Database
from eidolon.embedding import Document, Embedder
from eidolon.housekeeping import in_batches
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

RecordKind = Literal[
    "turn", "call", "reach_out", "daily_task", "achievement", "letter", "diary", "schedule",
    "keepsake", "trip_photo", "read_aloud", "home",
]
"""A reach-out is neither a turn nor a call, nor counted as one anywhere; it is always on the
deployer's keys. Nor is a daily task done, which has no character and no service calls, nor
an achievement's stage unlocked or a character complete, which have none either."""
Result = Literal["success", "failed", "rejected", "left", "not_connected"]
"""How a turn or call turned out (GLOSSARY.md「回合」「接通」): a turn succeeded, failed on
a service, was rejected (over the limit, or its input), or the user left before it was
done; a call also may never have connected. A reach-out succeeds or fails. A daily task
done has which task it was (`happenings.DAILY_TASKS`) instead; an achievement's record, which
stage of which (`stage_unlocked`), or `COMPLETE`."""
RESULTS: tuple[Result, ...] = ("success", "failed", "rejected", "left", "not_connected")

COMPLETE = "complete"
"""The result of the record of a character complete."""


def stage_unlocked(achievement: str, stage: int) -> str:
    """The result of the record of the achievement's `stage` unlocked, by number."""
    return f"{achievement}:{stage}"


def stage_of(result: str) -> int:
    """The stage, by number, an achievement's stage unlocked's record's `result` is of."""
    return int(result.rsplit(":", 1)[1])

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
which the user did not take, nor of daily tasks done or achievements."""

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
-- so, by the record its reach-out keeps (`store.add_turn`, `store.keep_call`,
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

SLOT = 15 * 60
"""How long, in seconds, the slots records and service calls are rolled up in are: every
time zone is a whole number of them off UTC, so that the admin's round hours and days, where
the dashboard's stretches begin, each begin a slot."""
DAY = 24 * 60 * 60

DURATIONS: tuple[tuple[int, int | None], ...] = (
    (0, 60), (60, 180), (180, 600), (600, 1800), (1800, None)
)
"""The stretches, in seconds, calls' durations are counted in, as they are rolled up: should
they change, as should `SLOT` or what a record is rolled up by (`_RECORD_KEY`), the rollups
and their trigger are to be dropped (`ROLLUPS`), to be made anew."""

_ROLLUP_SCHEMA = """
-- The records and service calls rolled up as they are written (see the module's doc): each
-- row sums up those alike in all the dashboard counts them by that began in one slot,
-- `length` seconds long from `start` (epoch seconds). Those summed up only over a whole range
-- are rolled up into UTC days too; those that make the trend, only into slots of `SLOT`
-- seconds. A NULL is rolled up as '', or -1, so that rows alike are one.
--
-- `record_rollups` are the records, by `RecordGroup`'s fields; `taken_rollups` how many
-- turns and calls began in each slot; `user_rollups` who took any turn or call in it
-- (`who` is `user_id`, or once they are deleted `anonymous_id`), and whether a judged turn
-- of theirs in it began a cooldown. `call_rollups` are the service calls, by `CallGroup`'s
-- fields, with when the first of them was made.
--
-- `spread_rollups` are how long things took, for their spreads: how many of each `measure`
-- of the calls to each service on whose keys (of a record's, '' and 0), in each bucket
-- (`_bucket`), with the least and the most of them. Its `lasted_within` are how many calls
-- lasted within each of `DURATIONS`, by its index as the bucket.
CREATE TABLE IF NOT EXISTS record_rollups (
    length INTEGER NOT NULL,
    start INTEGER NOT NULL,
    kind TEXT NOT NULL,
    character_id TEXT NOT NULL,
    result TEXT NOT NULL,
    own_keys INTEGER NOT NULL,
    in_call INTEGER NOT NULL,
    end_reason TEXT NOT NULL,
    affinity_level INTEGER NOT NULL,
    out_of_line INTEGER NOT NULL,
    manipulation INTEGER NOT NULL,
    cooled INTEGER NOT NULL,
    ended INTEGER NOT NULL,
    answered INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (length, start, kind, character_id, result, own_keys, in_call, end_reason,
                 affinity_level, out_of_line, manipulation, cooled, ended, answered)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS taken_rollups (
    start INTEGER PRIMARY KEY,
    turns INTEGER NOT NULL,
    calls INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS user_rollups (
    length INTEGER NOT NULL,
    start INTEGER NOT NULL,
    who TEXT NOT NULL,
    cooled INTEGER NOT NULL,
    PRIMARY KEY (length, start, who)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS user_rollups_by_who ON user_rollups(who);
CREATE TABLE IF NOT EXISTS call_rollups (
    start INTEGER NOT NULL,
    service TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    own_key INTEGER NOT NULL,
    error TEXT NOT NULL,
    usage_unit TEXT NOT NULL,
    input_told INTEGER NOT NULL,
    output_told INTEGER NOT NULL,
    stripped_tags TEXT NOT NULL,
    first_at REAL NOT NULL,
    calls INTEGER NOT NULL,
    usage_input REAL,
    usage_output REAL,
    usage_cached REAL,
    PRIMARY KEY (start, service, provider, model, own_key, error, usage_unit, input_told,
                 output_told, stripped_tags)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS spread_rollups (
    length INTEGER NOT NULL,
    start INTEGER NOT NULL,
    measure TEXT NOT NULL,
    service TEXT NOT NULL,
    own_key INTEGER NOT NULL,
    bucket INTEGER NOT NULL,
    count INTEGER NOT NULL,
    least REAL NOT NULL,
    most REAL NOT NULL,
    PRIMARY KEY (length, start, measure, service, own_key, bucket)
) WITHOUT ROWID;
"""

ROLLUPS = ("record_rollups", "taken_rollups", "user_rollups", "call_rollups", "spread_rollups")
"""The rollups' tables: dropped all together, they are made anew from the records. Their
trigger (`record_rollups_answered`) is not dropped with them, and is to be too: else marking
a reach-out answered fails meanwhile."""


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
    result: Result | str
    """How it turned out; for a daily task done, which task; for an achievement, which
    stage, or `COMPLETE`."""
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
        self.result: Result | str | None = None
        """How it turned out; for a daily task done, which task; for an achievement, which
        stage, or `COMPLETE`."""
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
        self.handed_in = False
        """Whether it has been handed in to be written (`Database.write`): the service calls
        made after are written apart, under it."""
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
        self._log.record_soon(
            ServiceCall(origin, self._log.now(), error, latency_ms, billed), self
        )

    async def finish(
        self,
        result: Result | str,
        duration_s: float | None = None,
        end_reason: CallEndReason | None = None,
    ) -> None:
        """Write the record, as `result` (a call's with how long it lasted and why it
        ended; a daily task done's, which task), with its service calls; only the first
        time. A turn in a call is written with the call, or at once if the call's record has
        been handed in already. Writing it failing is only logged."""
        if self.finished:
            return
        self.finished = True
        self.result, self.duration_s, self.end_reason = result, duration_s, end_reason
        if self.call is None or self.call.handed_in:
            await self._log._write(self)


class ActivityLog:
    """Every turn's activity record and every service call's, kept in the database: each
    written as a write task of its own (ADR-0028), never in one with what it records, so that
    writing it failing, only logged, fails no turn nor call."""

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        db.prepare(_open)
        # On every connection: records and service calls are rolled up as they are written.
        db.function("spread_bucket", 1, _bucket, deterministic=True)
        db.prepare(_open_rollups)
        self._clock = clock
        self._soon: set[asyncio.Task[None]] = set()

    def now(self) -> datetime:
        return self._clock()

    def begin(
        self, kind: RecordKind, user_id: str, character_id: str, *, own_llm: bool, exempt: bool
    ) -> Recording:
        """Open the record of a turn or call the user begins with `character_id`, on their
        own LLM or not and exempt from the limit or not: a turn's record keeps the
        first, a call's the second (GLOSSARY.md「运行记录」)."""
        return Recording(self, kind, user_id, character_id, self._clock(), own_llm, exempt)

    async def forget_old(self) -> None:
        """Delete every record and service call older than `KEPT`, and the rollups of the
        slots and days over by then: one that is not holds some of those deleted still, as
        the range it begins does (`range_start`). In batches (`housekeeping.in_batches`)."""
        at = (self._clock() - KEPT).timestamp()
        await in_batches(self._db, _forget_calls_before, at)
        await in_batches(self._db, _forget_records_before, at)
        for table in ("record_rollups", "user_rollups", "spread_rollups"):
            for length in (SLOT, DAY):
                await in_batches(self._db, _forget_rollups_before, table, length, at - length)
        for table in ("taken_rollups", "call_rollups"):
            await in_batches(self._db, _forget_rollups_before, table, None, at - SLOT)

    async def daily_task_done(self, user_id: str, task_id: str, at: datetime) -> None:
        """The user did the daily task `at`: recorded, with no character, as a write task of
        its own; failing, only logged."""
        await Recording(self, "daily_task", user_id, "", at, False, False).finish(task_id)

    async def achievement_unlocked(
        self, user_id: str, character_id: str | None, achievement: str, stage: int, at: datetime
    ) -> None:
        """The user unlocked the achievement's `stage` `at`: recorded, with the character it is
        hers of, or none for those of all the characters together, as `daily_task_done` is."""
        await Recording(
            self, "achievement", user_id, character_id or "", at, False, False
        ).finish(stage_unlocked(achievement, stage))

    async def character_complete(self, user_id: str, character_id: str, at: datetime) -> None:
        """The user completed the character `at`: recorded, as `daily_task_done` is."""
        await Recording(self, "achievement", user_id, character_id, at, False, False).finish(
            COMPLETE
        )

    def ended_call(
        self,
        origin: Origin,
        started: float,
        error: ErrorKind | None,
        billed: Billing,
        first_token_at: float | None = None,
        stripped: Sequence[Crossing] = (),
    ) -> ServiceCall:
        """A service call, begun at `started` (`perf_counter`), ended now. An LLM's says
        when its first text or tool call came (`first_token_at`, also `perf_counter`), if one
        did; image generation's, which part of the line each tag stripped from her photo tag
        crossed."""
        return ServiceCall(
            origin, self._clock(), error, (perf_counter() - started) * 1000, billed,
            (first_token_at - started) * 1000 if first_token_at is not None else None,
            _counted(stripped),
        )

    async def call(
        self,
        origin: Origin,
        started: float,
        error: ErrorKind | None,
        billed: Billing,
        first_token_at: float | None = None,
    ) -> None:
        """A service call (`ended_call`) has ended: recorded under the turn it was made
        in, or on its own."""
        await self.record(
            self.ended_call(origin, started, error, billed, first_token_at), _current.get()
        )

    async def record(self, call: ServiceCall, into: Recording | None) -> None:
        """A service call, recorded under `into`: with it until it is handed in to be written
        (a turn in a call, with the call), under it after; on its own without one. Writing
        it failing is only logged."""
        if into is not None and not into.handed_in:
            into.calls.append(call)
            return
        try:
            await self._db.write(_insert_call_under, call, into)
        except Exception:
            log.exception("writing a call to %s failed", call.origin.service)

    async def aclose(self) -> None:
        """Wait for what `record_soon` is writing."""
        while self._soon:
            await asyncio.gather(*self._soon, return_exceptions=True)

    def record_soon(self, call: ServiceCall, into: Recording) -> None:
        """`record`, from a caller that cannot wait for it to be written."""
        if not into.handed_in:
            into.calls.append(call)
            return
        task = asyncio.ensure_future(self.record(call, into))
        self._soon.add(task)
        task.add_done_callback(self._soon.discard)

    async def since(self, moment: datetime) -> tuple[list[RecordRow], list[CallRow]]:
        """Every record and service call after `moment`, oldest first."""
        records, calls = await self._db.read(_since, moment.timestamp())
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

    async def tally(self, moment: datetime, starts: Sequence[datetime]) -> Tally:
        """The records and service calls from the start of the slot `moment` falls in
        (`range_start`), summed up from their rollups, for there may be millions: their trend
        in stretches beginning at `starts` (the first taking in whatever comes before it),
        and how many calls lasted within each of `DURATIONS`. All of it as of one moment,
        summed up on the thread it is read on."""
        return await self._db.read(_tally, range_start(moment).timestamp(), starts)

    async def billed_models(self, moment: datetime) -> dict[tuple[str, str, str], ServiceName]:
        """What the deployer's service calls after `moment` were billed for, by provider,
        model and unit, each with the service its latest call was to."""
        rows = await self._db.read(_billed_models, moment.timestamp())
        return {(provider, model, unit): service for provider, model, unit, service, _ in rows}

    async def deployer_calls_since(self, moment: datetime) -> dict[ServiceName, tuple[int, int]]:
        """How many calls to each service after `moment` were on the deployer's keys, and
        how many of them failed; only those with any."""
        rows = await self._db.read(_deployer_calls_since, moment.timestamp())
        return {service: (calls, failed) for service, calls, failed in rows}

    async def of_user(self, user_id: str, moment: datetime) -> list[RecordRow]:
        """The user's records of turns and calls after `moment`, oldest first, not of
        reach-outs; only how each turned out and on whose keys are of use to the admin's users
        page."""
        rows = await self._db.read(_of_user, user_id, moment.timestamp())
        return [
            RecordRow(kind, user_id, character, _time(started), result,
                      *_reading_own_keys(kind, own), None, in_call=bool(in_call))
            for kind, character, started, result, own, in_call in rows
        ]

    async def taken_since(
        self, user_ids: Sequence[str], moment: datetime
    ) -> dict[str, dict[str, int]]:
        """How many turns and calls (by `RecordKind`) each of the users took after
        `moment`, not counting those rejected (`TAKEN`); a user who took none is not in
        it."""
        if not user_ids:
            return {}
        taken: dict[str, dict[str, int]] = {}
        for user_id, kind, count in await self._db.read(
            _taken_since, list(user_ids), moment.timestamp()
        ):
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

    async def _write(self, recording: Recording) -> None:
        """Write a finished record with its service calls and, for a call, its turns, as
        they are now; failing, only logged."""
        try:
            await self._db.write(_insert_records, _handed_in(recording), secrets.token_hex(16))
        except Exception:
            log.exception("writing the activity record of a %s failed", recording.kind)


def read_reached_out_since(db: sqlite3.Connection, user_id: str, moment: datetime) -> int:
    """How many times characters reached out to the user since `moment`, as of when each
    began, read through `db`; only those recorded count, not those that failed or were
    refused."""
    [count] = db.execute(
        "SELECT COUNT(*) FROM activity_records WHERE user_id = ? AND started_at >= ?"
        " AND kind = 'reach_out' AND result = 'success'",
        (user_id, moment.timestamp()),
    ).fetchone()
    return count


@dataclass(frozen=True)
class _Written:
    """A record handed in to be written: with the service calls made in it so far."""

    recording: Recording
    calls: tuple[ServiceCall, ...]


def _handed_in(recording: Recording) -> list[_Written]:
    """`recording`, and for a call its turns finished, handed in to be written as they are
    now: the service calls made in them after are written apart (`ActivityLog.record`). A
    turn not finished yet is written as it is (`Recording.finish`)."""
    written = []
    for each in (recording, *(turn for turn in recording.turns if turn.finished)):
        each.handed_in = True
        written.append(_Written(each, tuple(each.calls)))
    return written


def _open(db: sqlite3.Connection) -> None:
    """Make the tables, and bring those of an older version up to date."""
    db.executescript(_SCHEMA)
    _let_latency_be_unknown(db)
    _add_llm_columns(db)
    _add_stripped_tags_column(db)
    _add_judgement_columns(db)
    _add_answered_column(db)
    _forget_own_llms(db)


def _open_rollups(db: sqlite3.Connection) -> None:
    """Make the rollups, from the records should there be none."""
    db.executescript(_ROLLUP_SCHEMA + _answered_trigger())
    _roll_up_all(db)


def _insert_records(db: sqlite3.Connection, written: list[_Written], anonymous: str) -> None:
    """Insert each record `written`, with its service calls; a turn in a call, under the
    call's record, which comes before it or was written already. Each is given its row's
    id: the service calls written apart under it come after (`_insert_call_under`)."""
    # Should the user have been deleted meanwhile, the records are theirs anonymously
    # (`database.ANONYMIZED_USER_DATA`), as one more deleted user.
    try:
        for each in written:
            _insert(db, each.recording, each.calls, anonymous)
    except BaseException:
        for each in written:  # rolled back
            each.recording.row_id = None
        raise


def _insert(
    db: sqlite3.Connection,
    recording: Recording,
    calls: Sequence[ServiceCall],
    anonymous: str,
) -> None:
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
        cursor = db.execute(insert, (recording.user_id, None, *row))
    except sqlite3.IntegrityError:  # the user was deleted meanwhile
        cursor = db.execute(insert, (None, anonymous, *row))
    recording.row_id = cursor.lastrowid
    _roll_up_records(db, "id = ?", (recording.row_id,))
    for call in calls:
        _insert_call(db, call, recording.row_id)


def _insert_call_under(db: sqlite3.Connection, call: ServiceCall, into: Recording | None) -> None:
    """Insert a service call under `into`, handed in to be written before it, or on its
    own."""
    _insert_call(db, call, into.row_id if into is not None else None)


def _insert_call(db: sqlite3.Connection, call: ServiceCall, record_id: int | None) -> None:
    origin, billed = call.origin, call.billed
    cursor = db.execute(
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
    _roll_up_calls(db, "id = ?", (cursor.lastrowid,))


def _since(db: sqlite3.Connection, at: float) -> tuple[list[Any], list[Any]]:
    records = db.execute(
        "SELECT kind, COALESCE(user_id, anonymous_id), character_id, started_at, result,"
        " own_keys, time_to_speak_ms, duration_s, end_reason, call_id IS NOT NULL,"
        f" {', '.join(JUDGEMENT_COLUMNS)}, ended_conversation, answered"
        " FROM activity_records WHERE started_at > ? ORDER BY started_at",
        (at,),
    ).fetchall()
    calls = db.execute(
        "SELECT service, provider, model, own_key, at, error, latency_ms, usage_unit,"
        " usage_input, usage_output, first_token_ms, reasoning_effort, usage_cached,"
        " stripped_tags FROM service_calls WHERE at > ? ORDER BY at",
        (at,),
    ).fetchall()
    return records, calls


def _billed_models(db: sqlite3.Connection, at: float) -> list[Any]:
    return db.execute(
        # The bare `service` is of the row whose `at` is the greatest.
        "SELECT provider, model, usage_unit, service, MAX(at) FROM service_calls"
        " WHERE at > ? AND own_key = 0 AND usage_unit IS NOT NULL"
        " AND (usage_input IS NOT NULL OR usage_output IS NOT NULL)"
        " GROUP BY provider, model, usage_unit",
        (at,),
    ).fetchall()


def _deployer_calls_since(db: sqlite3.Connection, at: float) -> list[Any]:
    return db.execute(
        "SELECT service, COUNT(*), SUM(error IS NOT NULL) FROM service_calls"
        " WHERE at > ? AND own_key = 0 GROUP BY service",
        (at,),
    ).fetchall()


def _of_user(db: sqlite3.Connection, user_id: str, at: float) -> list[Any]:
    return db.execute(
        "SELECT kind, character_id, started_at, result, own_keys,"
        " call_id IS NOT NULL FROM activity_records"
        f" WHERE user_id = ? AND started_at > ? AND {TURNS_AND_CALLS}"
        " ORDER BY started_at",
        (user_id, at),
    ).fetchall()


def _taken_since(db: sqlite3.Connection, user_ids: list[str], at: float) -> list[Any]:
    return db.execute(
        "SELECT user_id, kind, COUNT(*) FROM activity_records"
        f" WHERE user_id IN ({','.join('?' * len(user_ids))}) AND started_at > ?"
        f" AND {TAKEN} AND {TURNS_AND_CALLS} GROUP BY user_id, kind",
        [*user_ids, at],
    ).fetchall()


def _forget_calls_before(db: sqlite3.Connection, at: float, batch: int) -> int:
    """Delete `batch` at most of the service calls up to `at`: how many it deleted."""
    return db.execute(
        "DELETE FROM service_calls WHERE id IN"
        " (SELECT id FROM service_calls WHERE at <= ? LIMIT ?)",
        (at, batch),
    ).rowcount


def _forget_records_before(db: sqlite3.Connection, at: float, batch: int) -> int:
    """Delete `batch` at most of the records begun up to `at`: how many it deleted."""
    return db.execute(
        "DELETE FROM activity_records WHERE id IN"
        " (SELECT id FROM activity_records WHERE started_at <= ? LIMIT ?)",
        (at, batch),
    ).rowcount


def _forget_rollups_before(
    db: sqlite3.Connection, table: str, length: int | None, before: float, batch: int
) -> int:
    """Delete `batch` at most of the rollups in `table` of slots (or days), `length` long
    (None for a table of slots alone), that begin up to `before`: how many it deleted."""
    # By their primary key, which their tables have in place of a row id.
    key = ", ".join(
        name for _, name in sorted(
            (pk, name) for _, name, _, _, _, pk in db.execute(f"PRAGMA table_info({table})")
            if pk
        )
    )
    which, params = (
        ("start <= ?", (before,)) if length is None
        else ("length = ? AND start <= ?", (length, before))
    )
    return db.execute(
        f"DELETE FROM {table} WHERE ({key}) IN"
        f" (SELECT {key} FROM {table} WHERE {which} LIMIT ?)",
        (*params, batch),
    ).rowcount


def range_start(moment: datetime) -> datetime:
    """Where a range of the dashboard reaching back to `moment` begins: where the slot of
    `SLOT` seconds it falls in does, which its rollups begin with."""
    at = moment.timestamp()
    return _time(at - at % SLOT)


_RECORD_KEY = (
    ("kind", "{r}kind"),
    ("character_id", "{r}character_id"),
    ("result", "{r}result"),
    ("own_keys", "{r}own_keys != 0"),
    ("in_call", "{r}call_id IS NOT NULL"),
    ("end_reason", "IFNULL({r}end_reason, '')"),
    ("affinity_level", "IFNULL({r}affinity_level, -1)"),
    ("out_of_line", "IFNULL({r}out_of_line, -1)"),
    ("manipulation", "IFNULL({r}manipulation, -1)"),
    ("cooled", "IFNULL({r}cooled, -1)"),
    ("ended", "{r}ended_conversation IS NOT NULL"),
    ("answered", "{r}answered != 0"),
)
"""The columns of `record_rollups` a record is rolled up by, each with what it is of the
record, as SQL over a row of `activity_records` named by `{r}` (a prefix: '', `OLD.`)."""

_COOLING = (
    "kind = 'turn' AND result = 'success' AND affinity_level IS NOT NULL"
    " AND IFNULL(cooled, 0) != 0"
)
"""Whether a record, in SQL over `activity_records`, is of a judged turn that began a
cooldown."""

_BAND = "CASE " + " ".join(
    f"WHEN duration_s >= {low}" + (f" AND duration_s < {high}" if high is not None else "")
    + f" THEN {i}"
    for i, (low, high) in enumerate(DURATIONS)
) + " END"
"""Which of `DURATIONS` a call's record lasted within, by its index, as SQL over
`activity_records`; NULL for none."""

_SPREAD_ADDED = (
    " ON CONFLICT DO UPDATE SET count = count + excluded.count,"
    " least = MIN(least, excluded.least), most = MAX(most, excluded.most)"
)

BUCKETS_PER_DOUBLING = 16
"""How many buckets a spread has from any length of time to twice it: each is 2^(1/16)
wide, so that what a percentile took is told to within about 2%."""

_NOTHING = -(2**31)
"""The bucket of a time of nothing, which has no logarithm."""


def _bucket(value: float | None) -> int | None:
    """Which bucket of a spread `value` falls in; None of none."""
    if value is None:
        return None
    return math.floor(math.log2(value) * BUCKETS_PER_DOUBLING) if value > 0 else _NOTHING


def _start(column: str, length: int, row: str = "") -> str:
    """Where the slot (or day), `length` seconds long, a row's time `column` is in begins, as
    SQL over the row, named by `row` (a prefix)."""
    return f"CAST({row}{column} / {length} AS INTEGER) * {length}"


def _positions(first: int, last: int) -> str:
    """The columns from `first` to `last` of a SELECT, by their positions, to group by."""
    return ", ".join(str(i) for i in range(first, last + 1))


def _roll_up_records(db: sqlite3.Connection, where: str, params: Sequence[object]) -> None:
    """Roll up the records that are `where`, in SQL over `activity_records`, into the slots,
    and days, they began in."""
    key = ", ".join(expr.format(r="") for _, expr in _RECORD_KEY)
    for length in (SLOT, DAY):
        start = _start("started_at", length)
        db.execute(
            f"INSERT INTO record_rollups SELECT {length}, {start}, {key}, COUNT(*)"
            f" FROM activity_records WHERE {where}"
            f" GROUP BY {_positions(2, 2 + len(_RECORD_KEY))}"
            " ON CONFLICT DO UPDATE SET count = count + excluded.count",
            params,
        )
        db.execute(
            f"INSERT INTO user_rollups SELECT {length}, {start},"
            f" COALESCE(user_id, anonymous_id), MAX({_COOLING}) FROM activity_records"
            f" WHERE ({where}) AND {TURNS_AND_CALLS} GROUP BY 2, 3"
            " ON CONFLICT DO UPDATE SET cooled = MAX(cooled, excluded.cooled)",
            params,
        )
        for measure, kind, value, bucket in [
            ("time_to_speak", "turn", "time_to_speak_ms", "spread_bucket(time_to_speak_ms)"),
            ("lasted", "call", "duration_s", "spread_bucket(duration_s)"),
            ("lasted_within", "call", "duration_s", _BAND),
        ]:
            db.execute(
                f"INSERT INTO spread_rollups SELECT {length}, {start}, '{measure}', '', 0,"
                f" {bucket}, COUNT(*), MIN({value}), MAX({value}) FROM activity_records"
                f" WHERE ({where}) AND kind = '{kind}' AND {value} IS NOT NULL"
                f" AND {bucket} IS NOT NULL GROUP BY 2, 6" + _SPREAD_ADDED,
                params,
            )
    db.execute(
        f"INSERT INTO taken_rollups SELECT {_start('started_at', SLOT)}, SUM(kind = 'turn'),"
        f" SUM(kind = 'call') FROM activity_records WHERE ({where}) AND {TURNS_AND_CALLS}"
        " GROUP BY 1 ON CONFLICT DO UPDATE SET turns = turns + excluded.turns,"
        " calls = calls + excluded.calls",
        params,
    )


def _roll_up_calls(db: sqlite3.Connection, where: str, params: Sequence[object]) -> None:
    """Roll up the service calls that are `where`, in SQL over `service_calls`, into the
    slots, and for their spreads days, they were made in."""
    db.execute(
        f"INSERT INTO call_rollups SELECT {_start('at', SLOT)}, service, provider, model,"
        " own_key != 0, IFNULL(error, ''), IFNULL(usage_unit, ''), usage_input IS NOT NULL,"
        " usage_output IS NOT NULL, IFNULL(stripped_tags, ''), MIN(at), COUNT(*),"
        " SUM(usage_input), SUM(usage_output), SUM(usage_cached) FROM service_calls"
        f" WHERE {where} GROUP BY {_positions(1, 10)}"
        # Alike, all of them said what their input and output were billed for, or none did.
        " ON CONFLICT DO UPDATE SET first_at = MIN(first_at, excluded.first_at),"
        " calls = calls + excluded.calls, usage_input = usage_input + excluded.usage_input,"
        " usage_output = usage_output + excluded.usage_output, usage_cached ="
        " COALESCE(usage_cached + excluded.usage_cached, usage_cached, excluded.usage_cached)",
        params,
    )
    for length in (SLOT, DAY):
        for measure, value in [("latency", "latency_ms"), ("first_token", "first_token_ms")]:
            db.execute(
                f"INSERT INTO spread_rollups SELECT {length}, {_start('at', length)},"
                f" '{measure}', service, own_key != 0, spread_bucket({value}), COUNT(*),"
                f" MIN({value}), MAX({value}) FROM service_calls"
                f" WHERE ({where}) AND {value} IS NOT NULL GROUP BY 2, 4, 5, 6"
                + _SPREAD_ADDED,
                params,
            )


def _roll_up_all(db: sqlite3.Connection) -> None:
    """Roll up all the records and service calls, should there be no rollups of any: a
    database from before them, or whose rollups were dropped."""
    if _any_rolled_up(db):
        return
    began = perf_counter()
    _roll_up_records(db, "true", ())
    _roll_up_calls(db, "true", ())
    if _any_rolled_up(db):
        log.info("Rolled up the activity records in %.1fs", perf_counter() - began)


def _any_rolled_up(db: sqlite3.Connection) -> bool:
    """Whether any of the rollups' tables holds anything."""
    return any(db.execute(f"SELECT EXISTS (SELECT 1 FROM {t})").fetchone()[0] for t in ROLLUPS)


def _answered_trigger() -> str:
    """The trigger that moves a reach-out's record, as it is marked answered, in the
    rollups: the store marks it, in write tasks of its own."""
    moved = []
    for length in (SLOT, DAY):
        was = " AND ".join(
            f"{column} = ({expr.format(r='OLD.')})" for column, expr in _RECORD_KEY
        )
        now = ", ".join(expr.format(r="NEW.") for _, expr in _RECORD_KEY)
        moved += [
            f"UPDATE record_rollups SET count = count - 1 WHERE length = {length}"
            f" AND start = {_start('started_at', length, 'OLD.')} AND {was};",
            f"INSERT INTO record_rollups VALUES ({length},"
            f" {_start('started_at', length, 'NEW.')}, {now}, 1)"
            " ON CONFLICT DO UPDATE SET count = count + 1;",
        ]
    return (
        "CREATE TRIGGER IF NOT EXISTS record_rollups_answered AFTER UPDATE OF answered"
        " ON activity_records WHEN OLD.answered IS NOT NEW.answered BEGIN "
        + " ".join(moved) + " END;"
    )


def _whole_range(at: float) -> tuple[str, list[float]]:
    """Which rollups make up the whole of a range from `at`, the start of a slot, as SQL
    over a table of them by `length`, with its parameters: the days from the first wholly in
    it on, and the slots before."""
    first_day = math.ceil(at / DAY) * DAY
    return (
        f"(length = {DAY} AND start >= ? OR length = {SLOT} AND start >= ? AND start < ?)",
        [first_day, at, first_day],
    )


def _tally(db: sqlite3.Connection, at: float, starts: Sequence[datetime]) -> Tally:
    whole, whole_params = _whole_range(at)
    records = []
    for (
        kind, character, result, own, in_call, reason, level, out_of_line, manipulation,
        cooled, ended, answered, count,
    ) in db.execute(
        "SELECT kind, character_id, result, own_keys, in_call, NULLIF(end_reason, ''),"
        " NULLIF(affinity_level, -1), NULLIF(out_of_line, -1), NULLIF(manipulation, -1),"
        " NULLIF(cooled, -1), ended, answered, SUM(count) FROM record_rollups"
        f" WHERE {whole} GROUP BY {_positions(1, 12)} HAVING SUM(count) > 0",
        whole_params,
    ):
        judged, was_cooled = _reading_judgement(level, out_of_line, manipulation, cooled)
        records.append(RecordGroup(
            kind=kind, character_id=character, result=result, own_keys=bool(own),
            in_call=bool(in_call), end_reason=reason, judged=judged, cooled=was_cooled,
            ended_conversation=bool(ended), answered=bool(answered), count=count,
        ))
    stretch, stretch_params = _stretch("start", starts)
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
            f"SELECT {stretch}, service, provider, model, own_key, NULLIF(error, ''),"
            " NULLIF(usage_unit, ''), input_told, output_told, NULLIF(stripped_tags, ''),"
            " SUM(calls), SUM(usage_input), SUM(usage_output), SUM(usage_cached)"
            f" FROM call_rollups WHERE start >= ? GROUP BY {_positions(1, 10)}"
            " ORDER BY MIN(first_at)",
            (*stretch_params, at),
        )
    ]
    users, cooled_users = db.execute(
        "SELECT COUNT(DISTINCT who), COUNT(DISTINCT CASE WHEN cooled THEN who END)"
        f" FROM user_rollups WHERE {whole}",
        whole_params,
    ).fetchone()
    taken = {
        index: (turns, calls_taken)
        for index, turns, calls_taken in db.execute(
            f"SELECT {stretch}, SUM(turns), SUM(calls) FROM taken_rollups WHERE start >= ?"
            " GROUP BY 1",
            (*stretch_params, at),
        )
    }
    takers = dict(db.execute(
        f"SELECT {stretch}, COUNT(DISTINCT who) FROM user_rollups"
        f" WHERE length = {SLOT} AND start >= ? GROUP BY 1",
        (*stretch_params, at),
    ).fetchall())
    within = dict(db.execute(
        "SELECT bucket, SUM(count) FROM spread_rollups"
        f" WHERE measure = 'lasted_within' AND {whole} GROUP BY 1",
        whole_params,
    ).fetchall())
    latency, first_token = (
        {
            (service, bool(own)): spread
            for (service, own), spread in _spreads(db, measure, whole, whole_params).items()
        }
        for measure in ("latency", "first_token")
    )
    speaking, lasted = (
        _spreads(db, measure, whole, whole_params).get(("", 0), Spread(0, {}))
        for measure in ("time_to_speak", "lasted")
    )
    return Tally(
        records=records,
        calls=calls,
        users=users,
        cooled_users=cooled_users,
        stretches=[
            Taken(*taken.get(i, (0, 0)), takers.get(i, 0)) for i in range(len(starts))
        ],
        latency=latency,
        first_token=first_token,
        time_to_speak=speaking,
        lasted=lasted,
        lasted_within=[within.get(i, 0) for i in range(len(DURATIONS))],
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
    db: sqlite3.Connection, measure: str, whole: str, params: Sequence[object]
) -> dict[tuple[Any, int], Spread]:
    """The `Spread` of the `measure` over a range (`_whole_range`), for each service and
    whose keys it was on (of a record's, '' and 0)."""
    buckets: dict[tuple[Any, int], list[tuple[int, int, float, float]]] = {}
    for service, own, bucket, count, least, most in db.execute(
        "SELECT service, own_key, bucket, SUM(count), MIN(least), MAX(most) FROM spread_rollups"
        f" WHERE measure = ? AND {whole} GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        (measure, *params),
    ):
        buckets.setdefault((service, own), []).append((bucket, count, least, most))
    return {key: _spread(counted) for key, counted in buckets.items()}


def _spread(buckets: Sequence[tuple[int, int, float, float]]) -> Spread:
    """The `Spread` of the values in `buckets`, in order: how many of them in each, the least
    and the most. The value at a percentile's nearest rank is told as the least of its bucket
    or the most, if it is the first there or the last; else as the bucket's middle, as far as
    those allow."""
    known = sum(count for _, count, _, _ in buckets)
    percentiles: dict[int, float] = {}
    for p in PERCENTILES:
        rank = max(1, (p * known + 99) // 100)  # ceil(p * n / 100), and at least the first
        for bucket, count, least, most in buckets:
            if rank <= count:
                middle = (
                    0.0 if bucket == _NOTHING else 2 ** ((bucket + 0.5) / BUCKETS_PER_DOUBLING)
                )
                percentiles[p] = (
                    least if rank == 1 else most if rank == count
                    else min(max(middle, least), most)
                )
                break
            rank -= count
    return Spread(known, percentiles)


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
    failed: list[ServiceCall] = []

    def record_failed_attempt(failure: ServiceError) -> None:
        nonlocal started
        failed.append(log.ended_call(origin, started, error_kind(failure), billed.take()))
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
        failed.append(log.ended_call(origin, started, error, billed, stripped=stripped))
        for call in failed:
            await log.record(call, recording())


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
            await self._log.call(self._origin, started, error, billed, first_token_at)

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
