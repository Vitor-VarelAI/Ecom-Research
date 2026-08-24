"""Generic affiliate-monetization domain model (stdlib only, product-agnostic).

Nothing here knows about a specific product, merchant or commission: this is
the validated form of ``config/affiliate_offers.json`` produced by
``pipeline.monetization.config`` and consumed by the pure gate
(``pipeline.monetization.gate``).  Roles are operator-chosen; the pipeline
never infers or ranks offers by commission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Offer lifecycle statuses (operator-maintained; no automatic transitions).
OFFER_STATUSES = ("candidate", "applied", "approved", "active", "rejected")

# Operator-chosen roles; the gate emits at most one of each.
OFFER_ROLES = ("primary", "trust_fallback")

COMMISSION_TYPES = ("percentage", "fixed")

# Fixed PT-PT affiliate disclosure appended to every monetization emission.
# It is a plain disclosure — the schema has no health/benefit-claim field and
# this text makes none.
DISCLOSURE_PT_PT = (
    "Divulgação de afiliado: esta página pode conter links de afiliado; "
    "o operador pode receber uma comissão por compras efetuadas através "
    "desses links, sem custo adicional para o comprador."
)


@dataclass(frozen=True, slots=True)
class Offer:
    """One strictly validated affiliate offer (operator-configured)."""

    offer_id: str
    role: str
    merchant: str
    status: str
    destination_url: str
    affiliate_url_env: str
    commission_type: str
    commission_value: float
    commission_currency: str | None
    cookie_duration_days: int
    payout_eligibility: str
    shipping_markets: tuple[str, ...]
    asset_rights: str
    traffic_policy: str
    evidence_url: str
    checked_at: date
    valid_until: date


@dataclass(frozen=True, slots=True)
class ProductOffers:
    """Monetization configuration for one product (keyed by ``product_id``)."""

    product_id: str
    market: str
    approved: bool
    offers: tuple[Offer, ...]


@dataclass(frozen=True, slots=True)
class AffiliateCatalog:
    """Validated catalog loaded from ``config/affiliate_offers.json``."""

    schema_version: int
    products: tuple[ProductOffers, ...]
