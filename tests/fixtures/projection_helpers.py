"""Shared pack fixtures for the SQLite projection tests.

Builds deterministic current-classified and legacy pack dicts matching the
shapes the pipeline persists (``pipeline/output/pack.py`` + the classified
candidates from ``classify_ads_data``), so projection tests exercise real
pack structure without touching ``data/`` or ``packs/``.
"""

from __future__ import annotations

from typing import Any

from pipeline.matching.product_ads import MATCHER_VERSION, MATCHING_SCHEMA_VERSION

RUN_ID = "a2ab7c70195aa762987a3e2e07ce9b49dcd15c09a27204f494a5a9158e5aa739"
PRODUCT_ID = "e942e8bf7f30"
KEYWORD = "mochila personalizada"


def candidate(
    ad_id: str = "a1",
    *,
    advertiser: str = "Loja",
    page_id: str = "p1",
    days_active: int = 30,
    landing_url: str = "https://shop.example/produto?utm_source=x",
    copy: str = "Mochila personalizada premium",
    match_label: str = "matched",
    evidence_commercial: str = "mochilas personalizadas premium",
) -> dict[str, Any]:
    """One canonical candidate with a match verdict (classified shape)."""
    return {
        "ad_archive_id": ad_id,
        "advertiser": advertiser,
        "page_id": page_id,
        "days_active": days_active,
        "reported_active_time": f"{days_active} days",
        "link": f"https://www.facebook.com/ads/library/?id={ad_id}",
        "landing_url": landing_url,
        "copy": copy,
        "publisher_platforms": ["facebook"],
        "countries": ["PT"],
        "match_evidence": {
            "commercial": evidence_commercial,
            "geo": "mochilas personalizadas",
            "weak": "mochila personalizada premium envio rapido",
        },
        "match": {
            "label": match_label,
            "confidence": "high" if match_label == "matched" else "medium",
            "reasons": ["head_proven", "all_tokens_proven"],
            "matcher_version": MATCHER_VERSION,
            "head_token": "mochila",
            "geo_tokens": [],
            "missing_tokens": [],
            "weak_only_tokens": [],
        },
    }


def classified_ads_data(candidates: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    """A classified ``facebook_ads`` payload with counts/projections."""
    candidates = candidates if candidates is not None else [candidate()]
    matched = sum(1 for c in candidates if c["match"]["label"] == "matched")
    needs_review = sum(1 for c in candidates if c["match"]["label"] == "needs_review")
    rejected = sum(1 for c in candidates if c["match"]["label"] == "rejected")
    base: dict[str, Any] = {
        "source": "Facebook Ad Library via Apify",
        "query": KEYWORD,
        "evidence_scope": "global",
        "query_country": "ALL",
        "sample_limit": 100,
        "sample_capped": False,
        "raw_ads_count": len(candidates),
        "filtered_out_count": 0,
        "filtered_inactive_count": 0,
        "filtered_irrelevant_count": 0,
        "candidate_count": len(candidates),
        "duplicates_removed": 0,
        "relevance_policy": "gate1 + matcher",
        "matching_schema_version": MATCHING_SCHEMA_VERSION,
        "matcher_version": MATCHER_VERSION,
        "matched_count": matched,
        "needs_review_count": needs_review,
        "rejected_count": rejected,
        "total_ads": matched,
        "ad_ids": [c["ad_archive_id"] for c in candidates if c["match"]["label"] == "matched"],
        "top_ads": [c for c in candidates if c["match"]["label"] == "matched"][:3],
        "advertisers": [],
        "copy_examples": [],
        "saturation": "baixa",
        "global_market_proven": False,
        "candidates": candidates,
    }
    base.update(overrides)
    return base


def current_pack(**overrides: Any) -> dict[str, Any]:
    """A complete current classified pack (all required current-pack shape
    markers present).  Overrides win, so tests can break one marker at a time."""
    pack: dict[str, Any] = {
        "keyword": KEYWORD,
        "date": "2026-08-16",
        "score": {"score": 55.0, "label": "OBSERVAR", "breakdown": {"trend": 50}},
        "trends": {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []},
        "keyword_planner": {"volume": 1000, "cpc": 1.0, "related_keywords": []},
        "facebook_ads": classified_ads_data(),
        "seo": {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"},
        "feedback": None,
        "landing_price": {"error": "sem dados"},
        "suggested_keywords": [],
        "product_id": PRODUCT_ID,
        "product_name": "mochila personalizada",
        "research_keyword": KEYWORD,
        "run_id": RUN_ID,
        "mode": "sunday",
        "generated_at": "2026-08-16T07:00:59+00:00",
        "completed_at": "2026-08-16T07:00:59+00:00",
        "scoring_version": "1.0.0",
        "scoring_config": {"weights": {"trend": 0.3}, "thresholds": {"AVANÇAR": 70}, "labels": ["AVANÇAR", "OBSERVAR", "DESCARTAR"]},
        "matcher_version": MATCHER_VERSION,
        "matching_schema_version": MATCHING_SCHEMA_VERSION,
        "source_states": {"apify_ads": {"status": "valid", "origin": "fresh"}},
        "source_policies": {"apify_ads": {"retries": 0}},
        "artifact_complete": True,
    }
    pack.update(overrides)
    return pack


def legacy_pack(**overrides: Any) -> dict[str, Any]:
    """A run-scoped pack without any matching-schema markers (all real
    packs currently in ``data/`` predate classification)."""
    pack = current_pack()
    del pack["product_id"]
    del pack["product_name"]
    del pack["research_keyword"]
    del pack["matching_schema_version"]
    pack["facebook_ads"] = {
        "source": "Facebook Ad Library via Apify",
        "query": KEYWORD,
        "total_ads": 1,
        "ad_ids": ["a1"],
        "top_ads": [candidate()],
        "advertisers": [],
        "copy_examples": [],
        "saturation": "baixa",
        "raw_ads_count": 1,
        "sample_limit": 100,
        "sample_capped": False,
    }
    pack.update(overrides)
    return pack


def not_a_pack() -> dict[str, Any]:
    """Valid JSON that is not a research pack at all (like data/quota.json)."""
    return {"month": "2026-08", "calls": 21, "limit": 230, "firecrawl_credits": 179}
