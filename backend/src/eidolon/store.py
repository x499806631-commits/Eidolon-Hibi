"""Relational storage (SQLite) for users, their sessions, producer profiles, conversations,
their messages and events, her reach-outs, the photos she sent with her replies, and the
calls in them;
memories are kept by `memory`, recent usage by `usage`."""

import hashlib
import json
import secrets
import sqlite3
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from eidolon.affinity import Change
from eidolon.database import connect, delete_conversation_data, delete_user_data, tables
from eidolon.delivery_cues import without_cues
from eidolon.producer import Birthday, ProducerProfile
from eidolon.reply_format import Segment

Role = Literal["user", "character"]
EventKind = Literal["hung_up", "gift", "ended", "missed_call", "keepsake"]
PhotoStatus = Literal["pending", "ready", "failed"]
"""A photo being generated, sent, or not sent after all."""
PhotoFailure = Literal["timeout", "service_error", "bad_image", "own_key_failed", "used_up"]
"""Why a photo was not sent: the image service took too long, waiting its turn included;
it failed, or could not be reached; it answered with what is not one picture of the size
asked for; the user's own key to it failed; or the photos of the hour were used up as it
was about to be generated."""

_SCHEMA = """
-- `name` only tells users apart on the admin command line; it need not be unique.
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    consented_at TEXT,
    banned_at TEXT,
    ban_note TEXT
);
-- Only a hash of each session's token is kept; the times are epoch seconds.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    last_active_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_by_expiry ON sessions(expires_at);
-- A Google or GitHub identity a user signs in with; each belongs to one user only.
CREATE TABLE IF NOT EXISTS sign_in_methods (
    user_id TEXT NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    subject TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (provider, subject)
);
CREATE INDEX IF NOT EXISTS sign_in_methods_by_user ON sign_in_methods(user_id);
CREATE TABLE IF NOT EXISTS producer_profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id),
    form_of_address TEXT,
    gender TEXT,
    timezone TEXT,
    updated_at TEXT NOT NULL,
    birthday_month INTEGER,
    birthday_day INTEGER
);
-- `title` is its conversation title (`titles`), NULL until it is named, if ever.
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT
);
-- `hurt` and `out_of_line`, on the user's message of a typed turn (`Typed`), are what the
-- turn took off her affinity and whether it was out of line; NULL for a turn of a call, or
-- one recorded before typed turns were counted: an appeal (`appeals`) sets them back to nothing.
-- `appealed_at`, on her reply, is when the user said they did not mean it so (GLOSSARY.md「我不是这个意思」).
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'character')),
    text TEXT,
    ja TEXT,
    zh TEXT,
    segments TEXT,
    created_at TEXT NOT NULL,
    affinity TEXT,
    hurt REAL,
    out_of_line INTEGER,
    appealed_at TEXT
);
CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS conversation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    affinity TEXT,
    gift_id TEXT,
    liked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS conversation_events_by_conversation
    ON conversation_events(conversation_id, created_at);
-- A photo she sent with a reply (GLOSSARY.md「照片」, ADR-0024), one at most to a reply: the
-- photo tag she wrote for it, as she wrote it; whether it is being generated, was sent or was
-- not, and why not (`reason`); and once sent the picture itself, a WebP, read only on its own.
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id),
    tag TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'failed')),
    reason TEXT,
    image BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_by_conversation ON photos(conversation_id);
-- Each call that connected in a conversation (GLOSSARY.md「接通」), for stats alone: when it
-- connected and how long it lasted once it had, as its activity record says; nothing of what
-- was said. Not in the conversation's record: she knows nothing of it (ADR-0021).
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    connected_at TEXT NOT NULL,
    duration_s REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_by_conversation ON calls(conversation_id);
-- What she said first in a conversation (GLOSSARY.md「搭话」), as the model wrote it, delivery
-- cues and all, as her replies are kept; whether the user has seen it (GLOSSARY.md「没看过的
-- 搭话」); and the id of its activity record, which outlives neither the user's name on it nor
-- 90 days, so no reference.
CREATE TABLE IF NOT EXISTS reach_outs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    ja TEXT NOT NULL,
    zh TEXT NOT NULL,
    segments TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0,
    record_id INTEGER
);
CREATE INDEX IF NOT EXISTS reach_outs_by_conversation ON reach_outs(conversation_id, created_at);
-- The letters she wrote the user (GLOSSARY.md「来信」): longer than what she says in a
-- conversation and kept apart from any, the Japanese as the model wrote it, each segment with
-- its subtitle; why she wrote it (`occasion`), when it comes (`created_at`: written ahead, it
-- is not there till then), and when the user first read it, if they have.
CREATE TABLE IF NOT EXISTS letters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    occasion TEXT NOT NULL,
    ja TEXT NOT NULL,
    zh TEXT NOT NULL,
    segments TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT
);
CREATE INDEX IF NOT EXISTS letters_by_user ON letters(user_id, created_at);
-- The identities banned users signed in with, kept when those users were deleted so that
-- deleting is not unbanning; only a keyed hash of each (`accounts.identity_hash`), no one's.
CREATE TABLE IF NOT EXISTS banned_identities (
    identity_hash TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
"""

SESSION_LIFETIME = timedelta(days=30)
"""How long a session lasts after it was last used."""
SESSION_RENEWAL = timedelta(hours=1)
"""How long after its last renewal a session in use is renewed again, rather than on
every request."""


@dataclass(frozen=True)
class User:
    id: str
    name: str
    created_at: str
    consented_at: str | None = None
    """When they agreed to the privacy policy and terms of use; None until they do."""
    banned_at: str | None = None
    ban_note: str | None = None
    """Why they were banned, as the admin who banned them wrote; only admins see it."""


@dataclass(frozen=True)
class Session:
    """A signed-in browser's session. `renewed` says this use renewed it, so the browser's
    cookie should be renewed too."""

    user: User
    renewed: bool


@dataclass(frozen=True)
class SignInMethod:
    """An identity at a provider (Google or GitHub) that signs a user in; `subject` is the
    provider's id for it."""

    provider: str
    subject: str
    display_name: str
    email: str | None
    created_at: str


class IdentityTaken(Exception):
    """The identity already belongs to a user."""


class LastSignInMethod(Exception):
    """Removing it would leave the user no way to sign in."""


class ConversationEnded(Exception):
    """She has ended the conversation (GLOSSARY.md「已结束的对话」): nothing more is recorded
    in it."""


class ReachOutRefused(Exception):
    """What she said first is not recorded (GLOSSARY.md「搭话」): the user and she were in touch
    as she was thinking it, or it is no longer the conversation she was to say it in."""


class IsCurrent(Exception):
    """It is her current conversation (GLOSSARY.md「当前对话」), which is not deleted."""


class NotFound(LookupError):
    pass


@dataclass(frozen=True)
class Conversation:
    id: str
    user_id: str
    character_id: str
    created_at: str
    title: str | None = None
    """Its title (GLOSSARY.md「对话标题」), as it was when read; None until it is named, if
    ever."""


@dataclass(frozen=True)
class Photo:
    """The photo she sent with a reply (GLOSSARY.md「照片」): whether it is being generated, was
    sent or was not, and why not; and the photo tag she wrote for it, which only her prompt
    reads."""

    id: str
    status: PhotoStatus
    reason: PhotoFailure | None
    tag: str

    def shown(self) -> dict:
        """As the browser is told it: not the tag."""
        return {"id": self.id, "status": self.status, "reason": self.reason}


@dataclass(frozen=True)
class NewPhoto:
    """A photo she is sending with the reply being recorded, by its id, and the tag she
    wrote for it."""

    id: str
    tag: str


@dataclass(frozen=True)
class Message:
    """A user's input (`text`) or a character's reply.

    A reply keeps its full Japanese text (`ja`) and Chinese subtitle (`zh`), plus
    the segments that pair each Japanese line with its own subtitle, how the turn
    changed her affinity, if it did (`affinity`), and the photo she sent with it, if any.
    """

    id: int
    role: Role
    text: str | None
    ja: str | None
    zh: str | None
    created_at: str
    segments: list[Segment] = field(default_factory=list)
    affinity: Change | None = None
    photo: Photo | None = None
    appealed: bool = False
    """On her reply: the user said they did not mean what lowered her affinity (GLOSSARY.md
    「我不是这个意思」), and it was given back."""

    @property
    def photo_sent(self) -> bool:
        """Whether she sent a photo with it, and it is out."""
        return self.photo is not None and self.photo.status == "ready"

    @property
    def photo_failed(self) -> bool:
        """Whether she sent a photo with it that was not sent after all."""
        return self.photo is not None and self.photo.status == "failed"

    def without_cues(self) -> "Message":
        """The message as people see it: a reply is kept as the model wrote it, delivery
        cues and all (ADR-0007)."""
        if self.role != "character":
            return self
        return Message(
            self.id, self.role, self.text,
            None if self.ja is None else without_cues(self.ja),
            None if self.zh is None else without_cues(self.zh),
            self.created_at,
            [Segment(without_cues(s.ja), without_cues(s.zh)) for s in self.segments],
            self.affinity,
            self.photo,
            self.appealed,
        )

    def shown(self) -> dict:
        """The message as the browser is told it: as people see it, with its photo's id,
        status and reason if it has one, no key for it if not."""
        shown = asdict(self.without_cues())
        del shown["photo"]
        return shown | ({"photo": self.photo.shown()} if self.photo else {})


