"""
Additive provenance metadata: source policies and per-source state.

This module never changes adapter behaviour.  It only *describes* sources so
packs and run manifests can persist, in a safe non-secret way, what each
source is allowed to do (TTL, quota, timeout, retries, fallback) and what
state the evidence in a given run actually had.

Status vocabulary:

* ``valid``  — fresh evidence was produced (a valid zero-ad Apify result is
  valid: it is a real, bounded read of the source).
* ``stale``  — evidence was reused from a previous pack (Wednesday/Monthly).
* ``missing`` — not applicable or not attempted for this run/mode.
* ``failed`` — an attempt was made (request or parse) and it errored.

Policies contain no credentials and no token-bearing URLs.
"""

from __future__ import annotations

from typing import Any

from pipeline.adapters import trends

SOURCE_POLICIES: dict[str, dict[str, Any]] = {
    "apify_ads": {
        "timeout": "(10, 135)",
        "sample_limit": 100,
        "retries": 0,
        "fallback": "DADOS INSUFICIENTES; pack anterior preservado no pulse/mensal",
        "cost": "1 invocação do actor por keyword",
    },
    "trends": {
        "ttl_days": 30,
        "timeout_s": trends.REQUEST_TIMEOUT,
        "retries": 0,
        "quota_per_call": 1,
        "quota_limit": 230,
        "fallback": "cache 30 dias; payloads de erro nunca são cacheados",
    },
    "serp_seo": {
        "ttl_days": 30,
        "timeout_s": 15,
        "retries": 0,
        "quota_per_call": 1,
        "quota_limit": 230,
        "fallback": "cache 30 dias",
    },
    "keyword_csv": {
        "retries": 0,
        "manual": True,
        "fallback": "gracioso — score perde o sinal de volume",
    },
    "landing_price": {
        "max_fetches": 3,
        "timeout_s": 10,
        "retries": 0,
        "firecrawl_fallback": True,
        "firecrawl_credit_per_page": 1,
        "firecrawl_limit": 1000,
        "firecrawl_pause_s": 6,
        "fallback": "ignora landing não legível; nunca bloqueia o run",
    },
}

SOURCE_ORDER: list[str] = [
    "apify_ads",
    "trends",
    "serp_seo",
    "keyword_csv",
    "landing_price",
    "suggestions",
    "ad_cohort",
]


def source_state(
    *,
    origin: str,
    reason: str,
    timestamp: str | None = None,
    force_status: str | None = None,
) -> dict[str, Any]:
    """
    Build one source-state entry.

    ``origin`` is one of ``fresh`` / ``reused`` / ``not_attempted`` / ``error``.
    ``force_status`` overrides the automatic status (used for a missing CSV
    file, which is ``missing`` rather than ``failed``).
    """
    if force_status is not None:
        status = force_status
    elif origin == "reused":
        status = "stale"
    elif origin == "not_attempted":
        status = "missing"
    elif origin == "error":
        status = "failed"
    else:
        status = "valid"
    entry: dict[str, Any] = {"status": status, "origin": origin, "reason": reason}
    if timestamp:
        entry["generated_at"] = timestamp
    return entry


def snapshot() -> dict[str, Any]:
    """Deep-copied, credential-free policy snapshot for pack/run metadata."""
    return {name: dict(policy) for name, policy in SOURCE_POLICIES.items()}
