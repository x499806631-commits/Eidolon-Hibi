"""The API for signing in and out, and for the user's account.

Signing in with Google or GitHub goes through the browser:

    /api/auth/{provider}/start     → the provider's sign-in page
    /api/auth/{provider}/callback  ← back from it, with a code and the state sent there

The state is kept in a short-lived cookie of its own, so a callback can only finish a
sign-in this browser started. The callback sends the browser on: home when signed in,
the sign-in page with `?error=<reason>` when not (see `accounts.Refusal`, plus `state`
for a callback this browser did not start or that expired, `cancelled` when the user
turned the provider down, and `provider` when the provider could not be reached).

A signed-in user links another identity the same way, starting with `?link=1`; the
callback then sends the browser back to the sign-in settings (`/settings/sign-in`), with
`?linked=<provider>` or `?error=<reason>`.
"""

import logging
import secrets
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import (
    BaseModel, Field, Secret, StrictBool, StringConstraints, field_validator, model_validator,
)

from eidolon.accounts import SignInRefused, is_admin
from eidolon.activity import host
from eidolon.assembly import Backend
from eidolon.config import (
    DEFAULT_FISH_AUDIO_MODEL,
    SERVICES,
    BailianRegion,
    ChatModelSettings,
    FishAudioModel,
    ImageFormat,
    ImageSettings,
    LLMProtocol,
    LLMSettings,
    ReasoningEffort,
    SttKey,
    SttProvider,
    checked_extra_body,
    effort_problem,
)
from eidolon.features import FEATURES, Feature
from eidolon.identity import PROVIDER_NAMES, IdentityError
from eidolon.own_keys import NO_PHOTOS, BroughtKeys, OwnKeyRejected, named_services
from eidolon.price_catalog import describe_thinking
from eidolon.reach_outs import ReachOutsOff
from eidolon.store import LastSignInMethod
from eidolon.sessions import (
    SESSION_COOKIE,
    SignedIn,
    UserId,
    clear_session_cookie,
    session_user,
    set_session_cookie,
)

log = logging.getLogger(__name__)

STATE_COOKIE = "eidolon_oauth_state"
STATE_LIFETIME = 600
"""How long, in seconds, the browser has to sign in at the provider and come back."""


