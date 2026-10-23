import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from typing import Any, TypeVar

import httpx
import pytest

import eidolon.bailian
import eidolon.data_access
from eidolon.__main__ import main
from eidolon.activity import ActivityLog
from eidolon.app import create_app
from eidolon.assembly import Backend
from eidolon.clock import Clock
from eidolon.config import LLMSettings, Settings
from eidolon.data_access import Database
from eidolon.gift_catalog import GIFTS_FILE
from eidolon.identity import Identity
from eidolon.sessions import SESSION_COOKIE
from eidolon.store import Store
from tests.browser import Browser, open_browser
from tests.fakes import (
    DEPLOYER_FISH_KEY,
    DEPLOYER_SONIOX_KEY,
    START,
    FakeCatalog,
    FakeClock,
    FakeDashScope,
    FakeIdentityProvider,
    FakeLLM,
    FakeLLMService,
    FakeSpeech,
    build_faked,
)
from tests.stories import write_sample_stories

# A connection to the database taken on the event loop's thread fails the test, so that
# every path the tests run keeps the database off the loop.
eidolon.data_access.ON_THE_LOOP_RAISES = True

T = TypeVar("T")


def aside(work: Coroutine[Any, Any, T]) -> T:
    """What `work` comes to, run on an event loop of its own on a thread of its own and
    waited for here, blocking: for what is done where nothing can be awaited, as a test
    client is made, or as in another process."""
    with ThreadPoolExecutor(1) as thread:
        return thread.submit(asyncio.run, work).result()


@asynccontextmanager
async def store_in(database: Path) -> AsyncIterator[Store]:
    """The store of the database at `database`, on a data access layer of its own, as in
    another process."""
    db = Database(database)
    try:
        yield Store(db)
    finally:
        await db.close()


async def everyone_hears(client: httpx.AsyncClient) -> None:
    """The user's word of mouth off (GLOSSARY.md「传话」), as it all was before it: what the
    user tells any of them, all of them know."""
    res = await client.put("/api/account/features/word_of_mouth", json={"on": False})
    assert res.status_code == 204


@asynccontextmanager
async def database_in(database: Path) -> AsyncIterator[Database]:
    """A data access layer of its own on the database at `database`, as in another process."""
    db = Database(database)
    try:
        yield db
    finally:
        await db.close()


@asynccontextmanager
async def activity_in(
    database: Path, clock: Clock | None = None
) -> AsyncIterator[ActivityLog]:
    """The activity log of the database at `database`, on a data access layer of its own,
    as in another process, by `clock` (a fake one's by default)."""
    db = Database(database)
    try:
        yield ActivityLog(db, clock or FakeClock())
    finally:
        await db.close()


async def users_in(database: Path, *names: str) -> list[str]:
    """The ids of new users with these names, made in the database at `database` as in
    another process."""
    async with store_in(database) as store:
        return [(await store.create_user(name)).id for name in names]


