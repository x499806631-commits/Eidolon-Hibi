"""Asking a service which models it offers a key (`GET …/models`), for the settings page
and the admin console to pick a model from: what the key can use there, as the service
says, beside what models.dev lists (ADR-0027). Bounded, as anyone signed in may have the
server ask any service on the internet: a short timeout, a few pages, a capped body."""

import json
from typing import Any

import httpx

from eidolon.config import LLMProtocol
from eidolon.llm import LLMError
from eidolon.llm.anthropic import API_VERSION

LIST_TIMEOUT = httpx.Timeout(10, connect=5)
MAX_LIST_BYTES = 2_000_000
"""The most of a page read: a relay listing thousands of models runs to a few megabytes."""
MAX_PAGES = 3
MAX_MODELS = 2000
MAX_MODEL_LENGTH = 200
"""The longest model id kept, as long as one a user may write in."""


async def list_models(
    client: httpx.AsyncClient, protocol: LLMProtocol, base_url: str, api_key: str
) -> list[str]:
    """The ids of the models the service at `base_url` offers `api_key`, as it lists them
    (OpenAI's and Anthropic's `/models` alike), each once, at most `MAX_MODELS`. Raises
    LLMError saying why not."""
    url = base_url.strip().rstrip("/") + "/models"
    anthropic = protocol == "anthropic"
    headers = (
        {"x-api-key": api_key, "anthropic-version": API_VERSION}
        if anthropic else {"Authorization": f"Bearer {api_key}"}
    )
    params: dict[str, str] = {"limit": "1000"} if anthropic else {}
    ids: list[str] = []
    for _ in range(MAX_PAGES):
        page = await _page(client, url, headers, params)
        listed = page.get("data")
        if not isinstance(listed, list):
            raise LLMError("服务没有按 OpenAI 或 Anthropic 的格式列出模型")
        for m in listed:
            id = m.get("id") if isinstance(m, dict) else None
            if isinstance(id, str) and 0 < len(id) <= MAX_MODEL_LENGTH and id not in ids:
                ids.append(id)
        last = page.get("last_id")
        if len(ids) >= MAX_MODELS or page.get("has_more") is not True or not isinstance(last, str):
            break
        params = {**params, "after_id" if anthropic else "after": last}
    return ids[:MAX_MODELS]


async def _page(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], params: dict[str, str]
) -> dict[str, Any]:
    try:
        async with client.stream(
            "GET", url, headers=headers, params=params, timeout=LIST_TIMEOUT
        ) as res:
            body = await _read(res, MAX_LIST_BYTES if res.status_code < 400 else 2000)
    except httpx.HTTPError as e:
        raise LLMError(f"请求模型列表失败: {e!r}") from e
    text = body.decode(errors="replace")
    if res.status_code >= 400:
        raise LLMError(f"服务商返回 {res.status_code}：{_said(text)[:300]}")
    try:
        page = json.loads(text)
    except json.JSONDecodeError:
        raise LLMError("服务返回的模型列表无法解析") from None
    if not isinstance(page, dict):
        raise LLMError("服务没有按 OpenAI 或 Anthropic 的格式列出模型")
    return page


def _said(text: str) -> str:
    """What a service said in refusing, in its own words where its answer has them in one
    of the usual places (`{"error": {"message": …}}` and the like), else as it answered."""
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        return text.strip()
    if isinstance(answer, dict):
        error = answer.get("error")
        for said in (
            error.get("message") if isinstance(error, dict) else error,
            answer.get("message"),
            answer.get("detail"),
        ):
            if isinstance(said, str) and said.strip():
                return said.strip()
    return text.strip()


async def _read(res: httpx.Response, limit: int) -> bytes:
    """The body, up to `limit` bytes: an error's cut short, a list's refused past it."""
    body = bytearray()
    async for chunk in res.aiter_bytes():
        body += chunk
        if len(body) > limit:
            if res.status_code >= 400:
                return bytes(body[:limit])
            raise LLMError("服务返回的模型列表太大了")
    return bytes(body)
