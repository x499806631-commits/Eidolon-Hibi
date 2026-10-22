"""Local voices (GLOSSARY.md「本地语音」): speaking as the characters on a GPT-SoVITS server
the deployer runs themselves (its `api_v2.py`), in place of Fish Audio, for calls, for her
messages read aloud (GLOSSARY.md「朗读」), or both, each as the configuration has it
(`VoiceEngine`).

Each character's voice is set in a file of the deployer's own (`EIDOLON_LOCAL_VOICES`,
`local-voices/voices.toml` by default), kept out of the repository with the audio it names
(ADR-0003): a table per character, by her identifier, with the reference clip (3 to 10
seconds of her alone), what is said in it and its language, and, if trained for her, the
GPT and SoVITS weights:

    [mano]
    ref_audio = "mano/ref.wav"      # relative to the file, or absolute
    ref_text = "ほわっ……プロデューサーさん、おはようございます"
    ref_lang = "ja"                 # optional, ja by default
    gpt_weights = "D:/GPT-SoVITS/GPT_weights_v2/mano-e15.ckpt"        # optional
    sovits_weights = "D:/GPT-SoVITS/SoVITS_weights_v2/mano_e8_s200.pth"  # optional

The paths are sent to the server as they are resolved here: it is to run on the same
computer, or see the same files at the same paths. One without a voice there is voiced by
Fish Audio where the deployer has a key for it, and not at all otherwise.

The server holds one pair of weights at a time, so what is voiced on it is voiced one
piece at a time (`GptSovits`), the weights switched first where the next piece is
another's, those she has none of left as they are. Her delivery cues are not read, only
the words."""

import asyncio
import logging
import re
import tomllib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
import httpx

from eidolon.config import VOICE_ENGINES, VoiceEngine
from eidolon.delivery_cues import without_cues

log = logging.getLogger(__name__)

__all__ = ["VOICE_ENGINES", "VoiceEngine"]
DEFAULT_GPT_SOVITS_URL = "http://127.0.0.1:9880"
"""Where `api_v2.py` listens by default."""
_TIMEOUT = httpx.Timeout(120, connect=5)
"""A reply's worth of speech, voiced on the deployer's own graphics card."""
LANGUAGES = ("ja", "zh", "en", "ko", "yue", "auto", "all_ja", "all_zh")


class LocalVoicesError(RuntimeError):
    """The local voices file is broken: what, and where."""


class VoiceFailed(Exception):
    """The GPT-SoVITS server did not voice it: why."""


@dataclass(frozen=True)
class LocalVoice:
    """A character's voice on the GPT-SoVITS server."""

    ref_audio: str
    ref_text: str
    ref_lang: str = "ja"
    gpt_weights: str | None = None
    sovits_weights: str | None = None


def local_voices_path(raw: str | None) -> Path:
    return Path(raw or "../local-voices/voices.toml")


def load_local_voices(path: Path) -> dict[str, LocalVoice]:
    """The voices in the file at `path`, by character; none if there is no such file.
    Raises LocalVoicesError if it is broken.

    A relative path in it is of a file beside it, sent to the server as the server sees
    that folder: `server_dir`, at the top of the file, where the backend sees it in a
    container and the server elsewhere (`C:/Users/me/eidolon-sc/local-voices`); without
    it, as the backend sees it. A Windows path (`D:/...`) is absolute, wherever the
    backend runs."""
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise LocalVoicesError(f"{path} 读不了：{e}") from None
    server_dir = data.pop("server_dir", None)
    if server_dir is not None and (not isinstance(server_dir, str) or not server_dir.strip()):
        raise LocalVoicesError(f"{path} 的 server_dir 应该是一个路径")
    base = server_dir.strip().rstrip("/\\") if server_dir else str(path.parent.resolve())
    voices: dict[str, LocalVoice] = {}
    for character_id, entry in data.items():
        if not isinstance(entry, dict):
            raise LocalVoicesError(f"{path} 里的 {character_id} 应该是一张表（[{character_id}]）")
        ref_audio, ref_text = entry.get("ref_audio"), entry.get("ref_text")
        if not isinstance(ref_audio, str) or not ref_audio.strip():
            raise LocalVoicesError(f"{path} 里 [{character_id}] 缺 ref_audio（参考音频）")
        if not isinstance(ref_text, str) or not ref_text.strip():
            raise LocalVoicesError(f"{path} 里 [{character_id}] 缺 ref_text（参考音频说的话）")
        lang = entry.get("ref_lang", "ja")
        if lang not in LANGUAGES:
            raise LocalVoicesError(f"{path} 里 [{character_id}] 的 ref_lang 只能是 {'、'.join(LANGUAGES)}")
        voices[character_id] = LocalVoice(
            _resolved(base, ref_audio), ref_text.strip(), lang,
            _optional_path(base, path, entry.get("gpt_weights"), character_id, "gpt_weights"),
            _optional_path(base, path, entry.get("sovits_weights"), character_id, "sovits_weights"),
        )
    return voices


