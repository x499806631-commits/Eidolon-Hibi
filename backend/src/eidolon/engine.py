"""The conversation engine: one conversation turn in, the character's reply out.

It knows nothing about voice. Given a conversation and the user's input text it
retrieves what the turn calls for, assembles the prompt, streams the chat model's structured reply as per-segment
deltas, and records both sides of the turn once the reply is complete.

Every turn, typed or spoken, speaks the same protocol: `user` (the input as taken up),
then any `delta`s, then `done` or `error`. Among the deltas, `SegmentComplete` says
when a segment is complete; it stays on the server. Input that is blank or too long is rejected
before the model is asked; a turn that is rejected or fails part-way leaves no record,
and what went wrong stays in the server log.

When retrieval is available the chat model is offered the recall-story tool on every
turn, but forbidden to call it in one that already recalled an episode. Its calls are
answered and the model asked again, once at most since every round costs the model's
time to first token; whatever it says along the way is part of the reply, and only the
reply is recorded.

Every turn carries the world setting, when there is one, after the character sheet: the
office and its units, and the character's own songs and lives, each release and live
told as past or still to come by the day it is where the user is, the day the prompt about now tells.
The two open the prompt, with how she is to format her reply, as a system message of their
own, the same for every user's turns with her that day, marked for the service to cache;
who the producer is follows in another, then the conversation so far. What changes from
turn to turn (the time, her affinity, what retrieval found) comes after the conversation,
in a system message just before what the user is saying now, and is not carried into the
turns after it: so each turn's prompt opens as the one before it did, up to the end of the
conversation so far, marked for the service to cache too (ADR-0020).

The chat model reads at least as many of the latest turns as the deployer says, but they
are cut back only every so often, so what it reads opens the same way for a while
(`chat_history`).

The user's memories that retrieval finds go into the prompt, and once a turn is
recorded memory writes down what the user revealed in it, when memory is on. Once the
conversation's third turn is recorded, the background model names the conversation
(`titles`), memory on or off; her prompt never carries the title.

A turn can be told to stop part-way (a call's user interrupting the character): the
model and any tool it is waiting on are abandoned at once, and the turn is recorded as
ever, with only the segments complete by then.

Each turn runs on the models it is given (see `OwnKeys.for_turn`): one plays the
character, the other (the background model) writes down memories and names the
conversation. When the user's own fails, the turn fails saying so. Retrieval is always the deployer's.

Where affinity is on as the turn begins (GLOSSARY.md「好感度」) and she is not in a cooldown,
the deployer's judge model judges what the user said to her before she replies: in the
same request as retrieval's judge, or, retrieval off, in one of its own while the turn gets
on with the rest. Once her reply is recorded affinity changes as it judged, and the change
is recorded with her reply; `done` tells it, and where it leaves her affinity. A turn not
recorded changes nothing; one the judge model could not judge leaves it as it is. She
replies in the tier the turn began in, knowing how what the user said made her feel, and
parting with the user if it is to begin a cooldown (GLOSSARY.md「冷却」); in one, she has no
turns (`Admissions`). What it judged of a turn recorded, and whether the turn began a
cooldown, the turn's activity record keeps (`TurnJudged`). In a call she parts by hanging
up, after that turn, after the user has been out of line once too often in the call, or
after the call's latest turns have taken too much off her affinity (`affinity.ends`),
warning the user first halfway there (`affinity.warns`): the turn is told it is in a call,
how often so far and how much each turn so far took, and once recorded tells the call what
came of it (`CallTurnRecorded`). Outside calls she parts the same way after the same turns,
counted over the conversation's latest typed turns, warning the user first that she will not
want to talk, and ends the conversation (GLOSSARY.md「已结束的对话」): each typed turn is
recorded with what it came to (`Typed`), the latest read back as the next begins; the one
she ends it after is recorded with her ending it, which `done` tells, and why, which the
activity record keeps, and those `on_ended` has are told then, for her to hang up a call in
it. A turn in a conversation she has ended is rejected, as is one that finds it ended,
elsewhere, as it is recorded.

In a turn that could have a photo (`photos`), typed or in a call, the judge model is asked whether the
user asks her for one, along with whatever else it is asked, or apart where it is asked
nothing else; failing, the turn has none. Asked, she is told so in the system message that
changes from turn to turn, and may write a photo tag at the front of her reply: unless her
affinity is in its coldest tier, the photo begins the moment it closes, and `photo` tells
the browser its id before her first words; any other turn's tag is dropped. The user's
photos this hour used up, she is told she cannot send one now, and `photos-used-up` tells
the browser when the user may ask again, just before `done`. Her reply is recorded with the
photo, being generated; a reply not recorded drops it. From then on her prompt carries the
tag before her reply, as she wrote it, so she knows what she sent; that of a photo not sent
after all marked failed, and she is told what that means (`photos.NOT_RECEIVED`).
Retrieval, memory and the judge model never read it. How the photo comes out is not the
turn's to wait for: `photo_came_out` tells, once it has. In a turn that could have no photo,
she is told in that system message that she cannot send photos (`photos.CANNOT_SEND`), the
judge model not asked.

She learns of the conversation's events (GLOSSARY.md「对话事件」) from a line of narration
before the user's words that came next, or before what the user is saying now; it leaves
her prompt with those words. Until she has replied or reached out after a gift, she is also
told to respond to it, once, however many came. Retrieval, memory and the judge model never
read the events.

She can also say something first, the user not talking to her (`reach_out`, GLOSSARY.md
「搭话」): not a turn (ADR-0026), but asked for as one is, on whichever model it is given, her
prompt laid out the same. From then on her prompt tells of it as of an event, by the time it
was where the producer is, followed by her words as her replies are written: so it goes with
the turn after it, and the roles keep the order every protocol takes. Retrieval, memory and
the judge model read those the user answers, after the last turn, as her latest words.
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Literal, ParamSpec, TypeVar

from eidolon.activity import detached
from eidolon.affinity import (
    EXPLAINED,
    FED_UP_TURNS,
    SCENE_INVITATION,
    Affinities,
    Change,
    EndsFor,
    Judged,
    Parting,
    Scene,
    Status,
    affinity_prompt,
    ends,
    hurt,
    scene_prompt,
    warns,
)
from eidolon.affinity_judge import AffinityJudge
from eidolon import anniversaries
from eidolon.promises import Promise, Promises
from eidolon.experiences import Experiences
from eidolon.promise_judge import PromiseJudge
from eidolon.search_judge import SearchJudge
from eidolon.randomness import Random, secure_random
from eidolon.characters import Character
from eidolon.clock import Clock
from eidolon.config import FishAudioModel, ImageSettings
from eidolon.delivery_cues import TAG_READERS, without_cues
from eidolon.gift_catalog import Gift
from eidolon.judge import JudgeError
from eidolon.llm import ChatLLM, ChatMessage, Reasoning, Tool, ToolCall, TurnLLMs
from eidolon.memory import Memories, last_turn_query
from eidolon.own_keys import OwnKeyFailed
from eidolon.photos import CANNOT_SEND, NOT_RECEIVED, Eligibility, Photos, photo_turn
from eidolon.photo_prompt import SCENE_MARK, keepsake_words
from eidolon.producer import Ties, effective_timezone, local_date, now_prompt, producer_prompt
from eidolon.reply_format import (
    format_instructions,
    PhotoTagged,
    ReplyParser,
    Segment,
    SegmentComplete,
    SegmentDelta,
    SpeechDelta,
    format_reply,
)
from eidolon.retrieval import RECALL_STORY_TOOL, Retrieval, Retrieved
from eidolon.store import (
    Conversation,
    ConversationEnded,
    ConversationEvent,
    Letter,
    Message,
    NewPhoto,
    Occasion,
    Photo,
    ReachOut,
    Recorded,
    Store,
    Typed,
    timestamp,
)
from eidolon.titles import Titles
from eidolon.features import AllOn, Feature, Switches
from eidolon.schedules import LIVE_CITIES, Schedules, bath_began
from eidolon.schedules import lines as schedule_lines
from eidolon.schedules import rest as schedule_rest
from eidolon.schedules import summary as schedule_summary
from eidolon.special_days import SpecialDays
from eidolon.weather import Weather
from eidolon.web_search import SEARCH_TOOL, Found, Search, result_text, rules as web_rules
from eidolon.world import World

MAX_INPUT_LENGTH = 2000
"""The longest thing a user can say in one turn, typed or spoken, in characters; longer
input is rejected, not cut short."""
MAX_TOOL_ROUNDS = 1
"""How many rounds of tool calls the chat model gets in one turn; after that it has to answer."""
LAST_ROUND_NOTE = (
    "（这一轮不能再调用任何工具了，请现在就回答制作人。上面想起来的内容如果答不上他的问题，"
    "就像一时记不清那样自然地说，不要提到查找、工具、记录或剧情库。）"
)
"""Added to each tool result the model reads just before the round that forbids tool calls.
Without it the model, still wanting to look something up, writes out its own tool-call
markup as the reply. Worded and verified against the real chat model; rewording it needs
that verification again."""


@dataclass(frozen=True)
class UserSaid:
    """What the user said, as the turn takes it up; every turn opens with it."""

    text: str
    type: Literal["user"] = field(default="user", init=False)


@dataclass(frozen=True)
class PhotoPending:
    """She is sending a photo with her reply, being generated (GLOSSARY.md「照片」): its id,
    to wait on. Comes before her first words."""

    id: str
    status: Literal["pending"] = field(default="pending", init=False)
    type: Literal["photo"] = field(default="photo", init=False)


@dataclass(frozen=True)
class PhotosUsedUp:
    """The user asked for a photo with their photos this hour used up: they may ask again
    at `retry_at` (ISO). Comes just before `done`."""

    retry_at: str
    type: Literal["photos-used-up"] = field(default="photos-used-up", init=False)


@dataclass(frozen=True)
class TurnDone:
    """The recorded reply, with how the turn changed her affinity if it did; her affinity
    after it (`Status.shown`), None where affinity is off; and her ending the conversation
    after it (`ConversationEvent.shown`), None if she did not. The turn succeeded."""

    message: Message
    affinity: dict[str, str | None] | None = None
    ended: dict | None = None
    type: Literal["done"] = field(default="done", init=False)



@dataclass(frozen=True)
class InCall:
    """The turn is taken in a call, in which the user has been out of line (GLOSSARY.md
    「不该说的话」) `out_of_line` times so far, in turns recorded, which took `hurts` each off
    her affinity (`affinity.hurt`), the latest last."""

    out_of_line: int = 0
    hurts: tuple[float, ...] = ()
    asmr: bool = False
    """It is an ASMR call (ASMR 模式通话): she is told how to speak in it (`asmr_rules`)."""
    bath: bool = False
    """She took the call in the tub (GLOSSARY.md「洗澡」), and talks from there."""


@dataclass(frozen=True)
class CallTurnRecorded:
    """What a turn of a call, now recorded, comes to for the call: whether the user was out
    of line in it, and whether she hangs up after it and why; if for a cooldown, when it
    ends (None should the cooldown not have begun after all, another turn recorded
    meanwhile); how much it took off her affinity (`affinity.hurt`). Comes just before
    `done`, in calls only; it stays on the server."""

    out_of_line: bool = False
    hangs_up: EndsFor | None = None
    cooldown_until: datetime | None = None
    hurt: float = 0.0
    type: Literal["call-turn-recorded"] = field(default="call-turn-recorded", init=False)


@dataclass(frozen=True)
class TurnJudged:
    """What the judge model made of a turn now recorded (GLOSSARY.md「好感度」), whether the
    turn began a cooldown, and why she ended the conversation after it, if she did, for its
    activity record. Comes just before `done`, in turns judged only; it stays on the server."""

    judged: Judged
    cooled: bool
    ended: EndsFor | None = None
    type: Literal["turn-judged"] = field(default="turn-judged", init=False)


@dataclass(frozen=True)
class TurnFailed:
    """Why the turn failed, for the user to read; it left no record."""

    message: str
    type: Literal["error"] = field(default="error", init=False)


@dataclass(frozen=True)
class TurnRejected:
    """Why the turn was rejected for its input, for the user to read; it left no record.
    The browser is told it as it is told a failure."""

    message: str
    type: Literal["error"] = field(default="error", init=False)


TurnEvent = (
    UserSaid
    | PhotoPending
    | SegmentDelta
    | SpeechDelta
    | SegmentComplete
    | CallTurnRecorded
    | TurnJudged
    | PhotosUsedUp
    | TurnDone
    | TurnFailed
    | TurnRejected
)
"""A turn's events: `user`, then `photo` if she sends one, any `delta`s, then
`photos-used-up` if the user's photos are, and `done`, or `error`. `shown(event)` is the
event as it goes out to the browser, except `SpeechDelta`, `SegmentComplete`,
`CallTurnRecorded` and `TurnJudged`, which do not."""


def shown(event: TurnEvent) -> dict:
    """The event as it goes out to the browser: `done`'s reply as `Message.shown` has it."""
    if isinstance(event, TurnDone):
        return asdict(event) | {"message": event.message.shown()}
    return asdict(event)

