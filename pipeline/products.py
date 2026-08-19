"""Canonical product identity: a validated, immutable product registry.

`config/products.json` is the source of truth for the products once wired;
this module only reads it, fail-closed.  The loader never creates or
rewrites files and never leaks file contents in error messages.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Iterator

_PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
_TOP_LEVEL_KEYS = frozenset({"schema_version", "products"})
_PRODUCT_KEYS = frozenset({"product_id", "name", "research_keyword", "aliases", "active"})


class ProductRegistryError(Exception):
    """The registry file is missing, unreadable, or invalid (fail-closed)."""


class ProductNotFoundError(Exception):
    """A valid registry does not contain the requested product token."""


def normalize(value: str) -> str:
    """Fold a human string: strip, collapse internal whitespace, remove
    accents (NFKD + combining-char removal), then casefold."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.casefold().split())


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    name: str
    research_keyword: str
    aliases: tuple[str, ...]
    active: bool


def _iter_tokens(product: Product) -> Iterator[str]:
    """All normalized resolution tokens of a product, in priority order."""
    yield normalize(product.name)
    yield normalize(product.research_keyword)
    for alias in product.aliases:
        yield normalize(alias)


class ProductRegistry:
    """Ordered, immutable view over validated products."""

    __slots__ = ("_products", "_by_id", "_by_token")

    def __init__(self, products: Iterable[Product]) -> None:
        self._products = tuple(products)
        self._by_id = MappingProxyType({p.product_id: p for p in self._products})
        self._by_token = MappingProxyType(
            {token: p for p in self._products for token in _iter_tokens(p)}
        )

    @property
    def products(self) -> tuple[Product, ...]:
        return self._products

    def __iter__(self) -> Iterator[Product]:
        return iter(self._products)

    def __len__(self) -> int:
        return len(self._products)

    def active_products(self) -> tuple[Product, ...]:
        return tuple(product for product in self._products if product.active)

    def resolve(self, token: str) -> Product:
        """Resolve an exact product_id, else a normalized name/keyword/alias."""
        if isinstance(token, str):
            product = self._by_id.get(token)
            if product is None:
                product = self._by_token.get(normalize(token))
        else:
            product = None
        if product is None:
            raise ProductNotFoundError(f"unknown product token: {token!r}")
        return product


def load_registry(path: str | Path) -> ProductRegistry:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise ProductRegistryError(
            f"cannot read product registry at {path}: {exc.__class__.__name__}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProductRegistryError(
            f"invalid product registry at {path}: not valid JSON"
        ) from exc
    if not isinstance(data, dict):
        raise ProductRegistryError(
            f"invalid product registry at {path}: top-level value must be an object"
        )
    if set(data.keys()) != _TOP_LEVEL_KEYS:
        raise ProductRegistryError(
            f"invalid product registry at {path}: top-level keys must be exactly "
            f"{{'schema_version', 'products'}}"
        )
    if data.get("schema_version") != 1:
        raise ProductRegistryError(
            f"invalid product registry at {path}: schema_version must be 1"
        )
    products_raw = data.get("products")
    if not isinstance(products_raw, list):
        raise ProductRegistryError(
            f"invalid product registry at {path}: 'products' must be a list"
        )
    products = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(products_raw):
        if not isinstance(entry, dict):
            raise ProductRegistryError(
                f"invalid product registry at {path}: product at index {index} "
                f"must be an object"
            )
        if set(entry.keys()) != _PRODUCT_KEYS:
            raise ProductRegistryError(
                f"invalid product registry at {path}: product at index {index} "
                f"must have exactly the keys {{'product_id', 'name', "
                f"'research_keyword', 'aliases', 'active'}}"
            )
        product_id = entry["product_id"]
        if not isinstance(product_id, str) or not _PRODUCT_ID_RE.match(product_id):
            raise ProductRegistryError(
                f"invalid product registry at {path}: product at index {index} "
                f"has an invalid product_id (must match "
                f"^[A-Za-z0-9][A-Za-z0-9_-]{{5,63}}$)"
            )
        if product_id in seen_ids:
            raise ProductRegistryError(
                f"invalid product registry at {path}: duplicate product_id "
                f"'{product_id}'"
            )
        seen_ids.add(product_id)
        name = _require_nonblank_str(entry["name"], path, index, "name")
        research_keyword = _require_nonblank_str(
            entry["research_keyword"], path, index, "research_keyword"
        )
        aliases_raw = entry["aliases"]
        if not isinstance(aliases_raw, list):
            raise ProductRegistryError(
                f"invalid product registry at {path}: product at index {index} "
                f"has 'aliases' that is not a list"
            )
        aliases = tuple(
            _require_nonblank_str(alias, path, index, "aliases")
            for alias in aliases_raw
        )
        active = entry["active"]
        if not isinstance(active, bool):
            raise ProductRegistryError(
                f"invalid product registry at {path}: product at index {index} "
                f"has 'active' that is not a boolean"
            )
        products.append(
            Product(
                product_id=product_id,
                name=name,
                research_keyword=research_keyword,
                aliases=aliases,
                active=active,
            )
        )
    _validate_no_token_collisions(products, path)
    return ProductRegistry(products)


def _require_nonblank_str(value: Any, path: str | Path, index: int, field: str) -> str:
    if not isinstance(value, str) or not normalize(value):
        raise ProductRegistryError(
            f"invalid product registry at {path}: product at index {index} "
            f"must have a non-blank string '{field}'"
        )
    return value


def _validate_no_token_collisions(products: list[Product], path: str | Path) -> None:
    """Reject two different products sharing a normalized name/keyword/alias.
    Equivalent tokens repeated inside the same product are allowed."""
    index: dict[str, tuple[Product, str]] = {}
    for product in products:
        for token, field in (
            (normalize(product.name), "name"),
            (normalize(product.research_keyword), "research_keyword"),
        ):
            previous = index.get(token)
            if previous is not None and previous[0].product_id != product.product_id:
                raise ProductRegistryError(
                    f"invalid product registry at {path}: normalized {field} of "
                    f"product '{product.product_id}' collides with a token of "
                    f"product '{previous[0].product_id}'"
                )
            index[token] = (product, field)
        for alias in product.aliases:
            token = normalize(alias)
            previous = index.get(token)
            if previous is not None and previous[0].product_id != product.product_id:
                raise ProductRegistryError(
                    f"invalid product registry at {path}: normalized alias of "
                    f"product '{product.product_id}' collides with a token of "
                    f"product '{previous[0].product_id}'"
                )
            index[token] = (product, "alias")
