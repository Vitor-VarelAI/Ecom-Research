"""Strict loader for ``config/affiliate_offers.json`` (fail-closed, stdlib only).

The loader never raises and never leaks file contents or validation details:
any missing/unreadable/malformed config yields ``None`` (monetization
disabled) behind a single sanitized log line, so a bad config can never
break a research run nor expose raw config/errors.  The committed config
carries ``schema_version: 1`` and no products/offers (empty-but-valid
config loads as a valid empty catalog; the gate then yields nothing).

Validation contract (all-or-nothing per file):

* exact top-level/product/offer key sets (unknown keys reject the file);
* ``schema_version`` must be exactly 1;
* ``product_id`` must match the products.json registry format and be unique;
* ``offer_id`` must match the offer format and be unique across the catalog;
* product ``market`` must be covered by every offer's ``shipping_markets``;
* URLs must be http(s), without userinfo/query/fragment/control characters
  and sanitizable to their public canonical form (stored sanitized);
* dates must be valid ISO with ``checked_at <= valid_until``;
* ``commission_currency`` must be 3-letter ISO and is required for ``fixed``;
* ``commission_value`` positive and finite; ``cookie_duration_days`` a
  positive integer;
* at most one ACTIVE offer per role (per product).
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pipeline.monetization.model import (
    COMMISSION_TYPES,
    OFFER_ROLES,
    OFFER_STATUSES,
    AffiliateCatalog,
    Offer,
    ProductOffers,
)

logger = logging.getLogger("monetization.config")

_TOP_LEVEL_KEYS = frozenset({"schema_version", "products"})
_PRODUCT_KEYS = frozenset({"product_id", "market", "approved", "offers"})
_OFFER_KEYS = frozenset({
    "offer_id", "role", "merchant", "status", "destination_url",
    "affiliate_url_env", "commission_type", "commission_value",
    "commission_currency", "cookie_duration_days", "payout_eligibility",
    "shipping_markets", "asset_rights", "traffic_policy", "evidence_url",
    "checked_at", "valid_until",
})

# Same format as the canonical products.json registry.
_PRODUCT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{5,63}$")
_OFFER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_SAFE_SCHEMES = frozenset({"http", "https"})


class _InvalidConfig(Exception):
    """Internal signal: this file is malformed (details never surface)."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidConfig
        result[key] = value
    return result