@dataclass(frozen=True)
class AppealTarget:
    """Her reply as an appeal weighs it (`appeals`): whose conversation, how it changed her
    affinity, whether it was appealed already, is the conversation's latest message, and the
    conversation ended; the user's message before it (`said_id`, None if none) and what that
    took off her affinity (`hurt`, nothing for a turn of a call)."""

    conversation_id: str
    character_id: str
    change: Change | None
    appealed: bool
    latest: bool
    ended: bool
    said_id: int | None
    hurt: float


@dataclass(frozen=True)
class Typed:
    """What a typed turn, one taken outside calls, comes to for her ending the conversation
    (GLOSSARY.md「已结束的对话」): how much it took off her affinity (`affinity.hurt`), and
    whether it was out of line."""

    hurt: float = 0.0
    out_of_line: bool = False


@dataclass(frozen=True)
class ConversationEvent:
    """Something that happened in a conversation that is not a turn (GLOSSARY.md「对话事件」):
    her hanging up on the user (`hung_up`), her ending the conversation (`ended`), after which
    it can only be read, a call of the user's she did not answer, in the bath (`missed_call`,
    GLOSSARY.md「洗澡」), her giving the user a keepsake (`keepsake`, GLOSSARY.md「纪念品」),
    which (`keepsake_id`) and its name (`note`), or the user giving her a gift (`gift`), which
    one (`gift_id`) and whether she liked it as it was given (`liked`), kept as it was
    should what she likes change. It can carry how it changed her affinity (`affinity`)."""

    id: int
    kind: EventKind
    created_at: str
    affinity: Change | None = None
    gift_id: str | None = None
    liked: bool = False
    keepsake_id: int | None = None
    note: str | None = None
    type: Literal["event"] = field(default="event", init=False)

    def shown(self) -> dict:
        """As the browser is told it: whether she liked the gift is for her prompt alone;
        which gift, for a gift's only; which keepsake, for a keepsake's only."""
        hidden = {"liked", "note"} | ({"gift_id"} if self.gift_id is None else set())
        hidden |= {"keepsake_id"} if self.keepsake_id is None else set()
        return {k: v for k, v in asdict(self).items() if k not in hidden}


@dataclass(frozen=True)
class ReachOut:
    """What she said first in a conversation, the user not talking to her (GLOSSARY.md
    「搭话」): neither a turn nor an event (ADR-0026), but said as she replies, in segments,
    each Japanese with its subtitle, kept as the model wrote them, delivery cues and all."""

    id: int
    ja: str
    zh: str
    segments: list[Segment]
    created_at: str
    type: Literal["reach_out"] = field(default="reach_out", init=False)

    def as_reply(self) -> Message:
        """As those reading only the messages read it: as her reply. Its id is the
        reach-out's, no message's."""
        return Message(
            self.id, "character", None, self.ja, self.zh, self.created_at, self.segments
        )

    def without_cues(self) -> "ReachOut":
        """As people see it."""
        return ReachOut(
            self.id, without_cues(self.ja), without_cues(self.zh),
            [Segment(without_cues(s.ja), without_cues(s.zh)) for s in self.segments],
            self.created_at,
        )

    def shown(self) -> dict:
        """As the browser is told it: as people see it."""
        return asdict(self.without_cues())


Recorded = Message | ConversationEvent | ReachOut

Occasion = Literal["ordinary", "producer_birthday", "her_birthday", "holiday"]
"""Why she wrote a letter: just because, the producer's birthday, her own, or a holiday."""


@dataclass(frozen=True)
class Letter:
    """A letter she wrote the user (GLOSSARY.md「来信」): in segments, each Japanese with its
    subtitle, as the model wrote them; why (`occasion`); when it comes (written ahead, it is
    not there till then), and when the user first read it (None while unread)."""

    id: int
    character_id: str
    occasion: Occasion
    ja: str
    zh: str
    segments: list[Segment]
    created_at: str
    read_at: str | None = None

    def shown(self) -> dict:
        """As the browser is told it: without delivery cues, which are for voicing."""
        return asdict(replace(
            self,
            ja=without_cues(self.ja),
            zh=without_cues(self.zh),
            segments=[Segment(without_cues(s.ja), without_cues(s.zh)) for s in self.segments],
        ))
"""What a conversation's record holds."""


@dataclass(frozen=True)
class CurrentConversation:
    """Her current conversation with a user (GLOSSARY.md「当前对话」) as reach-outs go by it:
    whether it has a turn yet; when the user and she were last in touch in it (the last turn
    recorded, or a call that connected ending; None before either), when she last reached out
    in it (None if never), and whether she has ended it."""

    conversation: Conversation
    has_turns: bool
    last_contact: datetime | None
    reached_out_at: datetime | None
    ended: bool


@dataclass(frozen=True)
class Contact:
    """A turn the user took with a character (the time of their message) or a gift they
    gave her, and whether she liked it as it was given, as stats count them."""

    character_id: str
    kind: Literal["turn", "gift"]
    at: datetime
    liked: bool = False


@dataclass(frozen=True)
class KeptCall:
    """A call the user had with a character that connected, when it did, and how long it
    lasted once it had, in seconds, as stats count them."""

    character_id: str
    connected_at: datetime
    duration_s: float


@dataclass(frozen=True)
class KeptPhoto:
    """The photo she sent with a reply, as kept but for its picture, as a data package carries
    it: `id` is its id where it was exported, a key in the package only."""

    id: str
    tag: str
    status: PhotoStatus
    reason: PhotoFailure | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KeptMessage:
    """A message as kept, all of it, as a data package carries it (GLOSSARY.md「数据包」): her
    reply as the model wrote it, delivery cues and all (ADR-0007), and for the user's message
    of a typed turn what it came to (`hurt`, `out_of_line`; None as for `Typed`)."""

    role: Role
    text: str | None
    ja: str | None
    zh: str | None
    segments: list[Segment]
    created_at: str
    affinity: Change | None
    hurt: float | None
    out_of_line: bool | None
    photo: KeptPhoto | None = None
    """The photo she sent with the reply, if any."""


@dataclass(frozen=True)
class KeptEvent:
    """A conversation event as kept, as a data package carries it."""

    kind: EventKind
    created_at: str
    affinity: Change | None
    gift_id: str | None
    liked: bool


@dataclass(frozen=True)
class ConnectedCall:
    """A call kept in a conversation (`keep_call`), as a data package carries it."""

    connected_at: str
    duration_s: float


@dataclass(frozen=True)
class KeptReachOut:
    """A reach-out as kept, as a data package carries it: as the model wrote it, delivery cues
    and all (ADR-0007); not whether the user has seen it."""

    ja: str
    zh: str
    segments: list[Segment]
    created_at: str


@dataclass(frozen=True)
class WholeConversation:
    """A conversation and everything kept under it, as a data package carries it: its
    messages in their order, its events, the calls kept in it and her reach-outs. `id` is its
    id where it was exported, a key in the package only."""

    id: int | str
    character_id: str
    created_at: str
    title: str | None
    messages: list[KeptMessage]
    events: list[KeptEvent]
    calls: list[ConnectedCall]
    reach_outs: list[KeptReachOut]


