"""Data packages (GLOSSARY.md「数据包」, ADR-0025): a user's own data as a zip they keep,
exported here and imported into this deployment or another, under this account or another.
The format is described for people in `docs/data-package-format.md`.

A package holds `manifest.json` first, stored uncompressed so that a browser can read it
from the start of the file alone; then the picture of each photo sent, a WebP of its own in
`photos/`; then `data.json`, indented, in English keys; and nothing else. The manifest
says which format the package is in (`FORMAT`), when it was exported, which categories it
holds and how many of each. A deployment imports packages of its own format and older ones,
and refuses newer ones (`TooNew`) rather than drop what it cannot read. Anything else wrong with a package refuses it as broken (`BrokenPackage`).

Importing writes everything in one transaction of its own, through a connection of its own
to the database, so that a package fails as a whole and leaves nothing behind. Neither
exporting nor importing is recorded: no activity record, nothing an admin sees."""

import asyncio
import io
import json
import sqlite3
import threading
import zipfile
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from functools import partial
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, get_args
from zoneinfo import ZoneInfo

from PIL import Image
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from eidolon.affinity import Change, Tier, next_day
from eidolon.characters import Character
from eidolon.database import connect
from eidolon.gift_catalog import Gift
from eidolon.memory import (
    MAX_MEMORY_LENGTH,
    CarriedMemory,
    Memories,
    Memory,
    PackageKey,
    add_carried,
)
from eidolon.producer import (
    MAX_FORM_OF_ADDRESS_LENGTH,
    Birthday,
    Gender,
    ProducerProfile,
    is_day_of_the_year,
)
from eidolon.promises import MAX_TEXT, NewPromise, Promise, Promises
from eidolon.experiences import Experience, Experiences, NewExperience
from eidolon.producer import effective_timezone
from eidolon.reply_format import Segment
from eidolon.store import (
    ConnectedCall,
    EventKind,
    KeptEvent,
    KeptMessage,
    KeptPhoto,
    KeptReachOut,
    PhotoFailure,
    Role,
    Store,
    WholeConversation,
    add_carried_conversation,
    write_profile,
)

FORMAT = 4
"""The newest format this version reads, and the one it exports. Changing the format adds
one to it, and keeps reading the older ones: 2 added her reach-outs (`reach_outs`), 3 the
promises still standing (`promises`), 4 her own doings (`experiences`)."""

REACH_OUTS_SINCE = 2
"""The format whose conversations first held her reach-outs: before it, none may."""

Category = Literal["conversations", "photos", "memories", "profile"]
CATEGORIES: tuple[Category, ...] = get_args(Category)
"""What a package may hold, in the order a manifest lists them. Photos go with the
conversations they were sent in, never without them."""

MANIFEST = "manifest.json"
DATA = "data.json"
PHOTOS = "photos/"
"""Where in a package the picture of each photo sent is: `photos/<its id>.webp`."""

MAX_PACKAGE_BYTES = 200 * 1024 * 1024
"""How large a package may be to import: a package with many photos is large, and one too
large for this can be exported again without them."""

MAX_MANIFEST_BYTES = 64 * 1024
"""How large `manifest.json` may be unzipped: it is a few lines."""

MAX_DATA_BYTES = 200 * 1024 * 1024
"""How large `data.json` may be unzipped, so that a small zip cannot unzip to fill the
memory."""

MAX_PHOTO_BYTES = 20 * 1024 * 1024
"""How large the picture of a photo may be unzipped: one is some hundred KB."""

IMPORTS_PER_DAY = 3
"""How many imports a user may make a day (`affinity.day_of`): only those that succeed count,
even one that added nothing."""


class TooNew(Exception):
    """A package in a format newer than this version reads: the deployer should upgrade."""


class Busy(Exception):
    """The user is already importing or exporting a package: one at a time."""


class ImportsUsedUp(Exception):
    """The user has made today's imports (`IMPORTS_PER_DAY`); they can again at `retry_at`,
    as the next day begins."""

    def __init__(self, retry_at: datetime, now: datetime) -> None:
        super().__init__(f"今天的导入次数已经用完了（每天 {IMPORTS_PER_DAY} 次）")
        self.retry_at = retry_at
        self.now = now


class TooLarge(Exception):
    """A package larger than `MAX_PACKAGE_BYTES`, refused before the rest of it is read."""