def load(path: str | Path) -> AffiliateCatalog | None:
    """Validated catalog, or None when missing/unreadable/invalid (fail-closed).

    Never raises and never exposes file contents, error messages or stack
    traces: the only output on failure is one sanitized log line.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError, _InvalidConfig):
        logger.warning("monetization: config indisponível ou inválido — monetização desativada")
        return None
    try:
        return _build(raw)
    except _InvalidConfig:
        logger.warning("monetization: config inválido — monetização desativada")
        return None


def _build(raw: Any) -> AffiliateCatalog:
    if not isinstance(raw, dict) or set(raw.keys()) != _TOP_LEVEL_KEYS:
        raise _InvalidConfig
    if raw.get("schema_version") != 1:
        raise _InvalidConfig
    products_raw = raw.get("products")
    if not isinstance(products_raw, list):
        raise _InvalidConfig
    products: list[ProductOffers] = []
    seen_product_ids: set[str] = set()
    seen_offer_ids: set[str] = set()
    for entry in products_raw:
        if not isinstance(entry, dict) or set(entry.keys()) != _PRODUCT_KEYS:
            raise _InvalidConfig
        product_id = entry["product_id"]
        if not isinstance(product_id, str) or not _PRODUCT_ID_RE.fullmatch(product_id):
            raise _InvalidConfig
        if product_id in seen_product_ids:
            raise _InvalidConfig
        seen_product_ids.add(product_id)
        market = entry["market"]
        if not isinstance(market, str) or not market.strip():
            raise _InvalidConfig
        approved = entry["approved"]
        if not isinstance(approved, bool):
            raise _InvalidConfig
        offers_raw = entry["offers"]
        if not isinstance(offers_raw, list):
            raise _InvalidConfig
        offers: list[Offer] = []
        active_by_role: dict[str, int] = {}
        for offer_entry in offers_raw:
            offer = _build_offer(offer_entry, seen_offer_ids)
            if _norm(market) not in {_norm(market_item) for market_item in offer.shipping_markets}:
                raise _InvalidConfig  # the operator's market must ship via this offer
            if offer.status == "active":
                active_by_role[offer.role] = active_by_role.get(offer.role, 0) + 1
            offers.append(offer)
        if any(count > 1 for count in active_by_role.values()):
            raise _InvalidConfig  # at most one ACTIVE offer per role
        products.append(
            ProductOffers(product_id=product_id, market=market, approved=approved, offers=tuple(offers))
        )
    return AffiliateCatalog(schema_version=1, products=tuple(products))


def _build_offer(raw: Any, seen_offer_ids: set[str]) -> Offer:
    if not isinstance(raw, dict) or set(raw.keys()) != _OFFER_KEYS:
        raise _InvalidConfig
    offer_id = raw["offer_id"]
    if not isinstance(offer_id, str) or not _OFFER_ID_RE.fullmatch(offer_id):
        raise _InvalidConfig
    if offer_id in seen_offer_ids:
        raise _InvalidConfig
    seen_offer_ids.add(offer_id)

    role = _require_choice(raw["role"], OFFER_ROLES)
    merchant = _require_text(raw["merchant"])
    status = _require_choice(raw["status"], OFFER_STATUSES)
    destination_url = _require_public_url(raw["destination_url"])
    affiliate_url_env = raw["affiliate_url_env"]
    if not isinstance(affiliate_url_env, str) or not _ENV_NAME_RE.fullmatch(affiliate_url_env):
        raise _InvalidConfig

    commission_type = _require_choice(raw["commission_type"], COMMISSION_TYPES)
    commission_value = raw["commission_value"]
    if isinstance(commission_value, bool) or not isinstance(commission_value, (int, float)):
        raise _InvalidConfig
    try:
        commission_finite = math.isfinite(float(commission_value))
    except (OverflowError, ValueError):
        commission_finite = False
    if not commission_finite or float(commission_value) <= 0:
        raise _InvalidConfig
    commission_currency = raw["commission_currency"]
    if commission_currency is not None:
        if not isinstance(commission_currency, str) or not _ISO_CURRENCY_RE.fullmatch(commission_currency):
            raise _InvalidConfig
    elif commission_type == "fixed":
        raise _InvalidConfig  # currency is required for fixed commissions

    cookie = raw["cookie_duration_days"]
    if isinstance(cookie, bool) or not isinstance(cookie, int) or cookie <= 0:
        raise _InvalidConfig

    payout_eligibility = _require_text(raw["payout_eligibility"])
    shipping_raw = raw["shipping_markets"]
    if not isinstance(shipping_raw, list) or not shipping_raw:
        raise _InvalidConfig
    shipping_markets = tuple(_require_text(item) for item in shipping_raw)
    asset_rights = _require_text(raw["asset_rights"])
    traffic_policy = _require_text(raw["traffic_policy"])
    evidence_url = _require_public_url(raw["evidence_url"])

    checked_at = _require_iso_date(raw["checked_at"])
    valid_until = _require_iso_date(raw["valid_until"])
    if checked_at > valid_until:
        raise _InvalidConfig

    return Offer(
        offer_id=offer_id,
        role=role,
        merchant=merchant,
        status=status,
        destination_url=destination_url,
        affiliate_url_env=affiliate_url_env,
        commission_type=commission_type,
        commission_value=float(commission_value),
        commission_currency=commission_currency,
        cookie_duration_days=cookie,
        payout_eligibility=payout_eligibility,
        shipping_markets=shipping_markets,
        asset_rights=asset_rights,
        traffic_policy=traffic_policy,
        evidence_url=evidence_url,
        checked_at=checked_at,
        valid_until=valid_until,
    )


def _norm(value: str) -> str:
    return value.strip().upper()


def _require_choice(value: Any, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _InvalidConfig
    return value


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or _has_control(value):
        raise _InvalidConfig
    return value.strip()


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _require_iso_date(value: Any) -> date:
    if not isinstance(value, str):
        raise _InvalidConfig
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _InvalidConfig


def _require_public_url(value: Any) -> str:
    """Strict public canonical URL: http(s), no userinfo/query/fragment, no
    control characters, valid host/port.  Returns the sanitized canonical
    form (scheme + host + safe port + path); anything else is invalid."""
    if not isinstance(value, str) or not value or _has_control(value):
        raise _InvalidConfig
    try:
        parsed = urlparse(value)
    except ValueError:
        raise _InvalidConfig
    if parsed.scheme.casefold() not in _SAFE_SCHEMES:
        raise _InvalidConfig
    if parsed.username is not None or parsed.password is not None:
        raise _InvalidConfig
    if parsed.query or parsed.fragment:
        raise _InvalidConfig
    hostname = parsed.hostname
    if not hostname or any(char.isspace() for char in hostname):
        raise _InvalidConfig
    try:
        port = parsed.port
    except ValueError:
        raise _InvalidConfig
    if port is not None and not (1 <= port <= 65535):
        raise _InvalidConfig
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host_for_netloc
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    if port is not None and port != default_port:
        netloc = f"{host_for_netloc}:{port}"
    return f"{parsed.scheme.casefold()}://{netloc}{parsed.path}"
