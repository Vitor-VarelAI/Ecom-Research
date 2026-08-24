"""Pure monetization gate: eligibility + deterministic emission.

The gate is deliberately product-agnostic.  It consumes the assembled
research result, the per-source states, the validated catalog, a
deterministic ``today`` and an environment lookup, and returns either None
or a bounded emission dict (up to two offers: active primary, then active
trust_fallback — the role order is operator-chosen, never ranked by
commission).  The environment VALUE of ``affiliate_url_env`` is only probed
for non-emptiness; it is never inspected into the emission — only the
variable NAME travels as ``affiliate_link_ref``.
"""

from __future__ import annotations

import math
import os
from datetime import date
from typing import Any, Callable

from pipeline.monetization.model import DISCLOSURE_PT_PT, AffiliateCatalog, Offer

# Critical sources whose state must be ``valid`` for monetization to attach:
# ads evidence, trend evidence and keyword-volume evidence.
CRITICAL_SOURCE_NAMES = ("apify_ads", "trends", "keyword_csv")


def evaluate(
    *,
    result: dict[str, Any],
    source_states: dict[str, Any],
    catalog: AffiliateCatalog | None,
    today: date,
    env_get: Callable[[str], str | None] = os.environ.get,
) -> dict[str, Any] | None:
    """Emit the bounded monetization payload, or None when not eligible.

    Returns None unless *every* condition holds: the score label is AVANÇAR
    and not incomplete; apify_ads/trends/keyword_csv source states are
    ``valid``; ads carry matched evidence (``matched_count``/``total_ads``
    > 0); the CSV has no error and a finite numeric volume >= 0; the product
    has an approved config entry with a market; and at least one offer is
    active, unexpired and its ``affiliate_url_env`` resolves to a non-empty
    environment value.
    """
    if catalog is None or not catalog.products:
        return None

    score_result = result.get("score_result") or {}
    if score_result.get("label") != "AVANÇAR":
        return None
    if score_result.get("incomplete") is True:
        return None

    for name in CRITICAL_SOURCE_NAMES:
        state = source_states.get(name)
        if not isinstance(state, dict) or state.get("status") != "valid":
            return None

    ads = result.get("ads_data")
    if not isinstance(ads, dict) or "error" in ads:
        return None
    total_ads = ads.get("total_ads")
    matched_count = ads.get("matched_count")
    if isinstance(total_ads, bool) or not isinstance(total_ads, (int, float)):
        return None
    if isinstance(matched_count, bool) or not isinstance(matched_count, (int, float)):
        return None
    if not (total_ads > 0 and matched_count > 0):
        return None

    csv_data = result.get("csv_data")
    if not isinstance(csv_data, dict) or "error" in csv_data:
        return None
    volume = csv_data.get("volume")
    if isinstance(volume, bool) or not isinstance(volume, (int, float)):
        return None
    try:
        volume_finite = math.isfinite(float(volume))
    except (OverflowError, ValueError):
        volume_finite = False
    if not volume_finite or float(volume) < 0:
        return None

    product_id = result.get("product_id")
    product = next((entry for entry in catalog.products if entry.product_id == product_id), None)
    if product is None or not product.approved or not product.market:
        return None

    eligible = []
    for offer in product.offers:
        if offer.status != "active":
            continue
        if offer.valid_until < today:
            continue
        value = env_get(offer.affiliate_url_env)
        if not isinstance(value, str) or not value.strip():
            continue
        eligible.append(offer)

    primary = next((offer for offer in eligible if offer.role == "primary"), None)
    fallback = next((offer for offer in eligible if offer.role == "trust_fallback"), None)
    emitted = [offer for offer in (primary, fallback) if offer is not None]
    if not emitted:
        return None

    return {
        "disclosure": DISCLOSURE_PT_PT,
        "market": product.market,
        "offers": [_emit_offer(offer) for offer in emitted],
    }


def _emit_offer(offer: Offer) -> dict[str, Any]:
    """Bounded, validated emission for one offer.  All metadata is validated
    by ``pipeline.monetization.config``; URLs are the stored public
    canonical forms; the tracked affiliate env VALUE never appears — only
    the variable NAME as ``affiliate_link_ref``."""
    return {
        "offer_id": offer.offer_id,
        "role": offer.role,
        "merchant": offer.merchant,
        "status": offer.status,
        "commission_type": offer.commission_type,
        "commission_value": offer.commission_value,
        "commission_currency": offer.commission_currency,
        "cookie_duration_days": offer.cookie_duration_days,
        "payout_eligibility": offer.payout_eligibility,
        "shipping_markets": list(offer.shipping_markets),
        "asset_rights": offer.asset_rights,
        "traffic_policy": offer.traffic_policy,
        "destination_url": offer.destination_url,
        "evidence_url": offer.evidence_url,
        "checked_at": offer.checked_at.isoformat(),
        "valid_until": offer.valid_until.isoformat(),
        "affiliate_link_ref": offer.affiliate_url_env,
    }
