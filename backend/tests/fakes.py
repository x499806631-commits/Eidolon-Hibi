"""Test doubles for external services."""

import asyncio
import base64
import html
import hashlib
import io
import json
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast, get_args
import zipfile
from http import HTTPStatus
from urllib.parse import quote, unquote, urlencode

import httpx
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from pipecat.frames.frames import (
    Frame,
    ProposedUserStartedSpeakingFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
)
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

from eidolon.assembly import Backend, build
from eidolon.billing import bill
from eidolon.call import SONIOX_FOLLOW_ON_TIMEOUT, Hearing
from eidolon.config import (
    BailianRegion,
    ChatModelSettings,
    CloudflareJudgeSettings,
    JevSettings,
    JudgeSettings,
    LLMSettings,
    ReasoningEffort,
    Settings,
    SttKey,
    SttProvider,
)
from eidolon.embedding import Document, EmbeddingError
from eidolon.rerank import RerankError
from eidolon.emotion import _EMOTION_OPTIONS, _NOD_OPTIONS, Emotion, EmotionAnswer, Nod
from eidolon.identity import Identity, IdentityError, ProviderName
from eidolon.llm import (
    ChatLLM,
    ChatMessage,
    ChatModels,
    LLMError,
    Reasoning,
    Streamed,
    Tool,
    ToolCall,
)
from eidolon.llm.providers import check_chat_models
from eidolon.mirror import Mirror
from eidolon.own_keys import OwnKeyRejected
from eidolon.affinity_judge import LEVEL as AFFINITY_LEVEL
from eidolon.affinity_judge import MANIPULATION, OUT_OF_LINE
from eidolon.judge import SAID, JudgeError
from eidolon.photo_judge import PHOTO
from eidolon.promise_judge import KEPT
from eidolon.search_judge import ASKED

if TYPE_CHECKING:
    from eidolon.judge import JudgeModel
from eidolon.retrieval import Need

START = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class FakeClock:
    """A clock the tests move forward themselves."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class FakeRandom:
    """A random source giving the numbers it was given, in order: each must be less than
    what it is asked for, and asking once they are all given fails, so a test sees a draw
    taking more or fewer than it meant to."""

    def __init__(self, *numbers: int) -> None:
        self._numbers = list(numbers)

    def __call__(self, below: int) -> int:
        assert self._numbers, "the fake random source has no numbers left"
        number = self._numbers.pop(0)
        assert 0 <= number < below, f"{number} is not below {below}"
        return number


DEPLOYER_KEY = "test-key"
"""The deployer's chat model key in the tests' settings."""


HANG = object()
"""A chunk in a FakeLLM reply where the model stops sending but never finishes."""


def system_of(prompt: Sequence[ChatMessage]) -> str:
    """What the system messages `prompt` opens with tell the model, as one text: joined by
    a blank line, as the Chat Completions adapter sends them."""
    opening: list[str] = []
    for message in prompt:
        if message.role != "system":
            break
        opening.append(message.content)
    return "\n\n".join(opening)


def context_of(prompt: Sequence[ChatMessage]) -> str:
    """What changes from turn to turn that `prompt` tells the model: the system message
    just before what the user is saying now, its last user message."""
    last = max(i for i, m in enumerate(prompt) if m.role == "user")
    context = prompt[last - 1]
    assert context.role == "system", "no system message before what the user is saying"
    return context.content


class FakeLLM:
    """Records every prompt it receives, the tools offered with it, whether calling them
    was forbidden (`forbidden`) and the API key it came with (`keys`), and replies from a
    script.

    Each scripted reply is a list of text chunks, tool calls and reasoning, streamed in
    order, so tests can split the structured reply at awkward boundaries or have the model
    call a tool; a reply that is an LLMError is raised instead, as a failing service would. With
    a `delay`, it waits that many seconds before each chunk after the first, as a slow
    model would; at `HANG` it waits forever, and at an `asyncio.Event` till it is set.
    Unless reached through a FakeLLMService, it is the deployer's model. With `tokens`, each
    reply it finishes says it was billed that many input and output tokens, and, with a
    third, that many of the input read from the prompt cache, as a real service does;
    without, it says nothing.
    """

    def __init__(
        self,
        *replies: Sequence[Streamed | object] | LLMError,
        delay: float = 0,
        tokens: tuple[int, int] | tuple[int, int, int] | None = None,
    ) -> None:
        self._replies = [r if isinstance(r, LLMError) else list(r) for r in replies]
        self._delay = delay
        self._tokens = tokens
        self.prompts: list[list[ChatMessage]] = []
        self.tools: list[list[Tool]] = []
        self.forbidden: list[bool] = []
        self.keys: list[str] = []

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        return self.stream_with(DEPLOYER_KEY, messages, tools, forbid_calls=forbid_calls)

    async def stream_with(
        self,
        key: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        self.prompts.append(list(messages))
        self.tools.append(list(tools))
        self.forbidden.append(forbid_calls)
        self.keys.append(key)
        if not self._replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        reply = self._replies.pop(0)
        if isinstance(reply, LLMError):
            raise reply
        for i, chunk in enumerate(reply):
            if i and self._delay:
                await asyncio.sleep(self._delay)
            if chunk is HANG:
                await asyncio.Event().wait()
            if isinstance(chunk, asyncio.Event):
                await chunk.wait()
                continue
            assert isinstance(chunk, str | ToolCall | Reasoning)
            yield chunk
        if self._tokens is not None:
            bill("tokens", *self._tokens)

    async def aclose(self) -> None:
        pass


class FakeLLMService:
    """Stands in for the LLM services users bring their own keys to: every model opened on
    it replies from `llm`'s script, recording the key it came with there and the reasoning
    effort it was opened with (`efforts`). Records what was tried before saving
    (`checked`); a key in `rejects` fails the try with that message. The try is the real
    check of the chat model at each of its efforts, on models that pass it without using
    up the script, recording the effort each was tried at (`efforts_checked`); an effort in
    `refused_efforts` fails it with that message."""

    def __init__(self, llm: FakeLLM | None = None) -> None:
        self.llm = llm or FakeLLM()
        self.checked: list[ChatModelSettings] = []
        self.opened: list[LLMSettings] = []
        self.rejects: dict[str, str] = {}
        self.refused_efforts: dict[str, str] = {}
        self.efforts: list[ReasoningEffort | None] = []
        self.efforts_checked: list[ReasoningEffort | None] = []

    def open(self, settings: LLMSettings) -> ChatLLM:
        self.opened.append(settings)
        return _KeyedLLM(self, settings)

    async def check(self, settings: ChatModelSettings) -> None:
        self.checked.append(settings)
        if settings.text.api_key in self.rejects:
            raise LLMError(self.rejects[settings.text.api_key])
        models = ChatModels(_TriedLLM(self, settings.text), _TriedLLM(self, settings.call))
        await check_chat_models(models, settings)

    async def aclose(self) -> None:
        pass


class _KeyedLLM:
    def __init__(self, service: FakeLLMService, settings: LLMSettings) -> None:
        self._service = service
        self._settings = settings

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        self._service.efforts.append(self._settings.reasoning_effort)
        return self._service.llm.stream_with(
            self._settings.api_key, messages, tools, forbid_calls=forbid_calls
        )

    async def aclose(self) -> None:
        pass


class _TriedLLM:
    def __init__(self, service: FakeLLMService, settings: LLMSettings) -> None:
        self._service = service
        self._settings = settings

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        effort = self._settings.reasoning_effort
        if effort is not None and effort in self._service.refused_efforts:
            raise LLMError(self._service.refused_efforts[effort])
        self._service.efforts_checked.append(effort)
        return
        yield

    async def aclose(self) -> None:
        pass


class FakeServiceClients:
    """Stands in for making the deployer's service clients from their configuration (see
    `service_clients.ServiceClients`), `.env`'s as well as the admin console's: records
    the settings of every chat model it made (`opened`) and closed (`closed`), as text
    turns use it. Every chat model it makes, as text turns and calls use it, replies from
    `llm`'s script, recording the key it came with there (or is `llm`, if it is not a
    `FakeLLM`) and the reasoning effort it was made with (`efforts`), but passes the
    connectivity check without using up the script, recording the effort it was checked
    at (`efforts_checked`); a key in `rejects`, or an effort in `refused_efforts`, fails
    every request, the check too, with that message.

    Memory models it makes (`memory_opened`, closed in `closed` too) answer from
    `background`, recording the key each request came with (`memory_keys`). Judge models
    it makes (`judges_opened`, `judges_closed`) answer every question as `judge` does,
    recording the key each question came with there."""

    def __init__(self, llm: FakeLLM | None = None) -> None:
        self.llm = llm or FakeLLM()
        self.opened: list[LLMSettings] = []
        self.closed: list[LLMSettings] = []
        self.rejects: dict[str, str] = {}
        self.refused_efforts: dict[str, str] = {}
        self.efforts: list[ReasoningEffort | None] = []
        self.efforts_checked: list[ReasoningEffort | None] = []
        self.background = FakeBackgroundLLM()
        self.memory_opened: list[LLMSettings] = []
        self.memory_keys: list[str] = []
        self.judge = FakeJudgeModel()
        self.judges_opened: list[JudgeSettings] = []
        self.judges_closed: list[JudgeSettings] = []

    def chat_models(self, settings: ChatModelSettings) -> ChatModels:
        self.opened.append(settings.text)
        return ChatModels(
            _ConfiguredLLM(self, settings.text, self.closed),
            _ConfiguredLLM(self, settings.call, []),  # closed with the text turns' one
        )

    def memory_llm(self, settings: LLMSettings) -> ChatLLM:
        self.memory_opened.append(settings)
        return _ConfiguredLLM(self, settings, self.closed, memory=True)

    def judge_model(self, settings: JudgeSettings) -> "JudgeModel":
        self.judges_opened.append(settings)
        return _ConfiguredJudge(self, settings)


class _ConfiguredLLM:
    def __init__(
        self,
        clients: FakeServiceClients,
        settings: LLMSettings,
        closed: list[LLMSettings],
        memory: bool = False,
    ) -> None:
        self._clients = clients
        self._settings = settings
        self._closed = closed
        self._memory = memory

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        key, effort = self._settings.api_key, self._settings.reasoning_effort
        if key in self._clients.rejects:
            raise LLMError(self._clients.rejects[key])
        if effort is not None and effort in self._clients.refused_efforts:
            raise LLMError(self._clients.refused_efforts[effort])
        if any(t.name == "ping" for t in tools):  # the connectivity check
            if not self._memory:
                self._clients.efforts_checked.append(effort)
            return
        if self._memory:
            self._clients.memory_keys.append(key)
            async for item in self._clients.background.stream(messages, tools):
                yield item
            return
        self._clients.efforts.append(effort)
        llm = self._clients.llm
        stream = (
            llm.stream_with(key, messages, tools, forbid_calls=forbid_calls)
            if isinstance(llm, FakeLLM)
            else llm.stream(messages, tools, forbid_calls=forbid_calls)
        )
        async for item in stream:
            yield item

    async def aclose(self) -> None:
        self._closed.append(self._settings)


@dataclass(frozen=True)
class Judging:
    """A script for what the judge model makes of one turn's affinity: how likely each
    level is (each level the only one by default, `level`), whether it is out of line,
    whether it tries to dictate her affinity; or that the request fails."""

    levels: Mapping[int, float] = field(default_factory=lambda: {2: 1.0})
    out_of_line: bool = False
    manipulation: bool = False
    fails: bool = False

    def answers(self) -> dict[str, Any]:
        return {
            AFFINITY_LEVEL: {"probabilities": {str(n): p for n, p in self.levels.items()}},
            OUT_OF_LINE: {"noul": 1.0 if self.out_of_line else 0.0},
            MANIPULATION: {"noul": 1.0 if self.manipulation else 0.0},
        }


def level(n: int, **judging: bool) -> Judging:
    """A turn judged at level `n` for sure."""
    return Judging({n: 1.0}, **judging)


class FakeJudgeModel:
    """Stands in for the judge model's service: answers what each turn needs as the script
    `needs` says, her emotion in each segment as `feels` does; without them, the retrieval
    questions with the probabilities in `retrieval`, her emotion with those in `emotion`
    and the nod with neither. Answers each turn's affinity questions as the next of
    `judgings` says, as having no effect once they run out, recording what the user said
    (`affinity_asked`); and whether the user asks for a photo with the probability `photo`,
    recording what they said (`photo_asked`). Records the key each request came with and what
    it asked (`asked`: "turn", with or without affinity's questions and the photo's,
    "affinity" for those alone, with or without the photo's, "photo" for that alone,
    "emotion" or "check"), and everything it was told of each (`states`)."""

    def __init__(self) -> None:
        self.needs: FakeJudge | None = None
        self.feels: FakeJudge | None = None
        self.retrieval = {"needs_retrieval": 0.1, "recall": 0.1, "about_producer": 0.1}
        self.emotion = {"平静": 1.0}
        self.judgings: list[Judging] = []
        self.asked: list[tuple[str, str]] = []
        self.affinity_asked: list[str] = []
        self.photo = 0.0
        self.photo_asked: list[str] = []
        self.kept = 0.0
        self.kept_asked: list[str] = []
        self.asks_search = 0.0
        self.search_asked: list[str] = []
        self.states: list[dict[str, Any]] = []

    async def answer(
        self, key: str, state: dict[str, Any], questions: dict[str, dict]
    ) -> dict[str, Any]:
        kind = (
            "turn" if "recall" in questions
            else "affinity" if AFFINITY_LEVEL in questions
            else "photo" if PHOTO in questions
            else "kept" if KEPT in questions
            else "search" if ASKED in questions
            else "emotion" if "emotion" in questions
            else "check"
        )
        self.asked.append((key, kind))
        self.states.append(state)
        scripted: dict[str, Any] = {}
        if AFFINITY_LEVEL in questions:
            self.affinity_asked.append(state[SAID])
            judging = self.judgings.pop(0) if self.judgings else Judging()
            if judging.fails:
                raise JudgeError("fake judge model is down")
            scripted = judging.answers()
        if PHOTO in questions:
            self.photo_asked.append(state[SAID])
            scripted |= {PHOTO: {"noul": self.photo}}
        if KEPT in questions:
            self.kept_asked.append(state[SAID])
            scripted |= {KEPT: {"noul": self.kept}}
        if ASKED in questions:
            self.search_asked.append(state[SAID])
            scripted |= {ASKED: {"noul": self.asks_search}}
        if kind == "turn" and self.needs is not None:
            return await self.needs.need(state) | scripted
        if kind == "emotion" and self.feels is not None:
            return await self.feels.feel(state)
        return {
            name: {"noul": self.retrieval.get(name, 0.5)}
            if q.get("type") == "noul"
            else {"probabilities": self.emotion if name == "emotion" else {"都不是": 1.0}}
            for name, q in questions.items()
            if name not in scripted
        } | scripted


class _ConfiguredJudge:
    def __init__(self, clients: FakeServiceClients, settings: JudgeSettings) -> None:
        self._clients = clients
        self._settings = settings

    async def ask(self, state: dict[str, Any], questions: dict[str, dict]) -> dict[str, Any]:
        key = judge_key(self._settings)
        if key in self._clients.rejects:
            raise JudgeError(self._clients.rejects[key])
        return await self._clients.judge.answer(key, state, questions)

    async def aclose(self) -> None:
        self._clients.judges_closed.append(self._settings)


def judge_key(settings: JudgeSettings) -> str:
    return settings.api_token if isinstance(settings, CloudflareJudgeSettings) else settings.api_key


FAKE_JUDGE = JevSettings("fake-judge-key")
"""The judge model `.env` has when a test hands one a script: Jev, whose thresholds are
the ones every model not calibrated judges by."""


def build_faked(
    settings: Settings,
    llm: FakeLLM | None = None,
    *,
    clients: "FakeServiceClients | None" = None,
    background_llm: "FakeBackgroundLLM | None" = None,
    retrieval_judge: "FakeJudge | None" = None,
    emotion_judge: "FakeJudge | None" = None,
    **services: Any,
) -> Backend:
    """`build`, with the deployer's models made by `clients` (new ones by default):
    playing the characters from `llm`'s script, writing down memories from
    `background_llm`'s, judging what turns need from `retrieval_judge`'s and her emotions
    from `emotion_judge`'s, either judging both if only one is given. With a judge
    script, `.env` has the judge model `FAKE_JUDGE` unless it has one. Any other keyword
    is an outside service passed on to `build`."""
    clients = clients or FakeServiceClients()
    if llm is not None:
        clients.llm = llm
    if background_llm is not None:
        clients.background = background_llm
    if retrieval_judge is not None or emotion_judge is not None:
        clients.judge.needs = retrieval_judge or emotion_judge
        clients.judge.feels = emotion_judge or retrieval_judge
        if settings.judge is None:
            settings = replace(settings, judge=FAKE_JUDGE)
    return build(settings, clients=clients, **services)


class FakeBackgroundLLM:
    """Stands in for the background model: answers each request with the next scripted
    reply, and with no memories once the script runs out; a reply that is an exception
    is raised instead, one that is a function is called with the prompt, as one text, for
    the answer. Records every prompt it receives. With a `gate`, it answers only once the
    gate is open, like a slow model."""

    NOTHING = '{"memories": []}'

    def __init__(
        self, *replies: str | Exception | Callable[[str], str], gate: asyncio.Event | None = None
    ) -> None:
        self._replies = list(replies)
        self._gate = gate
        self.prompts: list[list[ChatMessage]] = []

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[Tool] = (),
        *,
        forbid_calls: bool = False,
    ) -> AsyncIterator[Streamed]:
        self.prompts.append(list(messages))
        reply = self._replies.pop(0) if self._replies else self.NOTHING
        if callable(reply):
            reply = reply("\n".join(m.content for m in messages))
        if self._gate is not None:
            await self._gate.wait()
        if isinstance(reply, Exception):
            raise reply
        for i in range(0, len(reply), 7):  # in chunks, as a streaming model would
            yield reply[i : i + 7]

    async def aclose(self) -> None:
        pass


OpenAIAnswer = Literal["b64_json", "data_url", "url", "not_base64", "chat"]
"""How the fake image service answers in OpenAI's image format: the picture as bare
base64, as a `data:` URL, as a URL to download it from, as `b64_json` that is not base64,
or in the shape of a chat completion, as a relay of the other kind would."""


class FakeImageService:
    """Stands in for image generation in NovelAI's own format (`POST /ai/generate-image`)
    and in OpenAI's (`POST /images/generations`): records each request, with the key it
    came on (`requests`: `key`, `url`, `body`), and answers with one PNG of the size asked
    for, carrying the prompt in its metadata as NovelAI's pictures do (`METADATA` in it):
    in NovelAI's format in a ZIP, in OpenAI's as `openai_answer` has it, a URL to it
    downloaded from `DOWNLOAD` (each download in `downloads`, failing with
    `download_status` if set). With a `gate`, it answers
    only once the gate is open, as a service taking its time. It can answer amiss instead:
    with an HTTP error (`status`, saying `error`), a picture of another `size`, or what is
    not a ZIP (`garbled`)."""

    METADATA = "fake NovelAI metadata"
    DOWNLOAD = "https://files.image.test/picture.png"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.downloads: list[str] = []
        self.gate: asyncio.Event | None = None
        self.status: int | None = None
        self.error = "fake image service error"
        self.download_status: int | None = None
        self.size: tuple[int, int] | None = None
        self.garbled = False
        self.openai_answer: OpenAIAnswer = "b64_json"
        self._pictures: dict[str, bytes] = {}

    def transport(self) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == self.DOWNLOAD:
            self.downloads.append(str(request.url))
            if self.download_status is not None:
                return httpx.Response(self.download_status)
            return httpx.Response(200, content=self._pictures.pop(self.DOWNLOAD))
        openai = request.url.path.endswith("/images/generations")
        assert request.method == "POST" and (
            openai or request.url.path.endswith("/ai/generate-image")
        )
        body = json.loads(request.content)
        self.requests.append({
            "key": request.headers["authorization"].removeprefix("Bearer "),
            "url": str(request.url),
            "body": body,
        })
        if self.gate is not None:
            await self.gate.wait()
        if self.status is not None:
            return httpx.Response(self.status, json={"error": self.error})
        if self.garbled:
            return httpx.Response(200, content=b"<html>not a ZIP</html>")
        if openai:
            asked_width, asked_height = (int(n) for n in body["size"].split("x"))
            width, height = self.size or (asked_width, asked_height)
            picture = png(width, height, f"{self.METADATA}: {body['prompt']}")
            return self._openai(picture)
        parameters = body["parameters"]
        width, height = self.size or (parameters["width"], parameters["height"])
        picture = png(width, height, f"{self.METADATA}: {body['input']}")
        return httpx.Response(200, content=zipped({"image_0.png": picture}))

    def _openai(self, picture: bytes) -> httpx.Response:
        encoded = base64.b64encode(picture).decode()
        match self.openai_answer:
            case "b64_json":
                data = {"b64_json": encoded}
            case "data_url":
                data = {"b64_json": f"data:image/png;base64,{encoded}"}
            case "not_base64":
                data = {"b64_json": "not base64!"}
            case "url":
                self._pictures[self.DOWNLOAD] = picture
                data = {"url": self.DOWNLOAD}
            case "chat":
                content = f"![image](data:image/png;base64,{encoded})"
                return httpx.Response(200, json={
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                })
        return httpx.Response(200, json={"created": 0, "data": [data]})


def png(width: int, height: int, comment: str = "") -> bytes:
    """A PNG of that size, carrying `comment` in its metadata."""
    info = PngInfo()
    info.add_text("Comment", comment)
    out = io.BytesIO()
    Image.new("RGB", (width, height), (250, 200, 210)).save(out, "PNG", pnginfo=info)
    return out.getvalue()


def zipped(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


DEPLOYER_SONIOX_KEY = "deployer-soniox-key"
DEPLOYER_FISH_KEY = "deployer-fish-key"
"""The deployer's speech keys in the tests' settings."""


class FakeSTT(STTService):
    """Stands in for speech recognition in one call, on `key`: ignores the microphone and
    hears what the test says the user said, the way Soniox reports an utterance."""

    def __init__(self, key: SttKey) -> None:
        super().__init__(ttfs_p99_latency=0.1, settings=STTSettings(model=None, language=None))
        self.key = key
        self.api_key = key.api_key

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        yield None

    async def hear(self, text: str) -> None:
        """The user said `text`: an utterance starts, is transcribed, and ends."""
        await self.broadcast_frame(ProposedUserStartedSpeakingFrame)
        await self.push_frame(TranscriptionFrame(text, "user", time_now_iso8601(), finalized=True))
        await self.broadcast_frame(ProposedUserStoppedSpeakingFrame)

    async def start_talking(self) -> None:
        """The user starts saying something, which never comes to any words."""
        await self.broadcast_frame(ProposedUserStartedSpeakingFrame)

    async def break_down(self, error: str = "fake speech recognition is down") -> None:
        """Fail as a rejected API key would: for good, saying `error`."""
        await self.push_error(error, force_treat_as_permanent=True)


class FakeTTS(TTSService):
    """Stands in for Fish Audio in one call, on `api_key` with `model`: records what it is
    asked to voice, and in which voice, and says a moment of silence for each."""

    def __init__(self, api_key: str, model: str, voice_id: str, sample_rate: int) -> None:
        super().__init__(
            sample_rate=sample_rate, settings=TTSSettings(model=model, voice=None, language=None)
        )
        self.api_key = api_key
        self.model = model
        self.voice_id = voice_id
        self.voiced: list[str] = []

    async def break_down(self, error: str = "fake speech synthesis is down") -> None:
        """Fail as a rejected API key would: for good, saying `error`."""
        await self.push_error(error, force_treat_as_permanent=True)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        self.voiced.append(text)
        samples = self.sample_rate // 10
        yield TTSAudioRawFrame(b"\0\0" * samples, self.sample_rate, 1, context_id=context_id)


class FakeSpeech:
    """Stands in for speech recognition and Fish Audio: the speech services of each call
    started, in order, each with the key it was made with, recognition waiting
    `follow_on_timeout` for follow-ons (Soniox's, unless set). Set `down` to have the
    services fail to be made at all. Records the keys tried before saving, by the service
    they are for (`checked`: "tts", "soniox" or "bailian"), and the model each key for
    Fish Audio was tried with (`tts_models`); a key in `rejects` fails its
    try with that message. A DashScope key works in its region in `regions`, Beijing if
    not there. Records, for each service made or key tried, whether it was on a user's own
    key (`own`: ("stt" | "tts" | "check_stt" | "check_tts", own)), and each word voiced as
    the service status tries Fish Audio, by key, model and voice (`voiced`), failing on a
    key in `rejects` as well."""

    def __init__(self) -> None:
        self.own: list[tuple[str, bool]] = []
        self.follow_on_timeout = SONIOX_FOLLOW_ON_TIMEOUT
        self.stts: list[FakeSTT] = []
        self.ttss: list[FakeTTS] = []
        self.down = False
        self.checked: list[tuple[str, str]] = []
        self.tts_models: list[str] = []
        self.rejects: dict[str, str] = {}
        self.regions: dict[str, BailianRegion] = {}
        self.voiced: list[tuple[str, str, str]] = []
        self.local_voices: set[str] = set()
        """The characters with a voice on the GPT-SoVITS server."""
        self.gpt_sovits_down = False
        self.read: list[tuple[str, ...]] = []
        """What was read aloud: ("fish", model, voice id, text) or ("gpt_sovits", url, id, text)."""

    def stt(self, key: SttKey, *, own: bool) -> Hearing:
        self.own.append(("stt", own))
        if self.down:
            raise RuntimeError("fake speech services are down")
        self.stts.append(FakeSTT(key))
        return Hearing(self.stts[-1], self.follow_on_timeout)

    def tts(
        self, api_key: str, model: str, voice_id: str, sample_rate: int, *, own: bool
    ) -> FakeTTS:
        self.own.append(("tts", own))
        self.ttss.append(FakeTTS(api_key, model, voice_id, sample_rate))
        return self.ttss[-1]

    async def check_stt(self, provider: SttProvider, api_key: str, *, own: bool) -> SttKey:
        self.own.append(("check_stt", own))
        self._try(provider, api_key)
        if provider == "bailian":
            return SttKey(provider, api_key, self.regions.get(api_key, "beijing"))
        return SttKey(provider, api_key)

    async def check_tts(self, api_key: str, model: str, *, own: bool) -> None:
        self.own.append(("check_tts", own))
        self.tts_models.append(model)
        self._try("tts", api_key)

    async def voice_a_word(self, api_key: str, model: str, voice_id: str) -> None:
        self.voiced.append((api_key, model, voice_id))
        if api_key in self.rejects:
            raise RuntimeError(self.rejects[api_key])

    def has_local_voice(self, character_id: str) -> bool:
        return character_id in self.local_voices

    def gpt_sovits_tts(self, base_url: str, character_id: str, sample_rate: int) -> FakeTTS:
        self.own.append(("tts", False))
        self.ttss.append(FakeTTS(base_url, "gpt-sovits", character_id, sample_rate))
        return self.ttss[-1]

    async def check_gpt_sovits(self, base_url: str) -> None:
        self.checked.append(("gpt_sovits", base_url))
        if self.gpt_sovits_down:
            raise OwnKeyRejected(f"连不上 GPT-SoVITS（{base_url}）")

    async def fish_read(self, api_key: str, model: str, voice_id: str, text: str) -> bytes:
        self.read.append(("fish", model, voice_id, text))
        if api_key in self.rejects:
            raise RuntimeError(self.rejects[api_key])
        return f"mp3:{text}".encode()

    async def gpt_sovits_read(self, base_url: str, character_id: str, text: str) -> bytes:
        self.read.append(("gpt_sovits", base_url, character_id, text))
        if self.gpt_sovits_down:
            raise RuntimeError("down")
        return f"wav:{text}".encode()

    def _try(self, service: str, api_key: str) -> None:
        self.checked.append((service, api_key))
        if api_key in self.rejects:
            raise OwnKeyRejected(self.rejects[api_key])


class FakeFishAudio:
    """Stands in for Fish Audio's text to speech over HTTP (`POST /v1/tts`, as the greetings
    are voiced): records each request, with the key and model it came on, and answers with
    audio naming what it said in which voice. A voice in `refuses` is answered with that
    status, as Fish Audio answers a key without credit (402)."""

    HOST = "api.fish.audio"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.refuses: dict[str, int] = {}

    @staticmethod
    def audio(text: str, voice_id: str) -> bytes:
        return f"mp3: {text} ({voice_id})".encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/v1/tts", request.url
        assert request.headers["content-type"] == "application/json"
        body = json.loads(request.content)
        self.requests.append({
            "key": request.headers["authorization"].removeprefix("Bearer "),
            "model": request.headers.get("model"),
            **body,
        })
        if status := self.refuses.get(body.get("reference_id")):
            return httpx.Response(status, json={"status": status, "message": "Insufficient balance"})
        return httpx.Response(
            200, content=self.audio(body["text"], body["reference_id"]), headers={"content-type": "audio/mpeg"}
        )


@dataclass
class DashScopeTask:
    """A recognition task DashScope ran: in which region, the `run-task` that started it,
    each packet of audio it was sent, and whether it was told to finish."""

    region: BailianRegion
    run_task: dict
    audio: list[bytes] = field(default_factory=list)
    finished: bool = False


class FakeDashScope:
    """Stands in for DashScope's realtime speech recognition (阿里云百炼) in both its
    regions, over a real WebSocket on localhost: a key in `keys` works only in its region,
    and is refused anywhere else at the handshake (HTTP 401), as DashScope does. A task
    with a model in `not_enabled` fails before it starts. Once a task starts it is sent the
    `sentences` given, each as a `result-generated`, and then fails with `failure`
    (`error_code`, `error_message`), if set; told to finish, it finishes. Set `down` to
    have every handshake fail (HTTP 503). Records every handshake's region (`handshakes`)
    and every task started (`tasks`)."""

    def __init__(self) -> None:
        self.keys: dict[str, BailianRegion] = {}
        self.not_enabled: set[str] = set()
        self.sentences: list[dict] = []
        self.failure: tuple[str, str] | None = None
        self.down = False
        self.handshakes: list[BailianRegion] = []
        self.tasks: list[DashScopeTask] = []
        self.urls: dict[BailianRegion, str] = {}
        self._server: Server | None = None

    async def start(self) -> None:
        self._server = await serve(
            self._serve, "127.0.0.1", 0, process_request=self._handshake
        )
        port = self._server.sockets[0].getsockname()[1]
        self.urls = {r: f"ws://127.0.0.1:{port}/{r}" for r in get_args(BailianRegion)}

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def _handshake(self, connection: ServerConnection, request: Request) -> Response | None:
        region = _region(request.path)
        self.handshakes.append(region)
        if self.down:
            return connection.respond(HTTPStatus.SERVICE_UNAVAILABLE, "Service Unavailable\n")
        key = (request.headers.get("Authorization") or "").removeprefix("bearer ")
        if self.keys.get(key) != region:
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    async def _serve(self, connection: ServerConnection) -> None:
        assert connection.request is not None
        region = _region(connection.request.path)
        task: DashScopeTask | None = None

        async def send(event: str, payload: dict | None = None, **header: str) -> None:
            assert task is not None
            task_id = task.run_task["header"]["task_id"]
            message = {
                "header": {"task_id": task_id, "event": event, "attributes": {}, **header},
                "payload": payload or {},
            }
            await connection.send(json.dumps(message, ensure_ascii=False))

        try:
            async for message in connection:
                if isinstance(message, bytes):
                    assert task is not None
                    task.audio.append(message)
                    continue
                request = json.loads(message)
                action = request["header"]["action"]
                if action == "run-task":
                    task = DashScopeTask(region, request)
                    if request["payload"]["model"] in self.not_enabled:
                        await send(
                            "task-failed", error_code="ModelNotFound",
                            error_message="Model not found (or not enabled)",
                        )
                        return
                    self.tasks.append(task)
                    await send("task-started")
                    for sentence in self.sentences:
                        await send("result-generated", {"output": {"sentence": sentence}})
                    if self.failure is not None:
                        code, why = self.failure
                        await send("task-failed", error_code=code, error_message=why)
                        return
                elif action == "finish-task":
                    assert task is not None
                    task.finished = True
                    await send("task-finished", {"output": {}})
                    return
        except ConnectionClosed:
            pass


def _region(path: str) -> BailianRegion:
    """The region a fake DashScope URL is of."""
    return cast(BailianRegion, path.strip("/"))


def sentence(text: str, end: bool = False, **fields: object) -> dict:
    """A sentence of DashScope's `result-generated`, as far as the recognition has heard it."""
    return {"sentence_id": 1, "text": text, "sentence_end": end, "words": []} | fields


