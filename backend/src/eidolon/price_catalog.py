"""The price catalogs online (GLOSSARY.md「看板」): models.dev's and OpenRouter's lists of
models and what each costs per million tokens, in dollars, input read from the prompt
cache too where they say, for pricing the deployer's service calls in place of the presets
(`prices`). The way magpie prices its calls (github.com/yetone/magpie, `internal/catalog`).

The catalogs are fetched in the background as the server starts, again once they are a
day old, and sooner when a model has no price (`refresh_soon`), or when an admin asks
(`refresh`), each a task of its own on the event loop, written through the data access
layer. Kept in the database, and in memory as last read from it, they are matched to a
call by the host it was at and the model it named: the providers at that host
(models.dev's `api`, or the few without one written down here, or OpenRouter), then the
model's name, as it is and then looser and looser. A model at a host no catalog knows (a
relay, a proxy) is looked for among the providers that make models, and priced as its
maker prices it. OpenRouter's catalog is its own API's, not models.dev's copy, which goes
without the judge models' prices. The same hosts are the known providers', which alone
users' own LLMs may reach through the proxy (`known_provider`, ADR-0022).

models.dev's says how each model thinks, too (`Thinking`), found the same way (`thinking`):
for the efforts beside a model's to note which it is listed taking, a hint only, never
what is sent (ADR-0019). OpenRouter's says so in words of its own, not used.

models.dev's says, too, where each provider serves each model and over which protocol
(`Offer`): the providers and models a user's own LLM, or the deployer's, may be picked
from instead of written in by hand (`providers`, `offers`, ADR-0027)."""

import asyncio
import ipaddress
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, TypeVar
from urllib.parse import urlsplit

import httpx

from eidolon.clock import Clock
from eidolon.config import REASONING_EFFORTS, LLMProtocol, ReasoningEffort
from eidolon.data_access import Database

MODELS_DEV_URL = "https://models.dev/api.json"
OPENROUTER_URL = "https://openrouter.ai/api/v1/models?output_modalities=all"
"""Every model OpenRouter has: without `output_modalities=all` the judge models, which
answer with decisions, not text, are left out."""

log = logging.getLogger(__name__)

OPENROUTER = "openrouter"
OPENROUTER_HOST = "openrouter.ai"

HOSTS = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "google": "generativelanguage.googleapis.com",
    "xai": "api.x.ai",
    "mistral": "api.mistral.ai",
    "cohere": "api.cohere.com",
    "groq": "api.groq.com",
    "cerebras": "api.cerebras.ai",
    "deepinfra": "api.deepinfra.com",
    "togetherai": "api.together.xyz",
    "perplexity": "api.perplexity.ai",
    "venice": "api.venice.ai",
}
"""The hosts of the models.dev providers that do not say where they are served (`api`),
known providers whether or not the catalogs have been fetched (`Catalog.known_provider`)."""

