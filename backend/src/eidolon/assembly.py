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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from eidolon.accounts import Accounts
from eidolon.activity import ActivityLog, metered_retrieval
from eidolon.admission import Admissions
from eidolon.affinity import Affinities
from eidolon.announcements import Announcements
from eidolon.backup import Backups
from eidolon.call import Calls, Speech, SpeechServices
from eidolon.characters import Character, load_characters
from eidolon.gift_catalog import Gift, check_liked_gifts, gifts_path, load_gift_catalog
from eidolon.gifts import Gifts
from eidolon.hot_words import hot_words
from eidolon.randomness import Random, secure_random
from eidolon.clock import Clock, utc_now
from eidolon.config import DEFAULT_BAILIAN_STT_MODEL, Settings
from eidolon.data_package import DataPackages
from eidolon.embedding import EMBEDDING_TIMEOUT, Embedder, open_embedder
from eidolon.engine import ConversationEngine
from eidolon.identity import IdentityProvider, ProviderName, open_identity_providers
from eidolon.image_generation import GENERATION_TIMEOUT
from eidolon.live_config import LiveConfig, Reach
from eidolon.memory import Memories
from eidolon.price_catalog import Catalog
from eidolon.prices import Prices
from eidolon.own_keys import LLMService, OwnImageService, OwnKeys, ProviderLLMService
from eidolon.photos import Photos
from eidolon.proxy import ProxyRoutes
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
from eidolon.store import Store
from eidolon.titles import Titles
from eidolon.usage import UsageLimiter
from eidolon.special_days import SpecialDays, load_special_days, special_days_path
from eidolon.world import World, load_world, world_path

log = logging.getLogger(__name__)

Closer = Callable[[], Awaitable[None] | None]