class FakeIdentityProvider:
    """Stands in for Google or GitHub: the test says which identity the next sign-in there
    is (`will_identify`). Records the callback URL of every sign-in; set `down` to have it
    fail as an unreachable provider would."""

    AUTHORIZE_URL = "https://provider.invalid/authorize"

    def __init__(self, name: ProviderName) -> None:
        self.name: ProviderName = name
        self.callbacks: list[str] = []
        self.down = False
        self._identities: dict[str, Identity] = {}

    def will_identify(self, identity: Identity) -> str:
        """The code the provider will send back for this identity."""
        code = secrets.token_hex(4)
        self._identities[code] = identity
        return code

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"provider": self.name, "state": state, "redirect_uri": redirect_uri})
        return f"{self.AUTHORIZE_URL}?{query}"

    async def identify(self, code: str, redirect_uri: str) -> Identity:
        self.callbacks.append(redirect_uri)
        if self.down:
            raise IdentityError(f"fake {self.name} is down")
        if code not in self._identities:
            raise IdentityError(f"fake {self.name} does not know the code {code}")
        return self._identities.pop(code)

    async def aclose(self) -> None:
        pass


class FakeEmbedder:
    """Embeds text as counts of a few keywords, so the nearest passages are the ones
    sharing the query's keywords. Records every document (its text, and its title in
    `titles`) and query it embeds; set `down` to have it fail as an unreachable service
    would, or set `documents_down` to have it fail embedding documents only. A query among
    `hanging` is never answered: whoever asked gives up on it, which
    is recorded (`abandoned`)."""

    KEYWORDS = (
        "鳩", "公園", "優勝", "長崎", "ちゃんぽん", "写真", "負け", "猫", "北海道",
        "destination", "ドーム", "WING",
    )

    def __init__(self, model: str = "fake-embedding") -> None:
        self.model = model
        self.documents: list[str] = []
        self.titles: list[str | None] = []
        self.queries: list[str] = []
        self.down = False
        self.documents_down = False
        self.hanging: set[str] = set()
        self.abandoned: list[str] = []

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
        if self.down or self.documents_down:
            raise EmbeddingError("fake embedding service is down")
        self.documents.extend(d.text for d in documents)
        self.titles.extend(d.title for d in documents)
        return [self._vector(d.text) for d in documents]

    async def embed_query(self, text: str) -> list[float]:
        if self.down:
            raise EmbeddingError("fake embedding service is down")
        self.queries.append(text)
        if text in self.hanging:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.abandoned.append(text)
                raise
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        return [0.01] + [float(text.count(k)) for k in self.KEYWORDS]


