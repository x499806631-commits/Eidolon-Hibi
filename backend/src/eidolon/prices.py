"""Prices (GLOSSARY.md「看板」): what the deployer pays each provider for a model's usage,
for the dashboard to estimate what their keys cost.

A price is for a provider (the host the service is at, as service calls are recorded:
`activity.host`), a model and the unit it is billed in (`billing.Unit`): an amount for
each `PER` units of input and, where the service prices output apart (an LLM), of output,
and of the input read from the provider's prompt cache, in a currency. Where a price
comes from (`Pricing`): the price an admin set in the admin console, kept in the database
(`price_overrides`), if any; else, for a model billed in tokens, the catalogs online
(`price_catalog`), fetched each day; else the one in `PRESETS`, gathered from the
providers' price pages on the day each says (see `.scratch/admin-console/research.md`),
there for when the catalogs cannot be fetched and for what they do not price. Restoring
an admin's price deletes it.

The cost (`cost`) is each call's usage on the deployer's keys times its price at the time
it is asked for, summed by currency, never converted between them, and without any free
allowance taken off: an estimate, not a bill. The input an LLM read from the cache is
priced at the cache price, or as the rest of the input without one; a call that did not
say how much it read hit none. A call whose usage is unknown is counted, not priced; a
model without a price shows what it used, and no amount. Usage on users' own keys is
theirs to pay: shown, never priced.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from eidolon.activity import SERVICES, CallGroup
from eidolon.clock import Clock
from eidolon.config import ServiceName
from eidolon.data_access import Database
from eidolon.price_catalog import Catalog

PER: dict[str, int] = {
    "tokens": 1_000_000, "bytes": 1_000_000, "characters": 1_000_000, "seconds": 3600,
    "images": 1,
}
"""How many units a price is for: a million tokens, a million bytes (of text spoken, in
UTF-8) or characters (of it), an hour (of audio heard), one picture (generated)."""

PriceKey = tuple[str, str, str]
"""A provider, a model, a unit."""


@dataclass(frozen=True)
class Price:
    """What `PER` units of input, and of output, cost; output costs as input does when the
    service does not price it apart (`output` None). Input read from the prompt cache costs
    `cache_read`, or as the rest of the input does without one."""

    input: float
    output: float | None
    currency: str
    as_of: date
    """When it was gathered from the price page, or set in the admin console."""
    cache_read: float | None = None


GATHERED = date(2026, 10, 7)


def _tokens(
    provider: str,
    prices: dict[str, tuple[float, float | None] | tuple[float, float | None, float]],
    currency: str,
) -> dict[PriceKey, Price]:
    """`provider`'s models priced per million input and output tokens, and input tokens
    read from the cache where a third price says."""
    return {
        (provider, model, "tokens"): Price(
            price[0], price[1], currency, GATHERED, price[2] if len(price) > 2 else None
        )
        for model, price in prices.items()
    }


PRESETS: dict[PriceKey, Price] = {
    # LLMs, per million input, output and cached input tokens, at the standard (not batch)
    # price for a short context.
    **_tokens("api.openai.com", {
        "gpt-6-astra": (10, 50, 1),
        "gpt-6.1-sol": (2, 10, 0.1),
        "gpt-6-sol": (2, 10, 0.2),
        "gpt-6-luna": (0.1, 0.5, 0.01),
        "gpt-5.6-sol": (4, 20, 0.4),  # a promotional price, "at least through November 21, 2026"
        "gpt-5.4-mini": (0.75, 4.5, 0.075),
    }, "USD"),
    **_tokens("api.anthropic.com", {
        "claude-fable-5-1": (10, 50, 0.25),
        "claude-opus-5-5": (4, 20, 0.2),
        "claude-sonnet-5-5": (2, 10, 0.1),
        "claude-haiku-4-5": (1, 5, 0.1),
        "claude-haiku-4-5-20251001": (1, 5, 0.1),
    }, "USD"),
    # DeepSeek's price in yuan at peak hours (off-peak is half); the retired name is
    # served and billed as Flash.
    **_tokens("api.deepseek.com", {
        "deepseek-flash": (2, 8, 0.04),
        "deepseek-v4-flash": (2, 8, 0.04),
        "deepseek-v4-pro": (9, 27, 0.3),
    }, "CNY"),
    # Fish Audio, per million UTF-8 bytes of text spoken.
    **{
        ("api.fish.audio", model, "bytes"): Price(price, None, "USD", GATHERED)
        for model, price in {"s2.1-pro-free": 0, "s2.1-pro": 15, "s2-pro": 15, "s1": 15}.items()
    },
    # Drama 3, per million characters ($10 as it launched, through November 12, 2026).
    ("api.fish.audio", "drama-3", "characters"): Price(50, None, "USD", date(2026, 10, 17)),
    # Soniox, per hour of audio streamed (its own equivalent of the tokens it bills).
    ("stt-rt.soniox.com", "stt-rt-v5", "seconds"): Price(0.12, None, "USD", GATHERED),
    # The judge model, per million input tokens; output is free.
    **_tokens("openrouter.ai", {
        "typesafe/jev-1.13": (0.042, 0),
        "cloudflare/clef": (0.24, 0),
        "cloudflare/clef-flash": (0.09, 0),
    }, "USD"),
    **_tokens("api.cloudflare.com", {"clef": (0.24, None), "clef-flash": (0.09, None)}, "USD"),
    # Model Studio's decision model, free for a limited time while in preview, with no
    # price yet for after.
    **_tokens("cn-beijing.maas.aliyuncs.com", {"decision-model-preview": (0, None)}, "CNY"),
    **_tokens("ap-southeast-1.maas.aliyuncs.com", {"decision-model-preview": (0, None)}, "USD"),
    # Embedding and reranking, per million input tokens.
    **_tokens("api.siliconflow.cn", {
        "Qwen/Qwen3-Embedding-0.6B": (0.07, None),
        "Qwen/Qwen3-Embedding-4B": (0.14, None),
        "Qwen/Qwen3-Embedding-8B": (0.28, None),
        "Qwen/Qwen3-Reranker-0.6B": (0.07, None),
        "Qwen/Qwen3-Reranker-4B": (0.14, None),
        "Qwen/Qwen3-Reranker-8B": (0.28, None),
    }, "CNY"),
    **_tokens("generativelanguage.googleapis.com", {"gemini-embedding-2": (0.2, None)}, "USD"),
    # NovelAI, per picture of 832×1216 at 23 steps (`image_generation`): the 26 Anlas it
    # costs beyond a subscription's free generations. Within them, as on Opus, it costs
    # nothing; a relay has no preset.
    ("image.novelai.net", "nai-diffusion-5-curated", "images"): Price(
        0.036, None, "USD", date(2026, 10, 17)
    ),
}
"""The prices of the models supported, as their providers' price pages had them on
`GATHERED` (`.scratch/admin-console/research.md`; the cache prices
`.scratch/prompt-caching/research.md`): what a model is priced at when the catalogs online
do not price it, or have never been fetched. Not here, for want of a
price in the unit its usage is recorded in: Alibaba Cloud Model Studio's speech
recognition, billed by tokens, not seconds."""


def per(unit: str) -> int:
    return PER.get(unit, 1)


Source = Literal["override", "catalog", "catalog_upstream", "preset"]
"""Where a price is from: an admin, the catalogs (for a host no catalog knows, the
model's maker's: `catalog_upstream`), the presets."""


@dataclass(frozen=True)
class Found:
    """A price in effect, where it is from, and, from a catalog, the provider and model
    it is listed under there."""

    price: Price
    source: Source
    match: tuple[str, str] | None = None


def _undimensioned(model: str) -> str:
    """A model recorded with its dimensions (`gemini-embedding-2@1536`, see `embedding`)
    as the model it is."""
    return model.split("@", 1)[0]


class Pricing:
    """Every price in effect at one moment: an admin's, else the catalogs', else the
    preset's. A model recorded with its dimensions costs as the model does, unless priced
    apart."""

    def __init__(self, overrides: dict[PriceKey, Price], catalog: Catalog) -> None:
        self._overrides = overrides
        self._catalog = catalog
        self._found: dict[PriceKey, Found | None] = {}

    @property
    def catalog_as_of(self) -> date | None:
        """When the catalogs were fetched, if ever."""
        fetched = self._catalog.state().fetched_at
        return fetched.date() if fetched else None

    def find(self, provider: str, model: str, unit: str) -> Found | None:
        key = (provider, model, unit)
        if key not in self._found:
            self._found[key] = self._find(provider, model, unit)
        return self._found[key]

    def _find(self, provider: str, model: str, unit: str) -> Found | None:
        names = dict.fromkeys([model, _undimensioned(model)])
        for name in names:
            if (price := self._overrides.get((provider, name, unit))) is not None:
                return Found(price, "override")
        if (listed := self.listed(provider, model, unit)) is not None:
            return listed
        for name in names:
            if (price := PRESETS.get((provider, name, unit))) is not None:
                return Found(price, "preset")
        return None

    def listed(self, provider: str, model: str, unit: str) -> Found | None:
        """The catalogs' price of a model billed in tokens, if they price it."""
        as_of = self.catalog_as_of
        if unit != "tokens" or as_of is None:
            return None
        found = self._catalog.find(provider, _undimensioned(model))
        if found is None:
            return None
        listed, upstream = found
        return Found(
            Price(listed.input, listed.output, "USD", as_of, listed.cache_read),
            "catalog_upstream" if upstream else "catalog", (listed.provider, listed.model),
        )

    def describe(self, used: Iterable[PriceKey] = ()) -> list[dict]:
        """Every preset, every admin's price and every model `used`, each with where the
        price in effect is from, if any, and beside it the preset, the catalogs' and the
        admin's, by provider, model and unit."""
        overrides = self._overrides
        rows = []
        for key in sorted(PRESETS.keys() | overrides.keys() | set(used)):
            provider, model, unit = key
            found = self.find(*key)
            listed = self.listed(*key)
            rows.append({
                "provider": provider, "model": model, "unit": unit, "per": per(unit),
                "source": found.source if found else None,
                "preset": (
                    describe_price(key, Found(PRESETS[key], "preset")) if key in PRESETS
                    else None
                ),
                "catalog": describe_price(key, listed) if listed else None,
                "override": (
                    describe_price(key, Found(overrides[key], "override"))
                    if key in overrides else None
                ),
            })
        return rows