BASE_URLS: dict[str, tuple[LLMProtocol, str]] = {
    "openai": ("responses", "https://api.openai.com/v1"),
    "anthropic": ("anthropic", "https://api.anthropic.com/v1"),
    "google": ("chat-completions", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "xai": ("chat-completions", "https://api.x.ai/v1"),
    "mistral": ("chat-completions", "https://api.mistral.ai/v1"),
    "groq": ("chat-completions", "https://api.groq.com/openai/v1"),
    "cerebras": ("chat-completions", "https://api.cerebras.ai/v1"),
    "deepinfra": ("chat-completions", "https://api.deepinfra.com/v1/openai"),
    "togetherai": ("chat-completions", "https://api.together.xyz/v1"),
}
"""Where the models.dev providers that do not say (`HOSTS`) are reached, and over which
protocol, by those of them that speak one of ours: Google's own protocol is not, but its
OpenAI-compatible endpoint is."""

ANTHROPIC_SDKS = {"@ai-sdk/anthropic", "@ai-sdk/google-vertex/anthropic"}
RESPONSES_SDKS = {"@ai-sdk/openai", "@ai-sdk/azure"}
OTHER_SDKS = ("@ai-sdk/google", "@ai-sdk/amazon-bedrock", "@ai-sdk/mistral", "@ai-sdk/cohere")
"""The SDKs models.dev names a provider's or a model's protocol by (`npm`): Anthropic's
Messages, OpenAI's Responses, and protocols of their own we do not speak (prefixes). Any
other is taken for OpenAI's Chat Completions, as most are (`@ai-sdk/openai-compatible`)."""

_DOMAIN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
"""A name with a dot in it: not `localhost`, nor a placeholder (`${DATABRICKS_HOST}`)."""


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _named(host: str) -> bool:
    """Whether `host` is a name on the internet: not an address, nor a name for this
    machine (`*.localhost`), nor a placeholder (`${DATABRICKS_HOST}`)."""
    return (
        _DOMAIN.fullmatch(host) is not None and not host.endswith(".localhost")
        and not _is_address(host)
    )

MAKERS = [
    "openai", "anthropic", "google", "deepseek", "xai", "mistral", "moonshotai",
    "alibaba-cn", "zhipuai", "minimax",
]
"""The models.dev providers that make the models they serve, in the order a model at a
host no catalog knows (a relay's) is looked for among them."""

SHAPES: dict[object, LLMProtocol] = {"responses": "responses", "completions": "chat-completions"}
"""Which of OpenAI's protocols a model is spoken to over where models.dev says so of it
(`shape`), whatever its SDK would have it."""


def _protocol(npm: object, shape: object = None) -> LLMProtocol | None:
    """The protocol a provider or model is spoken to over, by the SDK models.dev names
    (`ANTHROPIC_SDKS`) and, for one of OpenAI's, the `shape` it gives; None for one of
    their own."""
    if not isinstance(npm, str):
        protocol: LLMProtocol | None = "chat-completions"
    elif npm in ANTHROPIC_SDKS:
        return "anthropic"
    elif npm in RESPONSES_SDKS:
        protocol = "responses"
    elif npm.startswith(OTHER_SDKS):
        return None
    else:
        protocol = "chat-completions"
    return SHAPES.get(shape, protocol)


def _https_host(api: str) -> str | None:
    """The host `api` is at if it is over HTTPS; not over HTTPS, it is on this machine or
    next to it."""
    try:
        parts = urlsplit(api)
        return parts.hostname if parts.scheme == "https" else None
    except ValueError:
        return None


def _usable(base_url: object) -> bool:
    """Whether `base_url` is one a provider serves its models at over HTTPS: at a name on
    the internet (`_named`), not a placeholder (`https://${AZURE_RESOURCE_NAME}…`)."""
    if not isinstance(base_url, str):
        return False
    try:
        parts = urlsplit(base_url)
        host = parts.hostname
    except ValueError:
        return False
    return (
        parts.scheme == "https" and host is not None and _named(host)
        and "$" not in base_url and "{" not in base_url
    )


STALE_AFTER = timedelta(hours=24)
"""How old the catalogs may be before they are fetched again."""
MISSING_AFTER = timedelta(hours=6)
"""How long since they were last fetched, or tried, before a model without a price has
them fetched again: they may list it by now."""
RETRY_AFTER = timedelta(minutes=10)
"""How long after a failed fetch before stale catalogs are tried again."""
CHECK_EVERY = 3600
"""How often, in seconds, the server looks whether the catalogs are stale."""


@dataclass(frozen=True)
class Listed:
    """A model a catalog prices: dollars per million input and output tokens (output
    None, priced as input, when the catalog does not say), and input tokens read from the
    prompt cache (None, priced as the rest of the input, when it does not say)."""

    provider: str
    model: str
    input: float
    output: float | None
    cache_read: float | None = None


@dataclass(frozen=True)
class Thinking:
    """How a model thinks, as models.dev lists it (`reasoning`, `reasoning_options`):
    whether it does, the efforts it takes (of `REASONING_EFFORTS`, in their order; none
    for one turned off by an effort), and whether it has a switch of its own to turn
    thinking on and off (`toggle`). One thinking by a budget of tokens, or as it will
    (`[]`, "no caller control"), takes no effort."""

    provider: str
    model: str
    thinks: bool
    efforts: tuple[ReasoningEffort, ...]
    switch: bool


@dataclass(frozen=True)
class Provider:
    """A provider models.dev lists serving models we can speak to (`Offer`), by its id and
    name, and where and over which protocol it serves them unless a model says otherwise
    (None where it does not say, or not over one of ours)."""

    id: str
    name: str
    protocol: LLMProtocol | None
    base_url: str | None


@dataclass(frozen=True)
class Offer:
    """A model a provider serves, by its id (what a call names) and its name, and where
    and over which protocol: one that calls tools and answers in text, as far as
    models.dev says, and is not deprecated."""

    provider: str
    model: str
    name: str
    protocol: LLMProtocol
    base_url: str


@dataclass(frozen=True)
class State:
    """When the catalogs were last fetched, when and why a fetch last failed since, and
    whether they are being fetched now."""

    fetched_at: datetime | None
    failed_at: datetime | None
    error: str | None
    fetching: bool = False


_SCHEMA = """
-- The price catalogs online as last fetched: each model they price, by provider, in
-- dollars per million tokens; how each model models.dev lists thinks; the host each
-- provider is at; the providers and models a user's LLM may be picked from; when they
-- were fetched.
CREATE TABLE IF NOT EXISTS price_catalog (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input REAL NOT NULL,
    output REAL,
    cache_read REAL,
    PRIMARY KEY (provider, model)
);
CREATE TABLE IF NOT EXISTS price_catalog_thinking (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    thinks INTEGER NOT NULL,
    efforts TEXT NOT NULL, -- separated by spaces
    switch INTEGER NOT NULL,
    PRIMARY KEY (provider, model)
);
CREATE TABLE IF NOT EXISTS price_catalog_hosts (
    host TEXT NOT NULL,
    provider TEXT NOT NULL,
    PRIMARY KEY (host, provider)
);
CREATE TABLE IF NOT EXISTS price_catalog_providers (
    provider TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    protocol TEXT,
    base_url TEXT
);
CREATE TABLE IF NOT EXISTS price_catalog_offers (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    name TEXT NOT NULL,
    protocol TEXT NOT NULL,
    base_url TEXT NOT NULL,
    PRIMARY KEY (provider, model)
);
CREATE TABLE IF NOT EXISTS price_catalog_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    fetched_at TEXT,
    failed_at TEXT,
    error TEXT
);
"""


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return float(value)


def _thinking(provider: str, model: str, m: dict) -> Thinking:
    """How `m` thinks, leaving out the efforts models.dev's own schema does not explain
    (`default`, `null`)."""
    options = m.get("reasoning_options")
    options = [o for o in options if isinstance(o, dict)] if isinstance(options, list) else []
    taken = {
        value
        for o in options
        if o.get("type") == "effort" and isinstance(values := o.get("values"), list)
        for value in values
    }
    return Thinking(
        provider, model, m.get("reasoning") is True,
        tuple(e for e in REASONING_EFFORTS if e in taken),
        any(o.get("type") == "toggle" for o in options),
    )


def parse_models_dev(
    data: object,
) -> tuple[list[Listed], list[Thinking], list[tuple[str, str]]]:
    """The models models.dev prices per token, how each model it lists thinks, and the
    hosts each provider is at (a model may say it is served at one of its own), leaving
    out its copy of OpenRouter's (`parse_openrouter`) and whatever it does not say
    plainly."""
    listed: list[Listed] = []
    thinking: list[Thinking] = []
    hosts: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        raise ValueError("models.dev: not an object")
    for provider, entry in data.items():
        if provider == OPENROUTER or not isinstance(entry, dict):
            continue
        api = entry.get("api")
        if isinstance(api, str):
            host = _https_host(api)
        else:
            host = HOSTS.get(provider)
        if host:
            hosts.append((host.lower(), provider))
        models = entry.get("models")
        for model, m in (models.items() if isinstance(models, dict) else []):
            if not isinstance(m, dict):
                continue
            thinking.append(_thinking(provider, model, m))
            own = m.get("provider")
            if isinstance(own, dict) and isinstance(at := own.get("api"), str):
                if (host := _https_host(at)) is not None:
                    hosts.append((host.lower(), provider))
            cost = m.get("cost")
            if not isinstance(cost, dict) or (input := _number(cost.get("input"))) is None:
                continue
            listed.append(Listed(
                provider, model, input, _number(cost.get("output")),
                _number(cost.get("cache_read")),
            ))
    return listed, thinking, hosts


def _serves(m: dict) -> bool:
    """Whether a model models.dev lists calls tools and answers in text, as far as it
    says, and is not deprecated."""
    modalities = m.get("modalities")
    output = modalities.get("output") if isinstance(modalities, dict) else None
    return (
        m.get("tool_call") is not False and m.get("status") != "deprecated"
        and not (isinstance(output, list) and "text" not in output)
    )


def parse_offers(data: object) -> tuple[list[Provider], list[Offer]]:
    """The providers models.dev lists, OpenRouter's among them, and the models each serves
    that a user's own LLM may be picked from (`Offer`): where a model says nothing of its
    own, where and over which protocol its provider serves models; a provider without a
    model like that is left out."""
    if not isinstance(data, dict):
        raise ValueError("models.dev: not an object")
    providers: list[Provider] = []
    offers: list[Offer] = []
    for id, entry in data.items():
        if not isinstance(id, str) or not isinstance(entry, dict):
            continue
        named = entry.get("name")
        name = named if isinstance(named, str) and named else id
        protocol: LLMProtocol | None
        api: object
        if "api" not in entry and id in BASE_URLS:
            protocol, api = BASE_URLS[id]
        else:  # where it does not say, its models may
            protocol, api = _protocol(entry.get("npm")), entry.get("api")
        base_url = api if isinstance(api, str) and _usable(api) else None
        served: list[Offer] = []
        models = entry.get("models")
        for model, m in (models.items() if isinstance(models, dict) else []):
            if not isinstance(m, dict) or not isinstance(model, str) or not _serves(m):
                continue
            own = m.get("provider")
            own = own if isinstance(own, dict) else {}
            speaks: LLMProtocol | None
            if "npm" in own:
                speaks = _protocol(own["npm"], own.get("shape"))
            elif protocol in ("responses", "chat-completions"):  # OpenAI's, `shape` picks
                speaks = SHAPES.get(own.get("shape"), protocol)
            else:
                speaks = protocol
            at = own["api"] if "api" in own else base_url
            if speaks is None or not isinstance(at, str) or not _usable(at):
                continue
            label = m.get("name")
            label = label if isinstance(label, str) and label else model
            served.append(Offer(id, model, label, speaks, at))
        if served:
            providers.append(Provider(id, name, protocol if base_url else None, base_url))
            offers += served
    return providers, offers


def parse_openrouter(data: object) -> list[Listed]:
    """The models OpenRouter prices, its prices per token made per million tokens; a
    price it gives as -1 (one that varies, its router's) is left out, and a price for the
    input read from the cache that is not a plain one is as none."""
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list):
        raise ValueError("OpenRouter: no list of models")
    listed = []
    for m in models:
        pricing = m.get("pricing") if isinstance(m, dict) else None
        if not isinstance(pricing, dict) or not isinstance(m.get("id"), str):
            continue
        try:
            input = float(pricing["prompt"])
            output = float(pricing["completion"]) if "completion" in pricing else None
        except (KeyError, TypeError, ValueError):
            continue
        if input < 0 or (output is not None and output < 0):
            continue
        cache_read = _per_token(pricing.get("input_cache_read"))
        listed.append(Listed(
            OPENROUTER, m["id"], round(input * 1_000_000, 9),
            None if output is None else round(output * 1_000_000, 9),
            None if cache_read is None else round(cache_read * 1_000_000, 9),
        ))
    return listed