@dataclass
class Backend:
    """Everything the API serves from, ready to run."""

    settings: Settings
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
    world: World | None = None
    """The world setting; None without its data file."""
    special_days: SpecialDays | None = None
    """The idols' birthdays and the holidays; None without the calendar."""
    _closers: list[Closer] = field(default_factory=list, repr=False)
    _keeping_fresh: asyncio.Task[None] | None = field(default=None, repr=False)
    _probing: asyncio.Task[None] | None = field(default=None, repr=False)
    _reaching_out: asyncio.Task[None] | None = field(default=None, repr=False)
    _writing: asyncio.Task[None] | None = field(default=None, repr=False)

    async def start(self) -> None:
        """What is done as the server starts, on its event loop: try the overrides of the
        configuration, falling back to `.env` where one fails; keep the price catalogs
        fresh from now on, fetching them in the background, try the services without
        calls for their status, and have characters reach out once their time comes."""
        await self.config.start()
        self._keeping_fresh = asyncio.create_task(self.prices.catalog.keep_fresh())
        self._probing = asyncio.create_task(self.service_status.keep_probing())
        self._reaching_out = asyncio.create_task(self.reach_outs.keep_reaching_out())
        self._writing = asyncio.create_task(self.letters.keep_writing())

    async def aclose(self) -> None:
        """Close what `build` opened, last opened first."""
        for task in (self._keeping_fresh, self._probing, self._reaching_out, self._writing):
            if task is not None:
                task.cancel()
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
    catalog_transport: httpx.BaseTransport | None = None,
    backup_transport: httpx.AsyncBaseTransport | None = None,
    image_transport: httpx.AsyncBaseTransport | None = None,
    random: Random | None = None,
    reach: Reach | None = None,
) -> Backend:
    """The backend `settings` describe, with any of the outside services passed in
    used instead of made. Raises RuntimeError, having opened nothing, if the character
    configuration (her liked gifts and all), the world setting, the gift catalog or the
    calendar is broken, or a character is none of its idols."""
    characters = load_characters(settings.characters_path)
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
    closers: list[Closer] = []

    store = Store(settings.database_path)
    closers.append(store.close)
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
    speech = speech or SpeechServices(
        bailian_model, readings, hot_words(readings, characters.values(), world), routes=routes
    )
    activity = ActivityLog(settings.database_path, clock)
    closers.append(activity.close)
    catalog = Catalog(settings.database_path, clock, catalog_transport)
    closers.append(catalog.close)
    prices = Prices(settings.database_path, clock, catalog)
    closers.append(prices.close)
    announcements = Announcements(settings.database_path, clock)
    closers.append(announcements.close)
    sponsor = Sponsor(settings.database_path, clock)
    closers.append(sponsor.close)
    backups = Backups(settings.database_path, clock, transport=backup_transport)
    closers.append(backups.aclose)
    image_client = None
    if image_transport is not None:
        image_client = httpx.AsyncClient(
            transport=image_transport, timeout=GENERATION_TIMEOUT, trust_env=False
        )
        closers.append(image_client.aclose)
    config = LiveConfig(
        settings, clients or ProviderClients(routes), clock, activity=activity, speech=speech,
        routes=routes, image_client=image_client,
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

    retrieval = None
    if retrieval_judge is not None and embedder is not None and reranker is not None:
        retrieval = Retrieval(
            retrieval_judge,
            open_reference_sources(
                settings.database_path, embedder, characters.values(), world
            ),
            reranker,
            lambda: config.rerank_thresholds.references,
        )
        closers.append(retrieval.close)
        memories = Memories(
            settings.database_path, characters, clock, embedder=embedder, reranker=reranker,
            min_relevance=lambda: config.rerank_thresholds.memories,
        )
    else:
        log.info(
            "retrieval and memory are off: they need the embedding service's key"
            " (SILICONFLOW_API_KEY, or GEMINI_API_KEY with EMBEDDING_PROVIDER=gemini),"
            " SILICONFLOW_API_KEY for reranking, and the judge model's (JEV_API_KEY, or"
            " CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID with JUDGE_PROVIDER=cloudflare)"
        )
        memories = Memories(settings.database_path, characters, clock)
    closers.append(memories.aclose)  # closed before the clients, so what it writes down can finish
    titles = Titles(store, characters, gift_catalog)
    closers.append(titles.aclose)  # likewise, so what it is naming can finish

    affinities = Affinities(settings.database_path, clock, lambda: config.affinity_on)
    closers.append(affinities.close)
    unphotographed = [c.id for c in characters.values() if not c.photographed]
    if config.image_settings is not None and unphotographed:
        log.warning(
            "%s have no photo_tag and photo_appearance in the character configuration, and"
            " send no photos", ", ".join(unphotographed),
        )
    usage = UsageLimiter(settings.database_path, lambda: config.usage_limits, clock)
    closers.append(usage.close)
    # Users' own image generation, in the deployment's art style; in tests, on the same
    # fake as the deployer's, told apart by its key.
    own_images = OwnImageService(
        public_only=settings.domain is not None, routes=routes,
        style=lambda: config.image_style, transport=image_transport,
    )
    closers.append(own_images.aclose)
    photos = Photos(store, config.image_generator, activity, usage, own_images.generator)
    closers.append(photos.aclose)  # closed before the store and usage, so no photo is still kept
    engine = ConversationEngine(
        store, characters, gift_catalog, memories, titles, clock, retrieval,
        lambda: config.history_turns, world, affinities, config.affinity_judge(), photos,
        special_days,
    )
    own_keys = OwnKeys(
        own_llm_service, config, activity, speech, exempting=lambda: config.exempting_keys,
        image_service=own_images,
    )
    admissions = Admissions(
        usage, activity, own_keys, cooldown_until=affinities.cooldown_until
    )
    calls = Calls(
        settings, engine, admissions, own_keys, lambda: config.usage_limits, speech, clock, store
    )
    closers.append(calls.aclose)  # closed before memory, so no call is still taking turns
    affinities.on_cooldown(calls.cooled)
    engine.on_ended(calls.conversation_ended)
    gifts = Gifts(
        settings.database_path, clock, lambda: config.affinity_on, affinities, characters,
        gift_catalog, random or secure_random, store,
    )
    closers.append(gifts.close)
    reach_outs = ReachOuts(
        settings.database_path, store, engine, characters, config, affinities, activity, usage,
        calls.in_call, clock, random or secure_random, special_days,
    )
    closers.append(reach_outs.close)
    letters = Letters(
        settings.database_path, store, engine, characters, config, affinities, activity,
        clock, random or secure_random, settings.letters, special_days,
    )
    closers.append(letters.close)
    config.on_change(gifts.notice)
    return Backend(
        settings=settings,
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
        backups=backups,
        photos=photos,
        service_status=service_status,
        data_packages=DataPackages(
            settings.database_path, store, memories, characters, gift_catalog, clock
        ),
        reach_outs=reach_outs,
        letters=letters,
        world=world,
        special_days=special_days,
        _closers=closers,
    )
