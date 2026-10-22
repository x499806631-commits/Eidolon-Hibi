"""The backend's HTTP API."""

import asyncio
import json
import math
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from eidolon.admission import CoolingDown
from eidolon.appeals_api import add_appeal_routes
from eidolon.assembly import Backend
from eidolon.call import (
    CallFailed,
    CallNotFound,
    CallOptions,
    NoAnswer,
    IceCandidate,
    Quality,
    SessionDescription,
)
from eidolon.characters import Character, ScriptedLine, in_idol_order
from eidolon.engine import ENDED, TurnJudged, shown
from eidolon.greetings import (
    LANDING_MEMORY_STILL_PATH,
    call_opener_path,
    call_poster_path,
    greeting_path,
    voxels_path,
)
from eidolon.reply_format import SegmentComplete, SegmentDelta, SpeechDelta
from eidolon.own_keys import BroughtKeys, CallsUnavailable, UnknownService, named_services
from eidolon.producer import (
    MAX_FORM_OF_ADDRESS_LENGTH,
    Birthday,
    Gender,
    ProducerProfile,
    is_day_of_the_year,
    is_timezone,
)
from eidolon.account_api import OwnKeysInput, add_account_routes
from eidolon.admin_api import add_admin_routes
from eidolon.admin_backup_api import add_admin_backup_routes
from eidolon.dashboard import add_dashboard_routes
from eidolon.data_package_api import add_data_package_routes
from eidolon.admin_users_api import add_admin_user_routes
from eidolon.admin_config_api import add_admin_config_routes
from eidolon.admin_prices_api import add_admin_price_routes
from eidolon.announcements_api import add_announcement_routes
from eidolon.gifts_api import add_gift_routes
from eidolon.photos_api import add_photo_routes
from eidolon.sessions import UserId, session_middleware
from eidolon.diary_api import add_diary_routes
from eidolon.keepsakes_api import add_keepsake_routes
from eidolon.schedules_api import add_schedule_routes
from eidolon.letters_api import add_letter_routes
from eidolon.experiences_api import add_experience_routes
from eidolon.promises_api import add_promise_routes
from eidolon.special_days_api import add_calendar_routes
from eidolon.sponsor_api import add_sponsor_routes
from eidolon.stats import add_stats_routes
from eidolon.service_status import add_service_status_routes
from eidolon.stand_outfits import list_path, outfit_path
from eidolon.store import Conversation, ConversationEvent, IsCurrent, ReachOut, Recorded
from eidolon.turn_outcome import TurnOutcome
from eidolon.usage import OverLimit
from eidolon.world import World

_PICTURE_CACHE = {"Cache-Control": "public, max-age=86400"}
"""Avatars and call backgrounds change only with a deploy (ADR-0003): browsers keep them a
day, then revalidate by ETag, so lists showing every avatar don't download them each visit."""


def _picture(path: Path, request: Request) -> Response:
    """An avatar or call background, or a 304 when the browser's copy is still current;
    FileResponse sets the ETag but, unlike StaticFiles, never answers 304 itself."""
    response = FileResponse(path, headers=_PICTURE_CACHE, stat_result=path.stat())
    etag = response.headers["etag"]
    cached_etags = request.headers.get("if-none-match", "")
    if etag in (tag.strip() for tag in cached_etags.split(",")):
        return Response(status_code=304, headers={"ETag": etag, **_PICTURE_CACHE})
    return response


class NewConversation(BaseModel):
    character_id: str


class TurnInput(BaseModel):
    text: str
    web: bool = False
    """Whether she may look things up on the web this turn (GLOSSARY.md「联网」): the user's
    browser keeps it per character, and sends it with every turn."""
    own_keys: OwnKeysInput | None = None
    """The user's own keys, which their browser keeps, for the turn to run on; the shape
    calls bring too. A turn uses only the LLMs and image generation, voice keys brought
    along go unused."""
    own_llm: object = None
    """Where pages from before brought their own LLM. Transitional, to be deleted once no
    such page is left open: a turn bringing it is refused, asking for a refresh, rather
    than quietly run on the deployer's key and counted."""


