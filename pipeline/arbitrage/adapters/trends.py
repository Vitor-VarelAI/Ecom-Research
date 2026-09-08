"""Read-only Trends/Keyword Planner evidence adapter."""

from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pipeline.arbitrage.evidence import KNOWN, ResolvedSignal


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def demand_score(
    trends: Mapping[str, Any],
    keyword_planner: Mapping[str, Any],
) -> float | None:
    """Return a deliberately conservative 0..10 secondary demand score.

    Volume supplies the main band.  Trend growth can move that band by at
    most two points, and cannot produce a score above five without volume.
    """

    if not isinstance(trends, Mapping) or "error" in trends:
        return None
    slope = _number(trends.get("slope_90d_pct", trends.get("slope_pct")))
    volume = _number(keyword_planner.get("volume")) if isinstance(keyword_planner, Mapping) else None
    if slope is None and volume is None:
        return None

    if volume is None:
        base = 0.0
    elif volume <= 0:
        base = 0.0
    elif volume <= 100:
        base = 1.0
    elif volume <= 500:
        base = 3.0
    elif volume <= 5_000:
        base = 5.0
    elif volume <= 20_000:
        base = 7.0
    else:
        base = 9.0

    adjustment = 0.0
    if slope is not None:
        if slope >= 100:
            adjustment = 2.0
        elif slope >= 50:
            adjustment = 1.0
        elif slope >= 20:
            adjustment = 0.5
        elif slope <= -100:
            adjustment = -2.0
        elif slope <= -50:
            adjustment = -1.0
        elif slope <= -20:
            adjustment = -0.5

    score = max(0.0, min(10.0, base + adjustment))
    if volume is None:
        score = min(5.0, score)
    return round(score, 2)


def derive_trends(
    pack: Mapping[str, Any],
    *,
    source_ref: str = "research_pack",
    observed_at: str | None = None,
) -> Mapping[str, ResolvedSignal]:
    trends = pack.get("trends")
    planner = pack.get("keyword_planner")
    if not isinstance(trends, Mapping) or not isinstance(planner, Mapping):
        return MappingProxyType({})
    score = demand_score(trends, planner)
    if score is None:
        return MappingProxyType({})
    return MappingProxyType(
        {
            "demand_score": ResolvedSignal(
                score,
                KNOWN,
                (source_ref,),
                ("research_pack",),
                () if observed_at is None else (observed_at,),
            )
        }
    )


__all__ = ["demand_score", "derive_trends"]
