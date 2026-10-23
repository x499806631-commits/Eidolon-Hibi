"""Her emotion in each segment of a reply during a call (GLOSSARY.md「情绪」), for her stand.

Each segment is judged once it is complete, in a request of its own, while it is being
voiced: which of seven emotions she shows saying it, and whether she agrees or disagrees,
so that she nods or shakes her head. The judge model (`judge`) answers both as Choice
questions, with each option's probability. Only the stand shows the answer: it never
reaches the conversation record, and the delivery cues, which only change her voice, stay
as they are.

The judge is always the deployer's judge model; without one no segment is judged. An
answer it is unsure of, its likeliest option below `min_probability`, says she is calm,
which the browser takes as keeping her mood; one that takes longer than `timeout`, or
fails, says nothing at all, and is not asked again.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from eidolon.characters import Character
from eidolon.judge import JudgeError, JudgeModel

log = logging.getLogger(__name__)

Emotion = Literal["calm", "happy", "shy", "surprised", "sad", "angry", "thinking"]
Nod = Literal["yes", "no"]

T = TypeVar("T")


@dataclass(frozen=True)
class SaidSegment:
    """A segment of her reply: its Japanese, delivery cues and all, and its subtitle."""

    ja: str
    zh: str


@dataclass(frozen=True)
class SegmentToJudge:
    """What a segment is judged by: who she is, what the user said, the reply's segments
    before it, and the segment itself."""

    character: Character
    user_said: str
    before: Sequence[SaidSegment]
    segment: SaidSegment


@dataclass(frozen=True)
class EmotionAnswer:
    """How likely each emotion is, and each of nodding, shaking her head and neither (None)."""

    emotions: Mapping[Emotion, float]
    nods: Mapping[Nod | None, float]


class EmotionJudge(Protocol):
    async def judge_emotion(self, segment: SegmentToJudge) -> EmotionAnswer:
        """How she feels saying `segment`. Raises JudgeError if it cannot be told."""
        ...


@dataclass(frozen=True)
class EmotionJudging:
    """Judging each segment's emotion during calls, with the deployer's `judge`."""

    judge: EmotionJudge
    min_probability: float
    """The likeliest emotion's probability below which the answer is calm, and the
    likeliest nod's below which there is none."""
    timeout: float
    """How long, in seconds, the judge may take before the segment goes unjudged."""

    async def message(self, reply: int, segment: int, said: SegmentToJudge) -> dict | None:
        """The `emotion` server message for segment `segment` of reply `reply`, or None if
        it could not be judged in time."""
        try:
            answer = await asyncio.wait_for(self.judge.judge_emotion(said), self.timeout)
        except TimeoutError:
            log.info("emotion judge took over %ss; the segment goes unjudged", self.timeout)
            return None
        except JudgeError:
            log.warning("emotion judge failed; the segment goes unjudged", exc_info=True)
            return None
        emotion, nod = decide(answer, self.min_probability)
        return {"type": "emotion", "reply": reply, "segment": segment, "emotion": emotion, "nod": nod}


def decide(answer: EmotionAnswer, min_probability: float) -> tuple[Emotion, Nod | None]:
    """The emotion and the nod `answer` says, each its likeliest option unless that is
    below `min_probability`: then she is calm, and neither nods nor shakes her head."""
    return (
        _likeliest(answer.emotions, min_probability, "calm"),
        _likeliest(answer.nods, min_probability, None),
    )


def _likeliest(probabilities: Mapping[T, float], min_probability: float, otherwise: T) -> T:
    if not probabilities:
        return otherwise
    best, probability = max(probabilities.items(), key=lambda item: item[1])
    return best if probability >= min_probability else otherwise


_EMOTION_OPTIONS: dict[str, tuple[Emotion, str]] = {
    "平静": ("calm", "普通地回应、打招呼、交代或确认事情，没有明显情绪"),
    "开心": ("happy", "高兴、兴奋、感激、期待"),
    "害羞": ("shy", "难为情、脸红、被夸或被说到心事时的不好意思"),
    "惊讶": ("surprised", "吃惊、意外、慌张"),
    "难过": ("sad", "伤心、失落、寂寞、担心、不甘心"),
    "生气": ("angry", "不满、责怪、赌气、不能原谅"),
    "思考": ("thinking", "在想、犹豫、斟酌措辞"),
}
_NOD_OPTIONS: dict[str, tuple[Nod | None, str]] = {
    "同意": ("yes", "肯定、答应、附和制作人的话，会点头"),
    "否定": ("no", "否认、拒绝、不同意制作人的话，会摇头"),
    "都不是": (None, "既不是同意也不是否定，例如陈述、提问、打招呼、说自己的事"),
}
_SCENE = (
    "角色扮演的通话中，角色是《偶像大师 闪耀色彩》283 事务所的偶像（或事务员），正在和制作人通话。"
    "`这一段台词` 是她此刻说出口的日语，方括号里的英文和 <whisper> 这类成对的标签是给配音的语气提示；"
    "`这一段的中文字幕` 是这段台词的意思。"
)
_EMOTION_QUESTION = {
    "type": "choice",
    "instructions": _SCENE + "说这一段时，她脸上最主要的情绪是哪一种？",
    "criteria": {name: description for name, (_, description) in _EMOTION_OPTIONS.items()},
}
_NOD_QUESTION = {
    "type": "choice",
    "instructions": _SCENE + "说这一段时，她是在对制作人的话表示同意，表示否定，还是都不是？",
    "criteria": {name: description for name, (_, description) in _NOD_OPTIONS.items()},
}


class ModelEmotionJudge:
    """The judge model, asked about each segment in one request of two Choice questions:
    her emotion, and whether she agrees or disagrees. Answers are read by option name, the
    order of the model's probabilities being any."""

    def __init__(self, judge_model: JudgeModel) -> None:
        self._judge_model = judge_model

    async def judge_emotion(self, segment: SegmentToJudge) -> EmotionAnswer:
        before = [f"{s.ja}（{s.zh}）" for s in segment.before]
        answers = await self._judge_model.ask(
            {
                "角色": segment.character.name,
                "制作人这句话": segment.user_said,
                "这条回复前面几段": before or "（这是回复的第一段）",
                "这一段台词": segment.segment.ja,
                "这一段的中文字幕": segment.segment.zh,
            },
            {"emotion": _EMOTION_QUESTION, "nod": _NOD_QUESTION},
        )
        return EmotionAnswer(
            _read(answers, "emotion", _EMOTION_OPTIONS), _read(answers, "nod", _NOD_OPTIONS)
        )


def _read(answers: dict, question: str, options: dict[str, tuple[T, str]]) -> dict[T, float]:
    """The probability of each of `options` the judge model gave in answer to `question`."""
    try:
        probabilities = answers[question]["probabilities"]
        read = {options[name][0]: float(p) for name, p in probabilities.items() if name in options}
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise JudgeError(f"判断模型对 {question} 的回答无法解析") from e
    if not read:
        raise JudgeError(f"判断模型对 {question} 的回答里没有任何选项")
    return read
