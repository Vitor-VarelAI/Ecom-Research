"""Shared product-registry fixtures for the canonical-product-identity slices.

Builds a valid ``config/products.json`` under a tmp root and exposes the
``pipeline.products.Product`` objects tests pass to run.py.  Product ids are
seeded from the stable keyword hash (SHA-256 first 12 hex chars), exactly
like the committed registry, so ``product_id == stable_product_id(keyword)``
and pack filenames/notification identities match historical semantics.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from pipeline.products import Product, ProductRegistry, load_registry


def stable_id(keyword: str) -> str:
    """The stable 12-hex product id for a keyword (registry seeding semantics)."""
    return hashlib.sha256(keyword.encode("utf-8")).hexdigest()[:12]


def product_entries(items: Iterable[str | dict[str, Any]]) -> list[dict[str, Any]]:
    """Registry entries for plain keyword strings or ``{keyword, name?, aliases?, active?}`` dicts."""
    entries: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            item = {"keyword": item}
        entries.append(
            {
                "product_id": stable_id(item["keyword"]),
                "name": item.get("name", item["keyword"]),
                "research_keyword": item["keyword"],
                "aliases": item.get("aliases", []),
                "active": item.get("active", True),
            }
        )
    return entries


def write_registry(root: str | Path, items: Iterable[str | dict[str, Any]]) -> ProductRegistry:
    """Write ``config/products.json`` under ``root`` and load it back fail-closed."""
    config_dir = Path(root) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "products.json"
    payload = {"schema_version": 1, "products": product_entries(items)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_registry(path)


def product(
    keyword: str,
    *,
    name: str | None = None,
    aliases: tuple[str, ...] = (),
    active: bool = True,
) -> Product:
    """A standalone Product (no registry needed) for direct run.py calls."""
    return Product(
        product_id=stable_id(keyword),
        name=name if name is not None else keyword,
        research_keyword=keyword,
        aliases=aliases,
        active=active,
    )