_SCHEMA = """
-- The prices admins set in the admin console, each in place of the preset of its provider,
-- model and unit, if any; `output` NULL when output costs as input does, `cache_read` NULL
-- when input read from the cache does.
CREATE TABLE IF NOT EXISTS price_overrides (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    unit TEXT NOT NULL,
    input REAL NOT NULL,
    output REAL,
    currency TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cache_read REAL,
    PRIMARY KEY (provider, model, unit)
);
"""


class Prices:
    """The prices admins set, over the catalogs' and the presets."""

    def __init__(self, db: Database, clock: Clock, catalog: Catalog) -> None:
        self._db = db
        db.prepare(_create)
        self._clock = clock
        self.catalog = catalog

    async def overrides(self) -> dict[PriceKey, Price]:
        return await self._db.read(_overrides)

    async def pricing(self) -> Pricing:
        """Every price in effect now."""
        return Pricing(await self.overrides(), self.catalog)

    async def save(
        self,
        key: PriceKey,
        input: float,
        output: float | None,
        currency: str,
        cache_read: float | None = None,
    ) -> None:
        """Price `key` so from now on, in place of its preset, if any."""
        await self._db.write(
            _save, key, input, output, currency, cache_read, self._clock().isoformat()
        )

    async def restore(self, key: PriceKey) -> bool:
        """Back to the preset of `key`, or unpriced without one; whether an admin had set
        its price."""
        return await self._db.write(_restore, key)