def _per_token(value: object) -> float | None:
    """An OpenRouter price per token, given as text, if it is one and not below 0."""
    try:
        price = float(value)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None


def _bare(model: str) -> str:
    """A model's name without a path (a vendor's, a relay's), in lower case."""
    return model.lower().rsplit("/", 1)[-1]


def _cut(name: str, at: str) -> str:
    return name.split(at, 1)[0].strip()


def _dashed(model: str) -> str:
    """A model's bare name with its dots as dashes: how two vendors writing a version
    4.6 and 4-6 are compared."""
    return _bare(model).replace(".", "-")


T = TypeVar("T")


def _match(listing: Mapping[str, Mapping[str, T]], providers: list[str], model: str) -> T | None:
    """What the first of `providers` listing `model` in `listing` (by provider, then by
    model) has for it: by its name as it is, then without a path or case
    (`openai/GPT-6-Sol` is `gpt-6-sol`), then without a `:tag` or a `(variant)` too, then
    with a dot taken for a dash (`claude-opus-4.6` is `claude-opus-4-6`). Of two a
    provider lists under names the same once loosened, the first by name."""
    for provider in providers:
        if (found := listing.get(provider, {}).get(model)) is not None:
            return found
    bare = _bare(model)
    untagged = _cut(_cut(bare, ":"), "(")
    loosened = [(_bare, bare), (_bare, untagged), (_dashed, untagged.replace(".", "-"))]
    for loosen, want in loosened:
        for provider in providers:
            listed = listing.get(provider, {})
            names = sorted(name for name in listed if loosen(name) == want)
            if names:
                return listed[names[0]]
    return None