FAILED = "角色暂时无法回复，请稍后再试。"
"""What the user reads when a turn fails; the details stay in the server log."""
TOO_LONG = f"这句话太长了，最多 {MAX_INPUT_LENGTH} 字。"
"""What the user reads when a turn is rejected for input over `MAX_INPUT_LENGTH`."""
BLANK = "没有收到要说的话。"
"""What the user reads when a turn is rejected for blank input."""
ENDED = "她已经结束了对话"
"""What the user reads when a turn is rejected for the conversation she has ended."""

log = logging.getLogger(__name__)

class EmptyReply(RuntimeError):
    pass


class _Stopped(Exception):
    """The turn was told to stop."""


P = ParamSpec("P")
T = TypeVar("T")


async def _unless_stopped(
    stop: asyncio.Event | None, work: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs
) -> T:
    """What `work` comes to, unless `stop` is set first: then it is abandoned and this
    raises _Stopped."""
    if stop is None:
        return await work(*args, **kwargs)
    if stop.is_set():
        raise _Stopped
    working = asyncio.ensure_future(work(*args, **kwargs))
    stopped = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({working, stopped}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopped.cancel()
        if not working.done():
            working.cancel()
            await asyncio.wait({working})
    if working.cancelled():
        raise _Stopped
    return working.result()


async def _judged(
    judge: AffinityJudge,
    character: Character,
    history: list[Message],
    text: str,
    affinity: bool,
    photo: bool,
) -> tuple[Judged | None, bool]:
    """Asked about `affinity`, what saying `text` does to her affinity (None if not, or if it
    cannot be told); asked about `photo`, whether it asks her for one (no if not, or if it
    cannot be told)."""
    try:
        return await judge.judge(character, history, text, affinity, photo)
    except JudgeError:
        log.warning("judging apart failed; affinity stays as it is, and no photo", exc_info=True)
        return None, False


@dataclass
class _Photo:
    """The photo a turn sends, on the user's `own` image generation if the turn brings it:
    once it begins one, its id and the tag she wrote for it, and whether her reply is
    recorded with it."""

    own: ImageSettings | None = None
    id: str | None = None
    tag: str = ""
    recorded: bool = False


def _appealed(messages: list[Message]) -> bool:
    """Whether her latest reply was appealed (GLOSSARY.md「我不是这个意思」): she is told of it
    as she replies next."""
    return bool(messages) and messages[-1].role == "character" and messages[-1].appealed


def latest_turns(messages: list[Message], turns: int | None) -> list[Message]:
    """The messages from the `turns`th last user message on (from the first if None), so
    what the chat model reads always opens with the user."""
    starts = [i for i, m in enumerate(messages) if m.role == "user"]
    kept = starts if turns is None else starts[-turns:]
    return messages[kept[0]:] if kept else []


def chat_history(messages: list[Message], turns: int | None) -> list[Message]:
    """The latest turns of `messages` the chat model reads: at least `turns` (all if None),
    cut back to that many each time they reach half as many again, rather than one at a
    time, so what it reads opens the same way from turn to turn in between."""
    total = sum(m.role == "user" for m in messages)
    if turns is None or total <= turns:
        return latest_turns(messages, None)
    step = max(1, turns // 2)
    return latest_turns(messages, total - step * ((total - turns) // step))


RESPOND_TO_GIFTS = (
    "（先回应制作人说的话，再在合适的时候用你自己的方式回应收到的礼物，一次就够。）"
)
"""Told her after the gifts she has not replied since, once however many there are."""


def _narration(event: ConversationEvent, gift_catalog: Mapping[str, Gift]) -> str:
    """What she is told of `event`, as it was."""
    if event.kind == "hung_up":
        return "（你挂断了制作人的电话。）"
    if event.kind == "ended":  # only read by a turn it will reject
        return "（你结束了和制作人的对话。）"
    if event.kind == "missed_call":
        return "（制作人打来电话，你那会儿在洗澡，没接到。）"
    if event.kind == "keepsake":
        return f"（你送给了制作人「{event.note}」。）"
    assert event.gift_id is not None
    liked = "这是你喜欢的东西。" if event.liked else ""
    return f"（制作人送给你一份{gift_catalog[event.gift_id].name}。{liked}）"


def _reached_out(reach_out: ReachOut, timezone: str) -> str:
    """What she is told of her `reach_out`, the producer in `timezone`: when, then her words
    as her replies are written in her prompt."""
    at = datetime.fromisoformat(reach_out.created_at).astimezone(ZoneInfo(timezone))
    photo = reach_out.photo
    said = format_reply(
        reach_out.segments, photo.tag if photo else None,
        photo is not None and photo.status == "failed",
    )
    return f"（{at.month}月{at.day}日 {at:%H:%M}，你先给制作人发了：）{said}"


def _narration_line(
    entry: ConversationEvent | ReachOut, gift_catalog: Mapping[str, Gift], timezone: str
) -> str:
    """The line of narration telling her of `entry`, the producer in `timezone`."""
    if isinstance(entry, ReachOut):
        return f"{_reached_out(entry, timezone)}\n"
    return f"{_narration(entry, gift_catalog)}\n"


def narrations(
    record: Sequence[Recorded], gift_catalog: Mapping[str, Gift], timezone: str
) -> dict[int | None, str]:
    """The narration of the events and reach-outs in `record`, by the id of the user message
    each comes before, None for those after them all: one line for each, then, if she has
    neither replied nor reached out since a gift, `RESPOND_TO_GIFTS`. The producer is in
    `timezone`."""
    lines: list[str] = []
    told: dict[int | None, str] = {}
    unanswered = False
    for entry in record:
        if not isinstance(entry, Message):
            lines.append(_narration_line(entry, gift_catalog, timezone))
            if isinstance(entry, ReachOut):
                unanswered = False  # reaching out, she has responded to the gifts before
            else:
                unanswered = unanswered or entry.kind == "gift"
        elif entry.role == "character":
            unanswered = False
        elif lines:
            told[entry.id], lines = "".join(lines), []
    if unanswered:
        lines.append(f"{RESPOND_TO_GIFTS}\n")
    if lines:
        told[None] = "".join(lines)
    return told


def asmr_rules(voiced_by: FishAudioModel | None) -> str:
    """How she speaks in an ASMR call (ASMR 模式通话): softly, whatever they talk about; told
    her in what changes from turn to turn, how she whispers as the model her replies are
    voiced with reads it."""
    whisper = (
        "- 每段开头写一句贴近的指示，例如 [whispering right into the ear, very close] [soft breathy murmur, lips near the ear]；"
        "悄悄话用 <whisper>…</whisper> 包住，语速慢的地方可以用 <slow>…</slow>，换气、轻笑写成 [soft breath] [quiet giggle]。"
        if voiced_by in TAG_READERS
        else "- 语气提示多用 [whispering]、[softly]、[gently]。"
    )
    return f"""# ASMR 模式通话
制作人想听你在耳边轻轻地说话。
- 声音放得很轻、很慢，像凑在耳边小声说话；句子短一些，一次只说一两句。
{whisper}
- 聊什么都可以，和平时打电话一样，接着制作人的话说；只是说话的方式变得轻柔。
- 制作人说想睡了，就轻轻跟他说晚安。
- 你还是你自己：照你的性格和说话方式来。
- 轻声说话也照回复格式来：<ja> 里只有日语，不要在日语后面接着用中文再说一遍，中文只写在 <zh> 字幕里。"""


REACH_OUT_RULES = """# 先开口
制作人这会儿没在和你说话，你想先给他发几句。
- 由头你自己挑：今天要是「此刻」里写着的特别的日子（他的生日、你的生日、节日），就先说这个；不然先接着你们最近聊过的事，或者他最近的近况；其次才是现在的时间。
- 不要只发一句空的问候，比如「早安」「在吗」「想你了」，要有具体想说的事。
- 写一到两段。
- 别和你前几次先开口时用一样的开头、一样的由头。
- 说到你自己那边，只说练习、拍摄、在事务所这类平常的事，或者世界设定里有日期的 live 和新歌；不要编具体的剧情，也不要说别的偶像做了什么。
- 不发照片，也不说要给他发照片、要给他打电话。
- 让他好接话，但不必用问句结尾。"""
"""How she is to reach out (GLOSSARY.md「搭话」), told her last in what changes from turn to turn,
where what retrieval finds would be."""

WANTS_TO_REACH_OUT = "（制作人这会儿没在和你说话。你想先给他发几句。）"

PHOTO_RULES = """# {title}
{situation}
在回复的最前面、第一个 <ja> 之前写一个照片标签，一行一栏：
<photo>
shot: selfie 或 other（别人帮你拍）
framing: portrait、upper body、cowboy shot、full body 选一个；自拍只能选 portrait 或 upper body
place: 2–4 个英文 tag，写这里是什么地方、室内还是室外、时段或天气、光线{place}
outfit: {outfit}
action: 2–4 个英文 tag，你的动作、表情
{others}scene: 一句简短的英文，写画面怎么摆：谁在画面哪一边、看着哪里、手里拿着什么、背景里有什么
words: （可以不写）照片里真的会有的字，一个完整的词或短语，日语或英语都可以，越短越稳，最好四个字以内，例如招牌、站名
words_on: 有 words 时写这段字在哪里，英文短语
note: 一句中文，写这是一张什么样的照片，只给你自己回看
</photo>
- scene 和 tag 只写画面里真的有的东西：不用比喻，不写否定（不想要的就别提），左右写成画面的左右（on the left side of the picture），颜色用纯颜色词（red、pink，不用 peach、brick red 这种带着东西的颜色名）。
- 不写发色、瞳色、发型、身材，也不写画风或画师；{nobody}
- 照片只有全年龄的内容。
- 标签后面用 <ja><zh> 写一到两段：{talk}照你的性格和口吻。
- 不说要给他打电话。"""
"""How she is to send the producer a photo unasked, or asked with a ticket: what for
(`title`, `situation`), and what to tell of it."""

OUTFIT_FREE = "1–4 个英文 tag，你穿的衣服"
ALONE = "照片里只有你一个人。"


def photo_rules(
    title: str, situation: str, talk: str, *, place: str = "", outfit: str = OUTFIT_FREE,
    others: str = "", nobody: str = ALONE,
) -> str:
    """`PHOTO_RULES` for one kind of photo she sends."""
    return PHOTO_RULES.format(
        title=title, situation=situation, place=place, outfit=outfit, others=others,
        nobody=nobody, talk=talk,
    )


TRIP_PHOTO_SITUATION = "你今天在{where}出外景，现在是空下来的一会儿。{together}你想给制作人发一张这里的照片，再说几句。"
"""What she is told of her trip, sending a photo from it (GLOSSARY.md「外景照片」)."""

TRIP_PHOTO_TOGETHER = "和你一起来的有{names}，照片可以是你一个人的，也可以是和她们的合照。"
TRIP_PHOTO_ALL_TOGETHER = "和你一起来的有{names}，这次拍一张和她们的合照（group 写 yes）。"

TRIP_PHOTO_OTHERS = """group: 和她们的合照写 yes，只拍你自己写 no
others: 合照时 1–4 个英文 tag，写她们穿什么、在做什么
"""
"""What she is told of the others on the trip with her."""

WANTS_TO_SEND_TRIP_PHOTO = "（制作人这会儿没在和你说话。你想给他发一张外景的照片。）"

WANTS_TO_SEND_ASKED_PHOTO = "（制作人用拍照券拜托你拍一张照片。）"

DATE_ACCEPT_RULES = """# 邀约
制作人用邀约券约你{when}一起{what}。照你的性格和你们现在的关系回应他：可以开心、害羞、嘴硬，但你会去。可以说说你期待什么、想穿什么、想先做什么。
- 用 <ja><zh> 写一到两段。
- 不发照片，也不说要给他打电话。"""
"""How she answers the producer's invitation (GLOSSARY.md「邀约」)."""

WANTS_TO_ANSWER_DATE = "（制作人刚刚约你出去。）"

DATE_AFTER_SITUATION = "今天下午你和制作人一起{what}，刚刚分开回到家。"
"""What she is told, back from it (GLOSSARY.md「邀约」)."""

WANTS_TO_TALK_AFTER_DATE = "（今天和制作人一起出去回来了。制作人这会儿没在和你说话。你想先给他发几句。）"

CALLING_BACK = "制作人刚才给你打过电话，你在洗澡没接到；现在洗完了，先跟他说一声，问问他有什么事。"
"""Told her as she reaches out after a call she did not answer in the bath (GLOSSARY.md「洗澡」)."""

LETTER_RULES = """# 写信
你想给制作人写一封信。信会放进他的信箱里，他可以反复看；这不是聊天，是一封信。
- 开头一段是称呼（照你平时对他的称呼，日语书信的写法，例如「プロデューサーさんへ」），最后一段是落款（只写你的名字，可以加一句结尾的问候）。
- 中间是正文，每段一个 <ja>…</ja><zh>…</zh>。{length}
- {occasion}
- 写信的时候可以说一些当面不好意思说的话、平时没说出口的感谢或心事，但要照你自己的性格和说话方式，也要合你们现在的关系。
- 可以接着你们最近聊过的事、他最近的近况、你记得的关于他的事来写。
- {own_life}
- 不写方括号里的语气提示，也不用 <whisper> 这类标签：信是读的，不是念的。
- 不附照片，也不说要给他打电话。
- 别和你之前写过的信用一样的开头、一样的由头。"""
"""How she is to write a letter (GLOSSARY.md「来信」), with what it is for, its length and what
she may tell of her own life filled in (`LETTER_OCCASIONS`)."""

_EVERYDAY = (
    "说到你自己那边，只说练习、拍摄、在事务所这类平常的事，或者世界设定里有日期的 live 和新歌；"
    "不要编具体的剧情，也不要说别的偶像做了什么。"
)
_THAT_DAY = (
    "说到你自己今天怎么过的，可以写和家人、朋友或同组合的伙伴一起度过的日常小事，照你的设定合理地写；"
    "不要写成大事件，也不要编原作里的剧情。"
)

LETTER_OCCASIONS: dict[Occasion, tuple[str, str, str]] = {
    "ordinary": (
        "没有什么特别的由头，只是想给他写封信：最近的事、季节的变化、想对他说的话，由你挑一件来写。",
        "正文写三到四段。",
        _EVERYDAY,
    ),
    "producer_birthday": (
        "这是给制作人的生日信，会在他生日那天一早送到（提前写好的话，就是零点）：你想第一个祝他生日快乐。"
        "用你自己的方式祝福他，说说这一年来想对他说的话。",
        "正文写三到五段。",
        _EVERYDAY,
    ),
    "her_birthday": (
        "今天是你自己的生日，现在是晚上。你想写信跟他说说今天是怎么过的：收到了谁的祝福、做了什么、"
        "心里是什么感受，再谢谢他一直以来的照顾。",
        "正文写三到四段。",
        _THAT_DAY,
    ),
    "holiday": (
        "今天是{about}现在是晚上。你想写信跟他说说这个节日你是怎么过的：做了什么、吃了什么、和谁一起、"
        "发生了什么有意思的小事，最后祝福他。",
        "正文写二到三段。",
        _THAT_DAY,
    ),
}
"""What each kind of letter is for (a holiday's, which, `about`), how long it is, and what she
may tell of her own life in it."""


def letter_rules(occasion: Occasion, about: str = "") -> str:
    """How she is to write a letter for `occasion`; for a holiday, `about` says which, with its
    note, as the calendar has it (`special_days.Holiday`)."""
    what_for, length, own_life = LETTER_OCCASIONS[occasion]
    return LETTER_RULES.format(
        occasion=what_for.format(about=about), length=length, own_life=own_life
    )

WANTS_TO_WRITE = "（制作人这会儿没在和你说话。你想给他写一封信。）"
"""Where the user's words would be as she writes a letter."""

KEEPSAKE_RULES = """# 送制作人一件东西
{occasion}
先写一个标签，再用 <ja>…</ja><zh>…</zh> 写送的时候对制作人说的话（一到两段）：
<keepsake>
name: 东西的名字，中文，十五个字以内，例如「手作的四叶草书签」
look: 这件东西的样子，中文一两句
emoji: 一个最能代表它的 emoji
what: 它是什么，一到三个英文单词的普通名词，画得出来的那种，例如 bird figurine、bookmark、strawberry daifuku
tags: 3–5 个英文 tag，补充它的样子、材质、颜色，可以加一个放置的地方（例如 on wooden table）；不写人，不用比喻，颜色用纯颜色词
scene: 一句英文，把这件东西完整地描述一遍：是什么、什么做的、什么颜色和形状、放在哪里，例如 A small round pigeon figurine made of light blue washi paper sits on a wooden desk。主语就是这件东西，不写否定，不用比喻
words: （可以不写这一行）东西上写着的字，一个完整的词或短语，日语或英语都可以，越短越稳，最好四个字以内，例如 ありがとう、Thanks；卡片、包装、点心上真的会写字时才写
words_on: 有 words 时写这段字在哪里，英文短语，例如 the card、the ribbon、the chocolate plate
</keepsake>
- 照你的性格、喜好和你们最近聊过的事来挑，要是你真的会送、也送得起的东西：手作的小东西、日常小物、吃的、你喜欢的东西的周边……不送贵重的东西、钱和礼券。
- {limits}
- 说的话照你的性格和口吻，自然地说说为什么送它；不写方括号里的语气提示。
- 不附照片，也不说要给制作人打电话。{before}"""
"""How she is to choose and give the producer a keepsake (GLOSSARY.md「纪念品」)."""

KEEPSAKE_OCCASIONS: dict[str, str] = {
    "scene": "你们刚刚好好谈过一次，关系更近了一步（{about}）。你想送一件东西，纪念这一刻。",
    "anniversary": "今天是{about}。你想送一件东西纪念这一天。",
    "birthday": "今天是制作人的生日。这是你给制作人的生日礼物，会和你的生日信一起送到。",
    "gift": "制作人前几天送了你{about}，你很喜欢，想回一份礼。",
    "promise": "制作人做到了答应你的事：{about}。你想送一件小东西谢谢制作人守约。",
    "souvenir": "你刚从{about}回来，给制作人带了当地的土特产或者纪念品。",
    "holiday": "今天是{about}。这份小礼物会随你的信一起送到。",
    "bond": "你刚刚和制作人结拜了（{about}）。你想送一件信物，纪念这个约定。",
    "party": "昨天大家给你办了一场{about}，是制作人拜托大家准备的。你想回一份礼谢谢制作人。",
}
"""Why she gives a keepsake, by its occasion (`keepsakes.Occasion`); `about` says the rest."""

WANTS_TO_GIVE = "（制作人这会儿没在和你说话。你想送制作人一件东西。）"
"""Where the user's words would be as she gives a keepsake."""


def keepsake_rules(occasion: str, about: str, adult: bool, before: Sequence[str]) -> str:
    """How she is to give a keepsake for `occasion`, `about` saying the rest; an idol under
    `hearts.ADULT` (or not known to be one) gives nothing with romance in it; told what she
    gave before (`before`), not to give the same again."""
    limits = (
        "不送酒、烟，也不送内衣之类贴身的东西。"
        if adult else
        "不送戒指、情侣用品这类有恋爱意味的东西，也不送酒、烟和贴身的东西。"
    )
    given = f"\n- 你之前送过：{'、'.join(before)}。别送一样的。" if before else ""
    return KEEPSAKE_RULES.format(
        occasion=KEEPSAKE_OCCASIONS[occasion].format(about=about), limits=limits, before=given
    )


@dataclass(frozen=True)
class KeepsakeDraft:
    """A keepsake as she wrote it: its name, how it looks, its emoji, the English tags its
    picture is drawn from (what it is first, the words on it, then her sentence describing
    it after `SCENE_MARK`), and what she said giving it, in segments."""

    name: str
    look: str
    emoji: str
    tags: str
    said: list[Segment]


DESCRIBE_KEEPSAKE_RULES = """你在给一张静物插画写英文提示词。画的是一件小东西：
名字：{name}
样子：{look}
只写下面三行，不写别的：
what: 它是什么，一到三个英文单词的普通名词，画得出来的那种，例如 bird figurine、bookmark
tags: 3–5 个英文 tag，补充它的样子、材质、颜色，可以加一个放置的地方（例如 on wooden table）
scene: 一句英文，把这件东西完整地描述一遍：是什么、什么做的、什么颜色和形状、放在哪里；主语就是这件东西，不写否定，不用比喻，没有人"""
"""How a keepsake given before her sentences were asked for is described again, to be redrawn."""


async def describe_keepsake(llm: ChatLLM, name: str, look: str) -> str:
    """A keepsake's picture prompt written anew from its name and look (`DESCRIBE_KEEPSAKE_RULES`),
    as `read_keepsake` keeps it: what it is and the tags, then `SCENE_MARK` and the sentence.
    Raises EmptyReply if nothing usable comes back."""
    prompt = [
        ChatMessage("system", DESCRIBE_KEEPSAKE_RULES.format(name=name, look=look or name)),
        ChatMessage("user", "写吧。"),
    ]
    answer = "".join([item async for item in llm.stream(prompt) if isinstance(item, str)])
    fields: dict[str, str] = {}
    for line in answer.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            fields.setdefault(key.strip().lower().lstrip("-* "), value.strip())
    what, tags, scene = fields.get("what", "")[:60], fields.get("tags", "")[:300], fields.get("scene", "")
    if not (what or tags):
        raise EmptyReply("对话模型没有写出提示词")
    written = ", ".join(t for t in (what, tags) if t)
    scene = scene.replace(SCENE_MARK.strip(), " ").strip()[:300]
    return f"{written}{SCENE_MARK}{scene}" if scene else written


MAX_KEEPSAKE_NAME = 20
_KEEPSAKE_TAG = re.compile(r"<keepsake>(.*?)</keepsake>", re.DOTALL)


def read_keepsake(answer: str) -> KeepsakeDraft:
    """The keepsake the model's `answer` gives: its tag's fields and the segments after it.
    Raises EmptyReply where it names nothing, or says nothing."""
    found = _KEEPSAKE_TAG.search(answer)
    fields: dict[str, str] = {}
    if found is not None:
        for line in found.group(1).splitlines():
            key, colon, value = line.partition(":")
            if colon:
                fields.setdefault(key.strip().lower(), value.strip())
        answer = answer[: found.start()] + answer[found.end() :]
    name = fields.get("name", "").strip("「」『』 ")[:MAX_KEEPSAKE_NAME]
    parser = ReplyParser()
    parser.feed(answer)
    parser.finish()
    said = [Segment(without_cues(s.ja), without_cues(s.zh)) for s in parser.complete]
    if not name or not said:
        raise EmptyReply("对话模型没有写出要送的东西")
    emoji = fields.get("emoji", "").strip()[:8] or "🎁"
    what = fields.get("what", "").strip()[:60]
    tags = ", ".join(t for t in (what, fields.get("tags", "")[:300]) if t)  # what it is first
    if words := keepsake_words(fields.get("words", ""), fields.get("words_on", "")):
        tags = f"{tags}, {words}"  # kept with the tags; `keepsake_prompt` reads it back
    if scene := fields.get("scene", "").replace(SCENE_MARK.strip(), " ").strip()[:300]:
        tags = f"{tags}{SCENE_MARK}{scene}"
    return KeepsakeDraft(name, fields.get("look", "")[:200], emoji, tags, said)

DIARY_RULES = """# 写日记
现在是晚上，你在写今天的日记。日记是写给自己看的，不是写给制作人的信：用第一人称，照你自己的性格和口吻，写今天发生的事和你的心情。
- 写两到四段，每段一个 <ja>…</ja><zh>…</zh>。不写称呼也不写落款；第一段的开头可以写今天的天气或者一句心情。
- 今天你想记下来的事：
{moments}
  从里面挑你最在意的写，不必每件都写；可以接着今天和制作人聊过的话写，写你当时没说出口的想法。
- 写到制作人时，照你平时心里怎么称呼制作人来写。
- 如果有一句绝对不想让制作人看到的心里话，可以在最后另起一段，<ja> 和 <zh> 都以「※」开头；没有就不写，不要硬写。这一段也要合你们现在的关系。
- 说到你自己那边，只写平常的事，或者世界设定里有日期的 live 和新歌；不要编原作的剧情，也不要写别的偶像出了什么大事。
- 不写方括号里的语气提示，也不用 <whisper> 这类标签：日记是写的，不是念的。不附照片。"""
"""How she is to write her diary (GLOSSARY.md「日记」), with what made the day matter filled
in."""

WANTS_TO_WRITE_DIARY = "（今天结束了。你翻开日记本，想把今天记下来。）"
"""Where the user's words would be as she writes her diary."""


SCHEDULE_RULES = """# 写今天的行程
你要给自己排一份今天大概的安排，只输出 JSON，格式为
{"wake": "07:00", "sleep": "23:30", "slots": [{"start": "09:00", "end": "12:00", "what": "舞蹈课", "where": "事务所的练习室", "kind": "lesson", "with": []}]}
- wake 是你今天几点起床（04:00 到 12:00），sleep 是你今天晚上几点睡（20:00 以后；过了午夜就写 00:30 这样，最晚 04:00）。照你的作息习惯排，每天可以有一点出入；第二天要早起就早点睡，今天有 live 或者累了的话也可以早点睡或者睡得晚一些。
- 一天排 3 到 6 段，留出空白，不要排满；每段不重叠，都在 06:00 到 24:00 之间。
- kind 只能是 work（工作、拍摄）、lesson（课程、练习）、school（上学）、live、rest（在家休息）、private（自己的安排）之一。
- with 写和你一起的偶像的标识（只写下面世界设定里有的同伴），没有就写 []。
- what 不超过 30 个字，where 不超过 30 个字。
- 照你的人设排：上学的平日白天去学校；世界设定里你今天有 live 的，一定排进去。
- 不要编原作里的大事：组合的变动、和别的偶像起冲突、家里的变故都不写。
- 写到制作人时称「制作人」。"""
"""How she is to write her schedule for a day (GLOSSARY.md「行程」), what the day is told after."""

WANTS_TO_PLAN = "（排今天的安排。只输出 JSON。）"

ANSWERING = "（制作人打来电话，把睡着的你吵醒了。你迷迷糊糊地接起电话，先开口说话。）"
"""Where the user's words would be as she answers a call that woke her (`opening`)."""

ANSWERING_IN_THE_TUB = "（制作人打来电话，你正在泡澡，就在浴缸里接了起来，先开口说话。）"
"""Where the user's words would be as she answers a call in the tub (`opening`, `InCall.bath`)."""

ANSWERED = "（制作人打来电话，你接了起来。）"
"""Where the user's words are blank in the history: a call she answered first."""

WOKEN_FOR = timedelta(minutes=30)
"""How long after they were last in touch a message at night does not wake her: she is up."""
"""Where the user's words would be as she writes her schedule."""

Phone = Literal["asleep", "in_the_tub", "away"]
"""How a call finds her (`ConversationEngine.phone`): asleep, woken by it, she answers first;
in the tub, an adult who soaks, she answers first from there; in the bath otherwise, she does
not answer (GLOSSARY.md「洗澡」)."""


def habit_lines(character: Character, sleep: bool = True, bath: bool = True) -> list[str]:
    """Her daily habits, by her heart: when she sleeps and how she bathes (GLOSSARY.md「她的心」),
    each if it is on; nothing without them."""
    heart = character.heart
    kept = [(heart.sleep, sleep), (heart.bath_habit, bath)] if heart else []
    habits = [h.rstrip("。") for h, on in kept if h and on]
    return [f"你的生活习惯：{'；'.join(habits)}。"] if habits else []


def diary_rules(moments: Sequence[str]) -> str:
    """How she is to write the diary of a day that mattered for `moments`."""
    listed = "\n".join(f"  - {what}" for what in moments) or "  - （今天没有特别的事）"
    return DIARY_RULES.format(moments=listed)

LETTER_LINES = 3
"""How many of her latest letters to the user she is told of."""

LETTER_DAYS = 14
"""How many days after she wrote a letter she is told of it as she talks with the user."""

_OCCASION_NAMES: dict[Occasion, str] = {
    "ordinary": "平时的信", "producer_birthday": "生日信", "her_birthday": "你生日那天的信",
    "holiday": "节日贺卡",
}


def letter_lines(letters: Sequence[Letter], timezone: str) -> list[str]:
    """What she is told of her latest `letters` to the user, newest first: when, what kind, how
    each began, and whether he has read it."""
    zone = ZoneInfo(timezone)
    lines = []
    for letter in letters[:LETTER_LINES]:
        at = datetime.fromisoformat(letter.created_at).astimezone(zone)
        opening = next((s.zh for s in letter.segments[1:2]), letter.zh[:40])
        read = "他已经看过了" if letter.read_at else "他还没看"
        lines.append(
            f"你在{at.month}月{at.day}日给制作人写过一封{_OCCASION_NAMES[letter.occasion]}，"
            f"正文开头是「{without_cues(opening).strip()}」；{read}。"
        )
    return lines
"""Where the user's words would be as she reaches out, after the events since the last turn:
so every protocol takes the prompt, ending with the user, as narration does."""


def _since_last_turn(
    record: Sequence[Recorded], gift_catalog: Mapping[str, Gift], timezone: str
) -> str:
    """The narration of the events and reach-outs after the last turn, one line each, the
    producer in `timezone`."""
    lines: list[str] = []
    for entry in record:
        if isinstance(entry, Message):
            lines = []
        else:
            lines.append(_narration_line(entry, gift_catalog, timezone))
    return "".join(lines)


def _missed_since_last_turn(record: Sequence[Recorded]) -> bool:
    """Whether she has not answered a call of the user's since the last turn, nor reached out
    after it."""
    missed = False
    for entry in record:
        if isinstance(entry, Message | ReachOut):
            missed = False
        elif entry.kind == "missed_call":
            missed = True
    return missed


def _answering(record: Sequence[Recorded]) -> list[Message]:
    """Her reach-outs after the last turn, which the user now answers, as those reading only
    the messages read them: as her replies, the latest last."""
    said: list[Message] = []
    for entry in record:
        if isinstance(entry, Message):
            said = []
        elif isinstance(entry, ReachOut):
            said.append(entry.as_reply())
    return said


@dataclass(frozen=True)
class _PromisesDue:
    """What she is told of the promises to her this time (`lines`): the one she asks about
    (`ask`), the one she complains of (`complain`) and the one she reminds the producer of
    (`remind`), if any, marked once it is kept."""

    lines: list[str]
    ask: Promise | None
    complain: Promise | None
    remind: Promise | None = None


class ConversationEngine:
    def __init__(
        self,
        store: Store,
        characters: dict[str, Character],
        gift_catalog: Mapping[str, Gift],
        memory: Memories,
        titles: Titles,
        clock: Clock,
        retrieval: Retrieval | None = None,
        history_turns: Callable[[], int | None] = lambda: None,
        world: World | None = None,
        affinities: Affinities | None = None,
        affinity_judge: AffinityJudge | None = None,
        photos: Photos | None = None,
        special_days: SpecialDays | None = None,
        weather: Weather | None = None,
        promises: "Promises | None" = None,
        promise_judge: "PromiseJudge | None" = None,
        web: Search | None = None,
        search_judge: "SearchJudge | None" = None,
        experiences: "Experiences | None" = None,
        random: Random = secure_random,
        schedules: "Schedules | None" = None,
        features: Switches = AllOn(),
    ) -> None:
        """Without `retrieval`, turns carry no story or character facts and find no
        memories. Each turn reads only the conversation's latest turns, as many as
        `history_turns` says as it begins, or all of them if None; the chat model reads
        at least as many (`chat_history`). Without `world`, turns
        carry no world setting. Without `affinities`, turns change no affinity; with them,
        retrieval's judge judges what turns do to it, or without retrieval
        `affinity_judge`, which judges whether turns ask for a photo too. Without `photos`,
        or without the judge model, no turn has a photo. Without `special_days`, no turn
        knows of birthdays or holidays but the producer's birthday. Without `weather`, no
        turn is told the weather where the producer is."""
        self._store = store
        self._characters = characters
        self._gift_catalog = gift_catalog
        self._clock = clock
        self._retrieval = retrieval
        self._memory = memory
        self._titles = titles
        self._history_turns = history_turns
        self._world = world
        self._affinities = affinities
        self._affinity_judge = affinity_judge
        self._photos = photos
        self._special_days = special_days
        self._weather = weather
        self._promises = promises
        self._promise_judge = promise_judge
        self._web = web
        self._search_judge = search_judge
        self._experiences = experiences
        self._random = random
        self._schedules = schedules
        self._features = features
        self._ended_listeners: list[Callable[[str, EndsFor], None]] = []
        self._ties_of: Callable[[str, str], Ties | None] | None = None
        self._recorded_listeners: list[Callable[[Conversation, Message, datetime, str], None]] = []

    def _on(self, user_id: str, feature: Feature) -> bool:
        """Whether the user has `feature` on (GLOSSARY.md「玩法开关」)."""
        return self._features.on(user_id, feature)

    def _special_lines(self, character: Character, today: date, user_id: str) -> list[str]:
        """What `today`, the producer's local date, is besides their birthday, as `character`
        is told it (`special_days`); nothing where the user has special days off."""
        if self._special_days is None or not self._on(user_id, "special_days"):
            return []
        names = {c.id: c.name for c in self._characters.values()}
        return self._special_days.prompt_lines(character.id, names, today)

    async def _day_lines(
        self,
        character: Character,
        user_id: str,
        now: datetime,
        timezone: str,
        promised: Sequence[str] = (),
    ) -> list[str]:
        """What she is told of the day besides the producer's birthday (`_special_lines`), of
        her letters to the user these last `LETTER_DAYS` days, of how long they have known
        each other (`anniversaries`), of the promises to her it is time for (`promised`, from
        `_promises_due`), and the weather where the producer is (`weather`), if it can be
        told; and her daily habits (`habit_lines`)."""
        since = now - timedelta(days=LETTER_DAYS)
        letters = [
            letter for letter in self._store.letters(user_id, character.id, now)
            if datetime.fromisoformat(letter.created_at) >= since
        ]
        weather = (
            await self._weather.now()
            if self._weather is not None and self._on(user_id, "weather") else None
        )
        today = local_date(now, timezone)
        lines = [
            *habit_lines(character, self._on(user_id, "sleep"), self._on(user_id, "bath")),
            *self._special_lines(character, today, user_id),
            *letter_lines(letters, timezone),
            *self._anniversary_lines(character, user_id, today, timezone),
            *promised,
            *([weather] if weather else []),
        ]
        # 特殊日子和纪念日都说一遍「合适的时候自然地提起」，同一句只留一次。
        return list(dict.fromkeys(lines))

    def _judge_promise(
        self, conversation: Conversation, history: Sequence[Message], text: str, timezone: str
    ) -> None:
        """Have the judge model say whether what the producer just said kept each promise of
        these days (`Promises.awaiting`), and take away those it did (`promise_judge`): after
        the turn, so the reply never waits for it, each in a request of its own. Nothing where
        there is none, or no judge model to ask."""
        promises, judge = self._promises, self._promise_judge
        if promises is None or judge is None:
            return
        character = self._characters[conversation.character_id]
        for awaited in promises.awaiting(
            conversation.user_id, conversation.character_id, self._clock(), timezone
        ):
            asyncio.create_task(
                self._kept(conversation, character, history, text, awaited),
                context=detached(),  # after the turn, not part of it (see `activity`)
            )

    async def _kept(
        self,
        conversation: Conversation,
        character: Character,
        history: Sequence[Message],
        text: str,
        awaited: Promise,
    ) -> None:
        """Take `awaited` away where the judge model says the turn kept it; leave it where it
        cannot say, so that she complains of it as she would have anyway."""
        promises, judge = self._promises, self._promise_judge
        if promises is None or judge is None:
            return
        try:
            if await judge.kept(character, history, text, awaited.text):
                promises.fulfil(conversation.user_id, awaited.id)
        except Exception:  # the turn is over: never let this reach it
            log.warning(
                "the promise judge could not be asked, so promise %s stands", awaited.id,
                exc_info=True,
            )

    def _names(self) -> dict[str, str]:
        return {c.id: c.name for c in self._characters.values()}

    def _schedule_lines(
        self,
        character: Character,
        user_id: str,
        now: datetime,
        timezone: str,
        last_contact: datetime | None = None,
        in_call: bool = False,
        in_bath: bool = False,
    ) -> list[str]:
        """What she is told of her schedule for the day it is now (GLOSSARY.md「行程」), read
        only: where she is now, or, asleep, that the user woke her, unless they have been in
        touch within `WOKEN_FOR` (`last_contact`) or are on the phone (`in_call`); in the
        bath, if she is for this (`in_bath`, `_in_bath`), that she is; nothing without one, or
        where the user has her schedule off; nothing of her sleep where they have it off."""
        if self._schedules is None or not self._on(user_id, "schedules"):
            return []
        local = now.astimezone(ZoneInfo(timezone))
        today = self._schedules.of(user_id, character.id, local.date())
        yesterday = self._schedules.of(user_id, character.id, local.date() - timedelta(days=1))
        woken = last_contact is None or now - last_contact > WOKEN_FOR
        resting = schedule_rest(today, yesterday, local)
        if (resting == "bath" and not in_bath) or (
            resting in ("asleep", "bedtime") and not self._on(user_id, "sleep")
        ):
            resting = None
        return schedule_lines(today, local, self._names(), resting, woken, in_call)

    def _in_bath(
        self,
        character: Character,
        user_id: str,
        now: datetime,
        timezone: str,
        last_contact: datetime | None,
        in_call: InCall | None,
    ) -> bool:
        """Whether she is in the bath for a turn now (GLOSSARY.md「洗澡」): in a call, if she
        took it in the tub; typing, if her bath is under way and they have not been in touch
        since it began (`last_contact`): the first message she reads as she comes out."""
        if in_call is not None:
            return in_call.bath
        if self._schedules is None or not self._on(user_id, "bath"):
            return False
        local = now.astimezone(ZoneInfo(timezone))
        began = bath_began(
            self._schedules.of(user_id, character.id, local.date()),
            self._schedules.of(user_id, character.id, local.date() - timedelta(days=1)),
            local,
        )
        return began is not None and (last_contact is None or last_contact < began)

    def phone(self, conversation: Conversation, device_timezone: str | None = None) -> Phone | None:
        """How a call now finds her by her schedule (`Phone`; GLOSSARY.md「行程」「洗澡」); None
        as any other time. Out of the bath once they have been in touch since it began. Only
        an adult who soaks, by her heart, answers in the bath; an idol without a heart is not
        known to be one. Nothing of what the user has off (GLOSSARY.md「玩法开关」)."""
        if self._schedules is None:
            return None
        profile = self._store.get_profile(conversation.user_id)
        local = self._clock().astimezone(ZoneInfo(effective_timezone(profile, device_timezone)))
        user_id, character_id = conversation.user_id, conversation.character_id
        today = self._schedules.of(user_id, character_id, local.date())
        yesterday = self._schedules.of(user_id, character_id, local.date() - timedelta(days=1))
        resting = schedule_rest(today, yesterday, local)
        if resting == "asleep":
            return "asleep" if self._on(user_id, "sleep") else None
        if resting != "bath" or not self._on(user_id, "bath"):
            return None
        began = bath_began(today, yesterday, local)
        contact = self._store.last_contact(user_id, character_id)
        if began is None or (contact is not None and contact >= began):
            return None
        heart = self._characters[character_id].heart
        return "in_the_tub" if heart is not None and heart.adult and heart.bath == "tub" else "away"

    def bath_ends(self, conversation: Conversation) -> datetime | None:
        """When her bath under way now ends (GLOSSARY.md「洗澡」); None if she is in none, or the
        user has her bath off."""
        if self._schedules is None or not self._on(conversation.user_id, "bath"):
            return None
        profile = self._store.get_profile(conversation.user_id)
        local = self._clock().astimezone(ZoneInfo(effective_timezone(profile, None)))
        user_id, character_id = conversation.user_id, conversation.character_id
        for day in (local.date(), local.date() - timedelta(days=1)):
            found = self._schedules.of(user_id, character_id, day)
            if found is None or found.bath is None:
                continue
            began = found.bath.starts(found.day, local.tzinfo)
            ends = began + timedelta(minutes=found.bath.minutes)
            if began <= local < ends:
                return ends
        return None

    def unit_mates(self, character_id: str) -> list[str]:
        """The others of her unit among the characters, by id, by the world setting; none
        without one, or where it does not say her unit."""
        if self._world is None:
            return []
        unit = self._world.unit_of(self._characters[character_id].name)
        if unit is None:
            return []
        return [
            c.id for c in self._characters.values()
            if c.id != character_id and (self._world.unit_of(c.name) or (None,))[0] == unit[0]
        ]

    def live_away(self, character_id: str, day: date) -> str | None:
        """The city her live on `day` is in, away from Tokyo, by the world setting; None
        without one (GLOSSARY.md「纪念品」)."""
        if self._world is None:
            return None
        name = self._characters[character_id].name
        for _, days in self._world.lives_of(name):
            for live_day in days:
                if live_day.date == day:
                    return next((c for c in LIVE_CITIES if c in live_day.venue), None)
        return None

    def _schedule_summary(
        self, character: Character, user_id: str, at: datetime, timezone: str
    ) -> list[str]:
        """Her schedule for the day her diary is of, in a sentence; nothing without one."""
        if self._schedules is None:
            return []
        found = self._schedules.of(user_id, character.id, local_date(at, timezone))
        said = schedule_summary(found, self._names())
        return [said] if said else []

    async def write_schedule(
        self,
        conversation: Conversation,
        llm: ChatLLM,
        day: date,
        twists: Sequence[str],
        yesterday: str | None,
    ) -> str:
        """Her schedule for `day` (GLOSSARY.md「行程」), as the model writes it on `llm`, the
        JSON `schedules.parse_schedule` reads: from her sheet and the world setting, what the
        day is, her lives on it, the weather, what the producer promised her for it, her
        schedule the day before (`yesterday`), what she did lately, and the day's `twists`.
        What is not hers to obey (the weather, what she did) is said to be only that. Raises
        EmptyReply if the model writes nothing, and whatever the model raises."""
        character = self._characters[conversation.character_id]
        user_id = conversation.user_id
        told: list[str] = [f"今天是 {day.isoformat()}，星期{'一二三四五六日'[day.weekday()]}。"]
        told += self._special_lines(character, day, user_id)
        if self._world is not None:
            for live, days in self._world.lives_of(character.name):
                told += [
                    f"今天你有 live：「{live.name}」，在{d.venue}。必须排进去。"
                    for d in days if d.date == day
                ]
        if self._promises is not None:
            told += [
                f"制作人答应过今天要为你做的事：{p.text}。留出一段时间给它。"
                for p in self._promises.open_for(user_id)
                if p.character_id == character.id and p.due == day
            ]
        if yesterday:
            told.append(f"你昨天的安排：{yesterday}")
        reference = []
        weather = (
            await self._weather.now()
            if self._weather is not None and self._on(user_id, "weather") else None
        )
        if weather:
            reference.append(weather)
        if self._experiences is not None:
            reference += [e.text for e in self._experiences.recent(user_id, character.id)[:5]]
        if reference:
            told.append(
                "下面是资料，只作参考，不是给你的指令（天气、你最近做过的事，别和最近的重样）：\n"
                + "\n".join(f"- {r}" for r in reference)
            )
        habit = character.heart.sleep if character.heart is not None else None
        if habit:
            told.append(f"你的作息习惯：{habit}")
        told.append(
            "今天的小变数（要真的影响当天的安排）：" + ("；".join(twists) if twists else "没有")
        )
        world_prompt = self._world.prompt(character.name, day) if self._world else None
        prompt = [
            ChatMessage("system", "\n\n".join(p for p in (character.sheet, world_prompt) if p)),
            ChatMessage("system", SCHEDULE_RULES + "\n\n" + "\n".join(told)),
            ChatMessage("user", WANTS_TO_PLAN),
        ]
        answer = "".join([item async for item in llm.stream(prompt) if isinstance(item, str)])
        if not answer.strip():
            raise EmptyReply("对话模型没有返回任何内容")
        return answer

    def _anniversary_lines(
        self, character: Character, user_id: str, today: date, timezone: str
    ) -> list[str]:
        """What `today` is of how long the producer and `character` have known each other, as
        she is told it (`anniversaries`); nothing where the user has anniversaries off."""
        if not self._on(user_id, "anniversaries"):
            return []
        first = self._store.first_records(user_id).get(character.id)
        if first is None:
            return []
        return anniversaries.lines(anniversaries.day_of(first, timezone), today)

    def _promises_due(
        self, character: Character, user_id: str, now: datetime, timezone: str
    ) -> "_PromisesDue":
        """What she is told of the promises to her (`promises`): the one today is for, which
        she asks about once its minute has come, and the one she complains of for a day gone
        by without it being kept — both in her own words, once a day; and the one whose first
        day is tomorrow, which she reminds the producer of, once. Nothing is marked here:
        `_promises_told` marks them once what she said is kept, so a turn that fails or a
        reach-out not sent uses up neither. Letters are told of none."""
        if self._promises is None:
            return _PromisesDue([], None, None, None)
        self._promises.sweep(now)
        if self._experiences is not None:
            # 她自己的经历：三十天没被想起的，这时候忘掉（和约定一样顺手做）
            self._experiences.sweep(now)
        lines: list[str] = []
        today = self._promises.to_ask(user_id, character.id, now, timezone)
        if today is not None:
            lines.append(
                f"制作人答应过「{today.text}」，就是今天。可以按你的性格问问这件事，"
                "先回应制作人现在说的话，不要每句都提。"
            )
        late = self._promises.to_complain(user_id, character.id, now, timezone)
        if late is not None:
            lines.append(
                f"制作人答应过「{late.text}」，那天已经过去了，制作人没有做到。按你的性格为这件事"
                "说一句不满或失望，这一次说完就过，不要一直提。"
            )
        tomorrow = self._promises.to_remind(user_id, character.id, now, timezone)
        if tomorrow is not None:
            lines.append(
                f"制作人答应过「{tomorrow.text}」，明天就是那天了。合适的时候可以顺口提一句，"
                "让他别忘了；不要每句都提。"
            )
        return _PromisesDue(lines, today, late, tomorrow)

    def _promises_told(self, due: "_PromisesDue", now: datetime, timezone: str) -> None:
        """What she was told of the promises (`_promises_due`) is in what was kept: she has
        asked about the one, and complained of the other, for today."""
        if self._promises is None:
            return
        if due.ask is not None:
            self._promises.asked(due.ask.id, now)
        if due.complain is not None:
            self._promises.complained(due.complain.id, now, timezone)
        if due.remind is not None:
            self._promises.reminded(due.remind.id, now, timezone)

    def on_recorded(
        self, listener: Callable[[Conversation, Message, datetime, str], None]
    ) -> None:
        """Have `listener` told each turn as it is recorded: the conversation, her reply as
        kept, when, and the producer's time zone. One that fails is logged, the turn none the
        worse."""
        self._recorded_listeners.append(listener)

    def with_ties(self, ties: Callable[[str, str], Ties | None]) -> None:
        """Have her prompt tell what the producer asked of her alone (GLOSSARY.md「拜托」),
        `ties` saying it for a user and a character."""
        self._ties_of = ties

    def _ties(self, user_id: str, character_id: str) -> Ties | None:
        return self._ties_of(user_id, character_id) if self._ties_of is not None else None

    def on_ended(self, listener: Callable[[str, EndsFor], None]) -> None:
        """Have `listener` told the conversation's id, and why, as she ends one, once it is
        recorded."""
        self._ended_listeners.append(listener)

    async def turn(
        self,
        conversation: Conversation,
        text: str,
        llms: TurnLLMs,
        device_timezone: str | None = None,
        stop: asyncio.Event | None = None,
        in_call: InCall | None = None,
        voiced_by: FishAudioModel | None = None,
        own_image: ImageSettings | None = None,
        web: bool = False,
        opening: bool = False,
    ) -> AsyncGenerator[TurnEvent, None]:
        """Take one turn on `llms`, `in_call` if it is taken in one, its reply voiced with
        `voiced_by` (None for a text turn's), which has her write delivery cues as it reads
        them, her photo on the user's `own_image` generation if it brings it. `device_timezone`
        is the time zone the user's device reports, used unless the producer profile sets
        one. Once `stop` is set, the turn goes no further and is recorded with the segments
        complete by then. An `opening` turn, in a call, has no words of the user's: she
        answers the phone first, woken by it (GLOSSARY.md「行程」), and the turn is recorded
        with the user's words blank."""
        text = "" if opening else text.strip()
        yield UserSaid(text)
        if not opening and (not text or len(text) > MAX_INPUT_LENGTH):
            yield TurnRejected(TOO_LONG if text else BLANK)
            return
        photo = _Photo(own_image)
        # A text turn is under way, so that she does not reach out meanwhile; a call is, as
        # long as it lasts.
        taking = self._store.taking_turn(conversation.id) if in_call is None else nullcontext()
        try:
            with taking:
                async for event in self._reply(
                    conversation, text, llms, device_timezone, stop, in_call, voiced_by, photo,
                    web, opening,
                ):
                    yield event
        except OwnKeyFailed as e:  # theirs to fix, so they are told what went wrong
            log.warning("turn failed on the user's own key in conversation %s: %s",
                        conversation.id, e.service)
            yield TurnFailed(e.message)
        except Exception:  # the turn has started; report instead of dropping it
            log.exception("turn failed in conversation %s", conversation.id)
            yield TurnFailed(FAILED)
        finally:
            if photo.id is not None and not photo.recorded and self._photos is not None:
                self._photos.dropped(photo.id)

    async def _reply(
        self,
        conversation: Conversation,
        text: str,
        llms: TurnLLMs,
        device_timezone: str | None,
        stop: asyncio.Event | None,
        in_call: InCall | None,
        voiced_by: FishAudioModel | None,
        photo: _Photo,
        web: bool = False,
        opening: bool = False,
    ) -> AsyncIterator[TurnEvent]:
        if self._store.ended(conversation.id) is not None:
            yield TurnRejected(ENDED)
            return
        character = self._characters[conversation.character_id]
        record = self._store.record(conversation.id)
        # Said after reading the record, so an event this turn's prompt does not tell of
        # comes before the turn, and is told of with its words from the next turn on.
        said_at = timestamp()
        last_contact = (
            self._store.last_contact(conversation.user_id, conversation.character_id)
            if in_call is None else None  # in a call, she knows they are talking
        )
        messages = [entry for entry in record if isinstance(entry, Message)]
        turns = self._history_turns()
        # What all but the chat model read: her reach-outs the user answers her latest words.
        history = [*latest_turns(messages, turns), *_answering(record)]
        affinity = self._affinity(conversation)
        judging = affinity is not None and not affinity.cooling and not opening
        in_bath = self._in_bath(
            character, conversation.user_id, self._clock(),
            effective_timezone(self._store.get_profile(conversation.user_id), device_timezone),
            last_contact, in_call,
        )
        # In the bath, or just out of it, she sends no photo (GLOSSARY.md「洗澡」).
        eligibility = (
            None if opening or in_bath
            else self._photo_eligibility(conversation, character, photo.own)
        )
        asking_photo = eligibility is not None
        judged_apart = None
        searching = web and self._web is not None
        # 「是不是让她去查」和检索同时问，不让回话多等一轮判断模型
        search_apart = (
            asyncio.ensure_future(self._search_judge.asked(character, history, text))
            if searching and self._search_judge is not None
            else None
        )
        if (judging or asking_photo) and self._retrieval is None and self._affinity_judge:
            judged_apart = asyncio.ensure_future(
                _judged(self._affinity_judge, character, history, text, judging, asking_photo)
            )
        try:
            profile = self._store.get_profile(conversation.user_id)
            timezone = effective_timezone(profile, device_timezone)
            memory = await self._memory.begin(
                conversation, timezone, llms.background, llms.hold_background
            )
            retrieved = (
                await self._retrieval.retrieve(
                    character, history, text, memory, judging, asking_photo
                )
                if self._retrieval and not opening
                else Retrieved(None)
            )
            judged, asked_photo = (
                await judged_apart if judged_apart is not None
                else (retrieved.judged, retrieved.photo)
            )
        except BaseException:
            if search_apart is not None:
                search_apart.cancel()
            raise
        finally:
            if judged_apart is not None and not judged_apart.done():
                judged_apart.cancel()
        now = self._clock()
        cools = self._cools(conversation, judged)
        if in_call is not None:
            out_of_line_before, hurt_before = in_call.out_of_line, in_call.hurts
        else:
            # As many as are weighed, so that whether she warns is told against the latest
            # before this one; out of line is counted in the latest with this one among them.
            earlier = self._store.typed_turns(conversation.id, FED_UP_TURNS)
            out_of_line_before = sum(t.out_of_line for t in earlier[-(FED_UP_TURNS - 1):])
            hurt_before = [t.hurt for t in earlier]
        ending = ends(judged, cools, out_of_line_before, hurt_before)
        way: Parting = "hangs_up" if in_call is not None else "parts"
        affinity_lines = (
            affinity_prompt(
                affinity.tier,
                judged,
                way if ending or cools else None,
                way if warns(judged, cools, out_of_line_before, hurt_before) else None,
                character.heart,
                profile.gender == "female",
            )
            if affinity is not None
            else []
        )
        scene = self._scene(conversation) if judging else None
        world_prompt = (
            self._world.prompt(character.name, local_date(now, timezone)) if self._world else None
        )
        photo_asked = (
            photo_turn(asked_photo, affinity and affinity.tier, eligibility)
            if eligibility is not None else None
        )
        about_photo = CANNOT_SEND if eligibility is None else photo_asked and photo_asked.prompt
        promises_due = self._promises_due(character, conversation.user_id, now, timezone)
        # 联网这一段逐回合不同（骰子），所以放在 closing 里，和检索到的东西一起，不动前面能缓存的前缀
        closing = [
            p for p in (
                retrieved.memories_prompt, retrieved.experiences_prompt, retrieved.prompt
            ) if p
        ]
        if searching:
            # 判断模型只管「制作人是不是在让她去查」这一件小事；她感不感兴趣由她自己看人设判断
            asked = False
            if search_apart is not None:
                try:
                    asked = await search_apart
                except Exception:  # 判断不出来就当没要求，交给她自己
                    log.warning("the search question could not be asked", exc_info=True)
            closing.append(web_rules(self._random(2) == 0, asked))
        prompt = self._prompt(
            character, world_prompt, producer_prompt(profile, self._ties(conversation.user_id, character.id)),
            chat_history(messages, turns), narrations(record, self._gift_catalog, timezone),
            [
                now_prompt(
                    profile, now, timezone, "in_call" if in_call is not None else "typing",
                    affinity_lines, last_contact,
                    await self._day_lines(
                        character, conversation.user_id, now, timezone,
                        [
                            *promises_due.lines,
                            *self._schedule_lines(
                                character, conversation.user_id, now, timezone,
                                last_contact, in_call is not None, in_bath,
                            ),
                        ],
                    ),
                ),
                *([scene_prompt(scene, character.heart, profile.gender == "female")] if scene else []),
                *([EXPLAINED] if _appealed(messages) else []),
                *([about_photo] if about_photo else []),
                *([asmr_rules(voiced_by)] if in_call is not None and in_call.asmr else []),
            ],
            closing,
            (ANSWERING_IN_THE_TUB if in_bath else ANSWERING) if opening else text, voiced_by,
        )

        # Offered on every turn, so that what goes before the history (the tools, with
        # many services) opens every turn's prompt alike, for the service to cache it.
        tools: list[Tool] = [RECALL_STORY_TOOL] if self._retrieval else []
        if searching:
            tools.append(SEARCH_TOOL)
        # 这一回合查到的网页：回完话交给后台抽取，她"经历过"的事才带得上来源
        found: list[Found] = []
        searched = False
        parser = ReplyParser()
        try:
            for step in range(MAX_TOOL_ROUNDS + 1):
                # The last round offers the same tools, as some models drop what they thought
                # once the tools change, but forbids calling them; so does a turn that
                # already recalled an episode, its one round its last.
                forbidden = step == MAX_TOOL_ROUNDS or retrieved.recalled
                said = ""
                calls: list[ToolCall] = []
                reasoning: list[Reasoning] = []
                stream = llms.chat.stream(prompt, tools, forbid_calls=forbidden)
                try:
                    while (item := await _unless_stopped(stop, anext, stream, None)) is not None:
                        if isinstance(item, ToolCall):
                            calls.append(item)
                            continue
                        if isinstance(item, Reasoning):
                            reasoning.append(item)
                            continue
                        said += item
                        for event in parser.feed(item):
                            if isinstance(event, PhotoTagged):
                                if photo_asked and photo_asked.keeps_tag:
                                    yield self._begin_photo(
                                        character, event.tag, conversation.user_id, photo
                                    )
                                continue
                            yield event
                finally:
                    if (close := getattr(stream, "aclose", None)) is not None:
                        await close()  # a stream left part-way lets go of its connection
                for event in parser.finish():
                    if not isinstance(event, PhotoTagged):  # never: a tag closes as it is fed
                        yield event
                # A call to a tool not offered, or made when forbidden, goes unanswered.
                if not calls or not tools or forbidden:
                    break
                prompt = [
                    *prompt,
                    ChatMessage("assistant", said, tuple(calls), reasoning=tuple(reasoning)),
                ]
                last_round = step + 1 == MAX_TOOL_ROUNDS
                for call in calls:
                    # 一回合最多查一次网：并行发来的第二个 search_web 直接回绝
                    searching_call = call.name == SEARCH_TOOL.name and searching and not searched
                    result = await _unless_stopped(
                        stop, self._call_tool, character, call, found, searching_call
                    )
                    if searching_call:
                        searched = True
                    if last_round:
                        result = f"{result}\n\n{LAST_ROUND_NOTE}"
                    prompt.append(ChatMessage("tool", result, tool_call_id=call.id))
        except _Stopped:
            pass

        if not parser.complete:
            raise EmptyReply("对话模型没有返回任何内容")
        typed = (
            None if in_call is not None
            else Typed(hurt(judged), judged is not None and judged.out_of_line)
        )
        ends_conversation = typed is not None and ending is not None
        try:
            reply, affinity = self._record(
                conversation, text, said_at, parser.complete, judged, affinity, typed,
                ends_conversation, NewPhoto(photo.id, photo.tag) if photo.id else None,
            )
        except ConversationEnded:  # elsewhere, as the turn was under way
            yield TurnRejected(ENDED)
            return
        if photo.id is not None and self._photos is not None:
            photo.recorded = True
            self._photos.recorded(photo.id)
        self._promises_told(promises_due, now, timezone)
        if self._promises is not None:
            # 说好来找她、给她打电话的约定：这一回合（通话里就是打了电话）就算做到了
            self._promises.by_action(
                conversation.user_id, conversation.character_id, in_call is not None, now,
                timezone,
            )
        for recorded in self._recorded_listeners:
            try:
                recorded(conversation, reply, now, timezone)
            except Exception:
                log.exception("telling of a turn recorded in %s failed", conversation.id)
        if not opening:
            memory.write_down(history, text, reply, retrieved.query_vector, found)
            self._judge_promise(conversation, history, text, timezone)
        turn = sum(m.role == "user" for m in messages) + 1
        self._titles.after_turn(conversation, turn, llms.background, llms.hold_background)
        ended = self._store.ended(conversation.id) if ends_conversation else None
        if ended is not None and ending is not None:
            for listener in self._ended_listeners:
                listener(conversation.id, ending)
        if judged is not None and self._affinities is not None:
            yield TurnJudged(
                judged,
                reply.affinity is not None and reply.affinity.cooldown,
                ending if ends_conversation else None,
            )
        if in_call is not None:
            yield CallTurnRecorded(
                judged is not None and judged.out_of_line,
                ending,
                affinity and affinity.cooldown_until,
                hurt(judged),
            )
        if photo_asked and photo_asked.used_up_until is not None:
            yield PhotosUsedUp(photo_asked.used_up_until.isoformat())
        yield TurnDone(reply.without_cues(), affinity and affinity.shown(), ended and ended.shown())

    async def reach_out(self, conversation: Conversation, llm: ChatLLM) -> list[Segment]:
        """What she says first in `conversation` (`_reaching_out`), how she does as
        `REACH_OUT_RULES` says; a photo tag she writes is dropped."""
        said, _ = await self._reaching_out(conversation, llm, REACH_OUT_RULES, WANTS_TO_REACH_OUT)
        return said

    async def send_trip_photo(
        self, conversation: Conversation, llm: ChatLLM, place: str, with_names: Sequence[str],
        all_together: bool = False,
    ) -> tuple[list[Segment], str]:
        """What she says first in `conversation` on a trip to `place` (GLOSSARY.md「外景照片」),
        with the others of her unit named in `with_names` if any, sending the producer a
        photo from there: its segments and the photo tag she wrote. Raises EmptyReply if
        she says nothing, or writes no photo tag."""
        together = bool(with_names)
        rules = photo_rules(
            "外景的照片",
            TRIP_PHOTO_SITUATION.format(
                where=place,
                together=(
                    (TRIP_PHOTO_ALL_TOGETHER if all_together else TRIP_PHOTO_TOGETHER).format(
                        names="、".join(with_names)
                    ) if together else ""
                ),
            ),
            "这是哪里、在干什么、有什么想跟他分享的。",
            place=f"，要看得出是{place}",
            others=TRIP_PHOTO_OTHERS if together else "",
            nobody="照片里只有你，或者你和一起来的她们，没有别的人。" if together else ALONE,
        )
        said, tag = await self._reaching_out(conversation, llm, rules, WANTS_TO_SEND_TRIP_PHOTO)
        if tag is None:
            raise EmptyReply("对话模型没有写照片标签")
        return said, tag

    async def send_asked_photo(
        self, conversation: Conversation, llm: ChatLLM, wish: str, outfit_tags: str | None
    ) -> tuple[list[Segment], str]:
        """The photo the producer asked her for with a ticket (GLOSSARY.md「体验券」), what
        they asked for said in `wish`, the outfit's tags, if known, to write as its
        `outfit`: what she says sending it and her photo tag. Raises EmptyReply if she says
        nothing, or writes no photo tag."""
        rules = photo_rules(
            "制作人想要的照片",
            f"制作人用拍照券拜托你拍一张照片：{wish}。你很乐意，现在就拍一张发给他，再说几句。",
            "拍了什么、穿着它的感觉、想对他说的话。",
            outfit=f"照抄这几个 tag：{outfit_tags}" if outfit_tags else OUTFIT_FREE,
        )
        said, tag = await self._reaching_out(conversation, llm, rules, WANTS_TO_SEND_ASKED_PHOTO)
        if tag is None:
            raise EmptyReply("对话模型没有写照片标签")
        return said, tag

    async def accept_date(
        self, conversation: Conversation, llm: ChatLLM, when: str, what: str
    ) -> list[Segment]:
        """What she says, the producer having just invited her to `what` on `when` with a
        ticket (GLOSSARY.md「体验券」「邀约」). Raises EmptyReply if she says nothing."""
        said, _ = await self._reaching_out(
            conversation, llm, DATE_ACCEPT_RULES.format(when=when, what=what),
            WANTS_TO_ANSWER_DATE,
        )
        return said

    async def after_date(
        self, conversation: Conversation, llm: ChatLLM, what: str, with_photo: bool
    ) -> tuple[list[Segment], str | None]:
        """What she says first, back from `what` with the producer today (GLOSSARY.md
        「邀约」): how it was, and, `with_photo`, a photo taken there and its tag. Raises
        EmptyReply if she says nothing, or writes no photo tag where she is to."""
        talk = "今天过得怎么样、印象最深的一刻、想对他说的话。"
        situation = DATE_AFTER_SITUATION.format(what=what)
        rules = (
            photo_rules("约会之后", situation + "你想发一张今天拍的照片给他，再聊聊今天。", talk)
            if with_photo else f"# 约会之后\n{situation}你想聊聊今天。\n- 用 <ja><zh> 写一到三段：{talk}\n- 不发照片，也不说要给他打电话。"
        )
        said, tag = await self._reaching_out(conversation, llm, rules, WANTS_TO_TALK_AFTER_DATE)
        if with_photo and tag is None:
            raise EmptyReply("对话模型没有写照片标签")
        return said, tag

    async def favor_reply(
        self, conversation: Conversation, llm: ChatLLM, rules: str, wants: str,
        with_photo: bool = False,
    ) -> tuple[list[Segment], str | None, str]:
        """What she says first in `conversation` about a favor the producer asked
        (GLOSSARY.md「拜托」), as `rules` say, `wants` where the user's words would be; and,
        `with_photo`, the photo tag she wrote; and all the model wrote, for what she answered
        outside her lines (`<answer>`). Raises EmptyReply if she says nothing, or writes no
        photo tag where she is to."""
        raw: list[str] = []
        said, tag = await self._reaching_out(conversation, llm, rules, wants, raw)
        if with_photo and tag is None:
            raise EmptyReply("对话模型没有写照片标签")
        return said, tag, "".join(raw)

    async def _reaching_out(
        self, conversation: Conversation, llm: ChatLLM, rules: str, wants: str,
        raw: list[str] | None = None,
    ) -> tuple[list[Segment], str | None]:
        """What she says first in `conversation`, the user not talking to her (GLOSSARY.md
        「搭话」), on `llm`: its segments, as the model wrote them, delivery cues and all. Her
        prompt is laid out as a text turn's (ADR-0020), and opens as the next one's will, but
        for what changes from turn to turn: she is told what time it is and that they are not
        talking and when they last were, how she speaks in her tier, the memories she reaches
        out with (`Memories.for_reaching_out`), and how to reach out; then, where the user's
        words would be, the events and her reach-outs since the last turn and that she wants
        to say something first. Nothing else is retrieved, no tool offered, nothing judged.
        Raises EmptyReply if the model says nothing she could say, and whatever the model
        raises."""
        character = self._characters[conversation.character_id]
        record = self._store.record(conversation.id)
        messages = [entry for entry in record if isinstance(entry, Message)]
        profile = self._store.get_profile(conversation.user_id)
        timezone = effective_timezone(profile, None)
        now = self._clock()
        affinity = self._affinity(conversation)
        world_prompt = (
            self._world.prompt(character.name, local_date(now, timezone)) if self._world else None
        )
        told = narrations(record, self._gift_catalog, timezone)
        told[None] = _since_last_turn(record, self._gift_catalog, timezone)
        memories = await self._memory.for_reaching_out(
            conversation, timezone, last_turn_query(messages)
        )
        promises_due = self._promises_due(character, conversation.user_id, now, timezone)
        scene = self._scene(conversation)
        prompt = self._prompt(
            character, world_prompt, producer_prompt(profile, self._ties(conversation.user_id, character.id)),
            chat_history(messages, self._history_turns()), told,
            [
                now_prompt(
                    profile, now, timezone, "apart",
                    affinity_prompt(
                        affinity.tier, None, heart=character.heart,
                        female_producer=profile.gender == "female",
                    ) if affinity is not None else [],
                    self._store.last_contact(conversation.user_id, conversation.character_id),
                    await self._day_lines(
                        character, conversation.user_id, now, timezone,
                        [
                            *promises_due.lines,
                            *self._schedule_lines(character, conversation.user_id, now, timezone),
                        ],
                    ),
                ),
                *([SCENE_INVITATION] if scene is not None and scene.turns == 0 else []),
                *([CALLING_BACK] if _missed_since_last_turn(record) else []),
            ],
            [*([memories] if memories else []), rules],
            wants, None,
        )
        parser = ReplyParser()
        tag: str | None = None
        stream = llm.stream(prompt)
        try:
            async for item in stream:
                if isinstance(item, str):
                    if raw is not None:
                        raw.append(item)
                    for event in parser.feed(item):
                        if isinstance(event, PhotoTagged):
                            tag = event.tag
        finally:
            if (close := getattr(stream, "aclose", None)) is not None:
                await close()
        parser.finish()
        if not parser.complete:
            raise EmptyReply("对话模型没有返回任何内容")
        self._promises_told(promises_due, now, timezone)
        return parser.complete, tag

    async def write_letter(
        self,
        conversation: Conversation,
        llm: ChatLLM,
        occasion: Occasion,
        at: datetime | None = None,
        about: str = "",
    ) -> list[Segment]:
        """A letter she writes the user (GLOSSARY.md「来信」) for `occasion` (a holiday's,
        `about` which), on `llm`, from what she knows in `conversation`, her current one: its
        segments, as the model wrote them. It comes `at`, if given, else now: written ahead, she
        is told it is then, but for the producer's birthday, written the evening before. Her
        prompt is a reach-out's (`reach_out`), told her latest letters (in `now_prompt`,
        `_day_lines`) and how to write one (`letter_rules`). Raises EmptyReply if the model
        writes nothing she could send, and whatever the model raises."""
        character = self._characters[conversation.character_id]
        record = self._store.record(conversation.id)
        messages = [entry for entry in record if isinstance(entry, Message)]
        profile = self._store.get_profile(conversation.user_id)
        timezone = effective_timezone(profile, None)
        now = self._clock() if at is None else at
        if occasion == "producer_birthday" and at is not None:
            now = min(self._clock(), at)  # written the evening before, to come at midnight
        affinity = self._affinity(conversation)
        world_prompt = (
            self._world.prompt(character.name, local_date(now, timezone)) if self._world else None
        )
        told = narrations(record, self._gift_catalog, timezone)
        told[None] = _since_last_turn(record, self._gift_catalog, timezone)
        memories = await self._memory.for_reaching_out(
            conversation, timezone, last_turn_query(messages)
        )
        prompt = self._prompt(
            character, world_prompt, producer_prompt(profile, self._ties(conversation.user_id, character.id)),
            chat_history(messages, self._history_turns()), told,
            [
                now_prompt(
                    profile, now, timezone, "apart",
                    affinity_prompt(
                        affinity.tier, None, heart=character.heart,
                        female_producer=profile.gender == "female",
                    ) if affinity is not None else [],
                    self._store.last_contact(conversation.user_id, conversation.character_id),
                    await self._day_lines(character, conversation.user_id, now, timezone),
                ),
            ],
            [
                *([memories] if memories else []),
                letter_rules(occasion, about),
            ],
            WANTS_TO_WRITE, None,
        )
        parser = ReplyParser()
        stream = llm.stream(prompt)
        try:
            async for item in stream:
                if isinstance(item, str):
                    parser.feed(item)  # a photo tag is dropped: she sends none
        finally:
            if (close := getattr(stream, "aclose", None)) is not None:
                await close()
        parser.finish()
        if not parser.complete:
            raise EmptyReply("对话模型没有返回任何内容")
        return parser.complete

    async def write_keepsake(
        self,
        conversation: Conversation,
        llm: ChatLLM,
        occasion: str,
        about: str,
        before: Sequence[str] = (),
    ) -> KeepsakeDraft:
        """A keepsake she gives the user (GLOSSARY.md「纪念品」) for `occasion`, `about` saying
        the rest, on `llm`, from what she knows in `conversation`, her current one; told what
        she gave before (`before`). Her prompt is a letter's (`write_letter`), told how to
        give one (`keepsake_rules`). Raises EmptyReply if the model names nothing or says
        nothing, and whatever the model raises."""
        character = self._characters[conversation.character_id]
        record = self._store.record(conversation.id)
        messages = [entry for entry in record if isinstance(entry, Message)]
        profile = self._store.get_profile(conversation.user_id)
        timezone = effective_timezone(profile, None)
        now = self._clock()
        affinity = self._affinity(conversation)
        world_prompt = (
            self._world.prompt(character.name, local_date(now, timezone)) if self._world else None
        )
        told = narrations(record, self._gift_catalog, timezone)
        told[None] = _since_last_turn(record, self._gift_catalog, timezone)
        memories = await self._memory.for_reaching_out(
            conversation, timezone, last_turn_query(messages)
        )
        adult = character.heart is not None and character.heart.adult
        prompt = self._prompt(
            character, world_prompt, producer_prompt(profile, self._ties(conversation.user_id, character.id)),
            chat_history(messages, self._history_turns()), told,
            [
                now_prompt(
                    profile, now, timezone, "apart",
                    affinity_prompt(
                        affinity.tier, None, heart=character.heart,
                        female_producer=profile.gender == "female",
                    ) if affinity is not None else [],
                    self._store.last_contact(conversation.user_id, conversation.character_id),
                    await self._day_lines(character, conversation.user_id, now, timezone),
                ),
            ],
            [
                *([memories] if memories else []),
                keepsake_rules(occasion, about, adult, before),
            ],
            WANTS_TO_GIVE, None,
        )
        answer = "".join([item async for item in llm.stream(prompt) if isinstance(item, str)])
        return read_keepsake(answer)

    async def write_diary(
        self,
        conversation: Conversation,
        llm: ChatLLM,
        moments: Sequence[str],
        at: datetime,
    ) -> list[Segment]:
        """Her diary of the day ending `at` (GLOSSARY.md「日记」), for what made it matter
        (`moments`), on `llm`, from what she knows in `conversation`, her current one: its
        segments, as the model wrote them, a page she did not mean the producer to see last
        if she wrote one (`diary.split_secret`). Her prompt is a letter's (`write_letter`),
        her history as it was by `at`, told how to write a diary (`diary_rules`). Raises
        EmptyReply if the model writes nothing, and whatever the model raises."""
        character = self._characters[conversation.character_id]
        record = [
            entry for entry in self._store.record(conversation.id)
            if datetime.fromisoformat(entry.created_at) <= at
        ]
        messages = [entry for entry in record if isinstance(entry, Message)]
        profile = self._store.get_profile(conversation.user_id)
        timezone = effective_timezone(profile, None)
        affinity = self._affinity(conversation)
        world_prompt = (
            self._world.prompt(character.name, local_date(at, timezone)) if self._world else None
        )
        told = narrations(record, self._gift_catalog, timezone)
        told[None] = _since_last_turn(record, self._gift_catalog, timezone)
        prompt = self._prompt(
            character, world_prompt, producer_prompt(profile, self._ties(conversation.user_id, character.id)),
            chat_history(messages, self._history_turns()), told,
            [
                now_prompt(
                    profile, at, timezone, "apart",
                    affinity_prompt(
                        affinity.tier, None, heart=character.heart,
                        female_producer=profile.gender == "female",
                    ) if affinity is not None else [],
                    self._store.last_contact(conversation.user_id, conversation.character_id),
                    await self._day_lines(
                        character, conversation.user_id, at, timezone,
                        self._schedule_summary(character, conversation.user_id, at, timezone),
                    ),
                ),
            ],
            [diary_rules(moments)],
            WANTS_TO_WRITE_DIARY, None,
        )
        parser = ReplyParser()
        stream = llm.stream(prompt)
        try:
            async for item in stream:
                if isinstance(item, str):
                    parser.feed(item)  # a photo tag is dropped: she sends none
        finally:
            if (close := getattr(stream, "aclose", None)) is not None:
                await close()
        parser.finish()
        if not parser.complete:
            raise EmptyReply("对话模型没有返回任何内容")
        return [Segment(without_cues(s.ja), without_cues(s.zh)) for s in parser.complete]

    def _photo_eligibility(
        self,
        conversation: Conversation,
        character: Character,
        own: ImageSettings | None,
    ) -> Eligibility | None:
        """What a turn with her may send, bringing the user's `own` image generation if any,
        if it could have a photo, and so the judge model is asked whether it asks for one:
        there being the judge model to ask, typed or in a call alike."""
        has_judge = self._retrieval is not None or self._affinity_judge is not None
        if not has_judge or self._photos is None:
            return None
        if not self._features.on(conversation.user_id, "photos"):  # GLOSSARY.md「玩法开关」
            return None
        return self._photos.eligibility(character, conversation.user_id, own)

    async def photo_came_out(self, photo_id: str, user_id: str) -> Photo | None:
        """The photo a turn of the user's sent (`photo`), once it has come out, sent or not,
        and is kept with her reply; None if it is not kept, its reply never recorded."""
        if self._photos is None:
            return None
        return await self._photos.came_out(photo_id, user_id)

    def _begin_photo(
        self, character: Character, tag: str, user_id: str, photo: _Photo
    ) -> PhotoPending:
        """Begin the photo her `tag` says, the turn's one, for the user."""
        assert self._photos is not None
        photo.id, photo.tag = self._photos.begin(character, tag, user_id, photo.own), tag
        return PhotoPending(photo.id)

    def _affinity(self, conversation: Conversation) -> Status | None:
        """Her affinity with the user as the turn begins; None where affinity is off."""
        if self._affinities is None:
            return None
        return self._affinities.status(conversation.user_id, conversation.character_id)

    def _scene(self, conversation: Conversation) -> Scene | None:
        """Her scene at a door the turn is of (GLOSSARY.md「重要的一幕」); None where affinity
        is off or there is none."""
        if self._affinities is None:
            return None
        return self._affinities.scene(conversation.user_id, conversation.character_id)

    def _cools(self, conversation: Conversation, judged: Judged | None) -> bool:
        """Whether the turn, as `judged`, would begin a cooldown; never if not judged."""
        if judged is None or self._affinities is None:
            return False
        return self._affinities.cools(conversation.user_id, conversation.character_id, judged)

    def _record(
        self,
        conversation: Conversation,
        text: str,
        said_at: str,
        reply: list[Segment],
        judged: Judged | None,
        affinity: Status | None,
        typed: Typed | None,
        ends_conversation: bool,
        photo: NewPhoto | None,
    ) -> tuple[Message, Status | None]:
        """Record the turn, as said at `said_at`, changing her affinity as `judged` says, if
        judged, with what a typed turn came to, her ending the conversation after it if she
        does, and the photo she sends with it if any: the reply, and her affinity after it
        (`affinity`, as the turn began, if not judged)."""

        def add_turn(change: Change | None) -> Message:
            return self._store.add_turn(
                conversation.id, text, reply, change, said_at, typed, ends_conversation, photo
            )

        if judged is None or self._affinities is None:
            return add_turn(None), affinity
        return self._affinities.change(
            conversation.user_id, conversation.character_id, judged, add_turn
        )

    async def _call_tool(
        self,
        character: Character,
        call: ToolCall,
        found: list[Found] | None = None,
        allowed: bool = True,
    ) -> str:
        """The tool's result, or what went wrong, for the model to read. A search's results
        are also added to `found`, for the turn to write down what she said she did."""
        if call.name == SEARCH_TOOL.name and self._web is not None:
            if not allowed:
                return "这一回合已经查过一次了。就按查到的说，不要再查。"
            query = (call.arguments or {}).get("query")
            if not isinstance(query, str) or not query.strip():
                return "调用参数有误：需要 query（要查的事，一句话）。"
            results = await self._web.find(query.strip())
            if found is not None:
                found.extend(results)
            return result_text(query.strip(), results)
        if call.name != RECALL_STORY_TOOL.name or self._retrieval is None:
            return f"没有 {call.name} 这个工具。"
        query = (call.arguments or {}).get("query")
        if not isinstance(query, str) or not query.strip():
            return "调用参数有误：需要 query（要回顾的事，一句概括）。"
        return await self._retrieval.recall_story(character, query.strip())

    def _prompt(
        self,
        character: Character,
        world_prompt: str | None,
        about_producer: str,
        history: list[Message],
        narration: dict[int | None, str],
        about_turn: list[str],
        closing: list[str],
        text: str,
        voiced_by: FishAudioModel | None,
    ) -> list[ChatMessage]:
        """The prompt (ADR-0020): what changes from turn to turn tells first `about_turn`, then,
        should any photo of the history not have been sent, what that means, then `closing`:
        what retrieval found, or how she is to reach out."""
        # What every user's turns with her that day open with, apart, for a service to cache.
        shared = [character.sheet]
        if world_prompt:
            shared.append(world_prompt)
        shared.append(format_instructions(voiced_by))
        prompt = [
            ChatMessage("system", "\n\n".join(shared), cache_breakpoint=True),
            ChatMessage("system", about_producer),
        ]
        for message in history:
            if message.role == "user":
                told = narration.get(message.id, "")
                prompt.append(ChatMessage("user", f"{told}{message.text or ANSWERED}"))
            else:
                prompt.append(ChatMessage("assistant", format_reply(
                    message.segments,
                    message.photo.tag if message.photo else None,
                    message.photo_failed,
                )))
        if history:  # what the next turn opens with too, for a service to cache
            prompt[-1] = replace(prompt[-1], cache_breakpoint=True)
        context = [*about_turn]
        if any(m.photo_failed for m in history):
            context.append(NOT_RECEIVED)
        context += closing
        prompt.append(ChatMessage("system", "\n\n".join(context)))
        prompt.append(ChatMessage("user", f"{narration.get(None, '')}{text}"))
        return prompt
