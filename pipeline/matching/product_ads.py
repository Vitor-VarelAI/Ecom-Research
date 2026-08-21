"""Deterministic product↔ad matching over canonical candidates.

This module is the single home of the match classification that the Apify
adapter used to enforce as its second (high-intent) gate.  The adapter
limits the source (gate 1: broad phrase/token relevance across copy +
advertiser text), drops inactive ads, normalizes and deduplicates every
active candidate and stops there — it never decides whether a candidate
belongs to the product.  Every canonical candidate is classified here, and
the matched-only projections (``top_ads``, ``ad_ids``, ``advertisers``,
``copy_examples``, ``saturation``, ``global_market_proven``) are derived
exclusively from ``matched`` candidates so landing, score, notifications
and the weekly/monthly pulses never consume ambiguous evidence.

Classification contract (v1):

* ``matched``      — product head proven in strong commercial-intent fields
                     AND every remaining meaningful keyword token (including
                     non-geographic modifiers such as ``personalizada`` or
                     ``aromáticas``) proven in strong fields AND every
                     geographic qualifier proven in the stricter geo-proof
                     fields.
* ``needs_review`` — product head proven in strong fields but at least one
                     meaningful token is absent from strong proof or appears
                     only in weak copy/context (body, advertiser, caption),
                     or a geographic qualifier is missing/weak-only.
* ``rejected``     — product head absent from strong commercial fields
                     (a body-only mention is the classic case).

Strong (commercial-intent) fields: title, headline, link_description, ad
creative link titles/descriptions and the sanitized landing/link URL
(scheme/hostname/path — never raw query, fragment or userinfo).
Geo-proof fields are stricter: title, headline, ad creative link titles and
the sanitized URL path/domain — generic shipping/context text never proves
a geographic market.

Everything here is deterministic and JSON-safe.  Candidates carry a small
canonical ``match_evidence`` shape (folded strong/geo/weak text built by the
adapter from the normalized ad — never raw actor payloads) and the verdict
records categorical confidence, ordered reason codes, the head token and
the matcher version.  ``classify_ads_data`` deep-copies its input and never
mutates it.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

from pipeline import suggestions

MATCHER_VERSION = "1.0.0"

# Version of the canonical candidate/matching result shape (candidates +
# verdicts + counts + projections).  Persisted packs carry it so the
# weekly/monthly pulses can fail closed before re-scoring or comparing
# cohorts across a pack produced under an older, incompatible shape.
MATCHING_SCHEMA_VERSION = 1

# Upper bound on persisted canonical candidates per keyword.  The actor
# returns at most 100 samples, so this invariant holds by construction; the
# cap is defensive and documented for consumers.
CANDIDATE_CAP = 100

# Geographic country/market qualifiers (accent/case folded) that keep the
# stricter geo-proof fields: they must be proven in product-identity
# fields only (title/headline, ad creative link titles and the sanitized
# landing/link URL path or domain).  The set is deliberately the same
# explicit core markets (PT/ES/FR/BR) the adapter always enforced.
GEOGRAPHIC_QUALIFIERS = frozenset({
    "portugal",
    "espanha",
    "franca",
    "brasil",
})

# Ordered reason codes emitted by the classifier (deterministic).
REASON_HEAD_PROVEN = "head_proven"
REASON_HEAD_UNPROVEN = "head_unproven"
REASON_GEO_UNPROVEN = "geo_unproven"
REASON_TOKEN_UNPROVEN = "token_unproven"
REASON_TOKEN_WEAK_ONLY = "token_weak_only"
REASON_ALL_TOKENS_PROVEN = "all_tokens_proven"

# Categorical confidence describing the strength of the match evidence.
CONFIDENCE_MATCHED = "high"
CONFIDENCE_NEEDS_REVIEW = "medium"
CONFIDENCE_REJECTED = "low"

MATCHED = "matched"
NEEDS_REVIEW = "needs_review"
REJECTED = "rejected"
LABELS = (MATCHED, NEEDS_REVIEW, REJECTED)


def _fold(value: Any) -> str:
    """Accent-fold a value and reduce it to deterministic searchable text."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[\w]+", _fold(value), flags=re.UNICODE)


