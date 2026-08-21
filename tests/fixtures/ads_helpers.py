"""Shared new-format ads_data fixtures (classified-candidate semantics).

The pipeline now persists canonical candidates classified by
``pipeline.matching.product_ads``: the adapter result carries
``candidates`` + ``matching_schema_version``, and ``classify_ads_data``
attaches match verdicts and derives the matched-only projections
(``total_ads``/``ad_ids``/``top_ads``/``advertisers``/``copy_examples``/
``saturation``/``global_market_proven``).  Run-level tests that exercise the
Wednesday/monthly legacy guard need previous packs whose ``facebook_ads``
carries ``matching_schema_version`` — this helper builds exactly that shape.
"""

from __future__ import annotations

from typing import Any

from pipeline.matching.product_ads import MATCHER_VERSION, MATCHING_SCHEMA_VERSION


def candidate_ad(
    ad_id: str = "a1",
    *,
    advertiser: str = "Loja",
    page_id: str = "p1",
    days_active: int = 30,
    landing_url: str = "https://shop.example/produto",
    copy: str = "Mochila personalizada premium",
) -> dict[str, Any]:
    """One canonical candidate with a matched verdict (shape produced by the
    matcher).  Deliberately matched-only: the helper cannot emit an
    inconsistent label/reasons pair."""
    return {
        "ad_archive_id": ad_id,
        "advertiser": advertiser,
        "page_id": page_id,
        "days_active": days_active,
        "reported_active_time": f"{days_active} days",
        "link": f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else "",
        "landing_url": landing_url,
        "copy": copy,
        "publisher_platforms": ["facebook"],
        "countries": ["PT"],
        "match_evidence": {
            "commercial": "mochilas personalizadas premium",
            "geo": "mochilas personalizadas",
            "weak": "mochila personalizada premium envio rapido",
        },
        "match": {
            "label": "matched",
            "confidence": "high",
            "reasons": ["head_proven", "all_tokens_proven"],
            "matcher_version": MATCHER_VERSION,
            "head_token": "mochila",
            "geo_tokens": [],
            "missing_tokens": [],
            "weak_only_tokens": [],
        },
    }


def classified_ads_data(**overrides: Any) -> dict[str, Any]:
    """A new-format, already-classified ads_data result: canonical candidates
    with match verdicts, matched-only projections, full counts and both
    schema/matcher versions.  Keyword/override-friendly for run-level tests."""
    candidate = candidate_ad()
    base: dict[str, Any] = {
        "source": "Facebook Ad Library via Apify",
        "evidence_scope": "global",
        "query_country": "ALL",
        "sample_limit": 100,
        "sample_capped": False,
        "raw_ads_count": 1,
        "filtered_out_count": 0,
        "filtered_inactive_count": 0,
        "filtered_irrelevant_count": 0,
        "candidate_count": 1,
        "duplicates_removed": 0,
        "relevance_policy": "gate1 + matcher",
        "matching_schema_version": MATCHING_SCHEMA_VERSION,
        "matcher_version": MATCHER_VERSION,
        "matched_count": 1,
        "needs_review_count": 0,
        "rejected_count": 0,
        "total_ads": 1,
        "ad_ids": ["a1"],
        "top_ads": [candidate],
        "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
        "copy_examples": ["Mochila personalizada premium"],
        "saturation": "baixa",
        "global_market_proven": False,
        "candidates": [candidate],
    }
    base.update(overrides)
    return base