class FakeJudge:
    """A script for the judge model (`FakeJudgeModel.needs` and `.feels`): every turn
    needs `answer`, or the judge model fails if it is None; it answers the yes-or-no
    questions with probabilities of 1 or 0, which any thresholds read as `answer`.
    Records the character's name and the user's words of each turn asked about (`asked`).
    Once told to `hold`, it answers no turn (or none of those where the user said one of
    what it was told) until told to `let_go`, `holding` set while it is held up answering.

    Asked about segments' emotions, it answers each in turn from `emotions`: an answer,
    None to fail, or `HANG` never to answer; calm and neither nodding nor not once they
    run out. Records what it was told of each segment (`segments`)."""

    def __init__(
        self,
        answer: Need | None = "references",
        emotions: Sequence[EmotionAnswer | object | None] = (),
    ) -> None:
        self.answer: Need | None = answer
        self.asked: list[tuple[str, str]] = []  # (character's name, text)
        self.emotions = list(emotions)
        self.segments: list[AskedSegment] = []
        self.holding = asyncio.Event()
        self._held: asyncio.Event | None = None
        self._holding_for: set[str] = set()

    def hold(self, *said: str) -> None:
        self._held = asyncio.Event()
        self._holding_for = set(said)

    def let_go(self) -> None:
        if self._held is not None:
            self._held.set()
            self._held = None
        self.holding.clear()

    async def need(self, state: dict[str, Any]) -> dict[str, Any]:
        self.asked.append((state["角色"], state[SAID]))
        held = self._held
        if held is not None and (not self._holding_for or state[SAID] in self._holding_for):
            self.holding.set()
            await held.wait()
        if self.answer is None:
            raise JudgeError("fake judge model is down")
        yes = {"references": "needs_retrieval", "recall": "recall", "memories": "about_producer"}
        return {
            question: {"noul": 1.0 if yes.get(self.answer) == question else 0.0}
            for question in ("needs_retrieval", "recall", "about_producer")
        }

    async def feel(self, state: dict[str, Any]) -> dict[str, Any]:
        self.segments.append(AskedSegment(
            state["角色"], state["制作人这句话"], state["这条回复前面几段"], state["这一段台词"],
            state["这一段的中文字幕"],
        ))
        answer = self.emotions.pop(0) if self.emotions else feeling("calm")
        if answer is None:
            raise JudgeError("fake judge model is down")
        if answer is HANG:
            await asyncio.Event().wait()
        assert isinstance(answer, EmotionAnswer)
        return {
            "emotion": {"probabilities": _named(answer.emotions, _EMOTION_OPTIONS)},
            "nod": {"probabilities": _named(answer.nods, _NOD_OPTIONS)},
        }