def _create(db: sqlite3.Connection) -> None:
    db.executescript(_SCHEMA)
    columns = {name for _, name, *_ in db.execute("PRAGMA table_info(price_overrides)")}
    # Prices set before cache prices were have none: cached input as the rest.
    if "cache_read" not in columns:
        db.execute("ALTER TABLE price_overrides ADD COLUMN cache_read REAL")


def _overrides(db: sqlite3.Connection) -> dict[PriceKey, Price]:
    rows = db.execute(
        "SELECT provider, model, unit, input, output, currency, updated_at, cache_read"
        " FROM price_overrides"
    ).fetchall()
    return {
        (provider, model, unit): Price(
            input, output, currency, datetime.fromisoformat(updated).date(), cache_read
        )
        for provider, model, unit, input, output, currency, updated, cache_read in rows
    }


def _save(
    db: sqlite3.Connection, key: PriceKey, input: float, output: float | None, currency: str,
    cache_read: float | None, now: str,
) -> None:
    provider, model, unit = key
    db.execute(
        "INSERT OR REPLACE INTO price_overrides"
        " (provider, model, unit, input, output, currency, updated_at, cache_read)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (provider, model, unit, input, output, currency, now, cache_read),
    )


def _restore(db: sqlite3.Connection, key: PriceKey) -> bool:
    deleted = db.execute(
        "DELETE FROM price_overrides WHERE provider = ? AND model = ? AND unit = ?", key
    )
    return deleted.rowcount > 0