class Admin:
    """Drives the admin command line against the test database."""

    def __init__(self, database_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self._database_path = database_path
        self._capsys = capsys

    def run(self, *args: str) -> str:
        code, out, err = self._invoke(args)
        assert code == 0, err
        return out

    def fails(self, *args: str) -> str:
        code, _, err = self._invoke(args)
        assert code != 0
        return err

    def invoke(self, *args: str) -> tuple[int, str, str]:
        """The exit code, standard output and standard error."""
        return self._invoke(args)

    def _invoke(self, args: tuple[str, ...]) -> tuple[int, str, str]:
        self._capsys.readouterr()
        # On a thread of its own, as in a process of its own: it runs an event loop.
        with ThreadPoolExecutor(1) as thread:
            code = thread.submit(main, ["--db", str(self._database_path), *args]).result()
        captured = self._capsys.readouterr()
        return code, captured.out, captured.err


ClientFactory = Callable[..., httpx.AsyncClient]

MANO_SHEET = "# 人设\n你是櫻木真乃。口癖是「ほわっ」。"
KOGANE_SHEET = "# 人设\n你是月岡恋鐘。说长崎方言。"
MANO_PHOTO = ("sakuragi mano", "brown hair, medium hair, brown eyes")
"""Mano's `photo_tag` and `photo_appearance`."""
AVATAR = b"\x89PNG\r\n\x1a\n fake avatar"
SCSP_AVATAR = b"\xff\xd8\xff fake SCSP avatar"
BACKGROUND = b"\xff\xd8\xff fake background"
REPO_GIFTS = Path(__file__).parents[2] / "characters" / GIFTS_FILE


class CharacterConfig:
    """Writes a character configuration directory, the way an administrator would, with the
    repository's gift catalog beside it unless a test writes its own (`gifts.toml`)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "characters.toml"
        self._entries: list[str] = []

    def add(
        self,
        id: str,
        name: str,
        sheet: str | None,
        *,
        intro: str = "",
        voice_id: str = "voice",
        avatar: bytes | None = None,
        scsp_avatar: bytes | None = None,
        color: str | None = "#ffbad6",
        background: bytes | None = None,
        speakers: list[str] | None = None,
        facts: str | None = None,
        idol_id: int | None = None,
        default_outfit: str | None = None,
        greeting: tuple[str, str] | None = None,
        call_opener: tuple[str, str] | None = None,
        other_names: tuple[str, str, str] | None = None,
        liked_gifts: tuple[str, ...] | None = ("cake",),
        photo: tuple[str, str] | None = None,
    ) -> "CharacterConfig":
        """`greeting` is her greeting in Japanese, delivery cues and all, and its subtitle;
        `call_opener` her call opener, likewise; `other_names` her `name_zh`, `name_kana`
        and `name_romaji`; `liked_gifts` her liked gifts (None leaves them out), cake by
        default as every catalog the tests write has it; `photo` her `photo_tag` and
        `photo_appearance`."""
        entry = f'[[characters]]\nid = "{id}"\nname = "{name}"\nintro = "{intro}"\n'
        entry += f'voice_id = "{voice_id}"\nsheet = "sheets/{id}.md"\n'
        if sheet is not None:
            self.write(f"sheets/{id}.md", sheet)
        if avatar is not None:
            self.write(f"avatars/{id}.png", avatar)
            entry += f'avatar = "avatars/{id}.png"\n'
        if scsp_avatar is not None:
            self.write(f"avatars-scsp/{id}.jpg", scsp_avatar)
            entry += f'scsp_avatar = "avatars-scsp/{id}.jpg"\n'
        if color is not None:
            entry += f'color = "{color}"\n'
        if background is not None:
            self.write(f"backgrounds/{id}.jpg", background)
            entry += f'background = "backgrounds/{id}.jpg"\n'
        if facts is not None:
            self.write(f"facts/{id}.md", facts)
            entry += f'facts = "facts/{id}.md"\n'
        if speakers is not None:
            entry += f"speakers = {json.dumps(speakers, ensure_ascii=False)}\n"
        if idol_id is not None:
            entry += f"idol_id = {idol_id}\n"
        if default_outfit is not None:
            entry += f'default_outfit = "{default_outfit}"\n'
        if greeting is not None:
            text, subtitle = greeting
            entry += f"greeting = {json.dumps(text, ensure_ascii=False)}\n"
            entry += f"greeting_subtitle = {json.dumps(subtitle, ensure_ascii=False)}\n"
        if call_opener is not None:
            text, subtitle = call_opener
            entry += f"call_opener = {json.dumps(text, ensure_ascii=False)}\n"
            entry += f"call_opener_subtitle = {json.dumps(subtitle, ensure_ascii=False)}\n"
        if other_names is not None:
            for field, value in zip(("name_zh", "name_kana", "name_romaji"), other_names):
                entry += f"{field} = {json.dumps(value, ensure_ascii=False)}\n"
        if liked_gifts is not None:
            entry += f"liked_gifts = {json.dumps(list(liked_gifts))}\n"
        if photo is not None:
            entry += f"photo_tag = {json.dumps(photo[0])}\n"
            entry += f"photo_appearance = {json.dumps(photo[1])}\n"
        self._entries.append(entry)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(self._entries), encoding="utf-8")
        if not (gifts := self.directory / GIFTS_FILE).exists():
            gifts.write_bytes(REPO_GIFTS.read_bytes())
        return self

    def write(self, relative: str, content: str | bytes) -> None:
        target = self.directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


@pytest.fixture
def characters(tmp_path: Path) -> CharacterConfig:
    """Mano (with both avatars, a background, a stand and her photo tags) and Kogane (with none
    of them)."""
    return (
        CharacterConfig(tmp_path / "characters")
        .add(
            "mano", "櫻木真乃", MANO_SHEET, intro="illumination STARS 的中心", avatar=AVATAR,
            scsp_avatar=SCSP_AVATAR, color="#ffbad6", background=BACKGROUND, speakers=["真乃"],
            voice_id="mano-voice", idol_id=1, default_outfit="stand/1030010010", photo=MANO_PHOTO,
        )
        .add(
            "kogane", "月岡恋鐘", KOGANE_SHEET, intro="L'Antica 的偶像", color="#f84cad",
            speakers=["恋鐘"], voice_id="kogane-voice",
        )
    )


@pytest.fixture
def story(tmp_path: Path) -> Path:
    """A directory of sample story CSVs: Mano's W.I.N.G. episodes (won, and failure endings),
    a card of Kogane's that Mano also speaks in, and an event with neither."""
    return write_sample_stories(tmp_path / "story")


@pytest.fixture
def settings(tmp_path: Path, characters: CharacterConfig) -> Settings:
    return Settings(
        database_path=tmp_path / "eidolon.db",
        characters_path=characters.path,
        chat_llm=LLMSettings("http://llm.invalid/v1", "test-key", "test-model"),
        ice_servers=[],  # tests run offline: gather host candidates only
        dev_login=True,
        secret_key="test-secret-key",
        soniox_api_key=DEPLOYER_SONIOX_KEY,
        fish_audio_api_key=DEPLOYER_FISH_KEY,
    )


@pytest.fixture
def admin(settings: Settings, capsys: pytest.CaptureFixture[str]) -> Admin:
    return Admin(settings.database_path, capsys)


SITE = {"Origin": "http://localhost:3000"}
"""What the tests' clients send as browsers do: they are the site's own pages."""

COOKIE_DOMAIN = "test.local"
"""Where the cookies of the tests' clients (at http://test) are kept, as for any host
without a dot."""


def sign_in(store: Store, name: str, now: datetime) -> httpx.Cookies:
    """The cookies of a new session for the first user named `name`, who is made, having
    agreed to the terms, if there is none; blocking, so that a client is made signed in
    without awaiting anything."""
    return aside(_signed_in(store, name, now))


async def _signed_in(store: Store, name: str, now: datetime) -> httpx.Cookies:
    user = await store.first_user_named(name) or await store.create_user(name)
    if user.consented_at is None:
        await store.consent(user.id, now)
    cookies = httpx.Cookies()
    cookies.set(SESSION_COOKIE, await store.create_session(user.id, now), domain=COOKIE_DOMAIN)
    return cookies


def github_identity(
    subject: str = "1001",
    name: str = "producer",
    *,
    email: str | None = "producer@example.com",
    created_at: datetime = START - timedelta(days=800),
) -> Identity:
    return Identity("github", subject, name, email, email is not None, created_at)


def google_identity(
    subject: str = "g-1001",
    name: str = "Producer",
    *,
    email: str = "producer@example.com",
    verified: bool = True,
) -> Identity:
    return Identity("google", subject, name, email, verified, None)


async def sign_in_with(
    client: httpx.AsyncClient,
    provider: FakeIdentityProvider,
    identity: Identity,
    *,
    link: bool = False,
) -> httpx.Response:
    """Go through signing in at `provider` as `identity`, or with `link`, linking it to the
    signed-in user; the response to the callback."""
    start = await client.get(
        f"/api/auth/{provider.name}/start", params={"link": "1"} if link else None
    )
    assert start.status_code == 302, start.text
    [state] = parse_qs(urlsplit(start.headers["location"]).query)["state"]
    code = provider.will_identify(identity)
    return await client.get(
        f"/api/auth/{provider.name}/callback", params={"code": code, "state": state}
    )


async def agree_to_terms(client: httpx.AsyncClient) -> None:
    res = await client.post(
        "/api/account/consent", json={"agree_to_terms": True, "at_least_13": True}
    )
    assert res.status_code == 204, res.text


def sign_in_as(client: httpx.AsyncClient, other: httpx.AsyncClient) -> None:
    """Have `client` carry the session `other` carries from now on."""
    client.cookies.set(SESSION_COOKIE, other.cookies[SESSION_COOKIE], domain=COOKIE_DOMAIN)


@pytest.fixture
async def dashscope(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[FakeDashScope]:
    """Alibaba Cloud Model Studio's speech recognition, faked on localhost in both regions,
    where the backend connects to by default."""
    fake = FakeDashScope()
    await fake.start()
    monkeypatch.setattr(eidolon.bailian, "REGION_URLS", fake.urls)
    yield fake
    await fake.aclose()


@pytest.fixture
def speech() -> FakeSpeech:
    """Soniox and Fish Audio, faked."""
    return FakeSpeech()


@pytest.fixture
def own_llm() -> FakeLLMService:
    """The LLM services users bring their own keys to, faked."""
    return FakeLLMService()


@pytest.fixture
def github() -> FakeIdentityProvider:
    return FakeIdentityProvider("github")


@pytest.fixture
def google() -> FakeIdentityProvider:
    return FakeIdentityProvider("google")


@pytest.fixture
async def browser() -> AsyncIterator[Browser]:
    """The browser placing a call in the test."""
    async with open_browser() as browser:
        yield browser


@pytest.fixture
async def make_client(
    settings: Settings,
    speech: FakeSpeech,
    github: FakeIdentityProvider,
    google: FakeIdentityProvider,
    own_llm: FakeLLMService,
) -> AsyncIterator[ClientFactory]:
    """Makes API clients, the deployer's chat model playing from `llm`'s script; each is
    signed in as the user with the given name, by default the producer, who has agreed to
    the terms (None for a client not signed in). Any other keyword is a script for the
    deployer's models (`background_llm`, `retrieval_judge`, `emotion_judge`), the
    `FakeServiceClients` that make them (`clients`), or an outside service to use instead
    of the one the settings describe (see `fakes.build_faked`); speech, the identity
    providers (the `github` and `google` fixtures), the LLM services users bring their
    own keys to (`own_llm`) and the price catalogs online (an empty `FakeCatalog`) are
    fakes by default, and `voice_configured=False` leaves the deployer without speech
    keys."""
    backends: list[Backend] = []
    clients: list[httpx.AsyncClient] = []

    def factory(
        llm: FakeLLM,
        user: str | None = "producer",
        *,
        settings: Settings = settings,
        voice_configured: bool = True,
        **services: Any,
    ) -> httpx.AsyncClient:
        services.setdefault("identity_providers", {"github": github, "google": google})
        services.setdefault("own_llm_service", own_llm)
        services.setdefault("speech", speech)
        services.setdefault("catalog_transport", FakeCatalog().transport())
        if not voice_configured:
            settings = replace(settings, soniox_api_key=None, fish_audio_api_key=None)
        backend = build_faked(settings, llm, **services)
        backends.append(backend)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(backend)),
            base_url="http://test",
            headers=SITE,
            cookies=sign_in(backend.store, user, backend.clock()) if user is not None else None,
        )
        clients.append(client)
        return client

    yield factory
    for client in clients:
        await client.aclose()
    for backend in backends:  # the test transport runs no lifespan to close it
        await backend.aclose()