def _evidence_text(candidate: dict[str, Any], key: str) -> str:
    evidence = candidate.get("match_evidence")
    if isinstance(evidence, dict):
        value = evidence.get(key)
        if isinstance(value, str):
            return value
    return ""


def _meaningful_tokens(keyword: str) -> list[str]:
    """Deduped, order-preserved query tokens that must be proven in strong
    fields: every token that is neither a PT/EN stopword nor a geographic
    qualifier.  The first entry is the product head (same selection rule as
    ``suggestions.product_head_token`` for the non-degenerate case)."""
    tokens = list(dict.fromkeys(_tokens(keyword)))
    return [
        token
        for token in tokens
        if token not in GEOGRAPHIC_QUALIFIERS and not suggestions.is_stopword(token)
    ]


def _proven(token: str, corpus: list[str]) -> bool:
    return any(suggestions.tokens_equivalent(token, item) for item in corpus)


def classify_candidate(keyword: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Deterministic match verdict for one canonical candidate.

    Returns a JSON-safe ``match`` dict with ``label``, categorical
    ``confidence``, ordered ``reasons``, ``matcher_version``, the selected
    ``head_token`` and the bounded audit lists (``geo_tokens``,
    ``missing_tokens``, ``weak_only_tokens``).  The candidate dict itself is
    never mutated.
    """
    query_tokens = list(dict.fromkeys(_tokens(keyword)))
    meaningful = _meaningful_tokens(keyword)
    head = meaningful[0] if meaningful else suggestions.product_head_token(keyword)
    geo_tokens = [token for token in query_tokens if token in GEOGRAPHIC_QUALIFIERS]
    other_tokens = [token for token in meaningful if token != head]

    strong_tokens = _tokens(_evidence_text(candidate, "commercial"))
    geo_tokens_proof = _tokens(_evidence_text(candidate, "geo"))
    weak_tokens = _tokens(_evidence_text(candidate, "weak"))

    base = {
        "matcher_version": MATCHER_VERSION,
        "head_token": head,
        "geo_tokens": geo_tokens,
        "missing_tokens": [],
        "weak_only_tokens": [],
    }

    # Product head: must be proven in a strong commercial-intent field.
    if head is None or not _proven(head, strong_tokens):
        return {
            **base,
            "label": REJECTED,
            "confidence": CONFIDENCE_REJECTED,
            "reasons": [REASON_HEAD_UNPROVEN],
        }

    reasons: list[str] = [REASON_HEAD_PROVEN]
    geo_missing: list[str] = []
    other_missing: list[str] = []
    weak_only: list[str] = []

    # Geographic qualifiers keep the stricter geo-proof fields.
    for token in geo_tokens:
        if not _proven(token, geo_tokens_proof):
            geo_missing.append(token)
    if geo_missing:
        reasons.append(REASON_GEO_UNPROVEN)

    # Every remaining meaningful token needs strong proof; weak-only
    # (body/copy/context) or absent is never enough anymore — this
    # deliberately retires the old non-geographic-modifier exemption.
    for token in other_tokens:
        if _proven(token, strong_tokens):
            continue
        if _proven(token, weak_tokens):
            weak_only.append(token)
        else:
            other_missing.append(token)

    if other_missing:
        reasons.append(REASON_TOKEN_UNPROVEN)
    if weak_only:
        reasons.append(REASON_TOKEN_WEAK_ONLY)

    missing = geo_missing + other_missing
    if missing or weak_only:
        return {
            **base,
            "label": NEEDS_REVIEW,
            "confidence": CONFIDENCE_NEEDS_REVIEW,
            "reasons": reasons,
            "missing_tokens": missing,
            "weak_only_tokens": weak_only,
        }

    return {
        **base,
        "label": MATCHED,
        "confidence": CONFIDENCE_MATCHED,
        "reasons": [REASON_HEAD_PROVEN, REASON_ALL_TOKENS_PROVEN],
    }


def classify_candidates(keyword: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a deep copy of ``candidates`` with a ``match`` verdict attached
    to each (deterministic, JSON-safe).  The input list and dicts are never
    mutated."""
    classified: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            classified.append(copy.deepcopy(candidate))
            continue
        clean = copy.deepcopy(candidate)
        clean["match"] = classify_candidate(keyword, clean)
        classified.append(clean)
    return classified[:CANDIDATE_CAP]


def matched_candidates(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for candidate in classified if candidate.get("match", {}).get("label") == MATCHED]


def _matched_projections(classified: list[dict[str, Any]]) -> dict[str, Any]:
    """Matched-only projections with the historical shapes preserved for
    downstream consumers (landing, score, notifications, pulse)."""
    matched = matched_candidates(classified)

    top_ads = sorted(
        matched,
        key=lambda item: (-item["days_active"], item["ad_archive_id"], item["advertiser"]),
    )[:3]

    advertiser_groups: dict[str, dict[str, Any]] = {}
    for ad in matched:
        group_key = ad["page_id"] or _fold(ad["advertiser"])
        group = advertiser_groups.setdefault(
            group_key,
            {
                "page_id": ad["page_id"],
                "page_name": ad["advertiser"],
                "active_ads": 0,
                "oldest_ad_days": 0,
            },
        )
        group["active_ads"] += 1
        group["oldest_ad_days"] = max(group["oldest_ad_days"], ad["days_active"])

    advertisers = sorted(
        advertiser_groups.values(),
        key=lambda item: (-item["active_ads"], _fold(item["page_name"]), item.get("page_id", "")),
    )[:5]

    copy_examples: list[str] = []
    seen_copies: set[str] = set()
    for ad in top_ads + matched:
        copy = ad.get("copy", "").strip()
        folded = _fold(copy)
        if copy and folded not in seen_copies:
            seen_copies.add(folded)
            copy_examples.append(copy)
        if len(copy_examples) == 5:
            break

    matched_count = len(matched)
    return {
        "total_ads": matched_count,
        "ad_ids": [ad["ad_archive_id"] for ad in matched if ad["ad_archive_id"]],
        "top_ads": top_ads,
        "advertisers": advertisers,
        "copy_examples": copy_examples,
        "global_market_proven": any(item["active_ads"] >= 5 for item in advertiser_groups.values()),
        "saturation": "alta" if matched_count > 50 else "média" if matched_count >= 10 else "baixa",
    }


def _classification_counts(classified: list[dict[str, Any]]) -> dict[str, int]:
    counts = {label: 0 for label in LABELS}
    for candidate in classified:
        label = candidate.get("match", {}).get("label")
        if label in counts:
            counts[label] += 1
    return {
        "matched_count": counts[MATCHED],
        "needs_review_count": counts[NEEDS_REVIEW],
        "rejected_count": counts[REJECTED],
    }


def classify_ads_data(keyword: str, ads_data: dict[str, Any]) -> dict[str, Any]:
    """Classify every canonical candidate and derive the matched-only
    projections and full classification counts.

    Returns a deep copy of ``ads_data`` with ``candidates`` each carrying a
    ``match`` verdict, plus ``matcher_version``, ``total_ads`` (the matched
    count — decision semantics), ``matched_count``/``needs_review_count``/
    ``rejected_count`` and the matched-only ``ad_ids``/``top_ads``/
    ``advertisers``/``copy_examples``/``saturation``/
    ``global_market_proven``.  The input is never mutated.

    Error payloads and dicts without a ``candidates`` list (already-finalized
    data, test fixtures, legacy shapes) pass through as an unchanged deep
    copy — the adapter result is the single source of canonical candidates.
    """
    if not isinstance(ads_data, dict) or "error" in ads_data:
        return copy.deepcopy(ads_data)
    candidates = ads_data.get("candidates")
    if not isinstance(candidates, list):
        return copy.deepcopy(ads_data)
    classified = classify_candidates(keyword, candidates)
    result = copy.deepcopy(ads_data)
    result["candidates"] = classified
    result["matcher_version"] = MATCHER_VERSION
    result.update(_classification_counts(classified))
    result.update(_matched_projections(classified))
    return result
