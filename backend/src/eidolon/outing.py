"""Going out with her on the day of a day out (GLOSSARY.md「约会」): the producer meets her, and
they go through the stops of where they went (`outing_spots`), talking as in a text turn,
in her conversation, she told where they are each turn; a photo or two of them together on the
way; and when it is over, what happened written down for her to keep (a summary, kept as one of
her own doings, a moment for her diary, word of mouth, a card in the conversation).

A day out is met on its day, from `OPENS` to `CLOSES` in the producer's time zone, best within
`BEST`: met later than that, she says she waited. One never met by `CLOSES` is missed, and she
says so (`TripPhotos` asks `played`). Each day out is gone through once.
"""

import asyncio
import json
import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from eidolon.activity import ActivityLog, image_origin, timed
from eidolon.characters import Character
from eidolon.city_map import spot_of
from eidolon.data_access import Database
from eidolon.database import by_name
from eidolon.emotion import SaidSegment, SegmentToJudge, decide
from eidolon.engine import ConversationEngine
from eidolon.experiences import Experiences, NewExperience
from eidolon.features import AllOn, Switches
from eidolon.image_generation import ImageFailed
from eidolon.live_config import LiveConfig
from eidolon.llm import ChatLLM, ChatMessage
from eidolon.outing_spots import Stop, stops_of
from eidolon.photo_prompt import outing_prompt
from eidolon.producer import effective_timezone
from eidolon.relations import Relations
from eidolon.store import Conversation, Message, Store, add_outing_card, read_profile
from eidolon.trip_photos import TripPhotos

log = logging.getLogger(__name__)

OPENS = time(10)
"""From when on the day a day out can be met."""
BEST = (time(13), time(18))
"""When she expects to be met; later, she waited."""
CLOSES = time(22)
"""Past this, a day out not met is missed."""
MAX_PHOTOS = 2
"""Photos of them together a day out may take."""
BACKGROUND_TIME_LIMIT = 90.0
"""How long drawing a stop's background may take, in seconds."""

_SCHEMA = """
-- Each day out met (GLOSSARY.md「约会」): where, which stop they are at, when it began and
-- ended, how many photos were taken, and what was written down of it as it ended.
CREATE TABLE IF NOT EXISTS outings (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    character_id TEXT NOT NULL,
    day TEXT NOT NULL,
    what TEXT NOT NULL,
    spot TEXT,
    conversation_id TEXT NOT NULL,
    stop INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    late_minutes INTEGER NOT NULL DEFAULT 0,
    after_message INTEGER NOT NULL DEFAULT 0,
    ended_at TEXT,
    photos INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    UNIQUE (user_id, character_id, day)
);
-- The backgrounds drawn for each stop of each place, for a user (with image generation).
CREATE TABLE IF NOT EXISTS outing_backgrounds (
    user_id TEXT NOT NULL REFERENCES users(id),
    spot TEXT NOT NULL,
    stop TEXT NOT NULL,
    image BLOB NOT NULL,
    drawn INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, spot, stop)
);
"""


class NoDateToday(Exception):
    def __init__(self) -> None:
        super().__init__("今天没有和她约好的约会")


class NotYet(Exception):
    def __init__(self) -> None:
        super().__init__(f"还没到时间，{OPENS.strftime('%H:%M')} 以后才能去赴约")


class TooLate(Exception):
    def __init__(self) -> None:
        super().__init__("太晚了，今天的约会已经错过了")


class NoSuchOuting(Exception):
    def __init__(self) -> None:
        super().__init__("没有这次约会")


class OutingOver(Exception):
    def __init__(self) -> None:
        super().__init__("这次约会已经结束了")


class NoMorePhotos(Exception):
    def __init__(self) -> None:
        super().__init__(f"这次约会已经拍了 {MAX_PHOTOS} 张合照了")


class CannotDraw(Exception):
    def __init__(self) -> None:
        super().__init__("现在没有生图服务，画不了")