_WINDOWS_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _resolved(base: str, given: str) -> str:
    """`given` as the server is to find it: as it is if absolute, else in `base`."""
    given = given.strip()
    if _WINDOWS_ABSOLUTE.match(given) or given.startswith("/"):
        return given
    separator = "\\" if "\\" in base and "/" not in base else "/"
    given = given.replace("\\", "/")
    given = given[2:] if given.startswith("./") else given
    return base + separator + given


def _optional_path(
    base: str, path: Path, given: object, character_id: str, field: str
) -> str | None:
    if given is None:
        return None
    if not isinstance(given, str) or not given.strip():
        raise LocalVoicesError(f"{path} 里 [{character_id}] 的 {field} 应该是一个路径")
    return _resolved(base, given)


def spoken(text: str) -> str:
    """What of `text` the server is given: her words, without delivery cues."""
    return without_cues(text).strip()


class GptSovits:
    """The deployer's GPT-SoVITS server, at whatever address the configuration gives each
    time, reached directly (never through the proxy: it is the deployer's own); the
    characters' voices on it (`voices`). One request at a time, so that the weights loaded
    are the ones it is voiced in."""

    def __init__(
        self, voices: Mapping[str, LocalVoice] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.voices = dict(voices or {})
        self._transport = transport
        self._lock = asyncio.Lock()
        self._loaded: dict[str, str] = {}  # base url + kind -> weights in it

    def has(self, character_id: str) -> bool:
        return character_id in self.voices

    def _client(self, timeout: httpx.Timeout = _TIMEOUT) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=timeout, trust_env=False)

    async def stream(self, base_url: str, character_id: str, text: str) -> AsyncIterator[bytes]:
        """`text` said in her voice, as WAV: its header first, then the samples as they come.
        Raises VoiceFailed saying why it is not, KeyError for one without a voice."""
        voice = self.voices[character_id]
        said = spoken(text)
        if not said:
            return
        async with self._lock, self._client() as client:
            await self._load(client, base_url, voice)
            body = {
                "text": said, "text_lang": "ja", "ref_audio_path": voice.ref_audio,
                "prompt_text": voice.ref_text, "prompt_lang": voice.ref_lang,
                "media_type": "wav", "streaming_mode": True, "text_split_method": "cut5",
                "parallel_infer": False,
            }
            try:
                async with client.stream("POST", _url(base_url, "/tts"), json=body) as res:
                    if res.is_error:
                        detail = (await res.aread()).decode(errors="replace")[:200]
                        raise VoiceFailed(f"GPT-SoVITS 没能合成：{res.status_code} {detail}")
                    async for chunk in res.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.HTTPError as e:
                raise VoiceFailed(f"连不上 GPT-SoVITS（{base_url}）：{e!r}") from None

    async def read(self, base_url: str, character_id: str, text: str) -> bytes:
        """`text` said in her voice, the whole of it, as WAV."""
        return b"".join([chunk async for chunk in self.stream(base_url, character_id, text)])

    async def check(self, base_url: str) -> None:
        """Raise VoiceFailed unless something answers at `base_url`."""
        try:
            async with self._client(httpx.Timeout(5)) as client:
                await client.get(_url(base_url, "/docs"))
        except httpx.HTTPError as e:
            raise VoiceFailed(f"连不上 GPT-SoVITS（{base_url}）：{e!r}") from None

    async def _load(self, client: httpx.AsyncClient, base_url: str, voice: LocalVoice) -> None:
        for kind, weights in (("gpt", voice.gpt_weights), ("sovits", voice.sovits_weights)):
            key = f"{base_url}|{kind}"
            if weights is None or self._loaded.get(key) == weights:
                continue
            try:
                res = await client.get(
                    _url(base_url, f"/set_{kind}_weights"), params={"weights_path": weights}
                )
            except httpx.HTTPError as e:
                raise VoiceFailed(f"连不上 GPT-SoVITS（{base_url}）：{e!r}") from None
            if res.is_error:
                raise VoiceFailed(f"GPT-SoVITS 没能换上 {weights}：{res.status_code} {res.text[:200]}")
            self._loaded[key] = weights


def _url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + path
