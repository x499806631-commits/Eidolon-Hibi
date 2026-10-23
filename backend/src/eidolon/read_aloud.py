"""Reading her messages aloud (GLOSSARY.md「朗读」): one of her replies or reach-outs in a
conversation of the user's, said in her voice when the user asks for it, on the deployer's
speech synthesis as the configuration has it for reading (`LiveConfig.read_aloud_voice`):
Fish Audio, as mp3, or their own GPT-SoVITS server (GLOSSARY.md「本地语音」), as WAV, Fish
Audio speaking for one without a voice there if the deployer has a key for it.

What is read is her Japanese, as she wrote it, delivery cues and all, as the engine takes
them. Each reading is kept, by what was read, in what voice and with what (`read_aloud`),
so that asking again plays it at once and costs nothing; the user's, deleted with them,
in no data package. On the deployer's keys, it counts towards no limit of the user's but
its own: `PER_HOUR` new readings an hour. Each is an activity record of its own
(`read_aloud`), the request to the engine a service call of it."""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from eidolon.activity import ActivityLog, Origin, host, timed
from eidolon.call import Speech
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.greetings import FISH_TTS_HTTP_URL
from eidolon.live_config import LiveConfig
from eidolon.reply_format import Segment

PER_HOUR = 60
"""How many messages a user may have read aloud an hour, those read before not counted."""

_KEY = re.compile(r"^([mr])(\d+)$")

_SCHEMA = """
-- Her messages read aloud (GLOSSARY.md「朗读」), by what was read in which voice with what
-- (`digest`): the audio and its media type, and when it was made.
CREATE TABLE IF NOT EXISTS read_aloud (
    user_id TEXT NOT NULL REFERENCES users(id),
    digest TEXT NOT NULL,
    audio BLOB NOT NULL,
    media_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, digest)
);
"""


class NothingToRead(Exception):
    """No such message of hers in the user's conversations, or nothing in it to say."""


class CannotRead(Exception):
    """There is no speech synthesis to read it with."""

    def __init__(self) -> None:
        super().__init__("这个部署没有配置语音合成")


class ReadingTooOften(Exception):
    """The user has had `PER_HOUR` read aloud within the hour."""

    def __init__(self) -> None:
        super().__init__(f"一小时最多朗读 {PER_HOUR} 条，过一会儿再来吧")


@dataclass(frozen=True)
class Reading:
    audio: bytes
    media_type: str


class ReadAloud:
    def __init__(
        self,
        db: Database,
        characters: dict[str, Character],
        config: LiveConfig,
        speech: Speech,
        activity: ActivityLog,
        clock: Clock,
    ) -> None:
        self._characters = characters
        self._config = config
        self._speech = speech
        self._activity = activity
        self._clock = clock
        self._db = db
        db.prepare(_open)

    def has_local_voice(self, character_id: str) -> bool:
        """Whether she has a voice on the deployer's GPT-SoVITS server."""
        return self._speech.has_local_voice(character_id)

    def available(self) -> bool:
        """Whether there is speech synthesis to read with now."""
        return self._config.read_aloud_voice() is not None

    async def read(self, user_id: str, key: str) -> Reading:
        """Her message `key` (`m<id>` for a reply, `r<id>` for a reach-out) read aloud.
        Raises NothingToRead, CannotRead, ReadingTooOften, and whatever the engine raises."""
        character_id, segments = await self._db.read(_message, user_id, key)
        character = self._characters.get(character_id)
        text = "".join(s.ja for s in segments).strip()
        if character is None or not text:
            raise NothingToRead()
        voice = self._config.read_aloud_voice()
        if voice is None:
            raise CannotRead()
        engine = voice.engine
        if engine == "gpt_sovits" and not self._speech.has_local_voice(character_id):
            if voice.api_key is None:
                raise CannotRead()
            engine = "fish"  # without a voice of hers there
        said_with = (
            f"gpt_sovits|{voice.gpt_sovits_url}" if engine == "gpt_sovits"
            else f"fish|{voice.model}|{character.voice_id}"
        )
        digest = hashlib.sha256(f"{character_id}\n{said_with}\n{text}".encode()).hexdigest()
        if (kept := await self._db.read(_kept, user_id, digest)) is not None:
            return kept
        if await self._db.read(_made_since, user_id, self._clock() - timedelta(hours=1)) >= PER_HOUR:
            raise ReadingTooOften()
        recording = self._activity.begin(
            "read_aloud", user_id, character_id, own_llm=False, exempt=False
        )
        try:
            with recording.current():
                if engine == "gpt_sovits":
                    audio = await timed(
                        self._activity, Origin("tts", host(voice.gpt_sovits_url), "gpt-sovits"),
                        self._speech.gpt_sovits_read(voice.gpt_sovits_url, character_id, text),
                    )
                    media_type = "audio/wav"
                else:
                    assert voice.api_key is not None
                    audio = await timed(
                        self._activity, Origin("tts", host(FISH_TTS_HTTP_URL), voice.model),
                        self._speech.fish_read(voice.api_key, voice.model, character.voice_id, text),
                    )
                    media_type = "audio/mpeg"
        except BaseException:
            await recording.finish("failed")
            raise
        await recording.finish("success")
        if not audio:
            raise NothingToRead()
        await self._db.write(
            _keep, user_id, digest, audio, media_type, self._clock().astimezone(UTC).isoformat()
        )
        return Reading(audio, media_type)


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _keep(
    db: sqlite3.Connection, user_id: str, digest: str, audio: bytes, media_type: str, at: str
) -> None:
    db.execute(
        "INSERT OR REPLACE INTO read_aloud (user_id, digest, audio, media_type, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (user_id, digest, audio, media_type, at),
    )


def _message(db: sqlite3.Connection, user_id: str, key: str) -> tuple[str, list[Segment]]:
    found = _KEY.match(key)
    if found is None:
        raise NothingToRead()
    table = "messages" if found.group(1) == "m" else "reach_outs"
    role = " AND m.role = 'character'" if table == "messages" else ""
    row = by_name(db).execute(
        f"SELECT c.character_id, m.segments, m.ja FROM {table} m"
        " JOIN conversations c ON c.id = m.conversation_id"
        f" WHERE m.id = ? AND c.user_id = ?{role}",
        (int(found.group(2)), user_id),
    ).fetchone()
    if row is None:
        raise NothingToRead()
    segments = [Segment(**s) for s in json.loads(row["segments"] or "[]")]
    if not segments and row["ja"]:
        segments = [Segment(row["ja"], "")]
    return row["character_id"], segments


def _kept(db: sqlite3.Connection, user_id: str, digest: str) -> Reading | None:
    row = db.execute(
        "SELECT audio, media_type FROM read_aloud WHERE user_id = ? AND digest = ?",
        (user_id, digest),
    ).fetchone()
    return Reading(row[0], row[1]) if row else None


def _made_since(db: sqlite3.Connection, user_id: str, since: datetime) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM read_aloud WHERE user_id = ? AND created_at > ?",
        (user_id, since.astimezone(UTC).isoformat()),
    ).fetchone()[0]