class DevLogin(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class Deletion(BaseModel):
    """Deleting the user's data needs saying so, not just a request that might be a slip."""

    confirm: bool

    @model_validator(mode="after")
    def _confirmed(self) -> "Deletion":
        if not self.confirm:
            raise ValueError("需要确认才能删除全部数据")
        return self


ApiKey = Secret[
    Annotated[str, StringConstraints(min_length=8, max_length=500, pattern=r"^[\x21-\x7e]+$")]
]
"""A user's own key as a request brings it, never shown in a repr or traceback."""


class OwnLLMInput(BaseModel):
    """A model of the user's own, as their browser keeps it and brings it with each text
    turn and call: its effort and extra body checked here each time, as the admin console
    checks the deployer's when saved, for they are never saved (ADR-0017); left out by
    pages from before, for none."""

    base_url: str = Field(min_length=1, max_length=500)
    api_key: ApiKey
    model: str = Field(min_length=1, max_length=200)
    protocol: LLMProtocol = "chat-completions"
    reasoning_effort: ReasoningEffort | None = None
    extra_body: dict[str, Any] | None = None

    @field_validator("extra_body", mode="before")
    @classmethod
    def _extra_body(cls, value: object) -> dict[str, Any] | None:
        return None if value is None else checked_extra_body(value)

    @model_validator(mode="after")
    def _effort(self) -> "OwnLLMInput":
        _require_writable(self.reasoning_effort, self.protocol)
        return self

    def settings(self) -> LLMSettings:
        """The model as turns run on it: its address and model without surrounding spaces."""
        return LLMSettings(
            self.base_url.strip(), self.api_key.get_secret_value(), self.model.strip(),
            self.protocol, reasoning_effort=self.reasoning_effort, extra_body=self.extra_body,
        )


class OwnChatLLMInput(OwnLLMInput):
    """The user's own LLM playing the characters, at an effort of its own in calls (see
    `ChatModelSettings`)."""

    call_reasoning_effort: ReasoningEffort | None = None

    @model_validator(mode="after")
    def _call_effort(self) -> "OwnChatLLMInput":
        _require_writable(self.call_reasoning_effort, self.protocol)
        return self

    def chat_settings(self) -> ChatModelSettings:
        """Its `settings`, with its effort in calls."""
        return ChatModelSettings(self.settings(), self.call_reasoning_effort)


def _require_writable(effort: ReasoningEffort | None, protocol: LLMProtocol) -> None:
    """Raise ValueError saying why, if `protocol` has no way to write `effort`."""
    if effort is not None and (problem := effort_problem(effort, protocol)):
        raise ValueError(problem)


class OwnTTSInput(BaseModel):
    """The user's own key for speech synthesis (Fish Audio), as their browser keeps it,
    with the model they chose; the free one, kept before they could choose."""

    api_key: ApiKey
    model: FishAudioModel = DEFAULT_FISH_AUDIO_MODEL


class OwnSTTInput(BaseModel):
    """The user's own key for speech recognition, as their browser keeps it: for the
    service it is for, with the region a DashScope key works in, as its try found."""

    api_key: ApiKey
    provider: SttProvider = "soniox"
    region: BailianRegion | None = None

    def key(self) -> SttKey:
        """As calls hear on it; a region only for DashScope."""
        region = self.region if self.provider == "bailian" else None
        return SttKey(self.provider, self.api_key.get_secret_value(), region)


class OwnImageInput(BaseModel):
    """The user's own image generation, as their browser keeps it: in which format, where,
    with which key and model; how many at a time and the art style are not theirs to set."""

    api_format: ImageFormat
    base_url: str = Field(min_length=1, max_length=500)
    api_key: ApiKey
    model: str = Field(min_length=1, max_length=200)

    def settings(self) -> ImageSettings:
        """As her photos are generated on it: its address without surrounding spaces or a
        trailing slash, as the admin console keeps the deployer's, and its model without
        surrounding spaces."""
        return ImageSettings(
            self.api_key.get_secret_value(), self.base_url.strip().rstrip("/"),
            self.model.strip(), self.api_format,
        )


class OwnKeysInput(BaseModel):
    """The user's own keys a turn or a call brings, by service; each may be left out. Their
    own model for memories (`memory_llm`) only with their own LLM."""

    llm: OwnChatLLMInput | None = None
    tts: OwnTTSInput | None = None
    stt: OwnSTTInput | None = None
    memory_llm: OwnLLMInput | None = None
    image: OwnImageInput | None = None

    @model_validator(mode="after")
    def _memory_llm_with_llm(self) -> "OwnKeysInput":
        if self.memory_llm is not None and self.llm is None:
            raise ValueError("后台模型要和自带的 LLM 一起带上")
        return self

    def brought(self) -> BroughtKeys:
        return BroughtKeys(
            self.llm.chat_settings() if self.llm else None,
            self.tts.api_key.get_secret_value() if self.tts else None,
            self.stt.key() if self.stt else None,
            self.memory_llm.settings() if self.memory_llm else None,
            self.tts.model if self.tts else DEFAULT_FISH_AUDIO_MODEL,
            self.image.settings() if self.image else None,
        )


class ReachOutSwitch(BaseModel):
    """The user's own switch for reach-outs (GLOSSARY.md「搭话」)."""

    on: StrictBool


class FeatureSwitch(BaseModel):
    """The user's own switch for one of the fork's features (GLOSSARY.md「玩法开关」)."""

    on: StrictBool


def available_features(backend: Backend) -> list[Feature]:
    """The features the deployment has, which a user may switch off, in the order they are
    shown: her scenes where affinity is on, her diary, letters and schedule (with her sleep
    and her bath) where `.env` turns them on, the special days with the calendar, the weather
    with the producer's place, and always the anniversaries and the promises."""
    settings = backend.settings
    has: dict[Feature, bool] = {
        "scenes": backend.config.affinity_on,
        "diary": settings.diary,
        "letters": settings.letters,
        "keepsakes": settings.keepsakes,
        "schedules": settings.schedules,
        "sleep": settings.schedules,
        "bath": settings.schedules,
        "anniversaries": True,
        "special_days": backend.special_days is not None,
        "promises": True,
        "weather": backend.weather is not None,
    }
    return [f for f in FEATURES if has[f]]


class Consent(BaseModel):
    """Agreeing to the privacy policy and terms of use, and saying they are 13 or older."""

    agree_to_terms: bool
    at_least_13: bool

    @model_validator(mode="after")
    def _both(self) -> "Consent":
        if not self.agree_to_terms:
            raise ValueError("需要同意隐私政策和使用条款")
        if not self.at_least_13:
            raise ValueError("需要年满 13 岁才能使用")
        return self


def add_account_routes(app: FastAPI, backend: Backend) -> None:
    store, settings, clock = backend.store, backend.settings, backend.clock
    accounts, providers = backend.accounts, backend.identity_providers

    def callback_url(provider: str) -> str:
        return f"{settings.public_origin}/api/auth/{provider}/callback"

    def signed_in(user_id: str, response: Response, request: Request) -> None:
        """Start a session for the user in this browser, ending the one it had."""
        if old := request.cookies.get(SESSION_COOKIE):
            store.end_session(old)
        set_session_cookie(response, store.create_session(user_id, clock()), settings)

    @app.get("/api/auth/providers")
    def sign_in_providers() -> dict:
        """The ways to sign in on offer, in the order to show them, and how to reach the
        deployer, which the sign-in page tells a banned user (None if the deployer gave
        none)."""
        return {
            "providers": [p for p in PROVIDER_NAMES if p in providers],
            "dev_login": settings.dev_login,
            "contact": settings.contact,
        }

    @app.get("/api/auth/{provider}/start")
    def start_sign_in(provider: str, request: Request, link: bool = False) -> RedirectResponse:
        """Off to the provider to sign in, or with `link`, to link another identity to the
        signed-in user."""
        identity_provider = providers.get(provider)  # pyright: ignore[reportArgumentType]
        if identity_provider is None:
            raise HTTPException(404, "没有这种登录方式")
        if link:
            user = session_user(request)
            if user is None or user.consented_at is None:
                return RedirectResponse("/login", status_code=302)
        state = secrets.token_urlsafe(24)
        response = RedirectResponse(
            identity_provider.authorize_url(state, callback_url(provider)), status_code=302
        )
        response.set_cookie(
            STATE_COOKIE,
            f"{'link' if link else 'login'}:{provider}:{state}",
            max_age=STATE_LIFETIME,
            path="/api/auth",
            httponly=True,
            samesite="lax",
            secure=settings.domain is not None,
        )
        return response

    @app.get("/api/auth/{provider}/callback")
    async def finish_sign_in(
        provider: str,
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> RedirectResponse:
        purpose, expected_provider, expected_state = (
            request.cookies.get(STATE_COOKIE, "").split(":", 2) + ["", ""]
        )[:3]
        linking = purpose == "link"
        back = "/settings/sign-in" if linking else "/login"

        def go(to: str) -> RedirectResponse:
            response = RedirectResponse(to, status_code=302)
            response.delete_cookie(STATE_COOKIE, path="/api/auth")
            return response

        identity_provider = providers.get(provider)  # pyright: ignore[reportArgumentType]
        if (
            identity_provider is None
            or not state
            or not expected_state
            or expected_provider != provider
            or not secrets.compare_digest(state, expected_state)
        ):
            return go(f"{back}?error=state")
        if error or not code:
            return go(f"{back}?error=cancelled")
        try:
            identity = await identity_provider.identify(code, callback_url(provider))
        except IdentityError:
            log.warning("signing in with %s failed", provider, exc_info=True)
            return go(f"{back}?error=provider")
        try:
            if linking:
                user = session_user(request)
                if user is None or user.consented_at is None:
                    return go("/login?error=state")
                accounts.link(user.id, identity)
                return go(f"/settings/sign-in?linked={provider}")
            user = accounts.sign_in(identity)
        except SignInRefused as e:
            return go(f"{back}?error={e.reason}")
        response = go("/conversations")
        signed_in(user.id, response, request)
        return response

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> None:
        """Sign this browser out; its session ends even if it has already expired."""
        if token := request.cookies.get(SESSION_COOKIE):
            store.end_session(token)
        clear_session_cookie(response, settings)

    @app.get("/api/account")
    def account(user_id: UserId) -> dict:
        """The signed-in user, by the id their browser keeps their own keys apart by, the
        identities they sign in with, whether they are an admin, who may enter the admin
        console, the exempting keys now (GLOSSARY.md「免限额密钥」), in a fixed order, and
        whether they have their own switch for reach-outs on (GLOSSARY.md「搭话」), None where
        the deployer has reach-outs off, the user's own switches for the features the
        deployment has (GLOSSARY.md「玩法开关」), and the version of the sponsor code
        (GLOSSARY.md「赞赏」) while users are shown it, else None."""
        switches = backend.features.of(user_id)
        return {
            "id": user_id,
            "name": store.get_user(user_id).name,
            "sign_in_methods": [
                {"provider": m.provider, "display_name": m.display_name, "email": m.email}
                for m in store.sign_in_methods(user_id)
            ],
            "is_admin": is_admin(store, settings, user_id),
            "exempting_keys": [s for s in SERVICES if s in backend.config.exempting_keys],
            "reach_outs": backend.reach_outs.switch_of(user_id),
            "features": {f: switches[f] for f in available_features(backend)},
            "web_search": backend.web is not None,
            "sponsor_version": backend.sponsor.state().version,
        }

    @app.put("/api/account/reach-outs", status_code=204)
    def switch_reach_outs(body: ReachOutSwitch, user_id: UserId) -> None:
        """Turn the user's own switch for reach-outs on or off, for all the characters; 409
        where the deployer has reach-outs off."""
        try:
            backend.reach_outs.switch(user_id, body.on)
        except ReachOutsOff:
            raise HTTPException(409, "搭话没有开放") from None

    @app.put("/api/account/features/{feature}", status_code=204)
    def switch_feature(feature: str, body: FeatureSwitch, user_id: UserId) -> None:
        """Turn the user's own switch for `feature` on or off, for all the characters
        (GLOSSARY.md「玩法开关」); 404 for one the deployment does not have."""
        for known in available_features(backend):
            if known == feature:
                backend.features.switch(user_id, known, body.on)
                return
        raise HTTPException(404, "没有这个玩法")

    @app.delete("/api/account/sign-in-methods/{provider}", status_code=204)
    def unlink(provider: str, user_id: UserId) -> None:
        """Stop signing in with the identity at `provider`; never the last one."""
        try:
            found = store.remove_sign_in_method(user_id, provider)
        except LastSignInMethod:
            raise HTTPException(409, "这是你唯一的登录方式，解绑后就无法再登录了") from None
        if not found:
            raise HTTPException(404, "没有绑定这种登录方式")

    @app.post("/api/account/logout-everywhere", status_code=204)
    def logout_everywhere(user_id: UserId, response: Response) -> None:
        """Sign the user out on every device, this one included."""
        store.end_sessions(user_id)
        clear_session_cookie(response, settings)

    @app.post("/api/account/delete", status_code=204)
    async def delete_account(body: Deletion, user: SignedIn, response: Response) -> None:
        """Delete the user and all their data for good, signing them out everywhere; allowed
        before they agree to the terms too."""
        await accounts.delete(user.id)
        clear_session_cookie(response, settings)

    own_keys = backend.own_keys

    @app.exception_handler(OwnKeyRejected)
    async def own_key_rejected(_: Request, e: OwnKeyRejected) -> JSONResponse:
        """A user's own key failed its try: what the service said."""
        return JSONResponse({"detail": f"试调失败：{e}"}, status_code=422)

    @app.get("/api/account/keys/exemption")
    def exemption(user_id: UserId, own: str = "") -> dict:
        """Whether the user's text turns and calls would count towards nothing, if they
        brought their own keys for the services `own` names (as `llm,tts,stt`), but none
        of the keys: only by the rule, whatever their own limits and whether calls are
        available."""
        services = named_services(own)
        return {
            "turns": own_keys.exempt("turn", services),
            "calls": own_keys.exempt("call", services),
        }

    @app.post("/api/account/keys/llm/check")
    async def check_own_llm(body: OwnChatLLMInput, user_id: UserId) -> dict:
        """Try the user's own LLM at its text effort and its call effort before their
        browser keeps it, keeping nothing here; 422 with what the service said if the try
        failed, or with why its effort or extra body cannot be sent. Its address and model
        as the browser is to keep them."""
        own = body.chat_settings()
        await own_keys.check_llm(own)
        return {"base_url": own.text.base_url, "model": own.text.model}

    @app.post("/api/account/keys/memory-llm/check")
    async def check_own_memory_llm(body: OwnLLMInput, user_id: UserId) -> dict:
        """Try the user's own model for memories at its effort before their browser keeps
        it, as `check_own_llm` does their LLM; why it failed comes after saying it was this
        one."""
        own = body.settings()
        await own_keys.check_memory_llm(own)
        return {"base_url": own.base_url, "model": own.model}

    @app.get("/api/account/keys/llm/efforts")
    def llm_efforts(
        user_id: UserId,
        protocol: LLMProtocol,
        base_url: Annotated[str, Query(max_length=500)],
        model: Annotated[str, Query(max_length=200)],
    ) -> dict | None:
        """How the model at `base_url` named `model` thinks, as models.dev lists it, for
        the efforts `protocol` sends (`price_catalog.describe_thinking`), or None if it
        is not listed, which has the catalogs fetched again if they were last tried a
        while ago. A hint for the deployer's model and a user's own alike: no key needed,
        nothing kept."""
        catalog = backend.prices.catalog
        try:  # the host, as the model's calls are priced by
            thinking = catalog.thinking(host(base_url.strip()), model.strip())
        except ValueError:  # not an address (`http://[`)
            thinking = None
        if thinking is None:
            catalog.refresh_soon(missing=True)
            return None
        return describe_thinking(thinking, protocol)

    @app.post("/api/account/keys/tts/check")
    async def check_own_tts(body: OwnTTSInput, user_id: UserId) -> dict:
        """Try the user's own key for speech synthesis before their browser keeps it,
        keeping nothing here; 422 with what the service said if the try failed."""
        await own_keys.check_tts(body.api_key.get_secret_value(), body.model)
        return {}

    @app.post("/api/account/keys/stt/check")
    async def check_own_stt(body: OwnSTTInput, user_id: UserId) -> dict:
        """Try the user's own key for speech recognition, by the service it is for, before
        their browser keeps it, keeping nothing here; 422 with what the service said if
        the try failed. A DashScope key is tried in each region: the one it works in, for
        the browser to keep with it (None for Soniox)."""
        key = await own_keys.check_stt(body.provider, body.api_key.get_secret_value())
        return {"region": key.region}

    @app.post("/api/account/keys/image/check")
    async def check_own_image(body: OwnImageInput, user_id: UserId) -> dict:
        """Try the user's own image generation before their browser keeps it, by generating
        one picture at their expense in the deployment's art style, keeping, counting and
        recording nothing; 422 with what the service said if the try failed, or untried
        where there is no judge model, without which no turn has a photo. Its address and
        model as the browser is to keep them."""
        if backend.config.judge_settings is None:
            raise OwnKeyRejected(NO_PHOTOS)
        own = body.settings()
        await own_keys.check_image(own)
        return {"base_url": own.base_url, "model": own.model}

    @app.post("/api/account/consent", status_code=204)
    def consent(body: Consent, user: SignedIn) -> None:
        """Agree to the privacy policy and terms of use, which the rest of the API waits for."""
        store.consent(user.id, clock())

    if settings.dev_login:

        @app.post("/api/auth/dev-login", status_code=204)
        def dev_login(body: DevLogin, request: Request, response: Response) -> None:
            """Sign in as the user with this name, made if there is none (development only)."""
            user = store.first_user_named(body.name) or store.create_user(body.name)
            signed_in(user.id, response, request)