class BrokenPackage(Exception):
    """Not a package this version can import: not a zip, entries it should not have, or
    JSON not in the format."""


def _stored_time(value: str) -> str:
    """A time as the database writes it (`_written`), from any ISO 8601 time with its zone:
    conversations and events are ordered by their times as text."""
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("a time without its zone")
    return _written(at)


def _written(at: datetime) -> str:
    """`at` as packages and the database write times: UTC, ISO 8601 with microseconds."""
    return at.astimezone(UTC).isoformat(timespec="microseconds")


Time = Annotated[str, AfterValidator(_stored_time)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PackagedProfile(_Strict):
    form_of_address: Annotated[str, Field(max_length=MAX_FORM_OF_ADDRESS_LENGTH)] | None
    gender: Gender | None
    timezone: Annotated[str, Field(max_length=100)] | None
    """As kept, even a zone this server does not know: it is passed over for the device's
    (`producer.effective_timezone`)."""
    birthday_month: int | None
    birthday_day: int | None

    @model_validator(mode="after")
    def _birthday(self) -> "PackagedProfile":
        month, day = self.birthday_month, self.birthday_day
        if (month is None) != (day is None):
            raise ValueError("half a birthday")
        if month is not None and day is not None and not is_day_of_the_year(month, day):
            raise ValueError("no such day")
        return self

    @classmethod
    def of(cls, profile: ProducerProfile) -> "PackagedProfile":
        birthday = profile.birthday
        return cls(
            form_of_address=profile.form_of_address,
            gender=profile.gender,
            timezone=profile.timezone,
            birthday_month=birthday and birthday.month,
            birthday_day=birthday and birthday.day,
        )

    def profile(self) -> ProducerProfile:
        birthday = (
            Birthday(self.birthday_month, self.birthday_day)
            if self.birthday_month is not None and self.birthday_day is not None
            else None
        )
        return ProducerProfile(
            self.form_of_address or None, self.gender, self.timezone or None, birthday
        )


PackageId = StrictInt | Annotated[str, Field(min_length=1, max_length=100)]
"""What a package calls one of its memories or conversations: a key within the package
only, the id it had where it was exported."""


class PackagedChange(_Strict):
    """How a turn or a gift changed her affinity, as kept with it (`affinity.Change`)."""

    up: bool
    tier_changed: bool
    tier: Tier
    cooldown: bool
    thawed: bool = False

    @classmethod
    def of(cls, change: Change | None) -> "PackagedChange | None":
        return change and cls(**asdict(change))

    def change(self) -> Change:
        return Change(**self.model_dump())


class PackagedSegment(_Strict):
    ja: str
    zh: str

    @classmethod
    def all_of(cls, segments: list[Segment]) -> "list[PackagedSegment]":
        return [cls(ja=s.ja, zh=s.zh) for s in segments]

    def kept(self) -> Segment:
        return Segment(self.ja, self.zh)


PhotoId = Annotated[str, Field(pattern=r"^[0-9A-Za-z_-]{1,100}$")]
"""What a package calls one of its photos, the name of its picture in it."""


class PackagedPhoto(_Strict):
    """The photo she sent with a reply: sent (`ready`), its picture in the package, or not
    sent after all (`failed`), and why not."""

    id: PhotoId
    tag: str
    status: Literal["ready", "failed"]
    reason: PhotoFailure | None
    created_at: Time
    updated_at: Time

    @property
    def sent(self) -> bool:
        return self.status == "ready"

    @model_validator(mode="after")
    def _why_not_if_not(self) -> "PackagedPhoto":
        if self.sent == (self.reason is not None):
            raise ValueError("a photo not sent without why, or why for one sent")
        return self

    @classmethod
    def of(cls, photo: KeptPhoto) -> "PackagedPhoto":
        """The photo; one still being generated as not sent for taking too long, as when the
        server restarts."""
        if photo.status == "pending":
            status, reason = "failed", "timeout"
        else:
            status, reason = photo.status, photo.reason
        return cls(
            id=photo.id, tag=photo.tag, status=status, reason=reason,
            created_at=photo.created_at, updated_at=photo.updated_at,
        )

    def not_sent(self) -> "PackagedPhoto":
        """The photo, sent but its picture gone as the package was written: as not sent."""
        return self.model_copy(update={"status": "failed", "reason": "timeout"})

    def kept(self) -> KeptPhoto:
        """The photo to add, its id still the package's."""
        return KeptPhoto(
            self.id, self.tag, self.status, self.reason, self.created_at, self.updated_at
        )


def sent(photos: list[PackagedPhoto]) -> list[PackagedPhoto]:
    """Those of `photos` she sent, whose pictures are in the package: what it counts as photos."""
    return [p for p in photos if p.sent]


class PackagedMessage(_Strict):
    role: Role
    text: str | None
    ja: str | None
    zh: str | None
    segments: list[PackagedSegment]
    created_at: Time
    affinity: PackagedChange | None
    hurt: float | None
    out_of_line: bool | None
    photo: PackagedPhoto | None = None
    """The photo she sent with her reply; no key for a message without one, or in a package
    without photos."""

    @model_validator(mode="after")
    def _photos_hers(self) -> "PackagedMessage":
        if self.photo is not None and self.role != "character":
            raise ValueError("a photo the user sent")
        return self

    @classmethod
    def of(cls, message: KeptMessage, with_photos: bool) -> "PackagedMessage":
        """The message, with its photo, if any, if `with_photos`."""
        packaged = cls(
            role=message.role, text=message.text, ja=message.ja, zh=message.zh,
            segments=PackagedSegment.all_of(message.segments),
            created_at=message.created_at, affinity=PackagedChange.of(message.affinity),
            hurt=message.hurt, out_of_line=message.out_of_line,
        )
        if with_photos and message.photo is not None:
            packaged.photo = PackagedPhoto.of(message.photo)
        return packaged

    def kept(self, with_photos: bool) -> KeptMessage:
        """The message to add, with its photo, if any, if `with_photos`."""
        return KeptMessage(
            self.role, self.text, self.ja, self.zh,
            [s.kept() for s in self.segments], self.created_at,
            self.affinity and self.affinity.change(), self.hurt, self.out_of_line,
            self.photo.kept() if with_photos and self.photo is not None else None,
        )


class PackagedEvent(_Strict):
    kind: EventKind
    created_at: Time
    affinity: PackagedChange | None
    gift_id: Annotated[str, Field(min_length=1, max_length=100)] | None
    """Which gift, for a gift's alone."""
    liked: bool

    @model_validator(mode="after")
    def _gift_for_a_gift(self) -> "PackagedEvent":
        if (self.kind == "gift") != (self.gift_id is not None):
            raise ValueError("a gift without which, or which gift for no gift")
        return self

    @classmethod
    def of(cls, event: KeptEvent) -> "PackagedEvent":
        return cls(
            kind=event.kind, created_at=event.created_at,
            affinity=PackagedChange.of(event.affinity), gift_id=event.gift_id, liked=event.liked,
        )

    def kept(self) -> KeptEvent:
        return KeptEvent(
            self.kind, self.created_at, self.affinity and self.affinity.change(), self.gift_id,
            self.liked,
        )


class PackagedCall(_Strict):
    connected_at: Time
    duration_s: Annotated[float, Field(ge=0)]


class PackagedReachOut(_Strict):
    """What she said first in the conversation, as the model wrote it, delivery cues and all."""

    ja: str
    zh: str
    segments: list[PackagedSegment]
    created_at: Time

    @classmethod
    def of(cls, reach_out: KeptReachOut) -> "PackagedReachOut":
        return cls(
            ja=reach_out.ja, zh=reach_out.zh, segments=PackagedSegment.all_of(reach_out.segments),
            created_at=reach_out.created_at,
        )

    def kept(self) -> KeptReachOut:
        return KeptReachOut(self.ja, self.zh, [s.kept() for s in self.segments], self.created_at)


class PackagedConversation(_Strict):
    id: PackageId
    character_id: Annotated[str, Field(min_length=1, max_length=100)]
    character_name: Annotated[str, Field(max_length=100)] | None
    """Her name where it was exported, for whoever reads the package, and for telling the
    user whose were skipped; None if she was no longer there."""
    created_at: Time
    title: str | None
    messages: list[PackagedMessage]
    """In the order they were recorded."""
    events: list[PackagedEvent]
    calls: list[PackagedCall]
    reach_outs: list[PackagedReachOut] = []
    """In the order they were recorded; none without the key, as in a package of a format
    before them."""

    @classmethod
    def of(
        cls, conversation: WholeConversation, name: str | None, with_photos: bool
    ) -> "PackagedConversation":
        """The conversation, with the photos she sent in it if `with_photos`."""
        return cls(
            id=conversation.id,
            character_id=conversation.character_id,
            character_name=name,
            created_at=conversation.created_at,
            title=conversation.title,
            messages=[PackagedMessage.of(m, with_photos) for m in conversation.messages],
            events=[PackagedEvent.of(e) for e in conversation.events],
            calls=[
                PackagedCall(connected_at=k.connected_at, duration_s=k.duration_s)
                for k in conversation.calls
            ],
            reach_outs=[PackagedReachOut.of(r) for r in conversation.reach_outs],
        )

    def photos(self) -> list[PackagedPhoto]:
        """The photos she sent in it, in their order."""
        return [m.photo for m in self.messages if m.photo is not None]

    def whole(self, gifts: Container[str], with_photos: bool) -> WholeConversation:
        """The conversation to add, with its photos if `with_photos`, but the gifts given in
        it that are not among `gifts`: her prompt names what she was given by the gift
        catalog."""
        return WholeConversation(
            self.id, self.character_id, self.created_at, self.title,
            [m.kept(with_photos) for m in self.messages],
            [e.kept() for e in self.events if e.gift_id is None or e.gift_id in gifts],
            [ConnectedCall(k.connected_at, k.duration_s) for k in self.calls],
            [r.kept() for r in self.reach_outs],
        )


class PackagedMemory(_Strict):
    id: PackageId
    source_character_id: Annotated[str, Field(min_length=1, max_length=100)]
    source_character_name: Annotated[str, Field(max_length=100)] | None
    """Her name where it was exported, for whoever reads the package; None if she was no
    longer there."""
    conversation: PackageId | None
    """The package's conversation it was told in; None without it."""
    text: Annotated[str, Field(min_length=1, max_length=MAX_MEMORY_LENGTH)]
    created_at: Time
    superseded_by: PackageId | None

    @classmethod
    def of(cls, memory: Memory, with_conversations: bool) -> "PackagedMemory":
        """The memory, saying which conversation it was told in if `with_conversations`, the
        package carrying them."""
        return cls(
            id=memory.id,
            source_character_id=memory.source_character_id,
            source_character_name=memory.source_character_name,
            conversation=memory.conversation_id if with_conversations else None,
            text=memory.text,
            created_at=_stored_time(memory.created_at),
            superseded_by=memory.superseded_by,
        )

    def carried(self, conversations: Mapping[PackageKey, str]) -> CarriedMemory:
        """The memory to add, told in the conversation here that `conversations` has for the
        package's; none if it has none, the package's not imported."""
        return CarriedMemory(
            key=self.id,
            source_character_id=self.source_character_id,
            text=self.text,
            created_at=self.created_at,
            superseded_by=self.superseded_by,
            conversation_id=None if self.conversation is None
            else conversations.get(self.conversation),
        )


class PackagedPromise(_Strict):
    """One promise the user still stands by (GLOSSARY.md「约定」): to which idol, what, and the
    day it is for. What has come of it does not travel: a package carries what is still to do."""

    character_id: str
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
    due: str
    """The day it is for, as `YYYY-MM-DD`."""

    @classmethod
    def of(cls, promise: Promise) -> "PackagedPromise":
        return cls(character_id=promise.character_id, text=promise.text, due=promise.due.isoformat())

    def new(self) -> NewPromise | None:
        """The promise to write down again here; None where its day cannot be read."""
        try:
            return NewPromise(self.text, date.fromisoformat(self.due))
        except ValueError:
            return None


class PackagedExperience(_Strict):
    """One of her own doings (GLOSSARY.md「她们的经历」): whose, what she did, what was looked up
    about it, and when it was last recalled — a package does not make one young again, so one
    already gone by its thirtieth day is not carried."""

    character_id: str
    text: Annotated[str, Field(min_length=1, max_length=200)]
    summary: str = ""
    url: str | None = None
    pinned: bool = False
    used_at: str | None = None
    """When it was last recalled, as times are written; null where nothing has yet."""

    @classmethod
    def of(cls, experience: Experience) -> "PackagedExperience":
        return cls(
            character_id=experience.character_id,
            text=experience.text,
            summary=experience.summary,
            url=experience.url,
            pinned=experience.pinned,
            used_at=_written(experience.last_used_at) if experience.last_used_at else None,
        )

    def new(self) -> NewExperience:
        return NewExperience(self.text, self.summary, self.url)

    def last_used(self) -> datetime | None:
        """When it was last recalled, read back; None where it cannot be read, so that it is
        counted from when it came in."""
        if not self.used_at:
            return None
        try:
            return datetime.fromisoformat(self.used_at)
        except ValueError:
            return None


class PackageData(_Strict):
    """`data.json`: a key for each category the package holds, none for the others."""

    profile: PackagedProfile | None = None
    conversations: list[PackagedConversation] | None = None
    memories: list[PackagedMemory] | None = None
    promises: list[PackagedPromise] | None = None
    """The promises (GLOSSARY.md「约定」) still standing: they travel with the memories."""
    experiences: list[PackagedExperience] | None = None
    """Her own doings (GLOSSARY.md「她们的经历」): they travel with the memories too."""

    @model_validator(mode="after")
    def _memories_told_in_conversations(self) -> "PackageData":
        ids = [c.id for c in self.conversations or []]
        if len(set(ids)) != len(ids):
            raise ValueError("two conversations under one id")
        for m in self.memories or []:
            if m.conversation is not None and m.conversation not in ids:
                raise ValueError("a memory told in no conversation of the package")
        return self

    @model_validator(mode="after")
    def _photos_one_by_one(self) -> "PackageData":
        ids = [p.id for p in self.photos()]
        if len(set(ids)) != len(ids):
            raise ValueError("two photos under one id")
        return self

    def photos(self) -> list[PackagedPhoto]:
        """The photos she sent in all its conversations."""
        return [p for c in self.conversations or [] for p in c.photos()]

    @model_validator(mode="after")
    def _memories_superseded_by_memories(self) -> "PackageData":
        ids = [m.id for m in self.memories or []]
        if len(set(ids)) != len(ids):
            raise ValueError("two memories under one id")
        for m in self.memories or []:
            if m.superseded_by is not None and (
                m.superseded_by == m.id or m.superseded_by not in ids
            ):
                raise ValueError("a memory superseded by no other memory of the package")
        return self


class Manifest(_Strict):
    format: StrictInt
    exported_at: Time
    categories: list[Category]
    counts: dict[Category, StrictInt]


class _Format(BaseModel):
    """Just the format of a manifest, read before the rest, whose shape it decides."""

    format: StrictInt


@dataclass
class Skipped:
    """Her conversations in a package that were not imported: she is not here."""

    character_name: str
    conversations: int


@dataclass
class Imported:
    """What an import added: only what is new, so importing the same package twice adds
    nothing the second time. `profile` is whether the producer profile was overwritten with
    one other than it was."""

    conversations: int = 0
    photos: int = 0
    """Only those sent, with their pictures: one not sent says only that she did not send it."""
    memories: int = 0
    promises: int = 0
    """The promises still standing when the package was read, and still to come."""
    experiences: int = 0
    """Her own doings still un-forgotten when the package was read."""
    profile: bool = False
    skipped: list[Skipped] = field(default_factory=list)


def chosen(include: Iterable[Category]) -> list[Category]:
    """The categories asked for, in their order; photos only with conversations."""
    asked = set(include)
    if "conversations" not in asked:
        asked.discard("photos")
    return [c for c in CATEGORIES if c in asked]


_SCHEMA = """
-- One row per import a user made that succeeded, at when it did: how many they have made
-- today. Those of earlier days are deleted as the next is made.
CREATE TABLE IF NOT EXISTS data_package_imports (
    user_id TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS data_package_imports_by_user ON data_package_imports (user_id, at);
"""


class DataPackages:
    """Exports a user's data package, and imports one into a user's data."""

    def __init__(
        self,
        database_path: Path,
        store: Store,
        memories: Memories,
        characters: Mapping[str, Character],
        gift_catalog: Mapping[str, Gift],
        clock: Callable[[], datetime],
        promises: Promises | None = None,
        experiences: Experiences | None = None,
    ) -> None:
        self._database_path = database_path
        self._store = store
        self._memories = memories
        self._characters = characters
        self._gift_catalog = gift_catalog
        self._clock = clock
        self._promises = promises
        self._experiences = experiences
        self._working: set[str] = set()
        self._working_lock = threading.Lock()
        db = connect(database_path)
        try:
            db.executescript(_SCHEMA)
        finally:
            db.close()

    def check_imports_left(self, user_id: str) -> None:
        """Raise `ImportsUsedUp` if the user has made today's imports."""
        now = self._clock()
        db = connect(self._database_path)
        try:
            (made,) = db.execute(
                "SELECT COUNT(*) FROM data_package_imports WHERE user_id = ? AND at >= ?",
                (user_id, _today_began(now)),
            ).fetchone()
        finally:
            db.close()
        if made >= IMPORTS_PER_DAY:
            raise ImportsUsedUp(next_day(now), now)

    def check_not_busy(self, user_id: str) -> None:
        """Raise `Busy` while the user is importing or exporting a package (`one_at_a_time`)."""
        with self._working_lock:
            if user_id in self._working:
                raise Busy()

    @contextmanager
    def one_at_a_time(self, user_id: str) -> Iterator[None]:
        """Held while the user imports a package, from reading its first byte to the end, or
        while their package is written to export; raises `Busy` while another of theirs is
        held. In this process only: a deployment runs one."""
        with self._working_lock:
            if user_id in self._working:
                raise Busy()
            self._working.add(user_id)
        try:
            yield
        finally:
            with self._working_lock:
                self._working.discard(user_id)

    def export(self, user_id: str, include: Iterable[Category], to: Path) -> None:
        """Write the user's package, with the categories in `include`, to the file `to`."""
        categories = chosen(include)
        data = PackageData()
        counts: dict[Category, int] = {}
        if "profile" in categories:
            data.profile = PackagedProfile.of(self._store.get_profile(user_id))
            counts["profile"] = 1
        if "conversations" in categories:
            data.conversations = [
                PackagedConversation.of(c, self._name(c.character_id), "photos" in categories)
                for c in self._store.whole_conversations(user_id)
            ]
            counts["conversations"] = len(data.conversations)
        if "photos" in categories:
            counts["photos"] = len(sent(data.photos()))
        if "memories" in categories:
            data.memories = [
                PackagedMemory.of(m, "conversations" in categories)
                for m in self._memories.kept(user_id)
            ]
            counts["memories"] = len(data.memories)
            # 约定跟记忆一起走（GLOSSARY.md「约定」）：还没做到的那些
            if self._promises is not None:
                data.promises = [
                    PackagedPromise.of(p) for p in self._promises.open_for(user_id)
                ]
            # 她自己的经历跟记忆一起走：已经忘掉的（三十天没被想起）不带走
            if self._experiences is not None:
                when = self._clock()
                data.experiences = [
                    PackagedExperience.of(e)
                    for e in self._experiences.all_of(user_id)
                    if (left := e.left(when)) is None or left > timedelta(0)
                ]
        now = self._clock()
        manifest = Manifest(
            format=FORMAT, exported_at=_written(now), categories=categories, counts=counts
        )
        with zipfile.ZipFile(to, "w") as package:
            package.writestr(
                zipfile.ZipInfo(MANIFEST, _zip_time(now)),
                _json(manifest.model_dump()),
                compress_type=zipfile.ZIP_STORED,
            )
            self._write_pictures(user_id, data, package, now)
            package.writestr(
                zipfile.ZipInfo(DATA, _zip_time(now)),
                _json(data.model_dump(exclude_unset=True)),
                compress_type=zipfile.ZIP_DEFLATED,
            )

    def _write_pictures(
        self, user_id: str, data: PackageData, package: zipfile.ZipFile, now: datetime
    ) -> None:
        """Write into `package` the picture of each photo sent in `data`, read one at a time;
        one whose picture is gone by then, its conversation deleted as the package is
        written, goes as not sent."""
        for conversation in data.conversations or []:
            for message in conversation.messages:
                photo = message.photo
                if photo is None or not photo.sent:
                    continue
                picture = self._store.photo_image(photo.id, user_id)
                if picture is None:
                    message.photo = photo.not_sent()
                    continue
                package.writestr(
                    zipfile.ZipInfo(_picture_entry(photo.id), _zip_time(now)),
                    picture,
                    compress_type=zipfile.ZIP_STORED,  # a WebP is compressed already
                )

    async def import_(self, user_id: str, package: Path, include: Iterable[Category]) -> Imported:
        """Import the categories of the package at `package` that `include` names into the
        user's data, all in one transaction, counted among today's imports; raises `TooNew`
        or `BrokenPackage` having written nothing. The memories it adds are embedded
        afterwards, in the background. Whether the user has imports left today is for the
        caller to check first (`check_imports_left`), while it holds `one_at_a_time`."""
        imported = await asyncio.to_thread(self._import, user_id, package, include)
        if imported.memories:
            self._memories.embed_carried(user_id)
        return imported

    def _import(self, user_id: str, package: Path, include: Iterable[Category]) -> Imported:
        try:
            zipped = zipfile.ZipFile(package)
        except (zipfile.BadZipFile, ValueError) as e:
            raise BrokenPackage(type(e).__name__) from None
        with zipped:
            return self._import_from(user_id, zipped, include)

    def _import_from(
        self, user_id: str, zipped: zipfile.ZipFile, include: Iterable[Category]
    ) -> Imported:
        manifest, data = _read(zipped)
        categories = set(chosen(include)) & set(manifest.categories)
        imported = Imported()
        db = connect(self._database_path)
        try:
            with db:
                if "profile" in categories and data.profile is not None:
                    profile = data.profile.profile()
                    if profile != self._store.get_profile(user_id):
                        write_profile(db, user_id, profile)
                        imported.profile = True
                conversations: dict[PackageKey, str] = {}
                if "conversations" in categories and data.conversations is not None:
                    conversations = self._add_conversations(
                        db, user_id, data.conversations, imported, zipped,
                        with_photos="photos" in categories,
                    )
                if "memories" in categories and data.memories is not None:
                    imported.memories = add_carried(
                        db, user_id, [m.carried(conversations) for m in data.memories]
                    )
                now = self._clock()
                if "memories" in categories and data.promises and self._promises is not None:
                    imported.promises = self._add_promises(user_id, data.promises, now)
                if "memories" in categories and data.experiences and self._experiences is not None:
                    imported.experiences = self._add_experiences(user_id, data.experiences, now)
                db.execute(
                    "DELETE FROM data_package_imports WHERE user_id = ? AND at < ?",
                    (user_id, _today_began(now)),
                )
                db.execute(
                    "INSERT INTO data_package_imports (user_id, at) VALUES (?, ?)",
                    (user_id, _written(now)),
                )
        finally:
            db.close()
        return imported

    def _add_conversations(
        self,
        db: sqlite3.Connection,
        user_id: str,
        packaged: list[PackagedConversation],
        imported: Imported,
        package: zipfile.ZipFile,
        with_photos: bool,
    ) -> dict[PackageKey, str]:
        """Add the package's conversations to the user's through `db`, with their photos if
        `with_photos`, the pictures of those sent read one at a time from `package`; counting
        those added, their photos, and those skipped, she not being here, into `imported`;
        what each is here, new or already here, by its id in the package."""
        here: dict[PackageKey, str] = {}
        skipped: dict[str, int] = {}
        for conversation in packaged:
            if conversation.character_id not in self._characters:
                name = conversation.character_name or conversation.character_id
                skipped[name] = skipped.get(name, 0) + 1
                continue
            here[conversation.id], new = add_carried_conversation(
                db, user_id, conversation.whole(self._gift_catalog, with_photos),
                partial(_picture, package),
            )
            imported.conversations += new
            if new and with_photos:
                imported.photos += len(sent(conversation.photos()))
        imported.skipped = [Skipped(name, count) for name, count in skipped.items()]
        return here

    def _add_promises(
        self, user_id: str, packaged: list[PackagedPromise], now: datetime
    ) -> int:
        """Write the package's promises down again for `user_id`, in their own time zone;
        those to an idol not here, and those for a day gone by, are left. How many came."""
        assert self._promises is not None
        zone = ZoneInfo(effective_timezone(self._store.get_profile(user_id), None))
        added = 0
        for entry in packaged:
            promise = entry.new()
            if promise is None or entry.character_id not in self._characters:
                continue
            self._promises.record(
                user_id, entry.character_id, [promise], now.astimezone(zone)
            )
            added += 1
        return added

    def _add_experiences(
        self, user_id: str, packaged: list[PackagedExperience], now: datetime
    ) -> int:
        """Keep her own doings the package carried, as old as they were there: those of an
        idol not here, and those whose thirtieth day has gone by, are left. How many came."""
        assert self._experiences is not None
        added = 0
        for entry in packaged:
            if entry.character_id not in self._characters:
                continue
            used_at = entry.last_used()
            carried = Experience(
                id=0, user_id=user_id, character_id=entry.character_id, text=entry.text,
                summary=entry.summary, url=entry.url,
                created_at=used_at or now, last_used_at=used_at, pinned=entry.pinned,
            )
            left = carried.left(now)
            if left is not None and left <= timedelta(0):
                continue  # 三十天已经过去，不带过来
            self._experiences.carry(
                user_id, entry.character_id, entry.new(), now, used_at, entry.pinned
            )
            added += 1
        return added

    def _name(self, character_id: str) -> str | None:
        character = self._characters.get(character_id)
        return character.name if character else None


def _today_began(now: datetime) -> str:
    """When the day it is at `now` (`affinity.day_of`) began, as times are written."""
    return _written(next_day(now) - timedelta(days=1))


def _read(zipped: zipfile.ZipFile) -> tuple[Manifest, PackageData]:
    """The manifest and data of the package `zipped`, checked, and the pictures of its
    photos with them, one at a time. Its format first, as soon as its manifest is read: a
    newer format may hold other entries, in another order."""
    try:
        entries = zipped.infolist()
        names = [e.filename for e in entries]
        if MANIFEST not in names:
            raise BrokenPackage("no manifest")
        raw_manifest = json.loads(_unzipped(zipped, MANIFEST, MAX_MANIFEST_BYTES))
        if _Format.model_validate(raw_manifest).format > FORMAT:
            raise TooNew()
        if len(set(names)) != len(names) or DATA not in names:
            raise BrokenPackage("entries it should not have")
        first = entries[0]
        if first.filename != MANIFEST or first.compress_type != zipfile.ZIP_STORED:
            raise BrokenPackage("its manifest not first, stored")
        manifest = Manifest.model_validate(raw_manifest)
        data = PackageData.model_validate_json(_unzipped(zipped, DATA, MAX_DATA_BYTES))
        if manifest.format < REACH_OUTS_SINCE and any(
            c.reach_outs for c in data.conversations or []
        ):
            raise BrokenPackage("reach-outs in a format before them")
        listed = set(manifest.categories)
        held = {c for c in CATEGORIES if c != "photos" and getattr(data, c) is not None}
        photos = data.photos()
        if held != listed - {"photos"} or (photos and "photos" not in listed) or (
            "photos" in listed and "conversations" not in listed
        ):
            raise BrokenPackage("categories not as the manifest lists them")
        if set(names) - {MANIFEST, DATA} != {_picture_entry(p.id) for p in sent(photos)}:
            raise BrokenPackage("pictures not as its photos are")
        for photo in sent(photos):
            _check_picture(_picture(zipped, photo.id))
    except (zipfile.BadZipFile, ValueError, ValidationError, KeyError) as e:
        # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise BrokenPackage(type(e).__name__) from None
    return manifest, data


def _picture_entry(photo_id: str) -> str:
    """Where in a package the picture of its photo is."""
    return f"{PHOTOS}{photo_id}.webp"


def _picture(zipped: zipfile.ZipFile, photo_id: str) -> bytes:
    """The picture of the package's photo, unzipped."""
    return _unzipped(zipped, _picture_entry(photo_id), MAX_PHOTO_BYTES)


def _check_picture(picture: bytes) -> None:
    """Refuse the package as broken unless `picture` opens as a WebP, of whatever size: but
    one of so many pixels that Pillow takes it for a decompression bomb, it refuses."""
    try:
        with Image.open(io.BytesIO(picture), formats=["WEBP"]) as opened:
            opened.load()
    except Exception as e:  # whatever Pillow raises for what is not a WebP
        raise BrokenPackage(type(e).__name__) from None


def _unzipped(zipped: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """The entry `name` unzipped, refusing it as broken past `limit` bytes, whatever its
    header says it unzips to."""
    with zipped.open(name) as entry:
        content = entry.read(limit + 1)
    if len(content) > limit:
        raise BrokenPackage(f"{name} too large")
    return content


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _zip_time(now: datetime) -> tuple[int, int, int, int, int, int]:
    return now.astimezone(UTC).timetuple()[:6]
