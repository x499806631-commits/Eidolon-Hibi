"""The admin console's configuration, under `/api/admin/config`, for admins only: the live
configuration (`live_config`) as `.env` has it and as it is in effect, changing an item
and restoring it to `.env`'s (ADR-0013). Of a key, only its last 4 characters are ever
shown; a key left out of a change stays as it is. What cannot be changed here
(`live_config.READ_ONLY`) is refused, saying why."""

from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictBool, ValidationError, field_validator

from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.config import (
    MAX_IMAGE_CONCURRENCY,
    BailianRegion,
    FishAudioModel,
    ImageFormat,
    JudgeProvider,
    LLMProtocol,
    SttProvider,
)
from eidolon.live_config import (
    AFFINITY,
    CHAT_LLM,
    IMAGE,
    JUDGE,
    JUDGE_THRESHOLDS,
    LIMITS,
    MEMORY_LLM,
    PROXY,
    REACH_OUTS,
    READ_ONLY,
    RERANK,
    SERVICE_STATUS,
    STT,
    STT_KEY_FIELDS,
    TTS,
    ConfigInvalid,
    ConfigRejected,
    KeysReadOnly,
    Unavailable,
)

ApiKey = Field(None, min_length=8, max_length=500, pattern=r"^[\x21-\x7e]+$")


