"""Immutable models for the isolated Offer + Creative Arbitrage contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MONETIZATION_MODELS = (
    "affiliate",
    "creator",
    "ambassador",
    "cpa",
    "revenue_share",
    "reseller",
    "dropshipping",
    "direct_fulfilment",
    "tiktok_shop_affiliate",
)
PRODUCT_TYPES = ("physical", "digital", "service")
PERMISSION_STATES = ("YES", "NO", "NEEDS_VERIFICATION", "NOT_APPLICABLE")
FACT_STATUSES = ("KNOWN", "CONTRADICTED")

TOP_LEVEL_KEYS = frozenset({"schema_version", "base_currency", "fx_rates", "offers"})
FX_RATE_KEYS = frozenset({"currency", "eur_per_unit", "source_ref", "observed_at"})
OFFER_KEYS = frozenset(
    {
        "offer_id",
        "name",
        "brand",
        "market_id",
        "research_keyword",
        "enabled",
        "monetization_model",
        "product_type",
        "links",
        "economics",
        "permissions",
        "us_readiness",
        "landing",
        "creative_scores",
        "meta_structure",
        "trends",
        "competition",
        "evidence",
    }
)
LINK_KEYS = frozenset({"program_url", "offer_url", "landing_url", "ad_links"})
ECONOMICS_KEYS = frozenset(
    {
        "retail_price",
        "aov",
        "affiliate_commission_pct",
        "cpa_payout",
        "expected_payout_per_order",
        "recurring_commission",
        "cookie_days",
        "wholesale_cost",
        "gross_spread",
        "monthly_fees",
        "minimum_order",
        "refund_rate",
        "refund_policy",
        "payment_fees",
        "fulfilment_cost",
        "shipping_cost",
        "expected_return_cost",
        "other_variable_cost",
        "expected_cpa",
        "currency",
    }
)
PERMISSIONS_KEYS = frozenset(
    {
        "meta_ads",
        "tiktok_ads",
        "google_ads",
        "custom_creatives",
        "brand_name_use",
        "brand_assets",
        "brand_bidding",
        "map_policy",
        "us_traffic",
    }
)
US_READINESS_KEYS = frozenset(
    {
        "us_warehouse",
        "us_fulfilment",
        "delivery_min_days",
        "delivery_max_days",
        "us_returns_address",
        "shipping_cost",
        "checkout_in_usd",
    }
)
LANDING_KEYS = frozenset({"functional_score", "quality_score"})
CREATIVE_SCORE_KEYS = frozenset(
    {
        "visual_transformation",
        "hookability",
        "problem_awareness",
        "demo_potential",
        "ugc_potential",
        "storytelling_potential",
        "ai_creative_potential",
        "before_after_potential",
        "angle_breadth",
        "creative_gap",
    }
)
META_STRUCTURE_KEYS = frozenset(
    {
        "advertiser_count",
        "active_ads",
        "oldest_ad_days",
        "ads_older_than_30d",
        "creative_concept_count",
        "variants_per_concept",
        "video_share",
        "image_share",
        "carousel_share",
        "ugc_share",
        "polished_share",
        "hooks",
        "observed_offers",
        "landing_pages",
        "ad_links",
    }
)
TRENDS_KEYS = frozenset({"demand_score"})
COMPETITION_KEYS = frozenset({"structure_score"})
EVIDENCE_KEYS = frozenset(
    {
        "source_ref",
        "source_type",
        "url",
        "stable_ref",
        "observed_at",
        "captured_at",
        "pack_id",
        "content_sha256",
        "excerpt",
    }
)
FACT_KEYS = frozenset({"value", "status", "source_refs"})


@dataclass(frozen=True, slots=True)
class Fact:
    """A known or explicitly contradicted evidence-backed value."""

    value: Any
    status: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Evidence:
    source_ref: str
    source_type: str
    url: str | None
    stable_ref: str | None
    observed_at: str
    captured_at: str
    pack_id: str | None
    content_sha256: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class FxRate:
    currency: str
    eur_per_unit: float
    source_ref: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class Links:
    program_url: Fact | None
    offer_url: Fact | None
    landing_url: Fact | None
    ad_links: Fact | None


@dataclass(frozen=True, slots=True)
class Economics:
    retail_price: Fact | None
    aov: Fact | None
    affiliate_commission_pct: Fact | None
    cpa_payout: Fact | None
    expected_payout_per_order: Fact | None
    recurring_commission: Fact | None
    cookie_days: Fact | None
    wholesale_cost: Fact | None
    gross_spread: Fact | None
    monthly_fees: Fact | None
    minimum_order: Fact | None
    refund_rate: Fact | None
    refund_policy: Fact | None
    payment_fees: Fact | None
    fulfilment_cost: Fact | None
    shipping_cost: Fact | None
    expected_return_cost: Fact | None
    other_variable_cost: Fact | None
    expected_cpa: Fact | None
    currency: Fact | None


@dataclass(frozen=True, slots=True)
class Permissions:
    meta_ads: Fact | None
    tiktok_ads: Fact | None
    google_ads: Fact | None
    custom_creatives: Fact | None
    brand_name_use: Fact | None
    brand_assets: Fact | None
    brand_bidding: Fact | None
    map_policy: Fact | None
    us_traffic: Fact | None


@dataclass(frozen=True, slots=True)
class USReadiness:
    us_warehouse: Fact | None
    us_fulfilment: Fact | None
    delivery_min_days: Fact | None
    delivery_max_days: Fact | None
    us_returns_address: Fact | None
    shipping_cost: Fact | None
    checkout_in_usd: Fact | None


@dataclass(frozen=True, slots=True)
class Landing:
    functional_score: Fact | None
    quality_score: Fact | None


@dataclass(frozen=True, slots=True)
class CreativeScores:
    visual_transformation: Fact | None
    hookability: Fact | None
    problem_awareness: Fact | None
    demo_potential: Fact | None
    ugc_potential: Fact | None
    storytelling_potential: Fact | None
    ai_creative_potential: Fact | None
    before_after_potential: Fact | None
    angle_breadth: Fact | None
    creative_gap: Fact | None


@dataclass(frozen=True, slots=True)
class MetaStructure:
    advertiser_count: Fact | None
    active_ads: Fact | None
    oldest_ad_days: Fact | None
    ads_older_than_30d: Fact | None
    creative_concept_count: Fact | None
    variants_per_concept: Fact | None
    video_share: Fact | None
    image_share: Fact | None
    carousel_share: Fact | None
    ugc_share: Fact | None
    polished_share: Fact | None
    hooks: Fact | None
    observed_offers: Fact | None
    landing_pages: Fact | None
    ad_links: Fact | None


@dataclass(frozen=True, slots=True)
class Trends:
    demand_score: Fact | None


@dataclass(frozen=True, slots=True)
class Competition:
    structure_score: Fact | None


@dataclass(frozen=True, slots=True)
class Offer:
    offer_id: str
    name: str
    brand: str
    market_id: str
    research_keyword: str
    enabled: bool
    monetization_model: str
    product_type: str
    links: Links
    economics: Economics
    permissions: Permissions
    us_readiness: USReadiness
    landing: Landing
    creative_scores: CreativeScores
    meta_structure: MetaStructure
    trends: Trends
    competition: Competition
    evidence: tuple[Evidence, ...]

    @property
    def evidence_registry(self) -> tuple[Evidence, ...]:
        return self.evidence


@dataclass(frozen=True, slots=True)
class ArbitrageConfig:
    schema_version: int
    base_currency: str
    fx_rates: tuple[FxRate, ...]
    offers: tuple[Offer, ...]
