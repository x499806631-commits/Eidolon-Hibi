"""The structured reply format and its streaming parser (ADR-0001).

The chat model answers in segments, each a Japanese line followed by its Chinese
subtitle:

    <ja>こんにちは、プロデューサーさん！</ja><zh>你好，制作人！</zh>

The parser turns a stream of arbitrary text chunks into per-segment deltas as soon
as they arrive, so Japanese text can reach TTS before the reply is finished. It also
says when each segment is complete: once its subtitle closes, the next segment opens
(the model skipped the subtitle), or the stream ends. The segments keep what the model
wrote; the deltas show it without delivery cues, and the Japanese is voiced with them
(see `delivery_cues`).

Before her first segment, in a turn she may send a photo in, she can write a photo tag
(GLOSSARY.md「照片」), `<photo>…</photo>`: the parser hands what it says over the moment it
closes (`PhotoTagged`), apart from the segments, neither shown nor voiced. One written
anywhere else, between segments or in the middle of her words, or after the first, is left
out of the reply as if never written.
"""

from dataclasses import dataclass, field
from typing import Literal

from eidolon.config import FishAudioModel
from eidolon.delivery_cues import TAG_READERS, CueFilter

Lang = Literal["ja", "zh"]

_FORMAT = """\
# 回复格式
无论用户用中文还是日语说话，你都只用日语回答，并给每句日语附上中文字幕。
把回复分成段，每段先写日语原文，紧接着写这段的中文字幕，格式如下：
<ja>日语原文</ja><zh>中文字幕</zh>
<ja> 里只写日语，一个中文词也不要混进去：日语原文会被念出来，中文只写在 <zh> 里。每段都要有字幕。
除了这些标签和标签里的内容，不要输出任何其他文字。

# 说话长度
像真人当面说话那样：接对方一句话，通常只回一两句。
- 平时只写一到两段，每段只有一句简短的口语。不写长句，不要把几件事塞进一段，也不要用「首先……其次……」这种条理分明的说法。
- 被要求讲一段事情时（包括回忆过去的事），可以多说几句，但最多四段左右；讲到自然的停顿处就停下，剩下的等对方追问再讲，不要一口气讲完。
- 不必每次都用提问收尾，话说完了自然停下就好。
- 对方一次问了几件事，每件都用一句话简短回应，不要漏掉，这时可以超过两段。
- 话多话少、怎么说仍以人设为准；这里只管一次说多少。

# 情绪与语气提示
让情绪自然地流露在日语的措辞里：感叹词、语气词、停顿、口语化的说法。但口吻和口癖始终以人设为准，不要为了显得有情绪而夸张。
"""

_CUES = """\
通话时日语原文会被念出来。情绪明显的段，可以在日语原文里加上语气提示，告诉配音这里该怎么念：用方括号括起简短的英文描述，放在段首管整段，放在句中管它后面的部分。平常说话、交代事情的段不加，一次回复里通常只有一两段需要。例如：
<ja>[shyly] え、えっと……ありがとう。</ja><zh>那、那个……谢谢。</zh><ja>明日は十時に集合だよね。</ja><zh>明天是十点集合对吧。</zh>
<ja>そっか……[disappointed] 行けないんだ。</ja><zh>这样啊……去不了了啊。</zh>
常用的有 [happily] [excitedly] [shyly] [embarrassed] [sadly] [disappointed] [sulking] [angrily] [worried] [surprised] [gently] [teasing] [whispering] [laughing] [sleepy]，也可以自由描述，例如 [laughing nervously]，但不要超过四个英文单词。
- 每段最多两个语气提示；拿不准要不要加时就不加。
- 语气提示只写在 <ja> 里；<zh> 只翻译台词，不带语气提示。
- 只用方括号加英文，不要用圆括号、方头括号或其他括号，也不要用日语或中文写提示。"""

_DIRECTED_CUES = """\
通话时日语原文会被念出来，配音会像演员一样照你的指示念。情绪明显的段，可以在日语原文里加上语气提示，告诉配音这里该怎么念：
- 用方括号括起英文的指示，放在段首管整段，放在句中管它后面的部分。可以像导演给演员说戏那样写一句完整的描述，但不要超过十个英文单词。
- 笑声、叹气、吸气、停顿这些声音，也用方括号写在它出现的地方，例如 [small laugh] [sigh] [shaky breath] [pause]。
- 只想改变几个词的念法时，用成对的标签把这几个词括起来：<whisper>小声</whisper>、<emphasis>加重</emphasis>、<soft>轻柔</soft>、<fast>快</fast>、<slow>慢</slow>、<stress>重读</stress>、<forceful>有力</forceful>。标签要在同一句里关上，不要跨句。
平常说话、交代事情的段不加，一次回复里通常只有一两段需要。例如：
<ja>[shyly, glancing away] え、えっと……ありがとう。</ja><zh>那、那个……谢谢。</zh><ja>明日は十時に集合だよね。</ja><zh>明天是十点集合对吧。</zh>
<ja>そっか……[trying to hide her disappointment] 行けないんだ。</ja><zh>这样啊……去不了了啊。</zh>
<ja>[small laugh] これ、<whisper>みんなには内緒</whisper>だよ？</ja><zh>这个，要对大家保密哦？</zh>
- 每段最多两三处语气提示；拿不准要不要加时就不加。
- 语气提示只写在 <ja> 里；<zh> 只翻译台词，不带语气提示。
- 只用方括号加英文，和上面这几种成对标签；不要用圆括号、方头括号或别的标签，也不要用日语或中文写提示。"""

