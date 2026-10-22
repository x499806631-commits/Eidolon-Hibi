"""Ambience picked for a call (环境音「自动」): which kind of sound goes under her voice, as the
judge model (`judge`) finds fits the time where the producer is, the season and what they
have been talking about.

The browser chooses the sounds; the backend only says which kind (`AmbienceKind`), the browser
playing one of its scenes of that kind. Only while the browser asks for it (an `ambience-auto`
client message, `CallTurns`) is it picked: as soon as asked, then every `PICK_EVERY` turns
recorded. Once something plays, another kind takes over only if the judge finds it clearly
likelier (`SWITCH_MARGIN`), so the sound does not hop about from turn to turn. An answer that
takes longer than `timeout`, or fails, changes nothing.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from eidolon.characters import Character
from eidolon.clock import Clock, utc_now
from eidolon.judge import JudgeError, JudgeModel
from eidolon.store import Message

log = logging.getLogger(__name__)

AmbienceKind = Literal[
    "rain", "storm", "fireplace", "waves", "summer-night", "study", "cafe", "none"
]

PICK_EVERY = 4
"""How many turns recorded after a pick the next one is made."""

SWITCH_MARGIN = 0.2
"""How much likelier than what is playing another kind must be found to take over."""

AMBIENCE_TIMEOUT = 8.0
"""How long, in seconds, the judge model may take to pick; nothing waits on it but the sound."""

HISTORY = 6
"""How many of the latest messages the judge model reads."""

_OPTIONS: dict[str, tuple[AmbienceKind, str]] = {
    "雨声": ("rain", "下雨天、潮湿的季节、安静的夜里想安心入睡，或者聊到了下雨"),
    "雷雨": ("storm", "夏天的傍晚或暴风雨的夜晚、有点不安想依偎着，或者聊到了打雷、台风"),
    "暖炉": ("fireplace", "寒冷的冬夜、圣诞和新年前后、需要温暖、窝在家里，或者聊到了冷、下雪"),
    "海浪": ("waves", "夏天、海边、旅行和假期、想放松发呆，或者聊到了大海、游泳"),
    "夏夜虫鸣": ("summer-night", "夏天到初秋的夜晚、乡下和星空、夏日祭和烟花，或者聊到了虫鸣、乘凉"),
    "书房": ("study", "学习、工作、看书、熬夜赶工，一边忙一边安静地陪着"),
    "咖啡店": ("cafe", "白天的闲聊、约会、喝咖啡或下午茶，想要一点热闹的人声"),
    "安静": ("none", "话题很沉重、她或制作人正在难过，放什么声音都不合适"),
}
_QUESTION = {
    "type": "choice",
    "instructions": (
        "角色扮演的通话中，角色是《偶像大师 闪耀色彩》的偶像，正在和她的制作人通话。"
        "通话时背景里会放一段环境音，营造气氛。按「现在」「季节」和「上文」聊的内容，"
        "此刻最适合放哪一种环境音？聊天里明确提到的场景优先，其次看时间和季节。"
        "制作人那边的真实天气也可以参考，比如下雨时雨声更合适。"
        "制作人是自己想听环境音才开的，除非话题明显不适合，不要选「安静」。"
    ),
    "criteria": {name: description for name, (_, description) in _OPTIONS.items()},
}

_WEEKDAYS = "一二三四五六日"
_SEASONS = {12: "冬天", 1: "冬天", 2: "冬天", 3: "春天", 4: "春天", 5: "春天",
            6: "夏天", 7: "夏天", 8: "夏天", 9: "秋天", 10: "秋天", 11: "秋天"}


def _part_of_day(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 9:
        return "早上"
    if hour < 11:
        return "上午"
    if hour < 13:
        return "中午"
    if hour < 17:
        return "下午"
    if hour < 19:
        return "傍晚"
    if hour < 23:
        return "晚上"
    return "深夜"


@dataclass(frozen=True)
class AmbienceMoment:
    """What the ambience is picked by: who she is, the time where the producer is, whether
    it is an ASMR call, the latest messages of the conversation, and the weather there if it
    is known (`weather.Weather.now`)."""

    character: Character
    local: datetime
    asmr: bool
    history: Sequence[Message]
    weather: str | None = None


def moment_state(moment: AmbienceMoment) -> dict[str, object]:
    """What the judge model is told of `moment`."""
    local = moment.local
    recent = []
    for message in moment.history[-HISTORY:]:
        if message.role == "user":
            recent.append(f"制作人：{message.text}")
        else:
            recent.append(f"{moment.character.name}：{message.without_cues().zh or ''}")
    state: dict[str, object] = {
        "角色": moment.character.name,
        "现在": f"{local.month}月{local.day}日 星期{_WEEKDAYS[local.weekday()]}"
        f" {_part_of_day(local.hour)} {local:%H:%M}",
        "季节": _SEASONS[local.month],
        "通话": "ASMR 模式，她贴在耳边小声说话" if moment.asmr else "普通的通话",
        "上文": recent or "（还没有聊什么）",
    }
    if moment.weather:
        state["天气"] = moment.weather
    return state


class AmbienceJudge(Protocol):
    async def judge_ambience(self, moment: AmbienceMoment) -> Mapping[AmbienceKind, float]:
        """How fitting each kind is. Raises JudgeError if it cannot be told."""
        ...


class ModelAmbienceJudge:
    """The judge model, asked one Choice question; answers are read by option name."""

    def __init__(self, judge_model: JudgeModel) -> None:
        self._judge_model = judge_model

    async def judge_ambience(self, moment: AmbienceMoment) -> Mapping[AmbienceKind, float]:
        answers = await self._judge_model.ask(moment_state(moment), {"ambience": _QUESTION})
        try:
            probabilities = answers["ambience"]["probabilities"]
            read: dict[AmbienceKind, float] = {
                _OPTIONS[name][0]: float(p) for name, p in probabilities.items() if name in _OPTIONS
            }
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise JudgeError("判断模型对环境音的回答无法解析") from e
        if not read:
            raise JudgeError("判断模型对环境音的回答里没有任何选项")
        return read


def decide(
    probabilities: Mapping[AmbienceKind, float], playing: AmbienceKind | None
) -> AmbienceKind | None:
    """The kind to play now, or None to keep `playing`: the likeliest, unless something is
    playing and the likeliest is not `SWITCH_MARGIN` likelier than it."""
    if not probabilities:
        return None
    best, probability = max(probabilities.items(), key=lambda item: item[1])
    if playing is None:
        return best
    if best == playing or probability < probabilities.get(playing, 0.0) + SWITCH_MARGIN:
        return None
    return best


@dataclass
class AmbiencePicking:
    """Picking the ambience during calls, with the deployer's `judge`."""

    judge: AmbienceJudge
    timeout: float
    """How long, in seconds, the judge may take before the pick is given up."""
    clock: Clock = field(default=utc_now)

    async def pick(self, moment: AmbienceMoment, playing: AmbienceKind | None) -> AmbienceKind | None:
        """The kind to switch to, or None to keep what is playing."""
        log.info("picking the ambience for %s", moment.character.id)
        try:
            probabilities = await asyncio.wait_for(self.judge.judge_ambience(moment), self.timeout)
        except TimeoutError:
            log.info("ambience judge took over %ss; the ambience stays", self.timeout)
            return None
        except JudgeError:
            log.warning("ambience judge failed; the ambience stays", exc_info=True)
            return None
        kind = decide(probabilities, playing)
        ranked = sorted(probabilities.items(), key=lambda item: -item[1])
        log.info(
            "ambience for %s: %s -> %s",
            moment.character.id,
            " ".join(f"{k} {p:.2f}" for k, p in ranked),
            kind or f"keep {playing}",
        )
        return kind