@dataclass(frozen=True)
class AskedSegment:
    """What the judge model was told of a segment: the character's name, what the user
    said, the reply's segments before it (each its Japanese and, in brackets, its
    subtitle; a note if there are none), and the segment's Japanese and subtitle."""

    character: str
    user_said: str
    before: list[str] | str
    ja: str
    zh: str


def _named(probabilities: Mapping[Any, float], options: Mapping[str, tuple[Any, str]]) -> dict:
    """`probabilities` by the names the judge model gives the options."""
    return {name: probabilities[o] for name, (o, _) in options.items() if o in probabilities}


def feeling(
    emotion: Emotion,
    probability: float = 0.9,
    nod: Nod | None = None,
    nod_probability: float = 0.9,
) -> EmotionAnswer:
    """An answer about a segment's emotion: `emotion` with `probability`, and `nod` with
    `nod_probability`, the rest shared out among the other options."""
    others = [e for e in get_args(Emotion) if e != emotion]
    emotions = {emotion: probability, **{e: (1 - probability) / len(others) for e in others}}
    no_nod = (1 - nod_probability) / 2
    nods: dict[Nod | None, float] = {"yes": no_nod, "no": no_nod, None: no_nod}
    nods[nod] = nod_probability
    return EmotionAnswer(emotions, nods)


class FakeReranker:
    """Ranks documents containing `prefer` first (scoring them 0.9), otherwise keeping the
    order it was given (scoring the rest `others`). Records every call; set `down` to have
    it fail them as a service still failing when asked again would."""

    def __init__(self, prefer: str = "", others: float = 0.5) -> None:
        self.prefer = prefer
        self.others = others
        self.calls: list[tuple[str, list[str]]] = []
        self.down = False

    async def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        self.calls.append((query, list(documents)))
        if self.down:
            raise RerankError("fake rerank service is down")
        order = sorted(range(len(documents)), key=lambda i: self.prefer not in documents[i])
        return [(i, 0.9 if self.prefer in documents[i] else self.others) for i in order[:top_n]]


class KeywordReranker:
    """Scores each document by the first of its keywords it contains, others `rest`."""

    model = "fake-reranker"

    def __init__(self, scores: dict[str, float], rest: float = 0.05) -> None:
        self.scores = scores
        self.rest = rest

    async def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        scored = [
            (i, next((s for k, s in self.scores.items() if k in d), self.rest))
            for i, d in enumerate(documents)
        ]
        return sorted(scored, key=lambda r: -r[1])[:top_n]


class FakeShinyColorsDB:
    """Stands in for the ShinyColorsDB API, the viewer that mirrors the game's scripts and
    the idols' albums, answering as they do: idols and their cards, each card's episodes,
    albums, and scripts; scripts and albums with an ETag, 304 to a request for a copy that
    is still current and 404 for one it does not have. Records every request, and which
    scripts (and albums, as `album/NN`) it sent in full. Set `down`
    to have it unreachable, or `down_after` to have it go down after that many requests."""

    def __init__(self) -> None:
        self.idols: dict[int, list[dict]] = {}
        self.events: dict[str, list[dict]] = {}
        self.scripts: dict[str, bytes] = {}
        self.albums: dict[int, dict] = {}
        self.requests: list[httpx.Request] = []
        self.sent: list[str] = []
        self.down = False
        self.down_after: int | None = None

    def add_card(
        self, idol: int, uuid: str, name: str, type: str, episodes: Sequence[tuple[int, str]] = ()
    ) -> None:
        self.idols.setdefault(idol, []).append(
            {"cardUuid": uuid, "cardName": name, "cardType": type, "cardHash": "v1", "lastModified": "2026-09-01"}
        )
        title = "eventTitle" if type.startswith("P_") else "eventName"
        self.events[uuid] = [{"eventId": event_id, title: t} for event_id, t in episodes]

    def update_card(self, uuid: str) -> None:
        """The card's data changes, as when the game revises it."""
        for card in (c for cards in self.idols.values() for c in cards if c["cardUuid"] == uuid):
            card["cardHash"] += "+"

    def add_album(
        self, idol: int, sections: dict[str, str], entries: Sequence[tuple[str, str, str, str]]
    ) -> None:
        """The idol's album: its sections (album type → title), and its entries, each
        `(category, communication id, title, album type)`."""
        self.albums[idol] = {
            "id": str(idol),
            "albumCommunicationTitles": [
                {"albumType": album_type, "id": str(1000 + n), "title": title}
                for n, (album_type, title) in enumerate(sections.items(), 1)
            ],
            "communications": [
                {"albumType": album_type, "communicationCategory": category,
                 "communicationId": number, "title": title, "isReleased": True}
                for category, number, title, album_type in entries
            ],
        }

    def add_script(self, script: str, frames: object) -> None:
        self.scripts[script] = json.dumps(frames, ensure_ascii=False).encode()

    def mirror(self) -> Mirror:
        return Mirror(httpx.AsyncClient(transport=httpx.MockTransport(self._handle)), interval=0)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down or (self.down_after is not None and len(self.requests) > self.down_after):
            raise httpx.ConnectError("connection refused")
        path, params = request.url.path, request.url.params
        if request.url.host == "api.shinycolors.moe":
            if path == "/info/idolList":
                return httpx.Response(200, json=[{"idolId": i} for i in self.idols])
            if path == "/info/idolInfo":
                return httpx.Response(200, json={"cardLists": self.idols.get(int(params["idolId"]), [])})
            if path in ("/info/pCardInfo", "/info/sCardInfo"):
                key = "cardIdolEvents" if path == "/info/pCardInfo" else "cardSupportEvents"
                return httpx.Response(200, json={key: self.events[params["cardId"]]})
            return httpx.Response(404)
        if request.url.host == "cf-static.shinycolors.moe":
            idol = int(path.removeprefix("/albums/album/").removesuffix(".json"))
            album = self.albums.get(idol)
            body = None if album is None else json.dumps(album, ensure_ascii=False).encode()
            return self._send(request, f"album/{idol:02d}", body)
        script = path.removeprefix("/json/").removesuffix(".json")
        return self._send(request, script, self.scripts.get(script))

    def _send(self, request: httpx.Request, name: str, body: bytes | None) -> httpx.Response:
        if body is None:
            return httpx.Response(404, text="<html>Not Found</html>")
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        self.sent.append(name)
        return httpx.Response(200, content=body, headers={"ETag": etag})


@dataclass
class LantisRelease:
    """A release on Lantis's Shiny Colors site, as its page shows it."""

    slug: str
    """The page's path segment, e.g. laca-25301."""
    title: str
    date: str
    """The 発売日 line as the page writes it: 2026/9/16."""
    catalog: str
    artist: str
    """The アーティスト line."""
    index: list[str]
    """The track list, a paragraph each, its lines as the page writes them, e.g.
    ["1．ヒカリのdestination\n歌：イルミネーションスターズ\n作詞：…"]."""
    artist_members: str | None = None
    """The line of idols under the アーティスト line, if the page has one."""
    banner_date: str | None = None
    """The date over the title (2026.09.16); by default the 発売日's."""


@dataclass
class FujiDisc:
    """A disc in ふじわらはじめ's database: its name, and its tracks, each a name, its
    units (name and members), its idols and its flags (orig, gamever, offvocal)."""

    tax_id: int
    name: str
    tracks: list[tuple[str, list[tuple[str, list[str]]], list[str], list[str]]]