@dataclass(frozen=True)
class Outing:
    id: int
    user_id: str
    character_id: str
    day: date
    what: str
    spot: str | None
    conversation_id: str
    stop: int
    started_at: datetime
    late_minutes: int
    after_message: int
    """The conversation's last message before they met: those after it are the day out's."""
    ended_at: datetime | None
    photos: int
    summary: dict | None

    @property
    def stops(self) -> tuple[Stop, ...]:
        return stops_of(self.spot)  # pyright: ignore[reportArgumentType]

    @property
    def here(self) -> Stop:
        return self.stops[min(self.stop, len(self.stops) - 1)]

    def shown(self) -> dict:
        return {
            "id": self.id, "character_id": self.character_id, "day": self.day.isoformat(),
            "what": self.what, "spot": self.spot, "conversation_id": self.conversation_id,
            "stops": [{"id": s.id, "name": s.name} for s in self.stops], "stop": self.stop,
            "started_at": self.started_at.isoformat(), "late_minutes": self.late_minutes,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "photos": self.photos, "max_photos": MAX_PHOTOS,
            "recap": (self.summary or {}).get("recap"),
        }


class Outings:
    """Every user's days out met, in `db` beside the users they belong to (ADR-0028)."""

    def __init__(
        self,
        db: Database,
        store: Store,
        engine: ConversationEngine,
        characters: Mapping[str, Character],
        config: LiveConfig,
        activity: ActivityLog,
        clock: Callable[[], datetime],
        trip_photos: TripPhotos,
        experiences: Experiences | None,
        moment: Callable[[str, str, str, datetime], Awaitable[None]] | None,
        relations: Relations | None,
        features: Switches = AllOn(),
    ) -> None:
        self._db = db
        self._store = store
        self._engine = engine
        self._characters = characters
        self._config = config
        self._activity = activity
        self._clock = clock
        self._trip_photos = trip_photos
        self._experiences = experiences
        self._moment = moment
        self._relations = relations
        self._features = features
        db.prepare(_open)
        engine.with_outings(self.prompt_for)

    async def _zone(self, user_id: str) -> ZoneInfo:
        profile = await self._db.read(read_profile, user_id)
        return ZoneInfo(effective_timezone(profile, None))

    async def today(self, user_id: str, character_id: str) -> dict:
        """The day out with her today, if there is one: what, where, when best, whether it
        can be met now, and the outing if met; and whether stops can be drawn."""
        zone = await self._zone(user_id)
        now = self._clock().astimezone(zone)
        planned = await self._db.read(_planned_on, user_id, character_id, now.date())
        outing = await self._db.read(_outing_on, user_id, character_id, now.date())
        can_draw = self._config.image_generator() is not None
        if planned is None:
            return {"date": None, "outing": None, "can_draw": can_draw}
        what = planned["what"]
        return {
            "date": {
                "day": now.date().isoformat(), "what": what, "spot": spot_of(what),
                "opens": OPENS.strftime("%H:%M"), "best_from": BEST[0].strftime("%H:%M"),
                "best_until": BEST[1].strftime("%H:%M"), "closes": CLOSES.strftime("%H:%M"),
                "can_start": outing is None and OPENS <= now.time() < CLOSES,
            },
            "outing": outing.shown() if outing else None,
            "can_draw": can_draw,
        }

    async def start(self, user_id: str, character_id: str) -> Outing:
        """Meet her for today's day out, in her current conversation; the one met already
        if they met. Raises NoDateToday, NotYet, TooLate."""
        zone = await self._zone(user_id)
        now = self._clock().astimezone(zone)
        planned = await self._db.read(_planned_on, user_id, character_id, now.date())
        if planned is None:
            raise NoDateToday()
        met = await self._db.read(_outing_on, user_id, character_id, now.date())
        if met is not None:
            return met
        if now.time() < OPENS:
            raise NotYet()
        if now.time() >= CLOSES:
            raise TooLate()
        conversation = await self._conversation(user_id, character_id)
        best_until = datetime.combine(now.date(), BEST[1], zone)
        late = max(0, int((now - best_until).total_seconds() // 60))
        what = planned["what"]
        before = await self._store.list_messages(conversation.id)
        after_message = max((m.id for m in before), default=0)
        await self._db.write(
            _start, user_id, character_id, now.date(), what, spot_of(what), conversation.id, now,
            late, after_message,
        )
        met = await self._db.read(_outing_on, user_id, character_id, now.date())
        assert met is not None
        return met

    async def _conversation(self, user_id: str, character_id: str) -> Conversation:
        for conversation, _, _ in await self._store.latest_conversations(user_id):
            if conversation.character_id == character_id:
                if await self._store.ended(conversation.id) is None:
                    return conversation
                break
        return await self._store.create_conversation(user_id, character_id)

    async def of(self, user_id: str, outing_id: int) -> Outing:
        found = await self._db.read(_outing, user_id, outing_id)
        if found is None:
            raise NoSuchOuting()
        return found

    async def next_stop(self, user_id: str, outing_id: int) -> Outing:
        """On to the next stop; at the last, nowhere further. Raises OutingOver."""
        outing = await self.of(user_id, outing_id)
        if outing.ended_at is not None:
            raise OutingOver()
        if outing.stop + 1 < len(outing.stops):
            await self._db.write(_set_stop, outing_id, outing.stop + 1)
        return await self.of(user_id, outing_id)

    async def prompt_for(self, conversation: Conversation) -> str | None:
        """What her text turn is told while she is out with the producer in `conversation`:
        where they are, and how to be there; None otherwise."""
        outing = await self._db.read(_active_in, conversation.id)
        if outing is None:
            return None
        here = outing.here
        stops = outing.stops
        late = (
            f"\n- 制作人比约好的时间晚到了 {outing.late_minutes} 分钟，你等了他很久；见面时按你的性格说一句，之后就不要再提。"
            if outing.late_minutes > 0 and outing.stop == 0 else ""
        )
        return (
            f"# 约会中\n你和制作人正在约会：{outing.what}。"
            f"现在在第 {outing.stop + 1} 站（共 {len(stops)} 站）：{here.name}。{here.scene}\n"
            "- 你们是面对面在一起，不是在发消息或打电话。用当面说话的口吻；看到什么、做了什么小动作，"
            "可以在台词的中文字幕里用（）简短写一句。\n"
            "- 制作人用（动作：……）写的是他当面做的事，自然地回应。\n"
            "- 围绕这一站能看到、能做的事聊；隔几轮可以自然地发生一点小事，不要每轮都有。\n"
            "- 不要自己说去下一站或结束约会，由制作人决定。"
            f"{late}"
        )

    async def photo(self, user_id: str, outing_id: int, wish: str) -> str:
        """Take a photo of them together at the stop they are at, as `wish` asks (may be
        empty), sent in the conversation as she takes it, drawn after: the conversation.
        Raises OutingOver, NoMorePhotos, CannotDraw, and what the model raises."""
        outing = await self.of(user_id, outing_id)
        if outing.ended_at is not None:
            raise OutingOver()
        if outing.photos >= MAX_PHOTOS:
            raise NoMorePhotos()
        if self._config.image_generator() is None or not await self._features.on(user_id, "photos"):
            raise CannotDraw()
        conversation = await self._store.get_conversation(outing.conversation_id)
        if conversation is None:
            raise NoSuchOuting()
        recording = self._activity.begin("trip_photo", user_id, outing.character_id, own_llm=False, exempt=False)
        lease = self._config.lease()
        try:
            with recording.current():
                said, tag = await self._engine.outing_photo(
                    conversation, lease.keys.chat_llm, outing.here.name, outing.here.tags, wish.strip()
                )
        except Exception:
            await recording.finish("failed")
            raise
        finally:
            lease.release()
        assert tag is not None  # `outing_photo` raises without one
        await self._db.write(_took_photo, outing_id)
        await self._trip_photos.send_outing_photo(conversation, said, tag, recording)
        return conversation.id

    async def background(self, user_id: str, spot: str, stop: str) -> bytes | None:
        return await self._db.read(_background, user_id, spot, stop)

    async def draw_background(self, user_id: str, outing_id: int, again: bool = False) -> bool:
        """Draw the background of the stop they are at, unless drawn already (or `again`):
        whether there is one. Raises CannotDraw."""
        outing = await self.of(user_id, outing_id)
        spot = outing.spot or "around"
        if not again and await self.background(user_id, spot, outing.here.id) is not None:
            return True
        generator = self._config.image_generator()
        if generator is None or not await self._features.on(user_id, "images"):
            raise CannotDraw()
        settings = generator.settings
        until = asyncio.get_running_loop().time() + BACKGROUND_TIME_LIMIT
        recording = self._activity.begin("trip_photo", user_id, outing.character_id, own_llm=False, exempt=False)
        try:
            with recording.current():
                image = await timed(
                    self._activity, image_origin(settings),
                    generator.generate(outing_prompt(outing.here.tags, settings.style), until), (),
                )
        except (ImageFailed, TimeoutError) as e:
            log.warning("a stop's background was not drawn: %s", e)
            await recording.finish("failed")
            return False
        await recording.finish("success")
        await self._db.write(_keep_background, user_id, spot, outing.here.id, image.webp)
        return True

    async def emotions(self, character_id: str, user_said: str, segments: list[tuple[str, str]]) -> list[dict]:
        """How she feels saying each segment of a reply on the way, for her stand, as calls
        judge it; calm for all without a judge model."""
        lease = self._config.lease()
        try:
            judging = lease.keys.emotions
            character = self._characters.get(character_id)
            if judging is None or character is None:
                return [{"emotion": "calm", "nod": None} for _ in segments]
            said = [SaidSegment(ja, zh) for ja, zh in segments]

            async def one(i: int) -> dict:
                try:
                    answer = await asyncio.wait_for(
                        judging.judge.judge_emotion(SegmentToJudge(character, user_said, said[:i], said[i])),
                        judging.timeout,
                    )
                except Exception:
                    return {"emotion": "calm", "nod": None}
                emotion, nod = decide(answer, judging.min_probability)
                return {"emotion": emotion, "nod": nod}

            return list(await asyncio.gather(*(one(i) for i in range(len(said)))))
        finally:
            lease.release()

    async def end(self, user_id: str, outing_id: int) -> Outing:
        """End the day out: what happened written down by the background model, kept as one
        of her own doings, a moment for her diary, word of mouth, and a card in the
        conversation. Raises OutingOver."""
        outing = await self.of(user_id, outing_id)
        if outing.ended_at is not None:
            raise OutingOver()
        now = self._clock()
        messages = [
            m for m in await self._store.list_messages(outing.conversation_id)
            if m.id > outing.after_message
        ]
        character = self._characters.get(outing.character_id)
        summary = await self._summary(outing, character, messages) if character else None
        summary = summary or {
            "recap": f"一起{outing.what}的一天。", "memory": "", "news": "", "moment": "",
        }
        zone = await self._zone(user_id)
        card = {
            "outing_id": outing.id, "spot": outing.spot, "what": outing.what,
            "day": outing.day.isoformat(), "recap": summary.get("recap", ""),
            "started_at": outing.started_at.astimezone(UTC).isoformat(),
            "photos": outing.photos,
        }
        await self._db.write(
            _end, outing_id, outing.conversation_id, now, json.dumps(summary, ensure_ascii=False), card
        )
        await self._trip_photos.date_met(user_id, outing.character_id, outing.day.isoformat())
        name = character.name if character else ""
        local = now.astimezone(zone)
        if self._experiences is not None and summary.get("memory"):
            await self._experiences.record(
                user_id, outing.character_id,
                [NewExperience(summary["memory"][:200], summary.get("recap", "")[:300])], now,
            )
        if self._moment is not None:
            try:
                await self._moment(
                    user_id, outing.character_id, summary.get("moment") or f"和制作人一起{outing.what}", local
                )
            except Exception:
                log.exception("noting a day out for the diary failed")
        if self._relations is not None:
            await self._relations.tell(
                user_id, outing.character_id,
                summary.get("news") or f"{name}和制作人一起{outing.what}", now,
            )
        return await self.of(user_id, outing_id)

    async def _summary(
        self, outing: Outing, character: Character, messages: list[Message]
    ) -> dict | None:
        lines = []
        for m in messages:
            if m.role == "user":
                lines.append(f"制作人：{m.text}")
            else:
                lines.append(f"{character.name}：" + "".join(s.zh for s in m.segments))
        if not lines:
            return None
        prompt = [
            ChatMessage("system", OUTING_SUMMARY.format(name=character.name, what=outing.what)),
            ChatMessage("user", "\n".join(lines)[-12000:]),
        ]
        lease = self._config.lease()
        try:
            llm: ChatLLM = lease.keys.memory_llm
            answer = "".join([c async for c in llm.stream(prompt) if isinstance(c, str)])
        except Exception:
            log.warning("summing up a day out failed", exc_info=True)
            return None
        finally:
            lease.release()
        found = re.search(r"\{.*\}", answer, re.S)
        if not found:
            return None
        try:
            read = json.loads(found.group(0))
        except ValueError:
            return None
        if not isinstance(read, dict) or not read.get("recap"):
            return None
        return {k: str(read.get(k) or "")[:300] for k in ("recap", "memory", "news", "moment")}

    async def played(self, user_id: str, character_id: str, day: str) -> bool:
        """Whether the day out of `day` was met."""
        return await self._db.read(_outing_on, user_id, character_id, date.fromisoformat(day)) is not None

    @staticmethod
    def closes(day: date, zone: ZoneInfo) -> datetime:
        return datetime.combine(day, CLOSES, zone)


OUTING_SUMMARY = """下面是{name}和制作人今天约会（{what}）时的全部对话。请整理成 JSON，只输出 JSON：
{{"recap": "用{name}自己的口吻、第一人称写两三句，回顾今天的约会：去了哪、印象最深的瞬间、心情",
"memory": "第三人称一句话，以「{name}」开头，写她和制作人今天约会的事和最重要的一两个细节，以后她能想起来",
"news": "第三人称一句话，以「{name}」开头，写成别的偶像会听说的样子，只说大概，不写私密的细节",
"moment": "以「{name}」开头的一句话，写今天对她来说最重要的一刻，给她写日记用"}}
只写对话里真的发生过的事，不要编。"""


def _open(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)


def _planned_on(db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> sqlite3.Row | None:
    return by_name(db).execute(
        "SELECT what FROM dates WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day.isoformat()),
    ).fetchone()


def _row(row: sqlite3.Row | None) -> Outing | None:
    if row is None:
        return None
    return Outing(
        row["id"], row["user_id"], row["character_id"], date.fromisoformat(row["day"]),
        row["what"], row["spot"], row["conversation_id"], row["stop"],
        datetime.fromisoformat(row["started_at"]), row["late_minutes"], row["after_message"],
        datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else None,
        row["photos"], json.loads(row["summary"]) if row["summary"] else None,
    )


def _outing_on(db: sqlite3.Connection, user_id: str, character_id: str, day: date) -> Outing | None:
    return _row(by_name(db).execute(
        "SELECT * FROM outings WHERE user_id = ? AND character_id = ? AND day = ?",
        (user_id, character_id, day.isoformat()),
    ).fetchone())


def _outing(db: sqlite3.Connection, user_id: str, outing_id: int) -> Outing | None:
    return _row(by_name(db).execute(
        "SELECT * FROM outings WHERE id = ? AND user_id = ?", (outing_id, user_id)
    ).fetchone())


def _active_in(db: sqlite3.Connection, conversation_id: str) -> Outing | None:
    return _row(by_name(db).execute(
        "SELECT * FROM outings WHERE conversation_id = ? AND ended_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone())


def _start(
    db: sqlite3.Connection, user_id: str, character_id: str, day: date, what: str,
    spot: str | None, conversation_id: str, now: datetime, late: int, after_message: int,
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO outings (user_id, character_id, day, what, spot, conversation_id,"
        " started_at, late_minutes, after_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, character_id, day.isoformat(), what, spot, conversation_id,
         now.astimezone(UTC).isoformat(), late, after_message),
    )


def _set_stop(db: sqlite3.Connection, outing_id: int, stop: int) -> None:
    db.execute("UPDATE outings SET stop = ? WHERE id = ?", (stop, outing_id))


def _took_photo(db: sqlite3.Connection, outing_id: int) -> None:
    db.execute("UPDATE outings SET photos = photos + 1 WHERE id = ?", (outing_id,))


def _end(
    db: sqlite3.Connection, outing_id: int, conversation_id: str, now: datetime, summary: str,
    card: dict,
) -> None:
    db.execute(
        "UPDATE outings SET ended_at = ?, summary = ? WHERE id = ?",
        (now.astimezone(UTC).isoformat(), summary, outing_id),
    )
    add_outing_card(db, conversation_id, card, now.astimezone(UTC).isoformat())


def _background(db: sqlite3.Connection, user_id: str, spot: str, stop: str) -> bytes | None:
    row = db.execute(
        "SELECT image FROM outing_backgrounds WHERE user_id = ? AND spot = ? AND stop = ?",
        (user_id, spot, stop),
    ).fetchone()
    return row[0] if row else None


def _keep_background(db: sqlite3.Connection, user_id: str, spot: str, stop: str, image: bytes) -> None:
    db.execute(
        "INSERT INTO outing_backgrounds (user_id, spot, stop, image) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (user_id, spot, stop) DO UPDATE SET image = excluded.image,"
        " drawn = drawn + 1",
        (user_id, spot, stop, image),
    )

