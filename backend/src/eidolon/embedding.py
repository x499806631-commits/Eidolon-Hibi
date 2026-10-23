"""Text embeddings for the vector index: Qwen3-Embedding on SiliconFlow, or Gemini
Embedding on Google's own API, whichever the deployment chose (ADR-0005).

A query holds up a turn, so it is given a few seconds and asked again once if it fails in
passing (`retry`); documents, embedded where no one waits on them (importing the story,
`eidolon reembed`, writing down memories), are given minutes and asked once."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import numpy as np

from eidolon.billing import bill
from eidolon.config import EmbeddingSettings
from eidolon.retry import TURN_TIMEOUT, ServiceError, answered, retry_once, transient_status

EMBEDDING_TIMEOUT = httpx.Timeout(120, connect=10)

_REQUEST_BATCH = 16
"""Texts sent in one request to SiliconFlow."""
_QUERY_INSTRUCTION = "Instruct: 根据问题，检索相关的剧情台词\nQuery: "

log = logging.getLogger(__name__)

GEMINI_BATCH = 100
"""Texts sent in one `batchEmbedContents` request; Google documents no limit. Each text
counts as one request against the rate limit (the free tier allows 100 a minute)."""
GEMINI_RETRIES = 5
"""How many times embedding documents waits out Gemini's rate limit before giving up."""
GEMINI_LONGEST_WAIT = 120.0
"""The longest, in seconds, it waits at a time; a longer delay (a daily quota) is an error."""


@dataclass(frozen=True)
class Document:
    """Text to embed for the index, with where it comes from: an episode's card or event and
    title for a story passage, a fact's headings for a character fact, None for a memory."""

    text: str
    title: str | None = None


class Embedder(Protocol):
    model: str
    """Vectors from different models are not comparable; the index keeps them apart by this."""

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class Reembeddable(Protocol):
    """What `eidolon reembed` embeds again after the embedding service, model or dimensions
    change (ADR-0005): the story corpus, the character facts, the work facts and the
    memories, each embedding with the embedder it was opened with."""

    @property
    def label(self) -> str:
        """What it is called in `eidolon reembed`'s log and report."""
        ...

    @property
    def unit(self) -> str:
        """What its count is counted in."""
        ...

    async def embed_missing(self, batch: int, progress: Callable[[int], None]) -> int:
        """Embed whatever has no vector from its embedder yet, `batch` at a time, each
        committed, telling `progress` the count so far as it goes; returns how many it
        embedded."""
        ...

    async def forget_other_models(self) -> int:
        """Drop the vectors not from its embedder; returns how many there were."""
        ...


class ClosableEmbedder(Embedder, Protocol):
    async def aclose(self) -> None: ...


class EmbeddingError(ServiceError):
    pass


class NoEmbedder:
    """For reading the story corpus without searching it or adding to it."""

    model = "none"

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
        raise EmbeddingError("没有配置向量化服务")

    async def embed_query(self, text: str) -> list[float]:
        raise EmbeddingError("没有配置向量化服务")


