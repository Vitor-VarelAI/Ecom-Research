"""Deterministic readiness buckets for Offer First candidates."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pipeline.arbitrage.gates import FAIL, GateReport
from pipeline.arbitrage.scoring import ScoreCard


class Bucket(str, Enum):
    TEST_NOW = "TEST_NOW"
    RESEARCH_FURTHER = "RESEARCH_FURTHER"
    WATCHLIST = "WATCHLIST"
    REJECT = "REJECT"


TEST_NOW = Bucket.TEST_NOW.value
RESEARCH_FURTHER = Bucket.RESEARCH_FURTHER.value
WATCHLIST = Bucket.WATCHLIST.value
REJECT = Bucket.REJECT.value


def classify_bucket(
    scorecard: ScoreCard,
    gates: GateReport | None = None,
) -> str:
    """Apply gate precedence before score thresholds."""

    report = gates or scorecard.gates
    if report.any_fail:
        return REJECT
    contribution = getattr(getattr(scorecard, "economics", None), "contribution_eur", None)
    if contribution is not None and contribution <= 0:
        return REJECT
    if (
        report.any_unknown
        or report.any_contradicted
        or scorecard.score is None
        or scorecard.score_48h is None
        or scorecard.incomplete
    ):
        return RESEARCH_FURTHER
    if scorecard.score >= 70 and scorecard.score_48h >= 70:
        return TEST_NOW
    return WATCHLIST


bucket_for = classify_bucket
choose_bucket = classify_bucket

__all__ = [
    "Bucket",
    "REJECT",
    "RESEARCH_FURTHER",
    "TEST_NOW",
    "WATCHLIST",
    "bucket_for",
    "choose_bucket",
    "classify_bucket",
]