def preset_as_of() -> date | None:
    """When the presets were gathered (the earliest, if on different days)."""
    return min((p.as_of for p in PRESETS.values()), default=None)


def describe_price(key: PriceKey, found: Found) -> dict:
    price = found.price
    return {
        "input": price.input,
        "output": price.output,
        "cache_read": price.cache_read,
        "currency": price.currency,
        "per": per(key[2]),
        "as_of": price.as_of.isoformat(),
        "source": found.source,
        **(
            {"match": {"provider": found.match[0], "model": found.match[1]}}
            if found.match else {}
        ),
    }


def amount(
    price: Price,
    unit: str,
    input: float | None,
    output: float | None,
    cached: float | None = None,
) -> float:
    """What `input` (`cached` of it read from the cache) and `output` cost at `price`."""
    output_price = price.input if price.output is None else price.output
    cache_price = price.input if price.cache_read is None else price.cache_read
    hit = cached or 0
    return (
        ((input or 0) - hit) * price.input + hit * cache_price + (output or 0) * output_price
    ) / per(unit)


def _sum(values: Iterable[float | None]) -> float | None:
    told = [v for v in values if v is not None]
    return sum(told) if told else None


def _order(service: str) -> int:
    return SERVICES.index(service) if service in SERVICES else len(SERVICES)  # pyright: ignore[reportArgumentType]


def _totals(amounts: Iterable[tuple[str, float]]) -> list[dict]:
    summed: dict[str, float] = defaultdict(float)
    for currency, value in amounts:
        summed[currency] += value
    return [{"currency": c, "amount": summed[c]} for c in sorted(summed)]


