"""The prices the dashboard estimates the deployer's costs at (`prices`), under
`/api/admin/prices`, for admins only: every preset and every model used lately, each
with the price in effect and where it is from, beside the preset, the catalogs' and the
price an admin set in its place; setting a price for any provider, model and unit,
restoring one, and fetching the catalogs again now. The models used lately without a
price are listed too, under the names their service calls were recorded with, for an
admin to price them."""

from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from eidolon.activity import KEPT
from eidolon.admin_api import Admin
from eidolon.assembly import Backend
from eidolon.price_catalog import describe_state
from eidolon.prices import Pricing, preset_as_of, unpriced

Unit = Literal["tokens", "bytes", "characters", "seconds", "images"]
"""The units service calls are billed in (`prices.PER`)."""
Currency = Literal["USD", "CNY"]
"""What prices are in: the providers' price pages are in dollars or yuan."""


class PriceInput(BaseModel):
    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)
    unit: Unit
    input: float = Field(ge=0, allow_inf_nan=False)
    output: float | None = Field(None, ge=0, allow_inf_nan=False)
    cache_read: float | None = Field(None, ge=0, allow_inf_nan=False)
    currency: Currency

    @field_validator("provider", "model")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


def add_admin_price_routes(app: FastAPI, backend: Backend) -> None:
    prices = backend.prices

    async def described() -> dict:
        described, without = await described_by(await prices.pricing())
        prices.catalog.refresh_soon(missing=any(u["unit"] == "tokens" for u in without))
        return {
            "preset_as_of": (d.isoformat() if (d := preset_as_of()) else None),
            "catalog": describe_state(prices.catalog.state()),
            "prices": described,
            "unpriced": without,
        }

    async def described_by(pricing: Pricing) -> tuple[list[dict], list[dict]]:
        """Every price, and the models billed without one."""
        billed = await backend.activity.billed_models(backend.clock() - KEPT)
        return pricing.describe(billed), unpriced(billed, pricing)

    @app.get("/api/admin/prices")
    async def get_prices(_: Admin) -> dict:
        return await described()

    @app.put("/api/admin/prices")
    async def save_price(price: PriceInput, _: Admin) -> dict:
        """Price a provider's model so from now on, in place of its preset, if any."""
        await prices.save((price.provider, price.model, price.unit), price.input,
                          price.output, price.currency, price.cache_read)
        return await described()

    @app.post("/api/admin/prices/refresh")
    async def refresh_catalog(_: Admin) -> dict:
        """Fetch the price catalogs online again now, or wait for the fetch under way."""
        await prices.catalog.refresh()
        return await described()

    @app.delete("/api/admin/prices")
    async def restore_price(
        _: Admin,
        provider: Annotated[str, Query()],
        model: Annotated[str, Query()],
        unit: Annotated[str, Query()],
    ) -> dict:
        """Back to the preset, or unpriced if there is none."""
        if not await prices.restore((provider, model, unit)):
            raise HTTPException(404, "这个模型没有在后台设过单价")
        return await described()