@dataclass
class CmsLive:
    """An article in the idolmaster portal CMS's LIVE-EVENT category, with the fields
    the import reads, as the API writes them."""

    id: str
    title: str
    brands: list[str]
    """Brand codes: SHINYCOLORS, SIDEM, …; a joint live has several."""
    dspdate: str
    """The display text of its days: 「2027年6月5日(土)、6日(日)」, a line each."""
    place: str
    """Its venue, or a tour's, a line each headed by the city: 「名古屋：日本ガイシホール」."""
    url: str | None = None
    """Its page, `event_url`; None for a `detail_page` article, which has `url_name`."""
    url_name: str | None = None
    article_type: str = "url_link"


@dataclass
class OldSiteEvent:
    """An event on the old official site's page of its year."""

    name: str
    dates: str
    """The 日時 cell's text, e.g. 「2019年3月9日(土) 開場17:00」."""
    place: str
    link: str | None = None


@dataclass
class WikiLive:
    """A live on wikiwiki's live pages, rendered as the wiki renders it."""

    title: str
    """The heading, with a prefix like 「(開催中止) 」 if the wiki gives one."""
    dates: list[str]
    """The 開催日時 lines: 「2027年6月5日(土) 16:00開場」."""
    performers: list[tuple[str | None, list[str]]]
    """Its performers: each day's heading (「2027年6月5日(土)」, or None for a list
    without days) and its lines as HTML: 「関根 瞳（櫻木 真乃役）/ …」, a unit's name,
    a struck performer made by `struck`."""
    page: str = "ライブイベント情報"
    """The page it is on: the main one, or a yearly log 「ライブイベント情報/開催情報log01」."""
    name: str | None = None
    """Its full name (公演名), if not the heading's."""


def struck(performer: str, note: str, news: str | None = None) -> str:
    """A performer the wiki strikes out, with a footnote: `note` and a link to `news`."""
    link = f' <a class="ext" href="{news}" rel="nofollow">{news}</a>' if news else ""
    tooltip = html.escape(f"<p>{note}{link}</p>", quote=True)
    return (
        f"<del>{performer}</del> "
        f'<a id="notetext_1"  class="note_super tooltip" data-tooltip-content="{tooltip}">*1</a>'
    )


@dataclass
class ImasDbShow:
    """One show's setlist on an imas-db live page."""

    heading: str
    """Its heading: 「セットリスト」, 「DAY1 セットリスト」, 「昼公演」, 「公演内容(全公演共通)」."""
    rows: list[tuple[str, str, str]]
    """Each row's number (empty for an MC), its title cell and its performers cell, as HTML
    (`imas_idol`, `imas_unit`, … and plain text like 「全員」)."""
    date: str | None = None
    """A date line in the show's own section, as M@STER EXPO's page has, if any."""


@dataclass
class ImasDbPage:
    """A page of imas-db's live records (`song/event/<file>`)."""

    file: str
    title: str
    date: str | None
    """The line under the tabs: 「2022/04/23(土) 幕張メッセ, 開場15:00/開演17:00」; None
    for a page without one."""
    shows: list[ImasDbShow]
    cast: str = ""
    """The 出演 list, as HTML: a performer followed by 「<small class="notes">※</small>」
    stood only on the songs that name her."""
    schedule: list[tuple[str, str]] = field(default_factory=list)
    """The 公演概要 table, if the page has one: each show's label and date line."""


@dataclass
class ImasDbLive:
    """A live on imas-db's index of live records."""

    name: str
    brands: str
    """Its brand ids, as the index writes them: 「8」 for Shiny Colors', 「1,4,5,6,8,9」 for a
    joint live."""
    pages: list[tuple[str, ImasDbPage, str]]
    """Its pages: the link's text, the page, and the date the index writes by it
    (「2022/04/23(土)」)."""


def imas_idol(name: str, cv: str, brand: str = "8", old: bool = False) -> str:
    """An idol in imas-db's markup: named by her CV on older pages, by herself on newer."""
    if old:
        return f'<span class="idol_sc_x" title="{name}(CV:{cv})">{cv}</span>'
    return (
        f'<span class="idol-name" data-brand-id="{brand}" data-character-id="1" '
        f'data-person-id="1" data-group-id="101" title="{name}(CV:{cv})">{name}</span>'
    )


def imas_unit(name: str, brand: str = "8", old: bool = False) -> str:
    if old:
        return f'<span class="idol_sc_unit01">{name}</span>'
    return f'<span class="idol-name" data-brand-id="{brand}" data-group-id="101">{name}</span>'


def imas_brand(name: str, brand: str) -> str:
    """A whole brand as one performer: シャイニーカラーズ is brand 8."""
    return f'<span class="idol-name" data-brand-id="{brand}">{name}</span>'


def imas_note(text: str) -> str:
    return f'<small class="notes">{text}</small>'


DANCE_ONLY = '<span class="notes" title="ダンスパフォーマンスのみ">(※)</span>'


def imas_badge(label: str, brand: str, slug: str) -> str:
    """The brand of the song, after its title at a joint live."""
    return (
        f'<span class="visually-hidden">(</span><small class="badge small bg-imas-brand-{slug}" '
        f'title="{brand}">{label}</small><span class="visually-hidden">)</span>'
    )