class SiliconFlowEmbedder:
    """OpenAI-compatible `/embeddings` endpoint. Qwen3-Embedding wants an instruction in
    front of queries and none in front of documents, whose titles it is not given."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        query_timeout: float = TURN_TIMEOUT,
    ) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/embeddings"
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT)
        self._query_timeout = query_timeout

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
        texts = [d.text for d in documents]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _REQUEST_BATCH):
            vectors.extend(await self._embed(texts[start : start + _REQUEST_BATCH]))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        texts = [_QUERY_INSTRUCTION + text]
        return (await retry_once(lambda: self._embed(texts, self._query_timeout)))[0]

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _embed(
        self, texts: Sequence[str], timeout: float | None = None
    ) -> list[list[float]]:
        """The vectors of `texts`, asked for in one request given `timeout` seconds if not
        None, or else as long as the client gives any."""
        res = await _post(
            self._client,
            timeout,
            self._url,
            json={"model": self.model, "input": list(texts), "encoding_format": "float"},
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if res.status_code >= 400:
            raise EmbeddingError(
                f"向量化服务返回 {res.status_code}: {res.text[:500]}",
                transient=transient_status(res.status_code),
            )
        try:
            body = res.json()
            data = sorted(body["data"], key=lambda d: d["index"])
        except (ValueError, KeyError, TypeError) as e:
            raise EmbeddingError(f"向量化服务的返回无法解析（{len(res.text)} 字）") from e
        if len(data) != len(texts):
            raise EmbeddingError(f"向量化服务返回了 {len(data)} 个向量，应为 {len(texts)} 个")
        usage = body.get("usage")
        if isinstance(usage, dict) and isinstance(tokens := usage.get("total_tokens"), int):
            bill("tokens", tokens)
        return [d["embedding"] for d in data]


class GeminiEmbedder:
    """Google's native `batchEmbedContents`, where each text is a request item of its own
    (texts sharing one item's `parts` would make one vector). Gemini Embedding 2 takes no
    task type, so queries and documents are told apart by prefixes. The model it stores
    vectors under carries the dimensions, so vectors of different lengths never mix.

    Embedding documents (importing, re-embedding, writing down memories) waits out the rate
    limit for as long as Google asks, a few times at most; a query, which holds up a turn,
    does not wait: it is asked again once at once, as for any failure in passing."""

    def __init__(
        self,
        api_key: str,
        model: str,
        dimensions: int,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        query_timeout: float = TURN_TIMEOUT,
    ) -> None:
        self.model = f"{model}@{dimensions}"
        self._name = f"models/{model}"
        self._dimensions = dimensions
        self._url = f"{base_url.rstrip('/')}/{self._name}:batchEmbedContents"
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT)
        self._sleep = sleep
        self._query_timeout = query_timeout

    async def embed_documents(self, documents: Sequence[Document]) -> list[list[float]]:
        texts = [f"title: {d.title or 'none'} | text: {d.text}" for d in documents]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), GEMINI_BATCH):
            batch = texts[start : start + GEMINI_BATCH]
            for attempt in range(GEMINI_RETRIES + 1):
                try:
                    vectors.extend(await self._embed(batch))
                    break
                except _RateLimited as e:
                    if attempt == GEMINI_RETRIES or not 0 < e.delay <= GEMINI_LONGEST_WAIT:
                        raise EmbeddingError(str(e)) from None
                    log.info("Gemini rate limit reached; waiting %.0f s", e.delay + 1)
                    await self._sleep(e.delay + 1)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        texts = [f"task: search result | query: {text}"]
        try:
            return (await retry_once(lambda: self._embed(texts, self._query_timeout)))[0]
        except _RateLimited as e:
            raise EmbeddingError(str(e), transient=True) from None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _embed(
        self, texts: Sequence[str], timeout: float | None = None
    ) -> list[list[float]]:
        """The vectors of `texts`, each a request item of its own, asked for in one request
        given `timeout` seconds if not None, or else as long as the client gives any."""
        requests = [
            {
                "model": self._name,
                "content": {"parts": [{"text": text}]},
                "output_dimensionality": self._dimensions,
            }
            for text in texts
        ]
        res = await _post(
            self._client,
            timeout,
            self._url,
            json={"requests": requests},
            headers={"x-goog-api-key": self._api_key},
        )
        if res.status_code == 429:
            raise _RateLimited(
                f"向量化服务返回 429（Gemini 限流：免费层每分钟 100 条文本，每条都计一次请求；"
                f"开通计费可以提高限额）: {res.text[:500]}",
                _retry_delay(res.text),
            )
        if res.status_code >= 400:
            hint = (
                "（Gemini API 不对中国大陆、香港、澳门开放，服务器需要在其他地区）"
                if "location is not supported" in res.text.lower()
                else ""
            )
            raise EmbeddingError(
                f"向量化服务返回 {res.status_code}{hint}: {res.text[:500]}",
                transient=transient_status(res.status_code),
            )
        try:
            vectors = [e["values"] for e in res.json()["embeddings"]]
        except (ValueError, KeyError, TypeError) as e:
            raise EmbeddingError(f"向量化服务的返回无法解析（{len(res.text)} 字）") from e
        if len(vectors) != len(texts):
            raise EmbeddingError(f"向量化服务返回了 {len(vectors)} 个向量，应为 {len(texts)} 个")
        return vectors


class _RateLimited(EmbeddingError):
    def __init__(self, message: str, delay: float) -> None:
        super().__init__(message, transient=True)
        self.delay = delay
        """Seconds Google asks to wait before retrying; 0 if it does not say."""


async def _post(
    client: httpx.AsyncClient, timeout: float | None, url: str, **request: Any
) -> httpx.Response:
    """The answer to a POST of `request` to `url`, given `timeout` seconds if not None.
    Raises EmbeddingError if it does not come."""
    async with answered("向量化服务", timeout, EmbeddingError):
        return await client.post(url, **request)


def _retry_delay(body: str) -> float:
    """The `retryDelay` of the RetryInfo in a Gemini error, e.g. `"36s"`."""
    found = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', body)
    return float(found.group(1)) if found else 0.0


def open_embedder(
    settings: EmbeddingSettings, client: httpx.AsyncClient | None = None
) -> ClosableEmbedder:
    """The embedder of the service the deployment chose, on `client`, else on one of its
    own."""
    if settings.provider == "gemini":
        assert settings.dimensions is not None, "Gemini's settings always carry dimensions"
        return GeminiEmbedder(
            settings.api_key, settings.model, settings.dimensions, settings.base_url, client
        )
    return SiliconFlowEmbedder(settings.api_key, settings.model, settings.base_url, client)


def nearest(
    vectors: Sequence[bytes], query: Sequence[float], limit: int
) -> list[tuple[int, float]]:
    """Indexes of the stored `vectors` most similar to `query` by cosine similarity, best
    first, with their scores."""
    if not vectors:
        return []
    query_vector = np.asarray(query, dtype=np.float32)
    matrix = np.stack([np.frombuffer(v, dtype=np.float32) for v in vectors])
    scores = matrix @ query_vector / (
        np.linalg.norm(matrix, axis=1) * np.linalg.norm(query_vector) + 1e-12
    )
    return [(int(i), float(scores[i])) for i in np.argsort(-scores)[:limit]]


def unit_rows(vectors: Sequence[bytes]) -> np.ndarray:
    """The stored `vectors` as the rows of a matrix, each scaled to length 1, so that
    `nearest_rows` finds by cosine similarity what `nearest` does, without working out
    their lengths again each time. Read-only."""
    if not vectors:
        return np.empty((0, 0), dtype=np.float32)
    matrix = np.stack([np.frombuffer(v, dtype=np.float32) for v in vectors])
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
    matrix.flags.writeable = False  # searched on many threads at once
    return matrix


def nearest_rows(
    matrix: np.ndarray, query: Sequence[float], limit: int, rows: np.ndarray | None = None
) -> list[tuple[int, float]]:
    """Like `nearest`, among the rows of a `unit_rows` matrix, or only those of it at
    `rows`: their indexes (among `rows`, if given) and scores, best first."""
    among = matrix if rows is None else matrix[rows]
    if not len(among):
        return []
    query_vector = np.asarray(query, dtype=np.float32)
    scores = among @ (query_vector / (np.linalg.norm(query_vector) + 1e-12))
    return [(int(i), float(scores[i])) for i in np.argsort(-scores)[:limit]]


def to_blob(vector: Sequence[float]) -> bytes:
    """A vector as stored in SQLite."""
    return np.asarray(vector, dtype=np.float32).tobytes()
