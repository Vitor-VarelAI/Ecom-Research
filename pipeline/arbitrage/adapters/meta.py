"""Read-only Meta evidence adapter for historical research packs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pipeline.arbitrage.evidence import KNOWN, ResolvedSignal, is_safe_public_url

_FIELDS = (
    "advertiser_count",
    "active_ads",
    "oldest_ad_days",
    "ads_older_than_30d",
    "ad_links",
    "landing_pages",
)


def _matched_candidates(pack: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    ads = pack.get("facebook_ads")
    if not isinstance(ads, Mapping):
        return ()
    raw_candidates = ads.get("candidates")
    if not isinstance(raw_candidates, (list, tuple)):
        raw_candidates = ads.get("matched_candidates")
    if not isinstance(raw_candidates, (list, tuple)):
        return ()
    matched: list[Mapping[str, Any]] = []
    for candidate in raw_candidates:
        if not isinstance(candidate, Mapping):
            continue
        match = candidate.get("match")
        if (
            isinstance(match, Mapping)
            and match.get("label") == "matched"
        ) or candidate.get("matched") is True or candidate.get("status") == "matched":
            matched.append(candidate)
    return tuple(matched)


def _signal(
    value: Any,
    *,
    source_ref: str,
    source_type: str,
    observed_at: str | None,
) -> ResolvedSignal:
    return ResolvedSignal(
        value,
        KNOWN,
        (source_ref,),
        (source_type,),
        () if observed_at is None else (observed_at,),
    )


def _finite_nonnegative(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def derive_meta(
    pack: Mapping[str, Any],
    *,
    source_ref: str = "research_pack",
    observed_at: str | None = None,
) -> Mapping[str, ResolvedSignal]:
    """Derive only the six explicitly permitted Meta structure fields."""

    candidates = _matched_candidates(pack)
    if not candidates:
        return MappingProxyType({})

    advertiser_names = {
        candidate["advertiser"].strip()
        for candidate in candidates
        if isinstance(candidate.get("advertiser"), str) and candidate["advertiser"].strip()
    }
    page_ids = {
        candidate["page_id"].strip()
        for candidate in candidates
        if isinstance(candidate.get("page_id"), str) and candidate["page_id"].strip()
    }
    advertisers = {
        str(candidate[key]).strip()
        for candidate in candidates
        for key in ("page_id", "advertiser")
        if isinstance(candidate.get(key), str) and candidate[key].strip()
    }
    advertiser_count = len(advertiser_names or page_ids or advertisers)

    ages = [
        age
        for candidate in candidates
        if (age := _finite_nonnegative(candidate.get("days_active"))) is not None
    ]
    links = sorted(
        {
            safe
            for candidate in candidates
            if (safe := is_safe_public_url(candidate.get("link"), allow_query=True)) is not None
        }
    )
    landing_pages = sorted(
        {
            safe
            for candidate in candidates
            if (safe := is_safe_public_url(candidate.get("landing_url"))) is not None
        }
    )

    result: dict[str, ResolvedSignal] = {
        "advertiser_count": _signal(
            advertiser_count,
            source_ref=source_ref,
            source_type="research_pack",
            observed_at=observed_at,
        ),
        "active_ads": _signal(
            len(candidates),
            source_ref=source_ref,
            source_type="research_pack",
            observed_at=observed_at,
        ),
        "ad_links": _signal(
            tuple(links),
            source_ref=source_ref,
            source_type="research_pack",
            observed_at=observed_at,
        ),
        "landing_pages": _signal(
            tuple(landing_pages),
            source_ref=source_ref,
            source_type="research_pack",
            observed_at=observed_at,
        ),
    }
    if ages:
        result["oldest_ad_days"] = _signal(
            max(ages),
            source_ref=source_ref,
            source_type="research_pack",
            observed_at=observed_at,
        )
        result["ads_older_than_30d"] = _signal(
            sum(age > 30 for age in ages),
            source_ref=source_ref,
            source_type="research_pack",
            observed_at=observed_at,
        )
    return MappingProxyType({key: result[key] for key in _FIELDS if key in result})


__all__ = ["derive_meta"]