def _trimmed(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("不能为空")
    return value


def _trimmed_if_given(value: str | None) -> str | None:
    return None if value is None else _trimmed(value)


class LLMInput(BaseModel):
    """A model slot as the admin saves it; without `api_key`, its key stays as it is."""

    protocol: LLMProtocol
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int | None = Field(None, gt=0)
    # Both checked by the live configuration, which says what is wrong in so many words.
    reasoning_effort: Any = None
    extra_body: Any = None
    api_key: str | None = ApiKey

    _trim = field_validator("base_url", "model")(_trimmed)


class ChatLLMInput(LLMInput):
    """The chat model, with its reasoning effort in calls besides its own, for text turns."""

    call_reasoning_effort: Any = None


class TTSInput(BaseModel):
    model: FishAudioModel
    api_key: str | None = ApiKey


class STTInput(BaseModel):
    """The service chosen, and its key, if it changes."""

    provider: SttProvider
    api_key: str | None = ApiKey


class ImageInput(BaseModel):
    """Image generation; without `api_key`, its key stays as it is. An art style left out
    or empty is none."""

    api_format: ImageFormat
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    concurrency: int = Field(ge=1, le=MAX_IMAGE_CONCURRENCY)
    style: str | None = Field(None, max_length=1000)
    api_key: str | None = ApiKey

    _trim = field_validator("base_url", "model")(_trimmed)


class JudgeInput(BaseModel):
    provider: JudgeProvider
    model: str = Field(min_length=1, max_length=200)
    account_id: str | None = Field(None, min_length=1, max_length=100)
    workspace_id: str | None = Field(None, min_length=1, max_length=100)
    region: BailianRegion | None = None
    emotion_timeout: float = Field(gt=0, le=30)
    api_key: str | None = ApiKey

    _trim = field_validator("model")(_trimmed)
    _trim_workspace = field_validator("workspace_id")(_trimmed_if_given)


class JudgeThresholdsInput(BaseModel):
    recall: float = Field(ge=0, le=1)
    emotion: float = Field(ge=0, le=1)
    out_of_line: float = Field(ge=0, le=1)
    manipulation: float = Field(ge=0, le=1)
    photo: float = Field(ge=0, le=1)


class RerankInput(BaseModel):
    references: float = Field(ge=0, le=1)
    memories: float = Field(ge=0, le=1)


class LimitsInput(BaseModel):
    """Each a whole number from 0 up, 0 for no limit, and whether each service is one of
    the exempting keys, true or false, not all false; checked by the live configuration,
    which says what is wrong in so many words."""

    turns_per_hour: Any
    calls_per_hour: Any
    photos_per_hour: Any
    call_minutes: Any
    turns_per_call: Any
    history_turns: Any
    exempting_llm: Any
    exempting_tts: Any
    exempting_stt: Any


class AffinityInput(BaseModel):
    """Whether affinity and gifts are on, true or false; checked by the live configuration."""

    enabled: Any


class ReachOutsInput(BaseModel):
    """Whether characters reach out to users, true or false, and how many times a day each
    user at most, a whole number from 1 up; checked by the live configuration."""

    enabled: Any
    per_day: Any


class ProxyInput(BaseModel):
    """The proxy as the admin saves it: the switch, the address (a password typed into it
    is kept apart, as a key is; empty for none), and a switch for each of the deployer's
    services and of users' own keys; without `password`, it stays as it is. The address
    is checked by the live configuration, which says what is wrong in so many words."""

    enabled: StrictBool
    url: str | None = Field(None, max_length=500)
    password: str | None = Field(None, min_length=1, max_length=500)
    chat_llm: StrictBool
    memory_llm: StrictBool
    judge: StrictBool
    embedding: StrictBool
    rerank: StrictBool
    tts: StrictBool
    stt: StrictBool
    image: StrictBool
    web_search: StrictBool
    own_llm: StrictBool
    own_tts: StrictBool
    own_stt: StrictBool
    own_image: StrictBool


class ServiceStatusInput(BaseModel):
    """Which services users are shown the status of, a switch for each, and over how many
    minutes, checked by the live configuration, which says what is wrong in so many words."""

    window_minutes: Any
    chat_llm: StrictBool
    memory_llm: StrictBool
    tts: StrictBool
    stt: StrictBool
    judge: StrictBool
    embedding: StrictBool
    rerank: StrictBool
    image: StrictBool
    web_search: StrictBool


INPUTS: dict[str, type[BaseModel]] = {
    CHAT_LLM: ChatLLMInput,
    MEMORY_LLM: LLMInput,
    TTS: TTSInput,
    STT: STTInput,
    IMAGE: ImageInput,
    JUDGE: JudgeInput,
    JUDGE_THRESHOLDS: JudgeThresholdsInput,
    RERANK: RerankInput,
    LIMITS: LimitsInput,
    AFFINITY: AffinityInput,
    REACH_OUTS: ReachOutsInput,
    PROXY: ProxyInput,
    SERVICE_STATUS: ServiceStatusInput,
}


def _change(item: str, body: BaseModel) -> dict[str, Any]:
    """What the input changes of the item: every field given (a model slot's max_tokens
    may be None, its protocol's default), a key, the judge model's account, workspace
    and region and a proxy's password only if given."""
    change = body.model_dump()
    for field in ("account_id", "workspace_id", "region"):
        if change.get(field, "") is None:
            del change[field]
    if change.get("password", "") is None:
        del change["password"]
    if key := change.pop("api_key", None):
        change[STT_KEY_FIELDS[change["provider"]] if item == STT else "api_key"] = key
    return change


def add_admin_config_routes(app: FastAPI, backend: Backend) -> None:
    config = backend.config

    def item_or_error(item: str) -> str:
        if item in READ_ONLY:
            raise HTTPException(403, f"这一项只能在服务器上改：{READ_ONLY[item]}")
        if item not in INPUTS:
            raise HTTPException(404, "没有这项配置")
        return item

    @app.get("/api/admin/config")
    def get_config(_: Admin) -> dict:
        return config.describe()

    @app.put("/api/admin/config/{item}")
    async def save(item: str, _: Admin, body: dict = Body()) -> dict:  # noqa: B008
        """Try the item and, if it works, run every turn and call from now on on it."""
        item = item_or_error(item)
        try:
            parsed = INPUTS[item].model_validate(body)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False, include_context=False)) from None
        try:
            await config.save(item, _change(item, parsed))
        except KeysReadOnly as e:
            raise HTTPException(409, str(e)) from None
        except Unavailable as e:
            raise HTTPException(409, str(e)) from None
        except ConfigInvalid as e:
            raise HTTPException(422, str(e)) from None
        except ConfigRejected as e:
            raise HTTPException(422, f"试调失败，没有保存：{e}") from None
        return config.describe()

    @app.delete("/api/admin/config/{item}")
    async def restore(item: str, _: Admin) -> dict:
        """Back to `.env`'s, from the next turn and call on."""
        item = item_or_error(item)
        try:
            await config.restore(item)
        except Unavailable as e:
            raise HTTPException(409, str(e)) from None
        return config.describe()