class FakeWorldSites:
    """Stands in for the sites `eidolon world sync` imports from, answering as they do:
    im@sparql's SPARQL endpoint (told apart by what each query asks for), the official
    site's idol index and unit pages, Lantis's Shiny Colors site (the discography and the
    releases' pages), ふじわらはじめ's API (its song list, songs and discs), the
    idolmaster portal's CMS API (its token and the LIVE-EVENT category), the old official
    site's event pages a year each, wikiwiki's live pages, and imas-db's index of live
    records and their pages. Records every request. Add a host's name to `down` to have it unreachable, or a page's path
    to `missing` to have it answer 404."""

    SPARQL = "sparql.crssnky.xyz"
    OFFICIAL = "shinycolors.idolmaster-official.jp"
    LANTIS = "shinycolors.lantis.jp"
    FUJI = "api.fujiwarahaji.me"
    CMS = "cmsapi-frontend.idolmaster-official.jp"
    OLD = "idolmaster.jp"
    WIKI = "wikiwiki.jp"
    IMASDB = "imas-db.jp"
    TOKEN = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

    def __init__(self) -> None:
        self.idols: list[dict[str, str]] = []
        """im@sparql's idols: `id` (the resource's last path segment), `name`, `kana`, `cv`."""
        self.staff: list[dict[str, str]] = []
        """im@sparql's staff: `id`, `name`, `kana`, `position`."""
        self.sparql_units: dict[str, tuple[str | None, list[str]]] = {}
        """im@sparql's units, by name: reading and members."""
        self.official_units: list[tuple[str, str, str, list[str]]] = []
        """The official site's units, in order: slug, logo text (empty for none), the
        name under the logo, members as the site writes them (family name, space, given)."""
        self.releases: list[LantisRelease] = []
        """Lantis's CDs, in the discography's order."""
        self.blu_rays: list[tuple[str, str]] = []
        """Lantis's Blu-rays: slug and title. The discography lists them apart from the CDs."""
        self.fuji_songs: dict[int, tuple[str, list[int]]] = {}
        """ふじわらはじめ's Shiny Colors songs by id: name and the discs it is on."""
        self.fuji_discs: dict[int, FujiDisc] = {}
        self.cms_lives: list[CmsLive] = []
        """The CMS's LIVE-EVENT articles, every brand's, in the order it lists them."""
        self.old_events: dict[int, list[OldSiteEvent]] = {}
        """The old official site's events by year, beside one of another brand's that
        every year's page has."""
        self.wiki_lives: list[WikiLive] = []
        self.imasdb_lives: list[ImasDbLive] = []
        """imas-db's lives, besides another brand's that its index always lists."""
        self.cms_token_refused = False
        """Whether the CMS refuses the token, as it does, in the body of an HTTP 200."""
        self.cms_page_most: int | None = None
        """The most articles the CMS answers with at once, whatever it is asked for."""
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()
        self.missing: set[str] = set()
        self.empty: set[str] = set()
        """ふじわらはじめ's API calls (path and query: v4/tax?id=4654) that answer with
        an empty body, as it does for some discs."""

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.down:
            raise httpx.ConnectError("connection refused")
        if host == self.SPARQL and request.url.path == "/spql/imas/query":
            return httpx.Response(200, json=self._query(request.url.params["query"]))
        if host == self.OFFICIAL and request.url.path.strip("/") not in self.missing:
            path = request.url.path.strip("/")
            if path == "idol":
                return httpx.Response(200, text=self._index())
            for slug, logo, name, members in self.official_units:
                if path == f"idol/{slug}":
                    return httpx.Response(200, text=self._unit_page(slug, logo, name, members))
        path = request.url.path.strip("/")
        if host == self.LANTIS and path not in self.missing:
            if path == "discography":
                return httpx.Response(200, text=self._discography())
            for release in self.releases:
                if path == f"releaseinfo/{release.slug}":
                    return httpx.Response(200, text=self._release_page(release))
        if host == self.FUJI and f"{path}?{request.url.query.decode()}" in self.empty:
            return httpx.Response(200, content=b"", headers={"Content-Type": "application/json"})
        if host == self.FUJI and path not in self.missing:
            params = request.url.params
            if path == "v4/list" and params.get("type") == "music" and params.get("music_type") == "sc":
                return httpx.Response(200, json=[
                    {"name": name, "type": "music", "music_type": "sc", "song_id": song_id,
                     "link": f"https://fujiwarahaji.me/music/sc/{song_id}",
                     "api": f"https://{self.FUJI}/v4/music?id={song_id}"}
                    for song_id, (name, _) in self.fuji_songs.items()
                ])
            if path == "v4/music" and int(params["id"]) in self.fuji_songs:
                return httpx.Response(200, json=self._fuji_song(int(params["id"])))
            if path == "v4/tax" and int(params["id"]) in self.fuji_discs:
                return httpx.Response(200, json=self._fuji_disc(self.fuji_discs[int(params["id"])]))
        if host == self.CMS and path not in self.missing:
            if path == "sitern/api/cmsbase/Token/get":
                return httpx.Response(200, json={
                    "statusCode": 200, "data": {"token": self.TOKEN, "limit": 1790579592, "time": 0},
                })
            if path == "sitern/api/idolmaster/Article/list":
                return httpx.Response(200, json=self._cms_list(request.url.params))
        if host == self.OLD and path not in self.missing:
            if match := re.fullmatch(r"event/(\d{4})\.php", path):
                return httpx.Response(200, text=self._old_page(int(match.group(1))))
        if host == self.WIKI and unquote(path) not in self.missing:
            page = unquote(path).removeprefix("shinycolors/")
            if page == "ライブイベント情報" or any(live.page == page for live in self.wiki_lives):
                return httpx.Response(200, text=self._wiki_page(page))
        if host == self.IMASDB and path not in self.missing:
            if path == "song/event":
                return httpx.Response(200, text=self._imasdb_index())
            pages = {page.file: page for live in self.imasdb_lives for _, page, _ in live.pages}
            if (page := pages.get(path.removeprefix("song/event/"))) is not None:
                return httpx.Response(200, text=self._imasdb_page(page))
        return httpx.Response(404, text="<html>Not Found</html>")

    def _imasdb_index(self) -> str:
        other = ImasDbLive("THE IDOLM@STER MILLION LIVE! 9thLIVE", "5", [
            ("DAY1", ImasDbPage("million_9th_day1.html", "9th", "2023/01/14(土)", []), "2023/01/14(土)"),
        ])
        badge = (
            '<span class="visually-hidden">(</span><span title="シャイニーカラーズ" '
            'class="badge small bg-imas-brand-shinycolors">シャニ</span>'
            '<span class="visually-hidden">)</span>'
        )
        items = []
        for live in [other, *self.imasdb_lives]:
            if len(live.pages) == 1 and live.pages[0][0] == live.name:
                _, page, day = live.pages[0]
                items.append(
                    f'<li data-brand-ids="{live.brands}" class="mb-3"><a href="./{page.file}">'
                    f'{html.escape(live.name)}</a> <small class="date">- {day}</small>\n {badge}</li>'
                )
                continue
            links = "".join(
                f'\t<li><a href="./{page.file}" title="{html.escape(live.name)} {label}">{label}</a>'
                f' <small class="date">- {day}</small></li>\n'
                for label, page, day in live.pages
            )
            items.append(
                f'<li data-brand-ids="{live.brands}">{html.escape(live.name)} {badge}\n\t<ul>\n'
                f'{links}\t</ul>\n</li>'
            )
        listed = "\n".join(items)
        return (
            '<html><body><header id="maruamyu-header"><h1 id="page_title">ライブイベント</h1></header>'
            '<main class="container-fluid"><p>アイマスライブイベントの記録です。</p>'
            f'<div class="section"><h2>年</h2>\n<ul>\n{listed}\n</ul></div>'
            '</main><footer id="maruamyu-footer"></footer></body></html>'
        )

    def _imasdb_page(self, page: ImasDbPage) -> str:
        parts = [
            f'<html><body><header><h1 id="page_title">{html.escape(page.title)}</h1></header><hr>',
            '<ul class="nav nav-tabs"><li class="nav-item"><a class="nav-link" href="./other.html">'
            'DAY2</a></li></ul>',
        ]
        if page.date:
            parts.append(f'<p>{page.date} <a href="https://idolmaster-official.jp/">詳細</a></p>')
        if page.schedule:
            rows = "".join(
                f"<tr> <td>{label}</td> <td>{day}</td> <td>ゲスト: なし</td> </tr>\n"
                for label, day in page.schedule
            )
            parts.append(
                '<div class="section" data-brand-id="8">\n<h2>公演概要</h2>\n'
                f'<div class="mx-3 my-2">{page.schedule[0][1][:10]} どこかのホール</div>\n'
                f'<table class="table table-sm"><tbody>\n{rows}</tbody></table>\n</div>'
            )
        if page.cast:
            parts.append(
                f'<div class="section" data-brand-id="8">\n<h2>出演</h2>\n<ul><li>{page.cast}</li></ul>\n</div>'
            )
        for show in page.shows:
            rows = "".join(
                f"<tr> <td>{number}</td> <td>{title}</td> <td>{who}</td> </tr>\n"
                for number, title, who in show.rows
            )
            date = f"<div>{show.date} 開場12:40/開演13:20</div>\n" if show.date else ""
            parts.append(
                '<div class="section" data-brand-id="8">\n'
                '<div class="section_header_notes" aria-hidden="true">[by imas-db.jp]</div>\n'
                f'<h2 class="trim-margin">{show.heading}</h2>\n{date}'
                '<table class="tracklist">\n'
                '<thead> <tr> <th>No.</th> <th>楽曲</th> <th>演者</th> </tr> </thead>\n'
                f'<tbody>\n<tr class="part-header"> <th colspan="3">【メドレー】</th> </tr>\n{rows}'
                '</tbody>\n</table>\n<div class="mx-3 my-2 notes">※ アンコール</div>\n</div>'
            )
        parts.append(
            '<hr><footer id="maruamyu-footer"><p>&copy;2007-2024 maruamyu.net</p></footer></body></html>'
        )
        return "\n".join(parts)

    def _cms_list(self, params) -> dict:
        if self.cms_token_refused or params.get("token") != self.TOKEN:
            return {"statusCode": 404, "data": False}
        wanted = json.loads(params["data"])
        assert wanted.get("category") == ["LIVE-EVENT"]
        start, limit = int(params.get("start", 0)), int(params.get("limit", 100))
        limit = min(limit, self.cms_page_most or limit)
        articles = [
            {
                "_id": live.id, "article_type": live.article_type, "title": live.title,
                "brand": [{"name": code.title(), "code": code} for code in live.brands],
                "event_place": live.place, "event_dspdate": live.dspdate,
                "event_url": live.url, "url_name": live.url_name,
                "url": f"https://idolmaster-official.jp/live-event/01_{live.id}.html",
            }
            for live in self.cms_lives
        ]
        page = articles[start:start + limit]
        return {"statusCode": 200, "data": {
            "apiStatus": True, "total_count": len(articles), "start": start, "limit": limit,
            "count": len(page), "time": 0, "article_list": page,
        }}

    def _old_page(self, year: int) -> str:
        events = [
            OldSiteEvent("THE IDOLM@STER SideM 某公演", f"{year}年1月5日(土)", "某ホール"),
            *self.old_events.get(year, []),
        ]
        blocks = "".join(
            '<div class="eventCont">\n<ul><li class="title">{name}</li></ul>\n'
            '<table border="0" cellpadding="0" cellspacing="0">\n'
            "<tr><th>公演名</th><td>{name}</td></tr>\n"
            "<tr><th>日時</th><td>{dates}</td></tr>\n"
            "<tr><th>場所</th><td>{place}</td></tr>\n"
            "<tr><th>出演者</th><td>［イルミネーションスターズ］<br>関根 瞳（櫻木真乃役）</td></tr>\n"
            "<tr><th>リンク</th><td>{link}</td></tr>\n</table>\n</div>\n".format(
                name=html.escape(event.name), dates=html.escape(event.dates),
                place=html.escape(event.place),
                link=f'公式サイト：<a href="{event.link}" target="_blank">{event.link}</a><br>'
                if event.link else "",
            )
            for event in events
        )
        return f'<html><body><div id="m1" class="detailCol">{blocks}</div></body></html>'

    def _wiki_page(self, page: str) -> str:
        logs = sorted({live.page for live in self.wiki_lives if live.page != "ライブイベント情報"})
        links = "".join(
            f'<li><a href="/shinycolors/{quote(log)}" title="{log}" class="rel-wiki-page">{log}</a></li>'
            for log in logs
        )
        blocks = []
        for live in self.wiki_lives:
            if live.page != page:
                continue
            dates = "".join(f"<li>{d}</li>" for d in live.dates)
            performers = []
            for day, lines in live.performers:
                body = "<br />\n".join(lines)
                performers.append(f"<li>{day}<br />\n{body}</li>" if day else body)
            listed = (
                '<ul class="list2">' + "\n".join(performers) + "</ul>"
                if any(day for day, _ in live.performers) else "<br />\n" + "\n".join(performers)
            )
            blocks.append(
                '<div class="accordion-container ">\n    <h3 class="accordion-header">'
                '<i class="fa-solid fa-plus-square hidden-on-open"></i>\n'
                f"        {html.escape(live.title)}\n    </h3>\n"
                '    <div class="accordion-content"><ul class="list1"><li>公演名<br />\n'
                f'{html.escape(live.name or live.title)}</li></ul>\n'
                f'<ul class="list1"><li>開催日時\n<ul class="list2">{dates}</ul></li></ul>\n'
                '<ul class="list1"><li>会場<br />\nどこかのホール</li></ul>\n'
                f'<ul class="list1"><li>出演者\n{listed}</li></ul>\n'
                "<p>詳細は、公式サイトへ</p>\n</div>\n</div>\n"
            )
        past = f'<h2 id="h2_content_1_9">公演終了(過去の公演情報)</h2>\n<ul class="list1">{links}</ul>'
        return (
            '<html><body><div id="body"><h2 id="h2_content_1_0">開催予定</h2>\n'
            f'{"".join(blocks)}<hr class="full_hr" />\n{past if page == "ライブイベント情報" else ""}'
            '</div><div id="main-contents-bottom"><a href="/shinycolors/櫻木真乃">櫻木 真乃 / 風野 灯織</a>'
            "</div></body></html>"
        )

    def _discography(self) -> str:
        def links(items: list[tuple[str, str]]) -> str:
            return "".join(
                f'<a href="https://{self.LANTIS}/releaseinfo/{slug}/" title="{html.escape(title)}">'
                f'<img src="x.jpg" class="hv"></a>\n'
                for slug, title in items
            )

        return (
            '<section id="discographys" class="mb96">\n'
            '<div class="CD_area"><div class="cat_cd">'
            '<h3><img src="/images/common/dsc_tit_cd.png" alt="CD" height="19"></h3></div>\n'
            '<div class="list"><div class="dsc_box mb0"><h4><span>■CD</span></h4>\n'
            f"{links([(r.slug, r.title) for r in self.releases])}</div></div>\n</div>\n"
            '<div class="BD_area"><div class="cat_cd">'
            '<h3><img src="/images/common/dsc_tit_bd.png" alt="CD" height="19"></h3></div>\n'
            f'<div class="list"><div class="dsc_box mb0">\n{links(self.blu_rays)}</div></div>\n</div>\n'
            "</section>"
        )

    def _release_page(self, release: LantisRelease) -> str:
        def paragraph(text: str) -> str:
            return "<p>" + "<br />\n".join(html.escape(line) for line in text.split("\n")) + "</p>"

        year, month, day = release.date.split("/")
        banner = release.banner_date or f"{year}.{int(month):02}.{int(day):02}"
        artist = f"アーティスト：{release.artist}"
        if release.artist_members:
            artist += f"\n{release.artist_members}"
        facts = f"発売日：{release.date}\n品番：{release.catalog}\n価格：1,980円（10%税込）"
        index = "\n".join(paragraph(p) for p in release.index)
        return (
            '<html><body><section id="release"><div class="release_box cd">\n'
            f'<div class="titles">\n<div class="date">{banner} RELEASE</div>\n'
            f"<h2>{html.escape(release.title)}</h2>\n</div>\n"
            f'<div class="release_contents">\n{paragraph(artist)}\n{paragraph(facts)}\n'
            '<h3><img src="/images/common/release_index.png" alt="インデックス"></h3>\n'
            f"{index}\n"
            '<h3><img src="/images/common/release_information.png" alt="インフォメーション"></h3>\n'
            "<p>描き下ろしイラストジャケット</p>\n"
            "</div><!--release_contents-->\n</div></section>\n"
            f"{self._discography()}</body></html>"
        )

    def _fuji_disc_ref(self, disc: FujiDisc) -> dict:
        return {
            "name": disc.name, "type": "disc", "tax_id": disc.tax_id,
            "link": f"https://fujiwarahaji.me/disc/{disc.tax_id}",
            "api": f"https://{self.FUJI}/v4/tax?id={disc.tax_id}",
        }

    @staticmethod
    def _fuji_idol(name: str) -> dict:
        return {"name": name, "type": "idol", "production": "sc", "cv": "某声優"}

    def _fuji_song(self, song_id: int) -> dict:
        name, discs = self.fuji_songs[song_id]
        return {
            "name": name, "type": "music", "song_id": song_id,
            "link": f"https://fujiwarahaji.me/music/sc/{song_id}",
            "api": f"https://{self.FUJI}/v4/music?id={song_id}",
            "member": [], "disc": [self._fuji_disc_ref(self.fuji_discs[d]) for d in discs],
        }

    def _fuji_disc(self, disc: FujiDisc) -> dict:
        return {
            **self._fuji_disc_ref(disc), "cat": "", "flag": "", "setlist": True,
            "song": [
                {
                    "name": name, "type": "music", "music_type": "sc", "song_text": "",
                    "unit": [
                        {"name": unit, "type": "unit", "member": [self._fuji_idol(m) for m in members]}
                        for unit, members in units
                    ],
                    "member": [self._fuji_idol(m) for m in idols] or None,
                    "member_text": "", "flag": flags,
                }
                for name, units, idols, flags in disc.tracks
            ],
        }

    def _query(self, query: str) -> dict:
        detail = "https://sparql.crssnky.xyz/imasrdf/RDFs/detail/"

        def uri(value: str) -> dict:
            return {"type": "uri", "value": value}

        def ja(value: str) -> dict:
            return {"type": "literal", "xml:lang": "ja", "value": value}

        rows: list[dict] = []
        if "imas:Unit" in query:
            for name, (kana, members) in self.sparql_units.items():
                for member in members:
                    row = {"unit": uri(detail + name.replace(" ", "")), "name": ja(name), "member": ja(member)}
                    if kana is not None:
                        row["kana"] = ja(kana)
                    rows.append(row)
        elif "imas:Staff" in query:
            for staff in self.staff:
                rows.append({
                    "staff": uri(detail + staff["id"]), "name": ja(staff["name"]),
                    "kana": ja(staff["kana"]), "position": ja(staff["position"]),
                    "officeName": ja("283プロダクション"),
                })
        elif "imas:Idol" in query:
            for idol in self.idols:
                rows.append({
                    "idol": uri(detail + idol["id"]), "name": ja(idol["name"]),
                    "kana": ja(idol["kana"]), "cv": ja(idol["cv"]),
                })
        return {"head": {"vars": []}, "results": {"bindings": rows}}

    def _index(self) -> str:
        items = "".join(
            f'<li class="idol-item js--scroll__trigger">\n'
            f'  <a href="./{slug}" id="{slug}" class="idol-link">\n'
            f'    <div class="idol-logo unit{n}"><img loading="lazy" src="x.png" alt="{slug}"></div>\n'
            f"  </a>\n</li>\n"
            for n, (slug, *_) in enumerate(self.official_units, 1)
        )
        return f'<html><body><main class="idol-unit"><ul class="idol-list">\n{items}</ul></main></body></html>'

    def _unit_page(self, slug: str, logo: str, name: str, members: list[str]) -> str:
        names = "".join(
            f'<li class="idol-group__name" data-id="{n}">\n'
            f'<a href="../../idol/{slug}/m{n}/" class="idol-group__name--text">{member}</a>\n</li>\n'
            for n, member in enumerate(members, 1)
        )
        nav = "".join(
            f'<li class="idol-nav__unit--btn"><a href="../../idol/{other}" class="idol-nav__unit--item">'
            f'<img loading="lazy" src="logo_{other}.png" alt="{other_logo}"></a></li>\n'
            for other, other_logo, *_ in self.official_units
        )
        return (
            f'<html><body><main class="idol common-main" data-id="{slug}">\n'
            f'<ul class="idol-group__list">\n{names}</ul>\n'
            f'<div class="idol-group__logo">\n<picture>\n'
            f'<img loading="lazy" src="../../assets/img/idol/{slug}/logo.png" alt="{logo}">\n'
            f'</picture>\n<p class="idol-group__logo--name">{name}</p>\n</div>\n'
            f'<ul class="idol-nav__unit">\n{nav}</ul>\n</main></body></html>'
        )


class FakeCatalog:
    """The price catalogs online, models.dev's and OpenRouter's, as `price_catalog` fetches
    them over HTTP (`transport`): what the test sets, empty by default, or failing. Counts
    the times both were fetched (`fetches`)."""

    def __init__(
        self, models_dev: dict | None = None, openrouter: list[dict] | None = None
    ) -> None:
        self.models_dev = models_dev or {}
        self.openrouter = openrouter or []
        self.failing = False
        self.fetches = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.failing:
            return httpx.Response(503, text="unavailable")
        if request.url.host == "models.dev":
            return httpx.Response(200, json=self.models_dev)
        assert request.url.host == "openrouter.ai", request.url
        self.fetches += 1
        return httpx.Response(200, json={"data": self.openrouter})