FORMAT_INSTRUCTIONS = _FORMAT + _CUES
"""The reply format, for a turn whose reply is voiced, if at all, with a model that reads
only the cues in square brackets, and short ones best."""

DIRECTED_FORMAT_INSTRUCTIONS = _FORMAT + _DIRECTED_CUES
"""The reply format, for a turn voiced with a model that reads paired tags too, and
directions as long as a director's (`delivery_cues.TAG_READERS`)."""


def format_instructions(voiced_by: FishAudioModel | None) -> str:
    """The reply format, for a turn voiced with `voiced_by`; None for one not voiced."""
    return DIRECTED_FORMAT_INSTRUCTIONS if voiced_by in TAG_READERS else FORMAT_INSTRUCTIONS


@dataclass(frozen=True)
class SegmentDelta:
    segment: int
    lang: Lang
    text: str
    type: Literal["delta"] = field(default="delta", init=False)


@dataclass(frozen=True)
class SegmentComplete:
    """Segment `segment` has all it will get. Stays on the server: a call needs to know
    when a reply has a complete segment, the browser does not."""

    segment: int
    type: Literal["segment-complete"] = field(default="segment-complete", init=False)


@dataclass(frozen=True)
class SpeechDelta:
    """More of segment `segment`'s Japanese to voice, sent along with the `SegmentDelta`
    that shows it: the same words, but with their delivery cues. Stays on the server."""

    segment: int
    text: str
    type: Literal["speech-delta"] = field(default="speech-delta", init=False)


@dataclass(frozen=True)
class PhotoTagged:
    """She wrote a photo tag at the front of her reply: what it says, between `<photo>` and
    `</photo>`, as she wrote it. Stays on the server."""

    tag: str
    type: Literal["photo-tagged"] = field(default="photo-tagged", init=False)


ParserEvent = SegmentDelta | SpeechDelta | SegmentComplete | PhotoTagged

_PHOTO: Literal["photo"] = "photo"
_TAGS = ("<ja>", "<zh>", f"<{_PHOTO}>")


@dataclass
class Segment:
    ja: str = ""
    zh: str = ""


