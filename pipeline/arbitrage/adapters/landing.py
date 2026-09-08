"""Read-only technical landing evidence adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pipeline.arbitrage.evidence import KNOWN, ResolvedSignal, is_safe_public_url


def _matched_landing_urls(pack: Mapping[str, Any]) -> tuple[str, ...]:
    ads = pack.get("facebook_ads")
    if not isinstance(ads, Mapping):
        return ()
    candidates = ads.get("candidates")
    if not isinstance(candidates, (list, tuple)):
        candidates = ads.get("matched_candidates")
    if not isinstance(candidates, (list, tuple)):
        return ()
    result: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        match = candidate.get("match")
        matched = (
            isinstance(match, Mapping) and match.get("label") == "matched"
        ) or candidate.get("matched") is True or candidate.get("status") == "matched"
        if not matched:
            continue
        safe = is_safe_public_url(candidate.get("landing_url"))
        if safe is not None:
            result.add(safe)
    return tuple(sorted(result))


def _successful_price(payload: Any) -> bool:
    if not isinstance(payload, Mapping) or "error" in payload:
        return False
    price_range = payload.get("price_range")
    if not isinstance(price_range, Mapping):
        return False
    minimum = price_range.get("min")
    maximum = price_range.get("max")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (minimum, maximum)):
        return False
    return (
        math.isfinite(float(minimum))
        and math.isfinite(float(maximum))
        and float(minimum) >= 0
        and float(maximum) >= float(minimum)
    )


def derive_landing(
    pack: Mapping[str, Any],
    *,
    source_ref: str = "research_pack",
    observed_at: str | None = None,
) -> Mapping[str, ResolvedSignal]:
    """Return functional evidence only; quality is intentionally untouched."""

    if not _successful_price(pack.get("landing_price")):
        return MappingProxyType({})
    if not _matched_landing_urls(pack):
        return MappingProxyType({})
    return MappingProxyType(
        {
            "functional_score": ResolvedSignal(
                10.0,
                KNOWN,
                (source_ref,),
                ("research_pack",),
                () if observed_at is None else (observed_at,),
                "successful price extraction from a matched safe landing URL",
            )
        }
    )


__all__ = ["derive_landing"]
