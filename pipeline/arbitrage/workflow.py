"""Local, read-only Offer + Creative Arbitrage workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pipeline.arbitrage.config import load_config
from pipeline.arbitrage.decision import decide_offer
from pipeline.arbitrage.evidence import KNOWN, build_offer_evidence
from pipeline.arbitrage.heuristics import signal_state, signal_value
from pipeline.arbitrage.models import ArbitrageConfig, Offer

SHORTLIST_BUCKETS = (
    "TEST_NOW",
    "RESEARCH_FURTHER",
    "WATCHLIST",
    "REJECT",
)


@dataclass(frozen=True, slots=True)
class ShortlistItem:
    """One public offer decision with only known evidence values exposed."""

    offer_id: str
    name: str
    brand: str
    market_id: str
    monetization_model: str
    readiness_status: str
    bucket: str
    commercial_score: int | None
    score_48h: int | None
    contribution_eur: Decimal | None
    reasons: tuple[str, ...]
    program_url: str | None
    landing_url: str | None
    ad_links: tuple[str, ...] | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.ad_links is not None:
            object.__setattr__(self, "ad_links", tuple(self.ad_links))

    def as_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "name": self.name,
            "brand": self.brand,
            "market_id": self.market_id,
            "monetization_model": self.monetization_model,
            "readiness_status": self.readiness_status,
            "bucket": self.bucket,
            "commercial_score": self.commercial_score,
            "score_48h": self.score_48h,
            "contribution_eur": _json_value(self.contribution_eur),
            "reasons": list(self.reasons),
            "program_url": self.program_url,
            "landing_url": self.landing_url,
            "ad_links": _json_value(self.ad_links),
        }


@dataclass(frozen=True, slots=True)
class OfferShortlist:
    """Deterministic four-section shortlist."""

    sections: Mapping[str, tuple[ShortlistItem, ...]]

    def __post_init__(self) -> None:
        normalized = {
            bucket: tuple(self.sections.get(bucket, ()))
            for bucket in SHORTLIST_BUCKETS
        }
        object.__setattr__(self, "sections", MappingProxyType(normalized))

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            bucket: [item.as_dict() for item in self.sections[bucket]]
            for bucket in SHORTLIST_BUCKETS
        }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def shortlist_to_json(shortlist: OfferShortlist) -> str:
    """Serialize a shortlist without converting Decimal values to floats."""

    return json.dumps(
        shortlist.as_dict(),
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
    )


def _known_signal_value(group: Any, name: str) -> Any:
    signal = getattr(group, name, None)
    if signal_state(signal) != KNOWN:
        return None
    return signal_value(signal)


def _known_ad_links(evidence: Any) -> tuple[str, ...] | None:
    value = _known_signal_value(getattr(evidence, "links", None), "ad_links")
    if value is None:
        value = _known_signal_value(
            getattr(evidence, "meta_structure", None),
            "ad_links",
        )
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _shortlist_item(offer: Offer, evidence: Any, decision: Any) -> ShortlistItem:
    links = getattr(evidence, "links", None)
    scorecard = decision.scorecard
    return ShortlistItem(
        offer_id=offer.offer_id,
        name=offer.name,
        brand=offer.brand,
        market_id=offer.market_id,
        monetization_model=offer.monetization_model,
        readiness_status=decision.readiness_status,
        bucket=decision.bucket,
        commercial_score=decision.commercial_score,
        score_48h=decision.score_48h,
        contribution_eur=scorecard.economics.contribution_eur,
        reasons=decision.reasons,
        program_url=_known_signal_value(links, "program_url"),
        landing_url=_known_signal_value(links, "landing_url"),
        ad_links=_known_ad_links(evidence),
    )


def build_offer_shortlist(
    config: ArbitrageConfig,
    as_of: date,
    data_dir: str | Path,
) -> OfferShortlist:
    """Resolve, score, and bucket every configured offer without network I/O."""

    items: dict[str, list[ShortlistItem]] = {
        bucket: [] for bucket in SHORTLIST_BUCKETS
    }
    offers = sorted(
        config.offers,
        key=lambda offer: (offer.offer_id, offer.name, offer.market_id),
    )
    for offer in offers:
        evidence = build_offer_evidence(offer, config, as_of, data_dir)
        decision = decide_offer(offer, evidence)
        item = _shortlist_item(offer, evidence, decision)
        items[item.bucket].append(item)

    sections = {
        bucket: tuple(
            sorted(
                items[bucket],
                key=lambda item: (item.offer_id, item.name, item.market_id),
            )
        )
        for bucket in SHORTLIST_BUCKETS
    }
    return OfferShortlist(sections)


def load_offer_shortlist(
    config_path: str | Path,
    data_dir: str | Path,
    *,
    as_of: date | None = None,
) -> OfferShortlist:
    """Load the offer config, then run the local evidence-to-shortlist flow."""

    config = load_config(config_path)
    return build_offer_shortlist(config, as_of or date.today(), data_dir)


__all__ = [
    "OfferShortlist",
    "SHORTLIST_BUCKETS",
    "ShortlistItem",
    "build_offer_shortlist",
    "load_offer_shortlist",
    "shortlist_to_json",
]