@dataclass
class ReplyParser:
    segments: list[Segment] = field(default_factory=list)
    _buffer: str = ""
    _inside: Lang | Literal["photo"] | None = None
    _photo: str = ""  # the open photo tag's content
    _photo_kept: bool = False  # whether the open photo tag is the one at the front
    _photo_seen: bool = False
    _resume: Lang | None = None  # the line the open photo tag was written in the middle of
    _stray: str = ""
    _said_complete: int = 0  # how many segments are known to be complete
    _line: CueFilter = field(default_factory=CueFilter)  # the open tag's content
    _events: list[ParserEvent] = field(default_factory=list)

    @property
    def complete(self) -> list[Segment]:
        """The segments said to be complete so far, leaving out any half-written one."""
        return self.segments[: self._said_complete]

    def feed(self, chunk: str) -> list[ParserEvent]:
        self._buffer += chunk
        while True:
            if self._inside is None:
                if not self._open_next_tag():
                    return self._hand_over()
            elif self._inside == _PHOTO:
                if not self._take_photo():
                    return self._hand_over()
                self._inside, self._resume = self._resume, None
            else:
                closed = self._take_content(self._inside)
                if self._inside == _PHOTO:  # written in the middle of her words
                    continue
                if not closed:
                    return self._hand_over()
                self._end_line(self._inside)
                if self._inside == "zh":
                    self._complete_up_to(len(self.segments))
                self._inside = None

    def finish(self) -> list[ParserEvent]:
        """Flush what is left at the end of a stream; the parser can then go on with the
        next stream of the same reply. A reply that ignored the format becomes one
        Japanese segment; a photo tag left open is left out."""
        if self._inside == _PHOTO:
            self._photo = ""
            if self._resume is not None:
                self._end_line(self._resume)
        elif self._inside is not None:
            keep = _partial_suffix(self._buffer, (f"</{self._inside}>",))
            text = self._buffer[: len(self._buffer) - keep]
            if text:
                self._append(self._inside, text)
            self._end_line(self._inside)
        elif not self.segments and (self._stray + self._buffer).strip():
            self._append("ja", (self._stray + self._buffer).strip())
            self._end_line("ja")
        self._buffer, self._inside, self._stray, self._resume = "", None, "", None
        self._complete_up_to(len(self.segments))
        return self._hand_over()

    def _open_next_tag(self) -> bool:
        names: tuple[Lang | Literal["photo"], ...] = ("ja", "zh", _PHOTO)
        starts: list[tuple[int, Lang | Literal["photo"]]] = [
            (i, name) for name in names if (i := self._buffer.find(f"<{name}>")) >= 0
        ]
        if not starts:
            keep = _partial_suffix(self._buffer, _TAGS)
            self._stray += self._buffer[: len(self._buffer) - keep]
            self._buffer = self._buffer[len(self._buffer) - keep :]
            return False
        start, lang = min(starts)
        self._stray += self._buffer[:start]
        self._buffer = self._buffer[start + len(f"<{lang}>") :]
        if lang == _PHOTO:
            front = not self.segments and not self._photo_seen and not self._stray.strip()
            self._inside, self._photo, self._photo_kept = _PHOTO, "", front
            self._photo_seen = True
            return True
        if lang == "ja" or not self.segments or self.segments[-1].zh:
            self._complete_up_to(len(self.segments))
            self.segments.append(Segment())
        self._inside = lang
        self._line = CueFilter()
        return True

    def _take_content(self, lang: Lang) -> bool:
        """Take what there is of the open tag's content, up to a photo tag written in it, if
        one is, which it opens; True if the tag closed."""
        close, photo = f"</{lang}>", f"<{_PHOTO}>"
        end = self._buffer.find(close)
        opens = self._buffer.find(photo)
        if opens >= 0 and (end < 0 or opens < end):
            if text := self._buffer[:opens]:
                self._append(lang, text)
            self._buffer = self._buffer[opens + len(photo) :]
            self._inside, self._photo, self._photo_kept = _PHOTO, "", False
            self._photo_seen, self._resume = True, lang
            return False
        closed = end >= 0
        if not closed:
            end = len(self._buffer) - _partial_suffix(self._buffer, (close, photo))
        text = self._buffer[:end]
        self._buffer = self._buffer[end + len(close) :] if closed else self._buffer[end:]
        if text:
            self._append(lang, text)
        return closed

    def _take_photo(self) -> bool:
        """Take what there is of the photo tag's content; True if the tag closed, when the one
        at the front is handed over."""
        close = f"</{_PHOTO}>"
        end = self._buffer.find(close)
        if end < 0:
            end = len(self._buffer) - _partial_suffix(self._buffer, (close,))
            self._photo += self._buffer[:end]
            self._buffer = self._buffer[end:]
            return False
        self._photo += self._buffer[:end]
        self._buffer = self._buffer[end + len(close) :]
        if self._photo_kept:
            self._events.append(PhotoTagged(self._photo))
        self._photo = ""
        return True

    def _append(self, lang: Lang, text: str) -> None:
        if not self.segments:
            self.segments.append(Segment())
        segment = self.segments[-1]
        setattr(segment, lang, getattr(segment, lang) + text)
        self._show(lang, *self._line.feed(text))

    def _end_line(self, lang: Lang) -> None:
        self._show(lang, *self._line.finish())
        self._line = CueFilter()

    def _show(self, lang: Lang, shown: str, voiced: str) -> None:
        """Show what the reply's text has come to, and voice its Japanese."""
        if shown:
            self._events.append(SegmentDelta(len(self.segments) - 1, lang, shown))
        if lang == "ja" and voiced:
            self._events.append(SpeechDelta(len(self.segments) - 1, voiced))

    def _complete_up_to(self, segments: int) -> None:
        """Say the first `segments` segments are complete, those not said to be already."""
        self._events += [SegmentComplete(i) for i in range(self._said_complete, segments)]
        self._said_complete = max(self._said_complete, segments)

    def _hand_over(self) -> list[ParserEvent]:
        """The events since the last were handed over."""
        events, self._events = self._events, []
        return events


def format_reply(
    segments: list[Segment], photo_tag: str | None = None, photo_failed: bool = False
) -> str:
    """Write segments back in the reply format, after the photo tag she sent with them if
    any, e.g. for earlier replies in the prompt; that of a photo not sent after all marked
    `status="failed"`."""
    marked = ' status="failed"' if photo_failed else ""
    photo = f"<{_PHOTO}{marked}>{photo_tag}</{_PHOTO}>" if photo_tag is not None else ""
    return photo + "".join(f"<ja>{s.ja}</ja><zh>{s.zh}</zh>" for s in segments)


def _partial_suffix(text: str, tags: tuple[str, ...]) -> int:
    """Length of the longest suffix of `text` that could be the start of one of `tags`."""
    for size in range(min(len(text), max(len(t) for t in tags) - 1), 0, -1):
        if any(t.startswith(text[-size:]) for t in tags):
            return size
    return 0
