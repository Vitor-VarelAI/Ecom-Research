"""Shared builders over the synthetic Telegram fixtures (no secrets).

These helpers convert ``tests/fixtures/telegram/synthetic_products.json``
into renderer-neutral model objects, resolving null field values to the
canonical placeholders via ``resolve_value``.
"""

from __future__ import annotations

import json
import os

from pipeline.notifications import (
    NotificationBatch,
    NotificationField,
    NotificationItem,
    NotificationSection,
    resolve_value,
    stable_product_id,
)

_FIXTURE_DIR = os.path.dirname(__file__)
DEFAULT_RUN_ID = "run-20260816-synthetic"


def load_fixture() -> dict:
    with open(
        os.path.join(_FIXTURE_DIR, "synthetic_products.json"), encoding="utf-8"
    ) as source:
        return json.load(source)


def field(data: dict) -> NotificationField:
    """One NotificationField; null values resolve to the placeholder of the
    field's declared absence meaning."""
    return NotificationField(
        value=resolve_value(data.get("value"), data.get("meaning") or "missing"),
        label=data.get("label"),
        is_header=bool(data.get("is_header")),
    )


def section(data: dict) -> NotificationSection:
    return NotificationSection(
        heading=data.get("heading"),
        fields=tuple(field(item) for item in data.get("fields", [])),
        as_table=bool(data.get("as_table")),
        as_details=bool(data.get("as_details")),
    )


def summary_item(data: dict) -> NotificationItem:
    """Content-only summary item (identity is assigned by the batch)."""
    return NotificationItem(
        kind="summary",
        title=data["title"],
        subtitle=data.get("subtitle"),
        sections=tuple(section(item) for item in data.get("sections", [])),
        footer_fields=tuple(field(item) for item in data.get("footer_fields", [])),
        footer=tuple(data.get("footer", [])),
    )


def product_item(data: dict) -> NotificationItem:
    """Content-only product item; the stable product id reuses the keyword
    hash semantics (identity is assigned by the batch)."""
    return NotificationItem(
        kind="product",
        title=data["keyword"],
        product=stable_product_id(data["keyword"]),
        header_fields=(
            NotificationField(value=f"{data['score']}/100", label="Score"),
            NotificationField(value=data["label"], label="Sinal advisory"),
        ),
        sections=tuple(section(item) for item in data.get("sections", [])),
        footer_fields=tuple(field(item) for item in data.get("footer_fields", [])),
        footer=tuple(data.get("footer", [])),
    )


def synthetic_batch(run_id: str = DEFAULT_RUN_ID, mode: str = "sunday") -> NotificationBatch:
    """Summary + the three synthetic products, in deterministic order."""
    fixture = load_fixture()
    return NotificationBatch.build(
        mode=mode,
        run_id=run_id,
        summary=summary_item(fixture["summary"]),
        products=[product_item(item) for item in fixture["products"]],
    )