class Store:
    def __init__(self, path: Path) -> None:
        self._db = connect(path)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._taking_turns: Counter[str] = Counter()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        columns = self._columns("users")
        if "name" not in columns:
            # Before access keys, users had no name; the one default user keeps its id as name.
            self._db.execute("ALTER TABLE users ADD COLUMN name TEXT")
            self._db.execute("UPDATE users SET name = id")
        self._add_missing_columns("users", ("consented_at", "banned_at", "ban_note"), "TEXT")
        self._add_missing_columns("producer_profiles", ("birthday_month", "birthday_day"), "INTEGER")
        self._add_missing_columns("messages", ("affinity",), "TEXT")
        self._add_missing_columns("messages", ("hurt",), "REAL")
        self._add_missing_columns("messages", ("out_of_line",), "INTEGER")
        self._add_missing_columns("messages", ("appealed_at",), "TEXT")
        self._add_missing_columns("conversations", ("title",), "TEXT")
        self._add_missing_columns("conversation_events", ("gift_id",), "TEXT")
        self._add_missing_columns("conversation_events", ("liked",), "INTEGER NOT NULL DEFAULT 0")
        self._add_missing_columns("conversation_events", ("keepsake_id",), "INTEGER")
        self._add_missing_columns("conversation_events", ("note",), "TEXT")
        # Whether she has responded to an event is whether she has replied since; never kept.
        if "responded" in self._columns("conversation_events"):
            self._db.execute("ALTER TABLE conversation_events DROP COLUMN responded")
        # Access keys are gone (ADR-0006), and with them unique names.
        self._db.execute("DROP INDEX IF EXISTS users_by_name")
        self._db.execute("DROP TABLE IF EXISTS access_keys")
        # Users' own keys stay in their browsers (ADR-0017); those kept here, encrypted, go.
        self._db.execute("DROP TABLE IF EXISTS own_keys")

    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self._db.execute(f"PRAGMA table_info({table})")}

    def _add_missing_columns(self, table: str, columns: tuple[str, ...], kind: str) -> None:
        """Add the columns a table made by an older version lacks; existing rows get NULL."""
        present = self._columns(table)
        for column in columns:
            if column not in present:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def create_user(self, name: str) -> User:
        user = User(uuid.uuid4().hex, name, _now())
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)",
                (user.id, user.name, user.created_at),
            )
        return user

    def list_users(self) -> list[User]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users ORDER BY created_at, rowid"
            ).fetchall()
        return [User(**row) for row in rows]

    def get_user(self, user_id: str) -> User:
        with self._lock:
            row = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        if row is None:
            raise NotFound(f"用户 {user_id} 不存在")
        return User(**row)

    def first_user_named(self, name: str) -> User | None:
        """The earliest user with this name, if any; names are not unique."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE name = ? ORDER BY created_at, rowid",
                (name,),
            ).fetchone()
        return User(**row) if row else None

    def find_users(
        self,
        query: str,
        offset: int,
        limit: int,
        ranking: tuple[str, list[Any]] | None = None,
    ) -> tuple[list[User], int]:
        """A page of the users whose id starts with `query`, or whose name, or the name or
        email of an identity they sign in with, has it in it, in any case; all users for
        an empty query. Newest first, or highest first by `ranking`, an SQL expression
        over a row of `users` with its parameters (as `ActivityLog.ranking`), then newest;
        with how many there are in all."""
        where, params = "", []
        if query := query.strip():
            # Taken as it is: `%` and `_` are not wildcards here.
            escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            inside = f"%{escaped}%"
            where = (
                "WHERE id LIKE ? ESCAPE '!' OR name LIKE ? ESCAPE '!' OR EXISTS ("
                "SELECT 1 FROM sign_in_methods m WHERE m.user_id = users.id"
                " AND (m.display_name LIKE ? ESCAPE '!' OR m.email LIKE ? ESCAPE '!'))"
            )
            params = [f"{escaped}%", inside, inside, inside]
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users {where}"
                f" ORDER BY {f'{ranking[0]} DESC, ' if ranking else ''}"
                "created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                [*params, *(ranking[1] if ranking else []), limit, offset],
            ).fetchall()
            [(total,)] = self._db.execute(f"SELECT COUNT(*) FROM users {where}", params)
        return [User(**row) for row in rows], total

    def last_active(self, user_ids: list[str]) -> dict[str, datetime]:
        """When each of the users, of those signed in anywhere, was last active: the
        latest renewal of any of their sessions."""
        with self._lock:
            rows = self._db.execute(
                "SELECT user_id, MAX(last_active_at) FROM sessions WHERE user_id IN"
                f" ({','.join('?' * len(user_ids))}) GROUP BY user_id",
                user_ids,
            ).fetchall()
        return {user_id: datetime.fromtimestamp(at, UTC) for user_id, at in rows}

    def set_banned(
        self, user_id: str, banned_at: datetime | None, note: str | None = None
    ) -> None:
        """Ban the user as of `banned_at`, with a note only admins see, signing them out
        everywhere; None lifts the ban, and the note with it. Raises NotFound for no such
        user."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE users SET banned_at = ?, ban_note = ? WHERE id = ?",
                (banned_at.isoformat() if banned_at else None,
                 note if banned_at else None, user_id),
            )
            if cursor.rowcount == 0:
                raise NotFound(f"用户 {user_id} 不存在")
            if banned_at is not None:
                self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def active_users(self, user_ids: set[str]) -> set[str]:
        """Those of the users who still exist and are not banned."""
        with self._lock:
            return {
                row["id"]
                for row in self._db.execute(
                    f"SELECT id FROM users WHERE banned_at IS NULL AND id IN"
                    f" ({','.join('?' * len(user_ids))})",
                    list(user_ids),
                )
            } if user_ids else set()

    def consent(self, user_id: str, now: datetime) -> None:
        """Record that the user agreed to the privacy policy and terms of use."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE users SET consented_at = ? WHERE id = ?", (now.isoformat(), user_id)
            )

    def user_signing_in_with(self, provider: str, subject: str) -> str | None:
        """The id of the user this identity belongs to, if any."""
        with self._lock:
            row = self._db.execute(
                "SELECT user_id FROM sign_in_methods WHERE provider = ? AND subject = ?",
                (provider, subject),
            ).fetchone()
        return row["user_id"] if row else None

    def create_user_signing_in_with(self, name: str, method: SignInMethod) -> User:
        """A new user, whose one sign-in method is `method`. Raises IdentityTaken, making
        no one, if the identity already belongs to someone."""
        user = User(uuid.uuid4().hex, name, _now())
        try:
            with self._lock, self._db:
                self._db.execute(
                    "INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)",
                    (user.id, user.name, user.created_at),
                )
                self._insert_sign_in_method(user.id, method)
        except sqlite3.IntegrityError:
            raise IdentityTaken from None
        return user

    def add_sign_in_method(self, user_id: str, method: SignInMethod) -> None:
        """Raises IdentityTaken if the identity already belongs to someone."""
        try:
            with self._lock, self._db:
                self._insert_sign_in_method(user_id, method)
        except sqlite3.IntegrityError:
            raise IdentityTaken from None

    def update_sign_in_method(self, provider: str, subject: str, display_name: str,
                              email: str | None) -> None:
        """Keep the name and email the provider now gives for the identity."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE sign_in_methods SET display_name = ?, email = ?"
                " WHERE provider = ? AND subject = ?",
                (display_name, email, provider, subject),
            )

    def sign_in_methods(self, user_id: str) -> list[SignInMethod]:
        """In the order they were added."""
        with self._lock:
            rows = self._db.execute(
                "SELECT provider, subject, display_name, email, created_at FROM sign_in_methods"
                " WHERE user_id = ? ORDER BY created_at, rowid",
                (user_id,),
            ).fetchall()
        return [SignInMethod(**row) for row in rows]

    def remove_sign_in_method(self, user_id: str, provider: str) -> bool:
        """Remove the user's sign-in method at `provider`; False if they have none there.
        Raises LastSignInMethod, removing nothing, if it is the only one they have."""
        with self._lock, self._db:
            providers = [
                row["provider"]
                for row in self._db.execute(
                    "SELECT provider FROM sign_in_methods WHERE user_id = ?", (user_id,)
                )
            ]
            if provider not in providers:
                return False
            if len(providers) == 1:
                raise LastSignInMethod
            self._db.execute(
                "DELETE FROM sign_in_methods WHERE user_id = ? AND provider = ?",
                (user_id, provider),
            )
        return True

    def delete_user(self, user_id: str, banned_identities: list[str] | None = None) -> None:
        """Delete the user and everything of theirs, for good, all at once (see
        `database.delete_user_data`), first putting `banned_identities`, hashes of the
        identities they signed in with, on the ban list if they were banned."""
        with self._lock:
            if banned_identities:
                with self._db:
                    self._db.executemany(
                        "INSERT OR IGNORE INTO banned_identities (identity_hash, added_at)"
                        " VALUES (?, ?)",
                        [(h, _now()) for h in banned_identities],
                    )
            delete_user_data(self._db, user_id)

    def is_banned_identity(self, identity_hash: str) -> bool:
        """Whether a banned user since deleted signed in with the identity of this hash."""
        with self._lock:
            return self._db.execute(
                "SELECT 1 FROM banned_identities WHERE identity_hash = ?", (identity_hash,)
            ).fetchone() is not None

    def create_session(self, user_id: str, now: datetime) -> str:
        """Start a session for the user; returns its token, which is not stored. Every
        session that has expired, whoever's it is, is deleted meanwhile, so that a user who
        does not come back leaves none behind."""
        token = secrets.token_urlsafe(32)
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.timestamp(),))
            self._db.execute(
                "INSERT INTO sessions (token_hash, user_id, last_active_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (_hash(token), user_id, now.timestamp(), (now + SESSION_LIFETIME).timestamp()),
            )
        return token

    def use_session(self, token: str, now: datetime) -> Session | None:
        """The session with this token, renewed for another `SESSION_LIFETIME` if it has
        not been for a while; None if there is none or it has expired."""
        token_hash = _hash(token)
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT user_id, last_active_at, expires_at FROM sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= now.timestamp():
                self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                return None
            renewed = now.timestamp() - row["last_active_at"] >= SESSION_RENEWAL.total_seconds()
            if renewed:
                self._db.execute(
                    "UPDATE sessions SET last_active_at = ?, expires_at = ? WHERE token_hash = ?",
                    (now.timestamp(), (now + SESSION_LIFETIME).timestamp(), token_hash),
                )
            user = self._db.execute(
                f"SELECT {_USER_COLUMNS} FROM users WHERE id = ?", (row["user_id"],)
            ).fetchone()
        return Session(User(**user), renewed)

    def end_session(self, token: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))

    def end_sessions(self, user_id: str) -> None:
        """Sign the user out everywhere."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def get_profile(self, user_id: str) -> ProducerProfile:
        """The user's producer profile; all defaults until they save one."""
        with self._lock:
            row = self._db.execute(
                "SELECT form_of_address, gender, timezone, birthday_month, birthday_day"
                " FROM producer_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return ProducerProfile()
        birthday = (
            Birthday(row["birthday_month"], row["birthday_day"])
            if row["birthday_month"] is not None and row["birthday_day"] is not None
            else None
        )
        return ProducerProfile(row["form_of_address"], row["gender"], row["timezone"], birthday)

    def save_profile(self, user_id: str, profile: ProducerProfile) -> None:
        with self._lock, self._db:
            write_profile(self._db, user_id, profile)

    def create_conversation(self, user_id: str, character_id: str) -> Conversation:
        conversation = Conversation(uuid.uuid4().hex, user_id, character_id, _now())
        with self._lock, self._db:
            self._insert_conversation(conversation)
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return Conversation(**row) if row else None

    def list_conversations(
        self, user_id: str, character_id: str
    ) -> list[tuple[Conversation, Recorded | None, bool]]:
        """The user's conversations with the character, newest first (the first is her
        current one, GLOSSARY.md「当前对话」), each with the last thing in its record as
        `latest_conversations` gives it, and whether she has ended it; for the past
        conversations panel."""
        with self._lock:
            return [
                (c, self._last(c.id), self._ended(c.id) is not None)
                for c in self._conversations(user_id, character_id)
            ]

    def name_conversation(self, conversation_id: str, title: str) -> None:
        """Give the conversation its title (GLOSSARY.md「对话标题」), unless it has one: it is
        named once."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title IS NULL",
                (title, conversation_id),
            )

    def delete_conversation(self, conversation: Conversation) -> None:
        """Delete a past conversation (GLOSSARY.md「历史对话」) and its record, for good, all
        at once; the memories written in it are kept, no longer saying where. Her current one
        is not deleted: `IsCurrent`. See `database.delete_conversation_data`."""
        with self._lock, self._db:
            hers = self._conversations(conversation.user_id, conversation.character_id)
            if hers[0].id == conversation.id:
                raise IsCurrent()
            delete_conversation_data(self._db, conversation.id)

    def whole_conversations(self, user_id: str) -> list[WholeConversation]:
        """Every one of the user's conversations, with every character, the oldest first, and
        all that is kept under each, for a data package; not the pictures of photos, read
        one at a time (`photo_image`)."""
        with self._lock:
            conversations = self._db.execute(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY created_at, rowid",
                (user_id,),
            ).fetchall()
            return [
                WholeConversation(
                    c["id"], c["character_id"], c["created_at"], c["title"],
                    [
                        KeptMessage(
                            m["role"], m["text"], m["ja"], m["zh"],
                            [Segment(**s) for s in json.loads(m["segments"] or "[]")],
                            m["created_at"], _change(m["affinity"]), m["hurt"],
                            None if m["out_of_line"] is None else bool(m["out_of_line"]),
                            None if m["photo_id"] is None else KeptPhoto(
                                m["photo_id"], m["photo_tag"], m["photo_status"],
                                m["photo_reason"], m["photo_created_at"], m["photo_updated_at"],
                            ),
                        )
                        for m in self._db.execute(
                            "SELECT m.*, p.id AS photo_id, p.tag AS photo_tag,"
                            " p.status AS photo_status, p.reason AS photo_reason,"
                            " p.created_at AS photo_created_at, p.updated_at AS photo_updated_at"
                            " FROM messages m LEFT JOIN photos p ON p.message_id = m.id"
                            " WHERE m.conversation_id = ? ORDER BY m.id",
                            (c["id"],),
                        )
                    ],
                    [
                        KeptEvent(
                            e["kind"], e["created_at"], _change(e["affinity"]), e["gift_id"],
                            bool(e["liked"]),
                        )
                        for e in self._db.execute(
                            # A keepsake's event goes with the keepsake, which a package does not carry.
                            f"SELECT {_EVENT_COLUMNS} FROM conversation_events"
                            " WHERE conversation_id = ? AND kind <> 'keepsake' ORDER BY id",
                            (c["id"],),
                        )
                    ],
                    [
                        ConnectedCall(k["connected_at"], k["duration_s"])
                        for k in self._db.execute(
                            "SELECT connected_at, duration_s FROM calls"
                            " WHERE conversation_id = ? ORDER BY id",
                            (c["id"],),
                        )
                    ],
                    [
                        KeptReachOut(
                            r["ja"], r["zh"], [Segment(**s) for s in json.loads(r["segments"])],
                            r["created_at"],
                        )
                        for r in self._db.execute(
                            "SELECT ja, zh, segments, created_at FROM reach_outs"
                            " WHERE conversation_id = ? ORDER BY id",
                            (c["id"],),
                        )
                    ],
                )
                for c in conversations
            ]

    def contacts(self, user_id: str) -> list[Contact]:
        """Every turn the user took and gift they gave, in what is left of their
        conversations, as stats count them (GLOSSARY.md「统计」); in no order."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.character_id, 'turn' AS kind, m.created_at, 0 AS liked FROM messages m"
                " JOIN conversations c ON c.id = m.conversation_id"
                " WHERE c.user_id = ? AND m.role = 'user'"
                " UNION ALL"
                " SELECT c.character_id, 'gift' AS kind, e.created_at, e.liked"
                " FROM conversation_events e JOIN conversations c ON c.id = e.conversation_id"
                " WHERE c.user_id = ? AND e.kind = 'gift'",
                (user_id, user_id),
            ).fetchall()
        return [
            Contact(r["character_id"], r["kind"], _at(r["created_at"]), bool(r["liked"]))
            for r in rows
        ]

    def keep_call(self, conversation_id: str, connected_at: datetime, duration_s: float) -> None:
        """Keep a call in the conversation that connected at `connected_at` and lasted
        `duration_s` seconds once it had, for stats; not into its record. It answers her
        latest reach-out in it (`_answer_reach_out`). Nothing is kept if the conversation is
        gone meanwhile, deleted with the user, say."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO calls (conversation_id, connected_at, duration_s)"
                " SELECT id, ?, ? FROM conversations WHERE id = ?",
                (connected_at.astimezone(UTC).isoformat(), duration_s, conversation_id),
            )
            self._answer_reach_out(conversation_id)

    def kept_calls(self, user_id: str) -> list[KeptCall]:
        """Every call kept in what is left of the user's conversations; in no order."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.character_id, k.connected_at, k.duration_s FROM calls k"
                " JOIN conversations c ON c.id = k.conversation_id WHERE c.user_id = ?",
                (user_id,),
            ).fetchall()
        return [KeptCall(r["character_id"], _at(r["connected_at"]), r["duration_s"]) for r in rows]

    def first_records(self, user_id: str) -> dict[str, datetime]:
        """When the first thing still in the user's conversations with each character, a
        message, an event or a call kept, was recorded; only those with anything left."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.character_id, MIN(r.created_at) AS first FROM ("
                " SELECT conversation_id, created_at FROM messages"
                " UNION ALL SELECT conversation_id, created_at FROM conversation_events"
                " UNION ALL SELECT conversation_id, connected_at FROM calls"
                ") r JOIN conversations c ON c.id = r.conversation_id"
                " WHERE c.user_id = ? GROUP BY c.character_id",
                (user_id,),
            ).fetchall()
        return {r["character_id"]: _at(r["first"]) for r in rows}

    def latest_conversations(
        self, user_id: str
    ) -> list[tuple[Conversation, Recorded | None, bool]]:
        """Each character's newest conversation with the user, the last thing in its record,
        a message, an event or a reach-out, as `record` orders them (None while it has none),
        and whether it has a reach-out the user has not seen (GLOSSARY.md「没看过的搭话」); for
        the conversation list."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM conversations WHERE user_id = ?"
                " ORDER BY created_at DESC, rowid DESC",
                (user_id,),
            ).fetchall()
            newest: dict[str, Conversation] = {}
            for row in rows:
                newest.setdefault(row["character_id"], Conversation(**row))
            return [(c, self._last(c.id), self._unseen(c.id)) for c in newest.values()]

    def _unseen(self, conversation_id: str) -> bool:
        """Whether the conversation has a reach-out the user has not seen."""
        return self._db.execute(
            "SELECT EXISTS (SELECT 1 FROM reach_outs WHERE conversation_id = ? AND seen = 0)",
            (conversation_id,),
        ).fetchone()[0] == 1

    def _last(self, conversation_id: str) -> Recorded | None:
        """The last thing in the record, but her ending it, as `record` orders them: the
        list shows her last line, and whether she sent a photo with it."""
        row = self._db.execute(
            f"SELECT {_MESSAGE_WITH_PHOTO} WHERE m.conversation_id = ?"
            " ORDER BY m.id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        message = _message_with_photo(row) if row else None
        event = self._db.execute(
            f"SELECT {_EVENT_COLUMNS} FROM conversation_events WHERE conversation_id = ?"
            " AND kind != 'ended' ORDER BY created_at DESC, id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        reach_out = self._db.execute(
            f"SELECT {_REACH_OUT_COLUMNS} FROM reach_outs WHERE conversation_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        between: list[ConversationEvent | ReachOut] = []
        if event is not None:
            between.append(_event(event))
        if reach_out is not None:
            between.append(_reach_out(reach_out))
        latest = max(between, key=_placed, default=None)
        if latest is None or message is None:
            return latest or message
        said = self._db.execute(
            "SELECT created_at FROM messages WHERE conversation_id = ? AND role = 'user'"
            " ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        last_turn_said = said["created_at"] if said else message.created_at
        after = _at(latest.created_at) >= _at(last_turn_said)  # as `record` places it
        return latest if after else message

    def add_turn(
        self,
        conversation_id: str,
        text: str,
        reply: list[Segment],
        affinity: Change | None = None,
        said_at: str | None = None,
        typed: Typed | None = None,
        ends: bool = False,
        photo: NewPhoto | None = None,
    ) -> Message:
        """Record the user's input, said at `said_at` (as `timestamp` gives it; now by
        default), and the character's reply together, with how the turn changed her
        affinity, if it did, and for a typed turn what it came to (`typed`); returns the
        reply. The photo she sends with it, if any, is recorded being generated. Should she
        end the conversation after it (`ends`), that is recorded with it, after it
        (`ended`). It answers her latest reach-out in it (`_answer_reach_out`). Into a
        conversation she has ended already, nothing is recorded: `ConversationEnded`."""
        with self._lock, self._db:
            if self._ended(conversation_id) is not None:
                raise ConversationEnded()
            self._insert_message(
                conversation_id, "user", text=text, created_at=said_at, typed=typed
            )
            replied = self._insert_message(
                conversation_id, "character", segments=reply, affinity=affinity
            )
            if photo is not None:
                now = _now()
                self._db.execute(
                    "INSERT INTO photos (id, conversation_id, message_id, tag, status,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (photo.id, conversation_id, replied.id, photo.tag, now, now),
                )
                replied = replace(replied, photo=Photo(photo.id, "pending", None, photo.tag))
            if ends:
                self._insert_event(conversation_id, "ended", _now())
            self._answer_reach_out(conversation_id)
            return replied

    def finish_photo(
        self, photo_id: str, image: bytes | None, reason: PhotoFailure | None
    ) -> bool:
        """Keep how the photo being generated came out: sent, as `image`, or not, for
        `reason`. False, keeping nothing, if it is no longer there, its conversation
        deleted meanwhile, say, or no longer being generated."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE photos SET status = ?, reason = ?, image = ?, updated_at = ?"
                " WHERE id = ? AND status = 'pending'",
                ("ready" if image is not None else "failed", reason, image, _now(), photo_id),
            )
        return cursor.rowcount > 0

    def give_up_photos(self, reason: PhotoFailure) -> int:
        """Keep every photo still being generated as not sent, for `reason`: nothing is
        generating it any more, the server having stopped meanwhile. How many there were."""
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE photos SET status = 'failed', reason = ?, updated_at = ?"
                " WHERE status = 'pending'",
                (reason, _now()),
            )
        return cursor.rowcount

    def photo(self, photo_id: str, user_id: str) -> Photo | None:
        """The photo, if it is in one of the user's conversations; None if not."""
        with self._lock:
            row = self._db.execute(
                "SELECT p.id, p.status, p.reason, p.tag FROM photos p"
                " JOIN conversations c ON c.id = p.conversation_id"
                " WHERE p.id = ? AND c.user_id = ?",
                (photo_id, user_id),
            ).fetchone()
        return Photo(**row) if row else None

    def photo_image(self, photo_id: str, user_id: str) -> bytes | None:
        """The picture of the photo, sent, in one of the user's conversations; None if there
        is none such."""
        with self._lock:
            row = self._db.execute(
                "SELECT p.image FROM photos p JOIN conversations c ON c.id = p.conversation_id"
                " WHERE p.id = ? AND c.user_id = ? AND p.status = 'ready'",
                (photo_id, user_id),
            ).fetchone()
        return row["image"] if row else None

    def add_gift(
        self,
        user_id: str,
        character_id: str,
        gift_id: str,
        liked: bool,
        affinity: Change | None,
    ) -> Conversation:
        """Record the user's giving the character the gift, now, whether she liked it, with
        how it changed her affinity, if it did; returns the conversation it is recorded in:
        her newest, her current one (GLOSSARY.md「当前对话」), if she has not ended it, else
        one begun for it, which is her current one from then on. Where it goes is chosen in
        the same transaction it is recorded in, so it never goes into one she ends
        meanwhile."""
        with self._lock, self._db:
            hers = self._conversations(user_id, character_id)
            conversation = hers[0] if hers and self._ended(hers[0].id) is None else None
            if conversation is None:
                conversation = Conversation(uuid.uuid4().hex, user_id, character_id, _now())
                self._insert_conversation(conversation)
            self._insert_event(conversation.id, "gift", _now(), affinity, gift_id, liked)
        return conversation

    def hang_up(self, conversation_id: str) -> ConversationEvent:
        """Record that she hung up on the user in the conversation, now (`hung_up`), unless
        she has ended it: then nothing more is recorded, and it is her ending it that is
        returned."""
        with self._lock, self._db:
            return self._ended(conversation_id) or self._insert_event(
                conversation_id, "hung_up", _now()
            )

    def give_keepsake(
        self, conversation_id: str, keepsake_id: int, name: str, at: str | None = None
    ) -> ConversationEvent | None:
        """Record that she gave the user the keepsake `keepsake_id`, named `name`, in the
        conversation, now or `at` (`keepsake`); nothing if she has ended it."""
        with self._lock, self._db:
            if self._ended(conversation_id) is not None:
                return None
            cursor = self._db.execute(
                "INSERT INTO conversation_events"
                " (conversation_id, kind, created_at, keepsake_id, note) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, "keepsake", at or _now(), keepsake_id, name),
            )
            assert cursor.lastrowid is not None
            row = self._db.execute(
                f"SELECT {_EVENT_COLUMNS} FROM conversation_events WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return _event(row)

    def miss_call(self, conversation_id: str) -> ConversationEvent:
        """Record that she did not answer the user's call in the conversation, now
        (`missed_call`)."""
        with self._lock, self._db:
            return self._insert_event(conversation_id, "missed_call", _now())

    def missed_call_after(self, conversation_id: str, after: datetime) -> datetime | None:
        """When she last did not answer the user's call in the conversation (`missed_call`),
        if it was after `after`; None if not."""
        with self._lock:
            row = self._db.execute(
                "SELECT created_at FROM conversation_events"
                " WHERE conversation_id = ? AND kind = 'missed_call' ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        missed = datetime.fromisoformat(row[0]) if row else None
        return missed if missed is not None and missed > after else None

    def ended(self, conversation_id: str) -> ConversationEvent | None:
        """Her ending the conversation (GLOSSARY.md「已结束的对话」), as recorded; None while
        it is not ended."""
        with self._lock:
            return self._ended(conversation_id)

    def _ended(self, conversation_id: str) -> ConversationEvent | None:
        row = self._db.execute(
            f"SELECT {_EVENT_COLUMNS} FROM conversation_events"
            " WHERE conversation_id = ? AND kind = 'ended' ORDER BY id LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return _event(row) if row else None

    def typed_turns(self, conversation_id: str, latest: int) -> list[Typed]:
        """What the conversation's `latest` typed turns came to, oldest first; turns of calls,
        and those recorded before typed turns were counted, are not among them."""
        with self._lock:
            rows = self._db.execute(
                "SELECT hurt, out_of_line FROM messages WHERE conversation_id = ?"
                " AND role = 'user' AND hurt IS NOT NULL ORDER BY id DESC LIMIT ?",
                (conversation_id, latest),
            ).fetchall()
        return [Typed(row["hurt"], bool(row["out_of_line"])) for row in reversed(rows)]

    def appeal_target(self, user_id: str, reply_id: int) -> "AppealTarget | None":
        """Her reply `reply_id` in one of the user's conversations, as an appeal weighs it
        (`appeals`); None if there is no such reply of theirs."""
        with self._lock:
            reply = self._db.execute(
                "SELECT m.id, m.conversation_id, m.affinity, m.appealed_at, c.character_id"
                " FROM messages m JOIN conversations c ON c.id = m.conversation_id"
                " WHERE m.id = ? AND c.user_id = ? AND m.role = 'character'",
                (reply_id, user_id),
            ).fetchone()
            if reply is None:
                return None
            said = self._db.execute(
                "SELECT id, hurt FROM messages WHERE conversation_id = ? AND id < ?"
                " ORDER BY id DESC LIMIT 1",
                (reply["conversation_id"], reply_id),
            ).fetchone()
            later = self._db.execute(
                "SELECT 1 FROM messages WHERE conversation_id = ? AND id > ? LIMIT 1",
                (reply["conversation_id"], reply_id),
            ).fetchone()
            ended = self._db.execute(
                "SELECT 1 FROM conversation_events WHERE conversation_id = ? AND kind = 'ended'"
                " LIMIT 1",
                (reply["conversation_id"],),
            ).fetchone()
        return AppealTarget(
            conversation_id=reply["conversation_id"],
            character_id=reply["character_id"],
            change=_change(reply["affinity"]),
            appealed=reply["appealed_at"] is not None,
            latest=later is None,
            ended=ended is not None,
            said_id=None if said is None else said["id"],
            hurt=0.0 if said is None or said["hurt"] is None else said["hurt"],
        )

    def record_appeal(self, reply_id: int, said_id: int, at: str) -> None:
        """Record the appeal of her reply `reply_id` at `at`: what the user's message
        `said_id` before it came to is set back to nothing."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE messages SET appealed_at = ? WHERE id = ?", (at, reply_id)
            )
            self._db.execute(
                "UPDATE messages SET hurt = 0, out_of_line = 0 WHERE id = ?", (said_id,)
            )

    def appeals_made(self, user_id: str) -> list[str]:
        """When the user appealed, every time, with every character (ISO)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT m.appealed_at FROM messages m"
                " JOIN conversations c ON c.id = m.conversation_id"
                " WHERE c.user_id = ? AND m.appealed_at IS NOT NULL",
                (user_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def list_messages(self, conversation_id: str) -> list[Message]:
        """The conversation's messages, each reply with its photo, if any, but not its
        picture."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_MESSAGE_WITH_PHOTO} WHERE m.conversation_id = ? ORDER BY m.id",
                (conversation_id,),
            ).fetchall()
        return [_message_with_photo(row) for row in rows]

    def record(self, conversation_id: str) -> list[Recorded]:
        """The conversation's messages, events and reach-outs in the order things happened: a
        turn where the user's words were said, its two messages together, an event or a
        reach-out where it happened; so one written while a turn was under way comes after
        the turn."""
        messages = self.list_messages(conversation_id)
        with self._lock:
            events = self._db.execute(
                f"SELECT {_EVENT_COLUMNS} FROM conversation_events WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
            reach_outs = self._db.execute(
                f"SELECT {_REACH_OUT_COLUMNS} FROM reach_outs WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
        pending: list[ConversationEvent | ReachOut] = sorted(
            [*map(_event, events), *map(_reach_out, reach_outs)], key=_placed
        )
        merged: list[Recorded] = []
        for message in messages:
            if message.role == "user":
                while pending and _at(pending[0].created_at) < _at(message.created_at):
                    merged.append(pending.pop(0))
            merged.append(message)
        return merged + pending

    def read_by_user(self, conversation_id: str) -> list[Recorded]:
        """The conversation's record as `record` gives it, read by the user: every reach-out
        in it is seen from now on (GLOSSARY.md「没看过的搭话」); one made after it was read, not."""
        entries = self.record(conversation_id)
        given = [entry.id for entry in entries if isinstance(entry, ReachOut)]
        if given:
            with self._lock, self._db:
                self._db.execute(
                    "UPDATE reach_outs SET seen = 1 WHERE conversation_id = ? AND id <= ?"
                    " AND seen = 0",
                    (conversation_id, max(given)),
                )
        return entries

    def current_conversations(self) -> list[CurrentConversation]:
        """Every user's current conversation with each character they have one with, as
        reach-outs go by it, but for banned users'; in no order."""
        with self._lock:
            rows = self._db.execute(
                "SELECT c.*,"
                " (SELECT MAX(m.created_at) FROM messages m WHERE m.conversation_id = c.id)"
                "  AS last_turn,"
                " (SELECT MAX(r.created_at) FROM reach_outs r WHERE r.conversation_id = c.id)"
                "  AS reached_out_at,"
                " EXISTS (SELECT 1 FROM conversation_events e WHERE e.conversation_id = c.id"
                "  AND e.kind = 'ended') AS ended"
                " FROM conversations c JOIN users u ON u.id = c.user_id AND u.banned_at IS NULL"
                " WHERE c.rowid = (SELECT n.rowid FROM conversations n"
                "  WHERE n.user_id = c.user_id AND n.character_id = c.character_id"
                "  ORDER BY n.created_at DESC, n.rowid DESC LIMIT 1)"
            ).fetchall()
            calls: dict[str, list[tuple[str, float]]] = {}
            for call in self._db.execute(
                "SELECT conversation_id, connected_at, duration_s FROM calls"
                f" WHERE conversation_id IN ({','.join('?' * len(rows))})",
                [row["id"] for row in rows],
            ):
                calls.setdefault(call["conversation_id"], []).append(
                    (call["connected_at"], call["duration_s"])
                )
        current = []
        for row in rows:
            current.append(CurrentConversation(
                Conversation(
                    row["id"], row["user_id"], row["character_id"], row["created_at"],
                    row["title"],
                ),
                row["last_turn"] is not None,
                _last_contact(row["last_turn"], calls.get(row["id"], [])),
                _at(row["reached_out_at"]) if row["reached_out_at"] else None,
                bool(row["ended"]),
            ))
        return current

    def last_contact(self, user_id: str, character_id: str) -> datetime | None:
        """When the user and the character were last in touch, in any of their conversations:
        her last turn recorded, or a call that connected ending, whichever was later; None
        before either."""
        with self._lock:
            [last_turn] = self._db.execute(
                "SELECT MAX(m.created_at) FROM messages m"
                " JOIN conversations c ON c.id = m.conversation_id"
                " WHERE c.user_id = ? AND c.character_id = ?",
                (user_id, character_id),
            ).fetchone()
            calls = self._db.execute(
                "SELECT k.connected_at, k.duration_s FROM calls k"
                " JOIN conversations c ON c.id = k.conversation_id"
                " WHERE c.user_id = ? AND c.character_id = ?",
                (user_id, character_id),
            ).fetchall()
        return _last_contact(last_turn, calls)

    @contextmanager
    def taking_turn(self, conversation_id: str) -> Iterator[None]:
        """A text turn is under way in the conversation, from when it reads the record till
        it is recorded or fails; meanwhile she reaches out in it not (`add_reach_out`)."""
        with self._lock:
            self._taking_turns[conversation_id] += 1
        try:
            yield
        finally:
            with self._lock:
                self._taking_turns[conversation_id] -= 1
                if not self._taking_turns[conversation_id]:
                    del self._taking_turns[conversation_id]

    def is_taking_turn(self, conversation_id: str) -> bool:
        """Whether a text turn is under way in the conversation (`taking_turn`)."""
        with self._lock:
            return conversation_id in self._taking_turns

    def add_reach_out(
        self,
        conversation_id: str,
        said: list[Segment],
        contact: datetime,
        in_call: Callable[[str], bool],
    ) -> ReachOut:
        """Record what she said first in the conversation, now, as she thought it after the
        user and she were last in touch at `contact`; not seen yet. Looked at again as it is
        recorded, it is not, `ReachOutRefused`, unless the conversation is still her current
        one, not ended, last in touch at `contact`, without a reach-out since, and without a
        text turn under way or a call, as `in_call` says."""
        created_at = _now()
        ja, zh = "".join(s.ja for s in said), "".join(s.zh for s in said)
        with self._lock, self._db:
            current = self._db.execute(
                "SELECT n.id FROM conversations c JOIN conversations n"
                " ON n.user_id = c.user_id AND n.character_id = c.character_id"
                " WHERE c.id = ? ORDER BY n.created_at DESC, n.rowid DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            [reached_out_at] = self._db.execute(
                "SELECT MAX(created_at) FROM reach_outs WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if (
                current is None or current["id"] != conversation_id
                or self._ended(conversation_id) is not None
                or self._last_contact_in(conversation_id) != contact
                or (reached_out_at is not None and _at(reached_out_at) >= contact)
                or conversation_id in self._taking_turns or in_call(conversation_id)
            ):
                raise ReachOutRefused()
            cursor = self._db.execute(
                "INSERT INTO reach_outs (conversation_id, ja, zh, segments, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (conversation_id, ja, zh, _stored_segments(said), created_at),
            )
        assert cursor.lastrowid is not None
        return ReachOut(cursor.lastrowid, ja, zh, list(said), created_at)

    def add_letter(
        self,
        user_id: str,
        character_id: str,
        occasion: Occasion,
        said: list[Segment],
        at: datetime | None = None,
    ) -> Letter:
        """Keep a letter she wrote the user, coming `at`, or now; unread."""
        created_at = _now() if at is None else at.astimezone(UTC).isoformat()
        ja, zh = "".join(s.ja for s in said), "".join(s.zh for s in said)
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO letters (user_id, character_id, occasion, ja, zh, segments,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, character_id, occasion, ja, zh, _stored_segments(said), created_at),
            )
        assert cursor.lastrowid is not None
        return Letter(cursor.lastrowid, character_id, occasion, ja, zh, list(said), created_at)

    def letters(
        self, user_id: str, character_id: str | None = None, until: datetime | None = None
    ) -> list[Letter]:
        """The user's letters, newest first; only hers if `character_id` is given, and only
        those come by `until`, if given (else those written ahead too)."""
        query = f"SELECT {_LETTER_COLUMNS} FROM letters WHERE user_id = ?"
        params: tuple[str, ...] = (user_id,)
        if character_id is not None:
            query += " AND character_id = ?"
            params += (character_id,)
        if until is not None:
            query += " AND created_at <= ?"
            params += (until.astimezone(UTC).isoformat(),)
        with self._lock:
            rows = self._db.execute(query + " ORDER BY created_at DESC, id DESC", params).fetchall()
        return [_letter(row) for row in rows]

    def letter(self, user_id: str, letter_id: int, until: datetime) -> Letter:
        """One of the user's letters come by `until`; NotFound if there is no such letter of
        theirs, or it has not come yet."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {_LETTER_COLUMNS} FROM letters"
                " WHERE id = ? AND user_id = ? AND created_at <= ?",
                (letter_id, user_id, until.astimezone(UTC).isoformat()),
            ).fetchone()
        if row is None:
            raise NotFound("没有这封信")
        return _letter(row)

    def read_letter(self, user_id: str, letter_id: int, now: datetime) -> Letter:
        """The user reads one of their letters, `now`: read then, unless it was before.
        NotFound if there is no such letter of theirs come by now."""
        letter = self.letter(user_id, letter_id, now)
        with self._lock, self._db:
            self._db.execute(
                "UPDATE letters SET read_at = ? WHERE id = ? AND read_at IS NULL",
                (now.astimezone(UTC).isoformat(), letter_id),
            )
        return self.letter(user_id, letter.id, now)

    def unread_letters(self, user_id: str, until: datetime) -> int:
        """How many of the user's letters come by `until` are unread."""
        with self._lock:
            [count] = self._db.execute(
                "SELECT COUNT(*) FROM letters WHERE user_id = ? AND read_at IS NULL"
                " AND created_at <= ?",
                (user_id, until.astimezone(UTC).isoformat()),
            ).fetchone()
        return count

    def reach_out_recorded_as(self, reach_out_id: int, record_id: int | None) -> None:
        """Keep the id of the activity record of the reach-out, once it is written; answered
        already, should the user and she have been in touch since it was recorded."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE reach_outs SET record_id = ? WHERE id = ?", (record_id, reach_out_id)
            )
            row = self._db.execute(
                "SELECT conversation_id, created_at FROM reach_outs WHERE id = ?",
                (reach_out_id,),
            ).fetchone()
            if row is None:  # deleted meanwhile
                return
            contact = self._last_contact_in(row["conversation_id"])
            if record_id is not None and contact is not None and contact > _at(row["created_at"]):
                self._mark_answered(record_id)

    def _answer_reach_out(self, conversation_id: str) -> None:
        """Mark her latest reach-out in the conversation answered, the user and she being in
        touch in it now: a turn recorded, or a call connected, after it and before the next.
        One whose record is not kept yet is marked as it is (`reach_out_recorded_as`)."""
        latest = self._db.execute(
            "SELECT record_id FROM reach_outs WHERE conversation_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if latest is not None and latest["record_id"] is not None:
            self._mark_answered(latest["record_id"])

    def _mark_answered(self, record_id: int) -> None:
        """Mark a reach-out's activity record answered. The record is the activity log's;
        none in a database without one."""
        if "activity_records" in tables(self._db):
            self._db.execute(
                "UPDATE activity_records SET answered = 1 WHERE id = ?", (record_id,)
            )

    def _last_contact_in(self, conversation_id: str) -> datetime | None:
        """When the user and she were last in touch in the conversation, as
        `current_conversations` has it."""
        [last_turn] = self._db.execute(
            "SELECT MAX(created_at) FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        calls = self._db.execute(
            "SELECT connected_at, duration_s FROM calls WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        return _last_contact(last_turn, calls)

    def close(self) -> None:
        self._db.close()

    def _insert_sign_in_method(self, user_id: str, method: SignInMethod) -> None:
        self._db.execute(
            "INSERT INTO sign_in_methods"
            " (user_id, provider, subject, display_name, email, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, method.provider, method.subject, method.display_name, method.email,
             method.created_at),
        )

    def _insert_message(
        self,
        conversation_id: str,
        role: Role,
        text: str | None = None,
        segments: list[Segment] | None = None,
        affinity: Change | None = None,
        created_at: str | None = None,
        typed: Typed | None = None,
    ) -> Message:
        created_at = created_at or _now()
        ja = zh = None
        if segments is not None:
            ja = "".join(s.ja for s in segments)
            zh = "".join(s.zh for s in segments)
        cursor = self._db.execute(
            "INSERT INTO messages (conversation_id, role, text, ja, zh, segments, created_at,"
            " affinity, hurt, out_of_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id, role, text, ja, zh, _stored_segments(segments), created_at,
                _stored_change(affinity),
                typed and typed.hurt, typed and int(typed.out_of_line),
            ),
        )
        assert cursor.lastrowid is not None
        return Message(cursor.lastrowid, role, text, ja, zh, created_at, segments or [], affinity)

    def _conversations(self, user_id: str, character_id: str) -> list[Conversation]:
        rows = self._db.execute(
            "SELECT * FROM conversations WHERE user_id = ? AND character_id = ?"
            " ORDER BY created_at DESC, rowid DESC",
            (user_id, character_id),
        ).fetchall()
        return [Conversation(**row) for row in rows]

    def _insert_conversation(self, conversation: Conversation) -> None:
        self._db.execute(
            "INSERT INTO conversations (id, user_id, character_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (conversation.id, conversation.user_id, conversation.character_id,
             conversation.created_at),
        )

    def _insert_event(
        self,
        conversation_id: str,
        kind: EventKind,
        created_at: str,
        affinity: Change | None = None,
        gift_id: str | None = None,
        liked: bool = False,
    ) -> ConversationEvent:
        cursor = self._db.execute(
            "INSERT INTO conversation_events"
            " (conversation_id, kind, created_at, affinity, gift_id, liked)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                conversation_id, kind, created_at, _stored_change(affinity), gift_id, int(liked),
            ),
        )
        assert cursor.lastrowid is not None
        return ConversationEvent(
            cursor.lastrowid, kind, created_at, affinity, gift_id=gift_id, liked=liked
        )


_USER_COLUMNS = "id, name, created_at, consented_at, banned_at, ban_note"
_MESSAGE_COLUMNS = (
    "m.id AS id, role, text, ja, zh, segments, m.created_at AS created_at, affinity,"
    " appealed_at"
)
_MESSAGE_WITH_PHOTO = (
    f"{_MESSAGE_COLUMNS}, p.id AS photo_id, p.status AS photo_status,"
    " p.reason AS photo_reason, p.tag AS photo_tag"
    " FROM messages m LEFT JOIN photos p ON p.message_id = m.id"
)
"""A message with its photo, if any, but not its picture (`_message_with_photo`)."""
_EVENT_COLUMNS = "id, kind, created_at, affinity, gift_id, liked, keepsake_id, note"
_REACH_OUT_COLUMNS = "id, ja, zh, segments, created_at"
_LETTER_COLUMNS = "id, character_id, occasion, ja, zh, segments, created_at, read_at"

def write_profile(db: sqlite3.Connection, user_id: str, profile: ProducerProfile) -> None:
    """Save the user's whole producer profile through `db`, in the caller's transaction: what
    `Store.save_profile` does, and importing a data package in a transaction of its own."""
    birthday = profile.birthday
    db.execute(
        "INSERT INTO producer_profiles"
        " (user_id, form_of_address, gender, timezone, birthday_month, birthday_day,"
        " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (user_id) DO UPDATE SET form_of_address = excluded.form_of_address,"
        " gender = excluded.gender, timezone = excluded.timezone,"
        " birthday_month = excluded.birthday_month, birthday_day = excluded.birthday_day,"
        " updated_at = excluded.updated_at",
        (
            user_id,
            profile.form_of_address,
            profile.gender,
            profile.timezone,
            birthday and birthday.month,
            birthday and birthday.day,
            _now(),
        ),
    )


def add_carried_conversation(
    db: sqlite3.Connection,
    user_id: str,
    conversation: WholeConversation,
    picture_of: Callable[[str], bytes],
) -> tuple[str, bool]:
    """Add the conversation a data package carries to the user's through `db`, in the
    caller's transaction, under an id of its own, its messages in their order and its events
    and calls as they were, her reach-outs as seen, each photo under an id of its own with its
    picture, if sent, as `picture_of` its id in the package reads it; its id here, and whether
    it is new. One the user already has with her, begun at the same time, is taken for it and
    left as it is, nothing of it merged. Which is her current one is as ever: the newest
    begun."""
    begun = _at(conversation.created_at)
    for row in db.execute(
        "SELECT id, created_at FROM conversations WHERE user_id = ? AND character_id = ?",
        (user_id, conversation.character_id),
    ).fetchall():
        if _at(row[1]) == begun:
            return row[0], False
    conversation_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO conversations (id, user_id, character_id, created_at, title)"
        " VALUES (?, ?, ?, ?, ?)",
        (conversation_id, user_id, conversation.character_id, conversation.created_at,
         conversation.title),
    )
    for m in conversation.messages:
        message_id = db.execute(
            "INSERT INTO messages (conversation_id, role, text, ja, zh, segments, created_at,"
            " affinity, hurt, out_of_line) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id, m.role, m.text, m.ja, m.zh,
                _stored_segments(m.segments if m.segments or m.role == "character" else None),
                m.created_at, _stored_change(m.affinity), m.hurt,
                None if m.out_of_line is None else int(m.out_of_line),
            ),
        ).lastrowid
        if (photo := m.photo) is not None:
            db.execute(
                "INSERT INTO photos (id, conversation_id, message_id, tag, status, reason,"
                " image, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex, conversation_id, message_id, photo.tag, photo.status,
                    photo.reason,
                    picture_of(photo.id) if photo.status == "ready" else None,
                    photo.created_at, photo.updated_at,
                ),
            )
    db.executemany(
        "INSERT INTO conversation_events"
        " (conversation_id, kind, created_at, affinity, gift_id, liked)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (conversation_id, e.kind, e.created_at, _stored_change(e.affinity), e.gift_id,
             int(e.liked))
            for e in conversation.events
        ],
    )
    db.executemany(
        "INSERT INTO calls (conversation_id, connected_at, duration_s) VALUES (?, ?, ?)",
        [(conversation_id, k.connected_at, k.duration_s) for k in conversation.calls],
    )
    db.executemany(
        "INSERT INTO reach_outs (conversation_id, ja, zh, segments, created_at, seen)"
        " VALUES (?, ?, ?, ?, ?, 1)",
        [
            (conversation_id, r.ja, r.zh, _stored_segments(r.segments), r.created_at)
            for r in conversation.reach_outs
        ],
    )
    return conversation_id, True


def _change(stored: str | None) -> Change | None:
    return Change(**json.loads(stored)) if stored else None


def _stored_change(change: Change | None) -> str | None:
    return change and json.dumps(asdict(change))


def _stored_segments(segments: list[Segment] | None) -> str | None:
    """A reply's segments as kept; None for the user's message, which has none."""
    if segments is None:
        return None
    return json.dumps([asdict(s) for s in segments], ensure_ascii=False)


def _message(row: sqlite3.Row) -> Message:
    return Message(
        row["id"], row["role"], row["text"], row["ja"], row["zh"], row["created_at"],
        [Segment(**s) for s in json.loads(row["segments"] or "[]")],
        _change(row["affinity"]),
        appealed=row["appealed_at"] is not None,
    )


def _message_with_photo(row: sqlite3.Row) -> Message:
    if row["photo_id"] is None:
        return _message(row)
    return replace(_message(row), photo=Photo(
        row["photo_id"], row["photo_status"], row["photo_reason"], row["photo_tag"]
    ))


def _event(row: sqlite3.Row) -> ConversationEvent:
    return ConversationEvent(
        row["id"], row["kind"], row["created_at"], _change(row["affinity"]),
        row["gift_id"], bool(row["liked"]), row["keepsake_id"], row["note"],
    )


def _letter(row: sqlite3.Row) -> Letter:
    return Letter(
        row["id"], row["character_id"], row["occasion"], row["ja"], row["zh"],
        [Segment(**s) for s in json.loads(row["segments"])], row["created_at"], row["read_at"],
    )


def _reach_out(row: sqlite3.Row) -> ReachOut:
    return ReachOut(
        row["id"], row["ja"], row["zh"],
        [Segment(**s) for s in json.loads(row["segments"])], row["created_at"],
    )


def _placed(entry: ConversationEvent | ReachOut) -> tuple[datetime, bool, int]:
    """Where an event or a reach-out comes among the others: by when it happened, events
    first at the same time."""
    return _at(entry.created_at), isinstance(entry, ReachOut), entry.id


def _at(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _call_ended(connected_at: str, duration_s: float) -> datetime:
    """When a call that connected at `connected_at` and lasted `duration_s` ended."""
    return _at(connected_at) + timedelta(seconds=duration_s)


def _last_contact(
    last_turn: str | None, calls: Iterable[tuple[str, float] | sqlite3.Row]
) -> datetime | None:
    """When a user and a character were last in touch: their last turn recorded, at
    `last_turn`, or the end of one of their `calls` that connected (when, and how long it
    lasted), whichever was later; None before either."""
    contacts = [_call_ended(at, seconds) for at, seconds in calls]
    if last_turn is not None:
        contacts.append(_at(last_turn))
    return max(contacts, default=None)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def timestamp() -> str:
    """Now, as the store writes when things happened."""
    return _now()


def _now() -> str:
    return datetime.now(UTC).isoformat()
