"""Validated market scopes and product-local research targets."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable

from pipeline.products import Product, normalize

_MARKET_ID_RE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_TOP_LEVEL_KEYS = frozenset({"schema_version", "markets"})
_MARKET_KEYS = frozenset({
    "market_id",
    "name",
    "currency",
    "ads_country",
    "trends_geo",
    "serp_gl",
    "serp_hl",
    "serp_location",
    "product_queries",
})


class MarketRegistryError(Exception):
    """The market registry is missing, unreadable, or invalid."""


class MarketTargetNotFoundError(Exception):
    """The requested product is not configured for the requested market."""


@dataclass(frozen=True, slots=True)
class Market:
    market_id: str
    name: str
    currency: str
    ads_country: str
    trends_geo: str
    serp_gl: str
    serp_hl: str
    serp_location: str
    product_queries: MappingProxyType

    def as_dict(self) -> dict[str, str]:
        return {
            "market_id": self.market_id,
            "name": self.name,
            "currency": self.currency,
            "ads_country": self.ads_country,
            "trends_geo": self.trends_geo,
            "serp_gl": self.serp_gl,
            "serp_hl": self.serp_hl,
            "serp_location": self.serp_location,
        }


@dataclass(frozen=True, slots=True)
class MarketTarget:
    product: Product
    market: Market
    research_keyword: str

    @property
    def product_id(self) -> str:
        return self.product.product_id

    @property
    def name(self) -> str:
        return self.product.name

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.product.aliases

    @property
    def active(self) -> bool:
        return self.product.active

    @property
    def market_id(self) -> str:
        return self.market.market_id


class MarketRegistry:
    """Ordered immutable market registry."""

    __slots__ = ("_markets", "_by_id")

    def __init__(self, markets: Iterable[Market]) -> None:
        self._markets = tuple(markets)
        self._by_id = MappingProxyType({market.market_id: market for market in self._markets})

    @property
    def markets(self) -> tuple[Market, ...]:
        return self._markets

    def targets_for(self, products: Iterable[Product]) -> tuple[MarketTarget, ...]:
        ordered_products = tuple(products)
        return tuple(
            MarketTarget(product=product, market=market, research_keyword=query)
            for market in self._markets
            for product in ordered_products
            if (query := market.product_queries.get(product.product_id)) is not None
        )

    def target(self, product: Product, market_id: str) -> MarketTarget:
        normalized_id = market_id.strip().upper() if isinstance(market_id, str) else ""
        market = self._by_id.get(normalized_id)
        if market is None:
            raise MarketTargetNotFoundError(f"unknown market: {normalized_id or '<empty>'}")
        query = market.product_queries.get(product.product_id)
        if query is None:
            raise MarketTargetNotFoundError(
                f"product {product.product_id} is not configured for market {market.market_id}"
            )
        return MarketTarget(product=product, market=market, research_keyword=query)


def load_market_registry(path: str | Path) -> MarketRegistry:
    try:
        with open(path, "r", encoding="utf-8") as source:
            data = json.load(source)
    except OSError as exc:
        raise MarketRegistryError(
            f"cannot read market registry at {path}: {exc.__class__.__name__}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise MarketRegistryError(
            f"invalid market registry at {path}: not valid JSON"
        ) from exc
    if not isinstance(data, dict) or set(data) != _TOP_LEVEL_KEYS:
        raise MarketRegistryError(
            f"invalid market registry at {path}: expected schema_version and markets"
        )
    if data.get("schema_version") != 1 or not isinstance(data.get("markets"), list):
        raise MarketRegistryError(
            f"invalid market registry at {path}: schema_version must be 1 and markets must be a list"
        )
    markets: list[Market] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["markets"]):
        if not isinstance(raw, dict) or set(raw) != _MARKET_KEYS:
            raise MarketRegistryError(
                f"invalid market registry at {path}: market at index {index} has invalid shape"
            )
        market_id = raw.get("market_id")
        currency = raw.get("currency")
        if not isinstance(market_id, str) or not _MARKET_ID_RE.fullmatch(market_id):
            raise MarketRegistryError(
                f"invalid market registry at {path}: market at index {index} has invalid market_id"
            )
        if market_id in seen:
            raise MarketRegistryError(
                f"invalid market registry at {path}: duplicate market_id {market_id}"
            )
        seen.add(market_id)
        if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
            raise MarketRegistryError(
                f"invalid market registry at {path}: market at index {index} has invalid currency"
            )
        text_fields = {}
        for field in ("name", "ads_country", "trends_geo", "serp_gl", "serp_hl", "serp_location"):
            value = raw.get(field)
            if not isinstance(value, str) or not normalize(value):
                raise MarketRegistryError(
                    f"invalid market registry at {path}: market at index {index} has invalid {field}"
                )
            text_fields[field] = value
        queries = raw.get("product_queries")
        if not isinstance(queries, dict) or not queries:
            raise MarketRegistryError(
                f"invalid market registry at {path}: market at index {index} needs product_queries"
            )
        clean_queries: dict[str, str] = {}
        for product_id, query in queries.items():
            if not isinstance(product_id, str) or not isinstance(query, str) or not normalize(query):
                raise MarketRegistryError(
                    f"invalid market registry at {path}: market at index {index} has invalid product query"
                )
            clean_queries[product_id] = query
        markets.append(Market(
            market_id=market_id,
            name=text_fields["name"],
            currency=currency,
            ads_country=text_fields["ads_country"],
            trends_geo=text_fields["trends_geo"],
            serp_gl=text_fields["serp_gl"],
            serp_hl=text_fields["serp_hl"],
            serp_location=text_fields["serp_location"],
            product_queries=MappingProxyType(clean_queries),
        ))
    return MarketRegistry(markets)