@dataclass(frozen=True)
class _Listing:
    """The catalogs as last fetched, as they are kept in memory to be looked up in."""

    models: Mapping[str, Mapping[str, Listed]]
    thinking: Mapping[str, Mapping[str, Thinking]]
    hosts: Mapping[str, list[str]]
    providers: list[Provider]
    no_providers: bool
    """Whether models.dev's models were fetched without its providers: by an older
    version, before there were any to keep. Catalogs fetched empty are not."""
    offers: Mapping[str, list[Offer]]
    state: State


class Catalog:
    """The price catalogs as last fetched, fetching them again on asking (`refresh`)."""

    def __init__(
        self, db: Database, clock: Clock, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._db = db
        self._listing = db.prepare(_open)
        self._clock = clock
        self._fetching: asyncio.Task[None] | None = None
        self._closed = False
        self._http = httpx.AsyncClient(
            transport=transport, timeout=60, headers={"User-Agent": "eidolon-sc"},
            follow_redirects=True,
        )

    def state(self) -> State:
        return replace(self._listing.state, fetching=self._busy())

    def _busy(self) -> bool:
        return self._fetching is not None and not self._fetching.done()

    def refresh_soon(self, missing: bool = False) -> None:
        """Fetch the catalogs again in the background if they are stale (`STALE_AFTER`, or
        fetched without the providers an LLM is picked from, as before there were any),
        or if a model is `missing` a price and they were last tried `MISSING_AFTER` ago;
        a failed fetch is not tried again for `RETRY_AFTER`. Returns at once; call it on
        the event loop."""
        state, now = self._listing.state, self._clock()
        tried = max((d for d in [state.fetched_at, state.failed_at] if d), default=None)
        stale = (
            state.fetched_at is None or now - state.fetched_at >= STALE_AFTER
            or self._listing.no_providers
        )
        due = (
            tried is None
            or (stale and now - tried >= RETRY_AFTER)
            or (missing and now - tried >= MISSING_AFTER)
        )
        if due and not self._closed and not self._busy():
            self._fetch_in_background()

    async def keep_fresh(self) -> None:
        """Look whether the catalogs are stale now and every `CHECK_EVERY` seconds after,
        until cancelled."""
        while True:
            self.refresh_soon()
            await asyncio.sleep(CHECK_EVERY)

    def known_provider(self, host: str) -> bool:
        """Whether `host` is where a provider the catalogs list serves its models over
        HTTPS, or one of those written down here (GLOSSARY.md「已知服务商」): a name on the
        internet, not an address, nor a name for this machine, nor a placeholder
        (`https://${DATABRICKS_HOST}`)."""
        host = host.lower().rstrip(".")
        listed = host in self._listing.hosts or host in HOSTS.values()
        return listed and _named(host)

    def providers(self) -> list[Provider]:
        """The providers a user's own LLM may be picked from, by name (`parse_offers`)."""
        return self._listing.providers

    def offers(self, provider: str) -> list[Offer]:
        """The models `provider` serves that a user's own LLM may be picked from, by name;
        none for a provider not listed."""
        return self._listing.offers.get(provider, [])

    def find(self, host: str, model: str) -> tuple[Listed, bool] | None:
        """The catalogs' price of `model` at `host`, if they have one, and whether it is
        its maker's, for a host no catalog knows (a relay, a proxy): what it is likely
        to cost there, not what it does."""
        return self._find(self._listing.models, host, model)

    def thinking(self, host: str, model: str) -> Thinking | None:
        """How `model` at `host` thinks, as models.dev lists it, found as its price is
        (`find`): at a host no catalog knows, as its maker lists it."""
        found = self._find(self._listing.thinking, host, model)
        return found[0] if found else None

    def _find(
        self, listing: Mapping[str, Mapping[str, T]], host: str, model: str
    ) -> tuple[T, bool] | None:
        providers = self._listing.hosts.get(host.lower())
        if providers:
            found = _match(listing, providers, model)
            return (found, False) if found is not None else None
        # its maker first, where its name says (`deepseek/deepseek-v4-pro`)
        named = model.lower().split("/", 1)[0] if "/" in model else None
        found = _match(listing, sorted(MAKERS, key=lambda m: m != named), model)
        return (found, True) if found is not None else None

    async def refresh(self) -> None:
        """Fetch the catalogs again, keeping the ones last fetched if either fails; when
        they are being fetched already, wait for that instead. Cancelling the wait leaves
        the fetch to finish. Once closing, nothing is fetched."""
        if self._closed:
            return
        fetching = self._fetching if self._busy() else self._fetch_in_background()
        assert fetching is not None
        await asyncio.shield(fetching)

    def _fetch_in_background(self) -> "asyncio.Task[None]":
        self._fetching = asyncio.create_task(self._fetch())
        self._fetching.add_done_callback(_logged)
        return self._fetching

    async def _fetch(self) -> None:
        now = self._clock()
        try:
            listed, thinking, hosts, providers, offers = await asyncio.to_thread(
                _from_models_dev, await self._get(MODELS_DEV_URL)
            )
            listed += await asyncio.to_thread(_from_openrouter, await self._get(OPENROUTER_URL))
        except (httpx.HTTPError, ValueError) as e:
            log.warning("could not fetch the price catalogs: %s", e)
            await self._db.write(
                _save_failure, now.isoformat(), f"{type(e).__name__}: {e}"[:500]
            )
        else:
            await self._db.write(
                _save, now.isoformat(), listed, thinking, hosts, providers, offers
            )
        self._listing = await self._db.read(_listing)

    async def _get(self, url: str) -> bytes:
        response = await self._http.get(url)
        response.raise_for_status()
        return response.content

    async def aclose(self) -> None:
        """Let a fetch under way finish, then close."""
        self._closed = True
        if self._fetching is not None:
            await asyncio.gather(self._fetching, return_exceptions=True)
        await self._http.aclose()


def _logged(fetching: "asyncio.Task[None]") -> None:
    """Log what a fetch failed with beyond the catalogs not being fetched, which no one
    may be waiting for."""
    if not fetching.cancelled() and (error := fetching.exception()) is not None:
        log.error("fetching the price catalogs failed", exc_info=error)


def _from_models_dev(
    content: bytes,
) -> tuple[list[Listed], list[Thinking], list[tuple[str, str]], list[Provider], list[Offer]]:
    """What models.dev's catalog, as fetched, prices, how its models think, where its
    providers are, and the providers and models an LLM may be picked from."""
    data = json.loads(content)
    return *parse_models_dev(data), *parse_offers(data)


def _from_openrouter(content: bytes) -> list[Listed]:
    return parse_openrouter(json.loads(content))


def _open(db: sqlite3.Connection) -> _Listing:
    """Make the tables, or bring them up to date, and read the catalogs kept in them."""
    db.executescript(_SCHEMA)
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(price_catalog)")}
    # Catalogs fetched before cache prices were kept have none until fetched again.
    if "cache_read" not in columns:
        db.execute("ALTER TABLE price_catalog ADD COLUMN cache_read REAL")
    return _listing(db)


def _listing(db: sqlite3.Connection) -> _Listing:
    listed = db.execute(
        "SELECT provider, model, input, output, cache_read FROM price_catalog"
    ).fetchall()
    thinking = db.execute(
        "SELECT provider, model, thinks, efforts, switch FROM price_catalog_thinking"
    ).fetchall()
    hosts = db.execute("SELECT host, provider FROM price_catalog_hosts").fetchall()
    providers = db.execute(
        "SELECT provider, name, protocol, base_url FROM price_catalog_providers"
    ).fetchall()
    offers = db.execute(
        "SELECT provider, model, name, protocol, base_url FROM price_catalog_offers"
    ).fetchall()
    state = db.execute("SELECT fetched_at, failed_at, error FROM price_catalog_state").fetchone()
    models: dict[str, dict[str, Listed]] = {}
    for provider, model, input, output, cache_read in listed:
        models.setdefault(provider, {})[model] = Listed(provider, model, input, output, cache_read)
    thinks: dict[str, dict[str, Thinking]] = {}
    for provider, model, does, efforts, switch in thinking:
        taken = efforts.split()
        thinks.setdefault(provider, {})[model] = Thinking(
            provider, model, bool(does),
            tuple(e for e in REASONING_EFFORTS if e in taken), bool(switch),
        )
    at: dict[str, list[str]] = {OPENROUTER_HOST: [OPENROUTER]}
    for host, provider in hosts:
        at.setdefault(host, []).append(provider)
    served: dict[str, list[Offer]] = {}
    for row in offers:
        served.setdefault(row[0], []).append(Offer(*row))
    when = lambda s: datetime.fromisoformat(s) if s else None  # noqa: E731
    return _Listing(
        models=models,
        thinking=thinks,
        hosts={host: sorted(set(listed)) for host, listed in at.items()},
        providers=sorted(
            (Provider(*row) for row in providers), key=lambda p: (p.name.casefold(), p.id)
        ),
        no_providers=not providers and bool(thinking),
        offers={
            p: sorted(o, key=lambda o: (o.name.casefold(), o.model)) for p, o in served.items()
        },
        state=(
            State(when(state[0]), when(state[1]), state[2]) if state else State(None, None, None)
        ),
    )


def _save(
    db: sqlite3.Connection,
    now: str,
    listed: Iterable[Listed],
    thinking: Iterable[Thinking],
    hosts: Iterable[tuple[str, str]],
    providers: Iterable[Provider],
    offers: Iterable[Offer],
) -> None:
    db.execute("DELETE FROM price_catalog")
    db.executemany(
        "INSERT OR REPLACE INTO price_catalog (provider, model, input, output, cache_read)"
        " VALUES (?, ?, ?, ?, ?)",
        ((m.provider, m.model, m.input, m.output, m.cache_read) for m in listed),
    )
    db.execute("DELETE FROM price_catalog_thinking")
    db.executemany(
        "INSERT OR REPLACE INTO price_catalog_thinking"
        " (provider, model, thinks, efforts, switch) VALUES (?, ?, ?, ?, ?)",
        ((t.provider, t.model, t.thinks, " ".join(t.efforts), t.switch) for t in thinking),
    )
    db.execute("DELETE FROM price_catalog_hosts")
    db.executemany(
        "INSERT OR IGNORE INTO price_catalog_hosts (host, provider) VALUES (?, ?)", hosts
    )
    db.execute("DELETE FROM price_catalog_providers")
    db.executemany(
        "INSERT OR REPLACE INTO price_catalog_providers (provider, name, protocol, base_url)"
        " VALUES (?, ?, ?, ?)",
        ((p.id, p.name, p.protocol, p.base_url) for p in providers),
    )
    db.execute("DELETE FROM price_catalog_offers")
    db.executemany(
        "INSERT OR REPLACE INTO price_catalog_offers"
        " (provider, model, name, protocol, base_url) VALUES (?, ?, ?, ?, ?)",
        ((o.provider, o.model, o.name, o.protocol, o.base_url) for o in offers),
    )
    db.execute(
        "INSERT OR REPLACE INTO price_catalog_state (id, fetched_at, failed_at, error)"
        " VALUES (1, ?, NULL, NULL)",
        (now,),
    )


def _save_failure(db: sqlite3.Connection, now: str, error: str) -> None:
    db.execute(
        "INSERT INTO price_catalog_state (id, failed_at, error) VALUES (1, ?, ?)"
        " ON CONFLICT (id) DO UPDATE SET failed_at = excluded.failed_at,"
        " error = excluded.error",
        (now, error),
    )


TurnsOff = Literal["by_effort", "by_switch"]
"""How a model's thinking is turned off, as models.dev lists it: by the effort none, or
by a switch of its own, which a protocol's none may not be."""


def describe_thinking(thinking: Thinking, protocol: LLMProtocol) -> dict:
    """How a model thinks, as models.dev lists it, for the efforts `protocol` sends: the
    efforts it takes save none, and how it is turned off (`TurnsOff`), if it is. The
    Anthropic protocol's none is a switch (`thinking: disabled`)."""
    turns_off: TurnsOff | None = (
        "by_effort" if "none" in thinking.efforts or (thinking.switch and protocol == "anthropic")
        else "by_switch" if thinking.switch else None
    )
    return {
        "provider": thinking.provider,
        "model": thinking.model,
        "thinks": thinking.thinks,
        "efforts": [e for e in thinking.efforts if e != "none"],
        "turns_off": turns_off,
    }


def describe_provider(provider: Provider, offers: Iterable[Offer]) -> dict:
    return {
        "id": provider.id, "name": provider.name,
        "protocol": provider.protocol, "base_url": provider.base_url,
        "base_urls": sorted({o.base_url for o in offers}),
    }


def describe_offer(offer: Offer) -> dict:
    return {
        "model": offer.model, "name": offer.name,
        "protocol": offer.protocol, "base_url": offer.base_url,
    }


def describe_state(state: State) -> dict:
    iso: Callable[[datetime | None], str | None] = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "fetched_at": iso(state.fetched_at),
        "failed_at": iso(state.failed_at),
        "error": state.error,
        "fetching": state.fetching,
    }
