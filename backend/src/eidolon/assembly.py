"""The assembly root: from settings to the running backend, and back down again.

`build` makes every module the backend runs on from `Settings`: the store, the
characters and the world setting and gift catalog beside them, the identity providers users sign in with, the chat and background models,
the services users bring their own keys to, retrieval, memory, the conversation engine,
usage limits, voice calls and reach-outs. It decides what is on. Retrieval needs the judge
model, the embedder and the reranker all at hand, and memory is on only with retrieval, since only
retrieval finds memories again; judging her emotion during calls needs only the judge
model, the same one; the
background model is always at hand, being the chat model unless configured apart.

The deployer's chat and memory models and judge model are made by `ServiceClients` from
the live configuration (`live_config`): `.env`'s settings as overridden in the admin
console, replaced while the backend runs as admins change them; the speech keys, the
thresholds, the limits, the exempting keys and whether affinity is on are read from it as
each turn or call starts, and the image generation as each photo is. `Backend.start`
tries the overrides as the server starts.

The outside services can be passed in instead of made from the settings, as tests do
with fakes; the deployer's chat, memory and judge models only ever by passing in the
`ServiceClients` that make them; the image service by its transport. Whoever passes one in closes it; `Backend.aclose`
closes only what `build` made, in the order that lets what is still running finish.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from eidolon.accounts import Accounts
from eidolon.activity import ActivityLog, MeteredWebSearch, Origin, host, metered_retrieval
from eidolon.admission import Admissions
from eidolon.affinity import Affinities
from eidolon.announcements import Announcements
from eidolon.appeals import Appeals
from eidolon.backup import Backups
from eidolon.call import Calls, Speech, SpeechServices
from eidolon.characters import Character, load_characters
from eidolon.gift_catalog import Gift, check_liked_gifts, gifts_path, load_gift_catalog
from eidolon.gifts import Gifts
from eidolon.happenings import Happenings
from eidolon.hot_words import hot_words
from eidolon.housekeeping import keep_tidy
from eidolon.loop_lag import watch_the_loop
from eidolon.randomness import Random, secure_random
from eidolon.clock import Clock, utc_now
from eidolon.config import DEFAULT_BAILIAN_STT_MODEL, Settings
from eidolon.corpus import StoryCorpus
from eidolon.data_access import Database
from eidolon.data_package import DataPackages
from eidolon.embedding import EMBEDDING_TIMEOUT, Embedder, open_embedder
from eidolon.engine import ConversationEngine
from eidolon.features import Features
from eidolon.identity import IdentityProvider, ProviderName, open_identity_providers
from eidolon.image_generation import GENERATION_TIMEOUT
from eidolon.live_config import LiveConfig, Reach
from eidolon.memory import Memories
from eidolon.price_catalog import Catalog
from eidolon.prices import Prices
from eidolon.own_keys import LLMService, OwnImageService, OwnKeys, ProviderLLMService
from eidolon.photos import Photos
from eidolon.proxy import ProxyRoutes
from eidolon.diary import Diary
from eidolon.keepsakes import Keepsakes
from eidolon import tickets
from eidolon.outfit_tags import OutfitTags
from eidolon.trip_photos import Dates, TripPhotos
from eidolon.relations import Relations
from eidolon.badges import Badges
from eidolon.outing import Outings
from eidolon.voices import GptSovits, load_local_voices
from eidolon.favors import Favors
from eidolon.read_aloud import ReadAloud
from eidolon.home import Home
from eidolon.schedule_writer import ScheduleWriter
from eidolon.schedules import Schedules
from eidolon.letters import Letters
from eidolon.reach_outs import ReachOuts
from eidolon.readings import load_readings, readings_path
from eidolon.rerank import (
    FALLBACK_MODEL,
    KNOWN_THRESHOLDS,
    RERANK_TIMEOUT,
    Reranker,
    SiliconFlowReranker,
    thresholds_for,
)
from eidolon.retrieval import Retrieval, open_reference_sources
from eidolon.service_clients import ProviderClients, ServiceClients
from eidolon.service_status import ServiceStatus, probes
from eidolon.sponsor import Sponsor
from eidolon.store import Conversation, Store
from eidolon.titles import Titles
from eidolon.usage import UsageLimiter
from eidolon.special_days import SpecialDays, load_special_days, special_days_path
from eidolon.hearts import hearts_path, load_hearts, with_hearts
from eidolon.promises import Promise, Promises, Settled
from eidolon.experiences import Experiences
from eidolon.weather import Weather
from eidolon.web_search import WebSearch
from eidolon.world import World, load_world, world_path

log = logging.getLogger(__name__)

Closer = Callable[[], Awaitable[None] | None]

THREADS = 32
"""How many threads the event loop's work waits on at once, on the default executor: the
data access layer's reads, for every turn, the work that is all computing (pictures,
similarities), and a backup or data package taking minutes too. More than the
default, a few on a small server, so that those do not keep turns waiting."""


@dataclass
class Backend:
    """Everything the API serves from, ready to run."""

    settings: Settings
    database: Database
    """The database, as the modules moved onto the data access layer read and write it."""
    store: Store
    characters: dict[str, Character]
    gift_catalog: dict[str, Gift]
    """The gift catalog, by identifier."""
    memories: Memories
    titles: Titles
    """Names conversations after their third turn."""
    engine: ConversationEngine
    usage: UsageLimiter
    clock: Clock
    calls: Calls
    speech: Speech
    """The speech services calls hear and speak with."""
    own_keys: OwnKeys
    admissions: Admissions
    """Lets text turns and calls begin, and settles them."""
    accounts: Accounts
    identity_providers: dict[ProviderName, IdentityProvider]
    activity: ActivityLog
    """Every turn's activity record, and every service call's, for the dashboard."""
    config: LiveConfig
    prices: Prices
    """The prices the dashboard estimates the deployer's costs at."""
    announcements: Announcements
    """What admins write for every user to see."""
    sponsor: Sponsor
    """The code users may scan to sponsor the deployment, where it is on."""
    affinities: Affinities
    """Every user's affinity with every character, where it is on."""
    gifts: Gifts
    """Every user's draw chances, backpack and reset cards, where gifts are open."""
    happenings: Happenings
    """What happens between users and characters, as it counts towards their daily tasks."""
    backups: Backups
    """The database's backups, as the admin console runs them, and the record of each."""
    photos: Photos
    """The photos she sends with her replies, being generated."""
    service_status: ServiceStatus
    """Whether each of the deployer's services works now, as users are shown it."""
    data_packages: DataPackages
    """Exports users' data packages, and imports them."""
    reach_outs: ReachOuts
    """Has characters reach out to users once their time comes."""
    letters: Letters
    """Has characters write users letters once their time comes."""
    appeals: Appeals
    """Gives back what turns the user did not mean so took off affinity."""
    diary: Diary
    """Has characters keep a diary of the days that mattered."""
    schedules: Schedules
    """Her schedule for each day (GLOSSARY.md「行程」)."""
    schedule_writer: ScheduleWriter
    """Has her schedule for each day written, ahead of the turns."""
    keepsakes: Keepsakes
    """The keepsakes she gives, and the cabinet (GLOSSARY.md「纪念品」)."""
    trip_photos: TripPhotos
    favors: Favors
    read_aloud: ReadAloud
    home: Home
    """The producer's home (GLOSSARY.md「制作人的家」)."""
    """The photos she sends from her trips (GLOSSARY.md「外景照片」), and those asked for with
    tickets, and her days out with the producer (GLOSSARY.md「体验券」「邀约」)."""
    outfit_tags: OutfitTags
    """The tags each outfit on the outfit lists is drawn with."""
    outings: Outings
    """The days out met (GLOSSARY.md「约会」), and running into them (「偶遇」)."""
    badges: Badges
    """Each user's titles (GLOSSARY.md「称号」)."""
    relations: Relations
    """How the characters stand with each other, and word of mouth (GLOSSARY.md「关系表」)."""
    dates: Dates
    """The days out the producer invited each character to (GLOSSARY.md「邀约」)."""
    features: Features
    """Each user's own switches for the fork's features (GLOSSARY.md「玩法开关」)."""
    world: World | None = None
    """The world setting; None without its data file."""
    special_days: SpecialDays | None = None
    """The idols' birthdays and the holidays; None without the calendar."""
    weather: Weather | None = None
    """The weather where the producer is; None unless `.env` gives their place."""
    promises: Promises | None = None
    """What the producer promised each idol for a day (GLOSSARY.md「约定」)."""
    web: "MeteredWebSearch | None" = None
    """The web search the characters may use (GLOSSARY.md「联网」); None without a key."""
    experiences: Experiences | None = None
    """Her own doings, by character (GLOSSARY.md「她们的经历」); read and managed on its page.
    A turn writes down and finds none yet: that comes with the page."""
    corpus: StoryCorpus | None = None
    """The story corpus retrieval searches; None with retrieval off."""
    _closers: list[Closer] = field(default_factory=list, repr=False)
    _keeping_fresh: asyncio.Task[None] | None = field(default=None, repr=False)
    _probing: asyncio.Task[None] | None = field(default=None, repr=False)
    _reaching_out: asyncio.Task[None] | None = field(default=None, repr=False)
    _writing: asyncio.Task[None] | None = field(default=None, repr=False)
    _keeping_diaries: asyncio.Task[None] | None = field(default=None, repr=False)
    _planning: asyncio.Task[None] | None = field(default=None, repr=False)
    _giving: asyncio.Task[None] | None = field(default=None, repr=False)
    _sending: asyncio.Task[None] | None = field(default=None, repr=False)
    _favoring: asyncio.Task[None] | None = field(default=None, repr=False)
    _tidying: asyncio.Task[None] | None = field(default=None, repr=False)
    _watching: asyncio.Task[None] | None = field(default=None, repr=False)
    _loading_stories: asyncio.Task[None] | None = field(default=None, repr=False)

    async def start(self) -> None:
        """What is done as the server starts, on its event loop: try the overrides of the
        configuration, falling back to `.env` where one fails; keep the price catalogs
        fresh from now on, fetching them in the background, try the services without
        calls for their status, have characters reach out once their time comes, delete
        what ages out (`housekeeping`), watch how far behind the loop runs (`loop_lag`),
        and load the story corpus's vectors to search in the background."""
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(THREADS, thread_name_prefix="eidolon")
        )
        await self.config.start()
        self._keeping_fresh = asyncio.create_task(self.prices.catalog.keep_fresh())
        self._probing = asyncio.create_task(self.service_status.keep_probing())
        self._reaching_out = asyncio.create_task(self.reach_outs.keep_reaching_out())
        self._writing = asyncio.create_task(self.letters.keep_writing())
        self._keeping_diaries = asyncio.create_task(self.diary.keep_writing())
        self._planning = asyncio.create_task(self.schedule_writer.keep_writing())
        self._giving = asyncio.create_task(self.keepsakes.keep_giving())
        self._sending = asyncio.create_task(self.trip_photos.keep_sending())
        self._favoring = asyncio.create_task(self.favors.keep_running())
        self._tidying = asyncio.create_task(keep_tidy(self.usage.forget_old, self.activity.forget_old))
        self._watching = asyncio.create_task(watch_the_loop(lambda: self.calls.in_progress))
        if self.corpus is not None:
            self._loading_stories = asyncio.create_task(self.corpus.load())

    async def aclose(self) -> None:
        """Close what `build` opened, last opened first, once what `start` began is over."""
        tasks = [
            task
            for task in (
                self._keeping_fresh, self._probing, self._reaching_out, self._tidying,
                self._watching, self._writing, self._keeping_diaries, self._planning,
                self._giving, self._sending, self._favoring, self._loading_stories,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for close in reversed(self._closers):
            result = close()
            if result is not None:
                await result


def build(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    clock: Clock | None = None,
    speech: Speech | None = None,
    identity_providers: dict[ProviderName, IdentityProvider] | None = None,
    own_llm_service: LLMService | None = None,
    clients: ServiceClients | None = None,
    catalog_transport: httpx.AsyncBaseTransport | None = None,
    backup_transport: httpx.AsyncBaseTransport | None = None,
    image_transport: httpx.AsyncBaseTransport | None = None,
    weather_transport: httpx.AsyncBaseTransport | None = None,
    web_transport: httpx.AsyncBaseTransport | None = None,
    random: Random | None = None,
    reach: Reach | None = None,
) -> Backend:
    """The backend `settings` describe, with any of the outside services passed in
    used instead of made. Raises RuntimeError, having opened nothing, if the character
    configuration (her liked gifts and all), the world setting, the gift catalog, the
    calendar or a heart (`hearts`) is broken, or a character is none of its idols and none of
    its staff."""
    characters = load_characters(settings.characters_path)
    characters = with_hearts(
        characters, load_hearts(hearts_path(settings.characters_path), characters.values())
    )
    gift_catalog = load_gift_catalog(gifts_path(settings.characters_path))
    check_liked_gifts(characters, gift_catalog)
    world = load_world(path := world_path(settings.characters_path))
    if world is None:
        log.info(
            "there is no world setting at %s: characters go without it (run `eidolon world"
            " sync` and commit the file it writes)",
            path,
        )
    else:
        world.check_characters(characters, path)
    special_days = load_special_days(special_days_path(settings.characters_path), characters.values())
    clock = clock or utc_now
    # Closed last, once what writes through it has finished.
    database = Database(settings.database_path)
    closers: list[Closer] = [database.close]

    store = Store(database)
    if identity_providers is None:
        identity_providers = open_identity_providers(settings.github, settings.google)
        closers.extend(p.aclose for p in identity_providers.values())
    # Users' own DashScope keys hear with the deployer's model, or the default one where the
    # deployer chose Soniox.
    bailian_model = (
        settings.bailian_stt_model if settings.stt_provider == "bailian"
        else DEFAULT_BAILIAN_STT_MODEL
    )
    # Each of the deployer's services, and users' own keys, through the proxy or direct, as
    # the configuration in effect has it at each request; their clients are made on these.
    routes = ProxyRoutes(settings.proxy)
    closers.append(routes.aclose)
    readings = load_readings(readings_path(settings.characters_path))
    local_voices = load_local_voices(settings.local_voices_path)
    if unknown := sorted(set(local_voices) - set(characters)):
        log.warning("%s 不是角色，它们的本地语音用不上", ", ".join(unknown))
    speech = speech or SpeechServices(
        bailian_model, readings, hot_words(readings, characters.values(), world), routes=routes,
        gpt_sovits=GptSovits(local_voices),
    )
    activity = ActivityLog(database, clock)
    closers.append(activity.aclose)
    catalog = Catalog(database, clock, catalog_transport)
    closers.append(catalog.aclose)
    prices = Prices(database, clock, catalog)
    announcements = Announcements(database, clock)
    sponsor = Sponsor(database, clock)
    backups = Backups(database, clock, transport=backup_transport)
    closers.append(backups.aclose)
    image_client = None
    if image_transport is not None:
        image_client = httpx.AsyncClient(
            transport=image_transport, timeout=GENERATION_TIMEOUT, trust_env=False
        )
        closers.append(image_client.aclose)
    config = LiveConfig(
        settings, clients or ProviderClients(routes), clock, db=database, activity=activity,
        speech=speech, routes=routes, image_client=image_client,
        **({"reach": reach} if reach is not None else {}),
    )
    closers.append(config.aclose)
    if own_llm_service is None:  # users' own LLMs write down memories too
        own_llm_service = ProviderLLMService(
            public_only=settings.domain is not None, routes=routes,
            known_provider=catalog.known_provider,
        )
        closers.append(own_llm_service.aclose)
    if embedder is None and settings.embedding is not None:
        opened = open_embedder(
            settings.embedding, routes.client("embedding", EMBEDDING_TIMEOUT)
        )
        closers.append(opened.aclose)
        embedder = opened
    if (
        settings.domain is not None
        and settings.embedding is not None
        and settings.embedding.provider == "gemini"
        and not settings.gemini_billing_enabled
    ):
        log.warning(
            "a public deployment is embedding with Gemini without GEMINI_BILLING_ENABLED=1:"
            " on the free tier Google may use what it embeds, users' memories included, to"
            " improve its products. Turn on billing and set GEMINI_BILLING_ENABLED=1, or"
            " embed with SiliconFlow (ADR-0005, revised 2026-09-27)"
        )
    if (r := settings.rerank) is not None:
        thresholds = thresholds_for(r)
        if r.model not in KNOWN_THRESHOLDS:
            log.warning(
                "rerank model %s has not been calibrated; its thresholds default to those of"
                " %s (set RERANK_MIN_RELEVANCE and RERANK_MEMORY_MIN_RELEVANCE to choose):"
                " using references %s, memories %s",
                r.model, FALLBACK_MODEL, thresholds.references, thresholds.memories,
            )
        if reranker is None:
            reranker = SiliconFlowReranker(
                r.api_key, r.model, r.base_url, routes.client("rerank", RERANK_TIMEOUT)
            )
            closers.append(reranker.aclose)
    service_status = ServiceStatus(
        activity, config,
        probes(config, speech, next(iter(characters.values())).voice_id, embedder, reranker),
        clock,
    )
    embedder, reranker = metered_retrieval(activity, settings, embedder, reranker)
    # The judge model is the live configuration's: retrieval asks the one in effect at
    # each question, and each call judges her emotions on the one it connected with.
    retrieval_judge = config.retrieval_judge()

    features = Features(database)
    promises = Promises(database, clock, random or secure_random, features)
    # 关系表和传话（GLOSSARY.md「关系表」「传话」）
    relations = Relations(database, characters, world, clock, features)

    # 她自己的经历：按角色存，和关于制作人的记忆分开
    experiences = Experiences(database, clock)

    # 联网搜索用能配的那把钥匙（默认沿用聊天模型的）：换聊天模型不该把搜索一起换掉
    search_key = settings.search_key or settings.chat_llm.api_key
    web = (
        MeteredWebSearch(
            WebSearch(
                search_key,
                web_transport or routes.transport("web_search"),
                clock,
                settings.search_url,
            ),
            activity,
            Origin("web_search", host(settings.search_url), "search"),
        )
        if search_key else None
    )
    if web is not None:
        closers.append(web.aclose)

    retrieval = None
    corpus = None
    if retrieval_judge is not None and embedder is not None and reranker is not None:
        references = open_reference_sources(database, embedder, characters.values(), world)
        corpus = references.corpus
        closers.append(corpus.aclose)  # closed before the database, loading from it
        retrieval = Retrieval(
            retrieval_judge,
            references,
            reranker,
            lambda: config.rerank_thresholds.references,
        )
        memories = Memories(
            database, characters, clock, embedder=embedder, reranker=reranker,
            min_relevance=lambda: config.rerank_thresholds.memories, promises=promises,
            experiences=experiences,
        )
    else:
        log.info(
            "retrieval and memory are off: they need the embedding service's key"
            " (SILICONFLOW_API_KEY, or GEMINI_API_KEY with EMBEDDING_PROVIDER=gemini),"
            " SILICONFLOW_API_KEY for reranking, and the judge model's (JEV_API_KEY, or"
            " CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID with JUDGE_PROVIDER=cloudflare)"
        )
        memories = Memories(
            database, characters, clock, promises=promises, experiences=experiences,
        )
    closers.append(memories.aclose)  # closed before the clients, so what it writes down can finish
    titles = Titles(store, characters, gift_catalog)
    closers.append(titles.aclose)  # likewise, so what it is naming can finish

    affinities = Affinities(database, clock, lambda: config.affinity_on, features)
    unphotographed = [c.id for c in characters.values() if not c.photographed]
    if config.image_settings is not None and unphotographed:
        log.warning(
            "%s have no photo_tag and photo_appearance in the character configuration, and"
            " send no photos", ", ".join(unphotographed),
        )
    usage = UsageLimiter(database, lambda: config.usage_limits, clock)
    # Users' own image generation, in the deployment's art style; in tests, on the same
    # fake as the deployer's, told apart by its key.
    own_images = OwnImageService(
        public_only=settings.domain is not None, routes=routes,
        style=lambda: config.image_style, transport=image_transport,
    )
    closers.append(own_images.aclose)
    photos = Photos(store, config.image_generator, activity, usage, own_images.generator)
    closers.append(photos.aclose)  # closed before the database, so no photo is still kept
    weather = (
        Weather(settings.weather.latitude, settings.weather.longitude, clock, weather_transport)
        if settings.weather is not None else None
    )
    if weather is not None:
        closers.append(weather.aclose)
    schedules = Schedules(database, clock)
    gifts = Gifts(
        database, clock, lambda: config.affinity_on, affinities, characters, gift_catalog,
        random or secure_random, promises,
        tickets.available(settings, features, lambda: config.image_generator() is not None),
    )
    happenings = Happenings(database, clock, lambda: config.affinity_on, gifts, activity)
    engine = ConversationEngine(
        database, store, characters, gift_catalog, memories, titles, clock, retrieval,
        lambda: config.history_turns, world, affinities, config.affinity_judge(), photos,
        happenings, special_days, weather, promises, config.promise_judge(), web,
        config.search_judge(), experiences, schedules=schedules, features=features,
    )
    own_keys = OwnKeys(
        own_llm_service, config, activity, speech, exempting=lambda: config.exempting_keys,
        image_service=own_images,
    )
    admissions = Admissions(
        usage, activity, own_keys, cooldown_until=affinities.cooldown_until
    )
    closers.append(admissions.aclose)  # closed after the calls, which may leave some to settle
    calls = Calls(
        settings, engine, admissions, own_keys, lambda: config.usage_limits, speech, clock, store,
        happenings, weather,
    )
    closers.append(calls.aclose)  # closed before memory, so no call is still taking turns
    engine.on_cooled(calls.cooled)
    engine.on_ended(calls.conversation_ended)
    reach_outs = ReachOuts(
        database, store, engine, characters, config, affinities, activity,
        calls.in_call, clock, random or secure_random, special_days, features,
    )
    letters = Letters(
        database, store, engine, characters, config, affinities, activity,
        clock, random or secure_random, settings.letters, special_days, features,
    )
    diary = Diary(
        database, store, engine, characters, config, affinities, activity,
        clock, settings.diary, special_days, features,
    )
    dates = Dates(database)
    favor_twists: list[Callable[[str, str, date], Awaitable[list[str]]]] = []

    async def twists_on(user_id: str, character_id: str, day: date) -> list[str]:
        return [t for twists in favor_twists for t in await twists(user_id, character_id, day)]

    schedule_writer = ScheduleWriter(
        schedules, store, engine, characters, config, activity, clock,
        random or secure_random, settings.schedules, features, dates.on, twists_on,
    )
    keepsakes = Keepsakes(
        database, store, engine, characters, config, activity, clock,
        random or secure_random, settings.keepsakes, schedules,
        lambda db, user_id: settings.letters and features.read_on(db, user_id, "letters"),
        features,
    )
    closers.append(keepsakes.close)
    trip_photos = TripPhotos(
        database, store, engine, characters, config, activity, clock,
        random or secure_random, settings.schedules, schedules, affinities, calls.in_call,
        features, dates,
    )
    closers.append(trip_photos.close)
    async def favor_moment(user_id: str, character_id: str, what: str, at: datetime) -> None:
        await diary.moment(user_id, character_id, what, at)
        her = characters[character_id].name if character_id in characters else ""
        await relations.tell(
            user_id, character_id, f"{her}{what}" if what.startswith("和") else f"{her}那边：{what}", at
        )

    favors = Favors(
        database, store, engine, characters, gift_catalog, config, activity, clock,
        random or secure_random, affinities, gifts, keepsakes, trip_photos, special_days,
        favor_moment, calls.in_call, features,
    )
    favor_twists.append(favors.twists_on)
    engine.with_ties(favors.ties)
    read_aloud = ReadAloud(database, characters, config, speech, activity, clock)
    home = Home(database, config, activity)
    outfit_tags = OutfitTags(database, settings.vision, settings.stand_assets_url)
    affinities.on_scene_done(keepsakes.scene_played)
    gifts.on_given(keepsakes.gift_given)
    promises.on_settled(keepsakes.promise_settled)
    letters.on_written(keepsakes.letter_written)

    async def keepsake_given(user_id: str, character_id: str, name: str, at: datetime) -> None:
        await diary.moment(user_id, character_id, f"把「{name}」送给了制作人", at)

    keepsakes.on_given(keepsake_given)
    engine.on_recorded(diary.turn_recorded)
    affinities.on_scene_done(diary.scene_played)
    gifts.on_given(diary.gift_given)
    memories.on_moment(diary.moment)
    promises.on_kept(diary.promise_kept)
    promises.on_broken(diary.promise_broken)

    async def promise_settled(promise: Promise, settled: Settled, _: datetime) -> None:
        await affinities.promise_settled(promise.user_id, promise.character_id, settled)

    promises.on_settled(promise_settled)
    _pass_word(relations, characters, engine, memories, gifts, affinities, promises, keepsakes,
               trip_photos, favors)
    outings = Outings(
        database, store, engine, characters, config, activity, clock, trip_photos, experiences,
        diary.moment, relations, features, affinities,
    )
    trip_photos.with_outings(outings.played, outings.closes)
    badges = Badges(database, characters, clock)
    outings.with_encounters_a_day(badges.perk)

    async def look_for_badges(conversation: Conversation, *_: object) -> None:
        await badges.check(conversation.user_id)

    engine.on_recorded(look_for_badges)
    config.on_change(gifts.notice)
    return Backend(
        settings=settings,
        database=database,
        store=store,
        characters=characters,
        gift_catalog=gift_catalog,
        memories=memories,
        titles=titles,
        engine=engine,
        usage=usage,
        clock=clock,
        calls=calls,
        speech=speech,
        own_keys=own_keys,
        admissions=admissions,
        accounts=Accounts(store, clock, memories, calls, settings.secret_key),
        identity_providers=identity_providers,
        activity=activity,
        config=config,
        prices=prices,
        announcements=announcements,
        sponsor=sponsor,
        affinities=affinities,
        gifts=gifts,
        happenings=happenings,
        backups=backups,
        photos=photos,
        service_status=service_status,
        data_packages=DataPackages(
            database, store, memories, characters, gift_catalog, clock, promises,
            experiences, diary,
        ),
        reach_outs=reach_outs,
        letters=letters,
        appeals=Appeals(database, affinities, clock),
        diary=diary,
        schedules=schedules,
        features=features,
        dates=dates,
        keepsakes=keepsakes,
        trip_photos=trip_photos,
        favors=favors,
        read_aloud=read_aloud,
        home=home,
        outfit_tags=outfit_tags,
        schedule_writer=schedule_writer,
        world=world,
        special_days=special_days,
        weather=weather,
        promises=promises,
        web=web,
        experiences=experiences,
        corpus=corpus,
        relations=relations,
        outings=outings,
        badges=badges,
        _closers=closers,
    )


def _pass_word(
    relations: Relations,
    characters: Mapping[str, Character],
    engine: ConversationEngine,
    memories: Memories,
    gifts: Gifts,
    affinities: Affinities,
    promises: Promises,
    keepsakes: Keepsakes,
    trip_photos: TripPhotos,
    favors: Favors,
) -> None:
    """Have her prompt tell her how she stands with the others, the memories of the others
    known to her only through them, and what happens between the producer and one of them
    told as news to the others (GLOSSARY.md「关系表」「传话」); what two of them did together
    for the producer changes how they stand."""
    engine.with_relations(relations)
    memories.with_sharing(relations.shares_with)

    def name(character_id: str) -> str:
        return characters[character_id].name if character_id in characters else ""

    async def gift_given(
        user_id: str, character_id: str, gift: str, liked: bool, _: object, at: datetime
    ) -> None:
        if liked:
            await relations.tell(user_id, character_id, f"制作人送了{name(character_id)}她喜欢的{gift}", at)

    async def scene_played(user_id: str, character_id: str, _: str, at: datetime) -> None:
        await relations.tell(
            user_id, character_id, f"{name(character_id)}和制作人之间好像发生了什么，关系一下子近了", at
        )

    async def promise_kept(user_id: str, character_id: str, text: str, at: datetime) -> None:
        await relations.tell(user_id, character_id, f"制作人做到了答应{name(character_id)}的事：{text}", at)

    async def promise_broken(user_id: str, character_id: str, text: str, at: datetime) -> None:
        await relations.tell(user_id, character_id, f"制作人没做到答应{name(character_id)}的事：{text}", at)

    async def keepsake_given(user_id: str, character_id: str, thing: str, at: datetime) -> None:
        await relations.tell(user_id, character_id, f"{name(character_id)}送了制作人{thing}", at)

    async def date_planned(user_id: str, character_id: str, day: date, what: str) -> None:
        await relations.tell(
            user_id, character_id,
            f"制作人约了{name(character_id)}{day.month}月{day.day}日{what}", relations.now(),
        )

    async def together(user_id: str, a: str, b: str, what: str, _: datetime) -> None:
        await relations.change(user_id, a, b, what, "favor")

    gifts.on_given(gift_given)
    affinities.on_scene_done(scene_played)
    promises.on_kept(promise_kept)
    promises.on_broken(promise_broken)
    keepsakes.on_given(keepsake_given)
    trip_photos.on_invited(date_planned)
    favors.on_together(together)