class CallRequest(BaseModel):
    conversation_id: str
    muted: bool = False
    quality: Quality = "standard"
    asmr: bool = False
    """An ASMR call (ASMR 模式通话): she speaks softly and slowly, whatever they talk about."""
    own_keys: OwnKeysInput | None = None
    """The user's own keys, which their browser keeps, for the call to run on."""


class BirthdayInput(BaseModel):
    month: int
    day: int

    @model_validator(mode="after")
    def _day_of_the_year(self) -> "BirthdayInput":
        if not is_day_of_the_year(self.month, self.day):
            raise ValueError(f"没有 {self.month} 月 {self.day} 日这一天")
        return self


class ProfileInput(BaseModel):
    """The whole producer profile; a field left out or empty returns to its default."""

    form_of_address: str | None = None
    gender: Gender | None = None
    timezone: str | None = None
    birthday: BirthdayInput | None = None

    @field_validator("form_of_address")
    @classmethod
    def _address(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if len(value) > MAX_FORM_OF_ADDRESS_LENGTH:
            raise ValueError(f"称呼最多 {MAX_FORM_OF_ADDRESS_LENGTH} 个字")
        return value or None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not is_timezone(value):
            raise ValueError(f"不认识的时区 {value}")
        return value or None


class Offer(BaseModel):
    """A WebRTC offer as the Pipecat client sends it; `requestData` carries our CallRequest."""

    model_config = ConfigDict(populate_by_name=True)

    sdp: str
    type: str
    pc_id: str | None = None
    restart_pc: bool | None = None
    call: CallRequest | None = Field(None, alias="requestData")


class IceCandidateInput(BaseModel):
    candidate: str
    sdp_mid: str
    sdp_mline_index: int


class IceCandidates(BaseModel):
    """ICE candidates the browser trickles in for one of its calls."""

    pc_id: str
    candidates: list[IceCandidateInput]


DeviceTimezone = Annotated[str | None, Header(alias="X-Timezone")]
"""The time zone the browser detected; the producer profile's own time zone overrides it."""


def _stand(character: Character, assets_url: str | None) -> dict | None:
    """Her stand on the call screen: where her default outfit's files are, and her outfit
    list (`stand_outfits`). None, and her call background alone, unless her idol, her
    default outfit and the stand assets are all configured."""
    if character.idol_id is None or character.default_outfit is None or assets_url is None:
        return None
    outfit = character.default_outfit
    return {
        "default_outfit": {"id": outfit, "url": f"{assets_url}/{outfit_path(outfit)}"},
        "outfits_url": f"{assets_url}/{list_path(character.idol_id)}",
    }


def _greeting(character: Character, assets_url: str | None) -> dict | None:
    """Her greeting on the landing page: what she says as people see it, its subtitle, and
    where `eidolon greeting upload` puts its audio (`greeting_path`), without asking the
    bucket whether it is there; no audio unless the stand assets and her voice are
    configured. None without a greeting."""
    return _scripted(character.greeting, greeting_path(character), assets_url)


def _call_opener(character: Character, assets_url: str | None) -> dict | None:
    """Her call opener on the landing page, as her greeting is (`call_opener_path`); None
    without one."""
    return _scripted(character.call_opener, call_opener_path(character), assets_url)


def _call_poster(character: Character, assets_url: str | None) -> str | None:
    """Her card on the landing page's incoming call (ADR-0003, 2026-10-12), at
    `call_poster_path`, listed without asking the bucket whether it is there (the page shows
    her colour instead when it is not). None unless the stand assets are served."""
    return f"{assets_url}/{call_poster_path(character)}" if assets_url else None


def _voxels(character: Character, assets_url: str | None) -> str | None:
    """Her voxel figure in the landing page's act about memory (ADR-0003, 2026-10-16), at
    `voxels_path`, listed as her call poster is."""
    return f"{assets_url}/{voxels_path(character)}" if assets_url else None


def _scripted(line: ScriptedLine | None, path: str | None, assets_url: str | None) -> dict | None:
    if line is None:
        return None
    return {
        "ja": line.ja,
        "subtitle": line.subtitle,
        "audio_url": f"{assets_url}/{path}" if assets_url and path else None,
    }


def _unit(character: Character, world: World | None) -> dict:
    """Her unit, its reading in hiragana, and where it is in the units' official order
    (`World.unit_of`); all None without the world setting, or if its sources could not
    agree on her unit."""
    unit, order = (world.unit_of(character.name) if world else None) or (None, None)
    reading = world.units[order].reading if world and order is not None else None
    return {"unit": unit, "unit_reading": reading, "unit_order": order}


def create_app(backend: Backend) -> FastAPI:
    """The API over `backend`, which the app closes when it shuts down."""
    store, characters, engine = backend.store, backend.characters, backend.engine
    memory, calls, admissions = backend.memories, backend.calls, backend.admissions
    gift_catalog, titles = backend.gift_catalog, backend.titles

    def gift_of(event: ConversationEvent) -> dict:
        """For a gift record, which gift and its name, to show it by."""
        if event.gift_id is None:
            return {}
        return {"gift_id": event.gift_id, "gift_name": gift_catalog[event.gift_id].name}

    def last_shown(last: Recorded | None) -> dict | None:
        """The last thing in a conversation as a list shows it: a message without its
        delivery cues, marked `photo` if it is her reply with a photo she sent (not one being
        generated or not sent after all), or a conversation event, or her reach-out, shown as
        her message is, marked `reach_out`; None while it has none."""
        if last is None:
            return None
        if isinstance(last, ConversationEvent):
            return {"type": "event", "kind": last.kind, "created_at": last.created_at} | gift_of(
                last
            )
        if isinstance(last, ReachOut):
            said = last.without_cues()
            return {
                "role": "character",
                "text": None,
                "ja": said.ja,
                "zh": said.zh,
                "created_at": said.created_at,
                "reach_out": True,
            }
        shown = last.without_cues()
        return {
            "role": shown.role,
            "text": shown.text,
            "ja": shown.ja,
            "zh": shown.zh,
            "created_at": shown.created_at,
        } | ({"photo": True} if shown.photo_sent else {})

    def recorded(entry: Recorded) -> dict:
        """A message, conversation event or reach-out as the browser reads it."""
        if isinstance(entry, ConversationEvent):
            return entry.shown() | gift_of(entry)
        return entry.shown()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await backend.start()
        yield
        await backend.aclose()

    app = FastAPI(title="Eidolon-SC", lifespan=lifespan)
    app.state.backend = backend
    app.middleware("http")(session_middleware(backend.settings))

    @app.exception_handler(OverLimit)
    async def over_limit(_: Request, e: OverLimit) -> JSONResponse:
        """Refused for now, with when the user can go on (also as `Retry-After`); or, set
        to their own keys only, with no time to wait for."""
        if e.retry_at is None:
            return JSONResponse({"detail": str(e), "retry_at": None}, status_code=429)
        wait = max(1, math.ceil((e.retry_at - e.now).total_seconds()))
        return JSONResponse(
            {"detail": str(e), "retry_at": e.retry_at.isoformat()},
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    @app.exception_handler(CoolingDown)
    async def cooling_down(_: Request, e: CoolingDown) -> JSONResponse:
        """Refused while she is in a cooldown, with when it ends; told apart from the limit's
        429."""
        return JSONResponse(
            {"detail": str(e), "cooldown_until": e.cooldown_until.isoformat()}, status_code=409
        )

    @app.exception_handler(CallsUnavailable)
    async def calls_unavailable(_: Request, e: CallsUnavailable) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=503)

    @app.exception_handler(UnknownService)
    async def unknown_service(_: Request, e: UnknownService) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(CallNotFound)
    async def call_not_found(_: Request, e: CallNotFound) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(CallFailed)
    async def call_failed(_: Request, e: CallFailed) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=500)

    @app.exception_handler(NoAnswer)
    async def no_answer(_: Request, e: NoAnswer) -> JSONResponse:
        """She did not pick up; told apart from the other refusals' 409."""
        return JSONResponse({"detail": str(e), "code": "no_answer"}, status_code=409)

    def conversation_or_404(conversation_id: str, user_id: str) -> Conversation:
        conversation = store.get_conversation(conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise HTTPException(404, "对话不存在")
        if conversation.character_id not in characters:  # removed from the configuration
            raise HTTPException(404, "角色不存在")
        return conversation

    def refuse_if_ended(conversation: Conversation) -> None:
        """A call into a conversation she has ended (GLOSSARY.md「已结束的对话」) is refused,
        before anything is asked or spent; told apart from the limit's 429."""
        if store.ended(conversation.id) is not None:
            raise HTTPException(409, ENDED)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    add_account_routes(app, backend)
    add_admin_routes(app, backend)
    add_dashboard_routes(app, backend)
    add_admin_backup_routes(app, backend)
    add_admin_user_routes(app, backend)
    add_admin_config_routes(app, backend)
    add_admin_price_routes(app, backend)
    add_announcement_routes(app, backend)
    add_gift_routes(app, backend)
    add_calendar_routes(app, backend)
    add_letter_routes(app, backend)
    add_diary_routes(app, backend)
    add_keepsake_routes(app, backend)
    add_schedule_routes(app, backend)
    add_appeal_routes(app, backend.appeals)
    add_promise_routes(app, backend)
    add_experience_routes(app, backend)
    add_photo_routes(app, backend)
    add_sponsor_routes(app, backend)
    add_data_package_routes(app, backend)
    add_stats_routes(app, backend)
    add_service_status_routes(app, backend.service_status)

    @app.get("/api/ice-servers")
    def ice_servers(user_id: UserId, own: str = "", conversation: str | None = None) -> dict:
        """What the browser needs before calling, if the call is to bring the user's own
        keys for the services `own` names (as `llm,tts,stt`), but none of the keys; also
        tells it when calls are unavailable, or, for a call into `conversation`, when she
        is in a cooldown or has ended it."""
        character_id = None
        if conversation:
            into = conversation_or_404(conversation, user_id)
            refuse_if_ended(into)
            character_id = into.character_id
        return calls.ice_servers(user_id, named_services(own), character_id)

    @app.post("/api/offer")
    async def offer(
        body: Offer, user_id: UserId, device_timezone: DeviceTimezone = None
    ) -> dict:
        """WebRTC signalling: a new call into one of the user's conversations, on the user's
        own keys it brings, or a renegotiation, which brings none."""
        description = SessionDescription(body.sdp, body.type)
        if body.pc_id is not None:  # renegotiating, possibly on a fresh peer connection
            return await calls.renegotiate(
                user_id, body.pc_id, description, bool(body.restart_pc)
            )
        if body.call is None:
            raise HTTPException(422, "缺少要通话的对话")
        conversation = conversation_or_404(body.call.conversation_id, user_id)
        refuse_if_ended(conversation)
        if engine.phone(conversation, device_timezone) == "away":  # GLOSSARY.md「洗澡」
            store.miss_call(conversation.id)
            raise NoAnswer()
        return await calls.start(
            user_id,
            conversation,
            characters[conversation.character_id],
            CallOptions(body.call.muted, body.call.quality, device_timezone, body.call.asmr),
            description,
            body.call.own_keys.brought() if body.call.own_keys else BroughtKeys(),
        )

    @app.patch("/api/offer")
    async def ice_candidates(body: IceCandidates, user_id: UserId) -> dict:
        candidates = [
            IceCandidate(c.candidate, c.sdp_mid, c.sdp_mline_index) for c in body.candidates
        ]
        await calls.add_candidates(user_id, body.pc_id, candidates)
        return {"ok": True}

    @app.get("/api/profile")
    def get_profile(user_id: UserId) -> dict:
        return asdict(store.get_profile(user_id))

    @app.put("/api/profile")
    def update_profile(body: ProfileInput, user_id: UserId) -> dict:
        birthday = body.birthday and Birthday(body.birthday.month, body.birthday.day)
        profile = ProducerProfile(body.form_of_address, body.gender, body.timezone, birthday)
        store.save_profile(user_id, profile)
        return asdict(profile)

    @app.get("/api/characters")
    def list_characters() -> list[dict]:
        """Who there is to talk to; shown before signing in too, and on the landing page.
        In the order of their idol numbers, those without one after them as configured."""
        listed = in_idol_order(characters.values())
        return [
            {
                "id": c.id,
                "name": c.name,
                "intro": c.intro,
                "avatar_url": f"/api/characters/{c.id}/avatar" if c.avatar else None,
                "scsp_avatar_url": f"/api/characters/{c.id}/scsp-avatar" if c.scsp_avatar else None,
                "color": c.color,
                "background_url": f"/api/characters/{c.id}/background" if c.background else None,
                "stand": _stand(c, backend.settings.stand_assets_url),
                "idol_id": c.idol_id,
                "name_zh": c.name_zh,
                "name_kana": c.name_kana,
                "name_romaji": c.name_romaji,
                **_unit(c, backend.world),
                "greeting": _greeting(c, backend.settings.stand_assets_url),
                "call_opener": _call_opener(c, backend.settings.stand_assets_url),
                "call_poster_url": _call_poster(c, backend.settings.stand_assets_url),
                "voxels_url": _voxels(c, backend.settings.stand_assets_url),
            }
            for c in listed
        ]

    @app.get("/api/landing")
    def landing() -> dict:
        """What the landing page shows that belongs to no one character: its act about memory
        as one picture, for visitors who see no animation (ADR-0003, 2026-10-16). Listed as a
        call poster is, without asking the bucket whether it is there (the page shows only the
        words instead); None unless the stand assets are served."""
        assets_url = backend.settings.stand_assets_url
        return {"memory_still_url": f"{assets_url}/{LANDING_MEMORY_STILL_PATH}" if assets_url else None}

    @app.get("/api/characters/{character_id}/avatar")
    def character_avatar(character_id: str, request: Request) -> Response:
        character = characters.get(character_id)
        if character is None or character.avatar is None:
            raise HTTPException(404, "没有这位角色的头像")
        return _picture(character.avatar, request)

    @app.get("/api/characters/{character_id}/scsp-avatar")
    def character_scsp_avatar(character_id: str, request: Request) -> Response:
        """Her avatar in SCSP's set, for users who switched to it in settings."""
        character = characters.get(character_id)
        if character is None or character.scsp_avatar is None:
            raise HTTPException(404, "没有这位角色的 SCSP 头像")
        return _picture(character.scsp_avatar, request)

    @app.get("/api/characters/{character_id}/background")
    def character_background(character_id: str, request: Request) -> Response:
        """The picture behind her call screen, served like her avatar."""
        character = characters.get(character_id)
        if character is None or character.background is None:
            raise HTTPException(404, "没有这位角色的通话背景图")
        return _picture(character.background, request)

    @app.get("/api/characters/{character_id}/affinity")
    def affinity(character_id: str, user_id: UserId) -> dict | None:
        """Her affinity with the user now, by tier only, and when the cooldown ends if she
        is in one; null while affinity is off."""
        if character_id not in characters:
            raise HTTPException(404, "角色不存在")
        status = backend.affinities.status(user_id, character_id)
        return status and status.shown()

    @app.get("/api/memories")
    async def list_memories(user_id: UserId) -> list[dict]:
        """Newest first, each with the name of the character it was told to and whether
        it has been superseded (ADR-0014)."""
        return [
            {
                "id": m.id,
                "text": m.text,
                "source_character_id": m.source_character_id,
                "source_character_name": m.source_character_name,
                "created_at": m.created_at,
                "superseded": m.superseded_by is not None,
            }
            for m in await memory.entries(user_id)
        ]

    @app.delete("/api/memories/{memory_id}", status_code=204)
    async def delete_memory(memory_id: int, user_id: UserId) -> None:
        if not await memory.delete(user_id, memory_id):
            raise HTTPException(404, "记忆不存在")

    @app.delete("/api/memories", status_code=204)
    async def clear_memories(user_id: UserId) -> None:
        await memory.clear(user_id)

    @app.post("/api/conversations", status_code=201)
    def create_conversation(body: NewConversation, user_id: UserId) -> dict:
        if body.character_id not in characters:
            raise HTTPException(404, "角色不存在")
        return asdict(store.create_conversation(user_id, body.character_id))

    @app.get("/api/conversations")
    async def list_conversations(character_id: str, user_id: UserId) -> list[dict]:
        """Her conversations with the user, newest first, the first her current one; each
        with its title, null if it has none, the last thing in it, as the conversation list
        shows it, and whether she has ended it. Those being named are waited for a while."""
        await titles.settle(user_id)
        listed = await asyncio.to_thread(store.list_conversations, user_id, character_id)
        return [
            asdict(c) | {"last_message": last_shown(last), "ended": ended}
            for c, last, ended in listed
        ]

    @app.get("/api/conversations/latest")
    def latest_conversations(user_id: UserId) -> list[dict]:
        """For the conversation list: each character's newest conversation with the user, the
        last thing in it, a message, a conversation event or a reach-out (null while it has
        none), and whether it has a reach-out the user has not seen, in one request."""
        return [
            {
                "character_id": c.character_id,
                "conversation_id": c.id,
                "created_at": c.created_at,
                "last_message": last_shown(last),
                "unseen_reach_out": unseen,
            }
            for c, last, unseen in store.latest_conversations(user_id)
        ]

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str, user_id: UserId) -> None:
        """Delete a past conversation (GLOSSARY.md「历史对话」), for good; her current one is
        not deleted (409)."""
        try:
            store.delete_conversation(conversation_or_404(conversation_id, user_id))
        except IsCurrent:
            raise HTTPException(409, "当前对话不能删除") from None

    @app.get("/api/conversations/{conversation_id}/messages")
    def list_messages(conversation_id: str, user_id: UserId) -> list[dict]:
        """The conversation's record: its messages, conversation events and reach-outs, in
        order. The user reads it: its reach-outs are seen from now on."""
        conversation = conversation_or_404(conversation_id, user_id)
        return [recorded(entry) for entry in store.read_by_user(conversation.id)]

    @app.post("/api/conversations/{conversation_id}/turns")
    def text_turn(
        conversation_id: str,
        body: TurnInput,
        user_id: UserId,
        device_timezone: DeviceTimezone = None,
    ) -> StreamingResponse:
        conversation = conversation_or_404(conversation_id, user_id)
        if body.own_llm is not None:  # transitional: see `TurnInput.own_llm`
            raise HTTPException(422, "页面已经更新，请刷新后再发")
        brought = body.own_keys.brought() if body.own_keys else BroughtKeys()
        admission = admissions.turn(user_id, conversation.character_id, brought)
        outcome = TurnOutcome(admission.recording, admission.settle)

        async def events() -> AsyncIterator[str]:
            # Closed as the response is, so that the turn is no longer under way once the user
            # has gone (`Store.taking_turn`).
            with outcome.running():
                async with aclosing(engine.turn(
                    conversation, body.text, admission.llms, device_timezone,
                    web=body.web,
                    own_image=brought.image,
                )) as turn:
                    async for event in turn:
                        outcome.see(event)
                        if isinstance(event, SegmentDelta):
                            outcome.began_speaking()  # the reply's first words have appeared
                        if isinstance(event, SegmentComplete | SpeechDelta | TurnJudged):
                            continue
                        yield _sse(shown(event))

        return _TurnResponse(events(), lambda: outcome.over(cut_short_by_failure=False))

    return app


class _TurnResponse(StreamingResponse):
    """A text turn's events, `end` called once they are sent or the page is gone, even
    gone before they began, when the stream of them never runs."""

    def __init__(self, events: AsyncIterator[str], end: Callable[[], None]) -> None:
        super().__init__(
            events,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        self._end = end

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._end()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