def cost(
    calls: Sequence[CallGroup],
    pricing: Pricing,
    starts: Sequence[datetime],
) -> dict:
    """What the deployer's `calls` cost at `prices`: in all, by service, by provider, model
    and unit (`rows`), and in each stretch of the range beginning at `starts`; with what
    users' own keys were billed for, unpriced."""
    deployers = [c for c in calls if not c.own_key]
    known: dict[tuple[str, str, str, str], list[CallGroup]] = defaultdict(list)
    unknown: dict[tuple[str, str, str], int] = defaultdict(int)
    for group in deployers:
        if group.billed:
            assert group.usage_unit is not None
            known[(group.service, group.provider, group.model, group.usage_unit)].append(group)
        else:
            unknown[(group.service, group.provider, group.model)] += group.calls

    rows: list[dict] = []
    for (service, provider, model, unit), groups in known.items():
        input = _sum(g.usage_input for g in groups)
        output = _sum(g.usage_output for g in groups)
        cached = _sum(g.usage_cached for g in groups)
        found = pricing.find(provider, model, unit)
        rows.append({
            "service": service, "provider": provider, "model": model, "unit": unit,
            "calls": sum(g.calls for g in groups), "usage_unknown": 0, "input": input,
            "output": output,
            "cached": cached,
            "price": describe_price((provider, model, unit), found) if found else None,
            "currency": found.price.currency if found else None,
            "amount": amount(found.price, unit, input, output, cached) if found else None,
        })
    for (service, provider, model), count in unknown.items():
        same = next(
            (r for r in rows if (r["service"], r["provider"], r["model"]) == (service, provider, model)),
            None,
        )
        if same is None:
            same = {
                "service": service, "provider": provider, "model": model, "unit": None,
                "calls": 0, "usage_unknown": 0, "input": None, "output": None, "cached": None,
                "price": None, "currency": None, "amount": None,
            }
            rows.append(same)
        same["calls"] += count
        same["usage_unknown"] += count
    rows.sort(key=lambda r: (_order(r["service"]), r["provider"], r["model"], r["unit"] or ""))

    services = []
    for service in sorted({r["service"] for r in rows}, key=_order):
        mine = [r for r in rows if r["service"] == service]
        services.append({
            "service": service,
            "totals": _totals((r["currency"], r["amount"]) for r in mine if r["amount"] is not None),
            "unpriced": sum(r["unit"] is not None and r["price"] is None for r in mine),
            "usage_unknown": sum(r["usage_unknown"] for r in mine),
        })

    return {
        "totals": _totals(
            (r["currency"], r["amount"]) for r in rows if r["amount"] is not None
        ),
        "services": services,
        "rows": rows,
        "usage_unknown": sum(unknown.values()),
        "trend": _trend(deployers, pricing, starts),
        "own_keys": _own_keys([c for c in calls if c.own_key]),
        "preset_as_of": (d.isoformat() if (d := preset_as_of()) else None),
        "catalog_as_of": (d.isoformat() if (d := pricing.catalog_as_of) else None),
    }


def unpriced(billed: dict[PriceKey, ServiceName], pricing: Pricing) -> list[dict]:
    """The models the deployer's calls were `billed` for without a price, under the names
    the calls were recorded with, for an admin to price; `billed` by provider, model and
    unit, with the service each was (`ActivityLog.billed_models`)."""
    seen = {(service, *key) for key, service in billed.items() if pricing.find(*key) is None}
    return [
        {"service": service, "provider": provider, "model": model, "unit": unit}
        for service, provider, model, unit in sorted(seen, key=lambda s: (_order(s[0]), s[1:]))
    ]


def _trend(
    calls: Sequence[CallGroup],
    pricing: Pricing,
    starts: Sequence[datetime],
) -> list[dict]:
    """What the calls in each stretch beginning at `starts` cost, by currency."""
    if not starts:
        return []
    amounts: list[dict[str, float]] = [defaultdict(float) for _ in starts]
    found: dict[PriceKey, Found | None] = {}
    for group in calls:
        if not group.billed or group.usage_unit is None:
            continue
        key = (group.provider, group.model, group.usage_unit)
        if key not in found:
            found[key] = pricing.find(*key)
        if (price := found[key]) is None:
            continue
        amounts[group.stretch][price.price.currency] += amount(
            price.price, group.usage_unit, group.usage_input, group.usage_output,
            group.usage_cached,
        )
    return [
        {"start": start.isoformat(), "amounts": dict(sorted(a.items()))}
        for start, a in zip(starts, amounts, strict=True)
    ]


def _own_keys(calls: Sequence[CallGroup]) -> list[dict]:
    """What users' own keys were billed for, by service and unit, with how many calls did
    not say."""
    groups: dict[tuple[str, str | None], list[CallGroup]] = defaultdict(list)
    for call in calls:
        groups[(call.service, call.usage_unit if call.billed else None)].append(call)
    return [
        {
            "service": service,
            "unit": unit,
            "calls": sum(c.calls for c in group),
            "input": _sum(c.usage_input for c in group) if unit else None,
            "output": _sum(c.usage_output for c in group) if unit else None,
        }
        for (service, unit), group in sorted(
            groups.items(), key=lambda g: (_order(g[0][0]), g[0][1] or "")
        )
    ]

