"""Public deterministic readiness decisions for Offer + Creative Arbitrage."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pipeline.arbitrage.buckets import (
    REJECT,
    RESEARCH_FURTHER,
    TEST_NOW,
    WATCHLIST as BUCKET_WATCHLIST,
)
from pipeline.arbitrage.evidence import CONTRADICTED, UNKNOWN
from pipeline.arbitrage.gates import GateReport, evaluate_gates
from pipeline.arbitrage.scoring import ScoreCard, score_offer
from pipeline.arbitrage.models import Offer

REJECT_READINESS = "REJECT"
RESEARCH_ONLY = "RESEARCH_ONLY"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
TESTABLE_48H = "TESTABLE_48H"
WATCHLIST = "WATCHLIST"

READINESS_STATUSES = (
    REJECT_READINESS,
    RESEARCH_ONLY,
    INSUFFICIENT_DATA,
    NEEDS_VERIFICATION,
    TESTABLE_48H,
    WATCHLIST,
)


@dataclass(frozen=True, slots=True)
class DecisionResult:
    """Immutable public decision with scores, gates, and ordered reasons."""

    readiness_status: str
    bucket: str
    reasons: tuple[str, ...]
    scorecard: ScoreCard
    gates: GateReport

    def __post_init__(self) -> None:
        if self.readiness_status not in READINESS_STATUSES:
            raise ValueError(f"invalid readiness status: {self.readiness_status}")
        if self.bucket not in {REJECT, RESEARCH_FURTHER, TEST_NOW, BUCKET_WATCHLIST}:
            raise ValueError(f"invalid bucket: {self.bucket}")
        object.__setattr__(self, "reasons", tuple(self.reasons))

    @property
    def commercial_score(self) -> int | None:
        return self.scorecard.score

    @property
    def score_48h(self) -> int | None:
        return self.scorecard.score_48h

    @property
    def contribution_eur(self) -> Decimal | None:
        return self.scorecard.economics.contribution_eur


def _append_unique(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _gate_reasons(
    report: GateReport,
    *,
    statuses: frozenset[str],
    blocking_only: bool = True,
) -> list[str]:
    return [
        result.reason
        for result in report.results.values()
        if result.status in statuses
        and (not blocking_only or result.blocking)
    ]


def _decision_for(
    scorecard: ScoreCard,
    gates: GateReport,
) -> tuple[str, str, tuple[str, ...]]:
    reasons: list[str] = []
    contribution = scorecard.economics.contribution_eur

    blocking_failures = _gate_reasons(gates, statuses=frozenset({"FAIL"}))
    if blocking_failures:
        reasons.extend(blocking_failures)
    if contribution is not None and contribution <= 0:
        _append_unique(reasons, "per-order contribution is not positive")
    if reasons:
        return REJECT_READINESS, REJECT, tuple(reasons)

    access = gates.monetization_access
    if access.status == UNKNOWN:
        return RESEARCH_ONLY, RESEARCH_FURTHER, (access.reason,)

    payout_unresolved = gates.payout_spread.status in {UNKNOWN, CONTRADICTED}
    economics_unresolved = (
        scorecard.economics.state != "KNOWN"
        or scorecard.economics.economics_score is None
    )
    budget_fit = scorecard.components_48h.get("budget_fit")
    if payout_unresolved or economics_unresolved or budget_fit is None:
        if payout_unresolved:
            _append_unique(reasons, gates.payout_spread.reason)
        if economics_unresolved:
            _append_unique(
                reasons,
                scorecard.economics.reason or "required economic inputs are unresolved",
            )
        if budget_fit is None:
            _append_unique(reasons, "48H budget fit is unresolved")
        return INSUFFICIENT_DATA, RESEARCH_FURTHER, tuple(reasons)

    unresolved_gates = _gate_reasons(
        gates,
        statuses=frozenset({UNKNOWN, CONTRADICTED}),
    )
    reasons.extend(unresolved_gates)
    for component in scorecard.unknown_components:
        _append_unique(reasons, f"{component} score evidence is unresolved")
    for component in scorecard.unknown_components_48h:
        _append_unique(reasons, f"48H {component} evidence is unresolved")
    if reasons:
        return NEEDS_VERIFICATION, RESEARCH_FURTHER, tuple(reasons)

    if budget_fit is not None and budget_fit < 75:
        _append_unique(reasons, "48H budget fit is below 75")
        if scorecard.score is not None and scorecard.score < 70:
            _append_unique(reasons, "Commercial Score is below 70")
        if scorecard.score_48h is not None and scorecard.score_48h < 70:
            _append_unique(reasons, "48H Score is below 70")
        return WATCHLIST, BUCKET_WATCHLIST, tuple(reasons)

    if (
        scorecard.score is not None
        and scorecard.score_48h is not None
        and scorecard.score >= 70
        and scorecard.score_48h >= 70
        and budget_fit is not None
        and budget_fit >= 75
        and contribution is not None
        and contribution > 0
    ):
        return (
            TESTABLE_48H,
            TEST_NOW,
            (
                "Commercial Score is at least 70",
                "48H Score is at least 70",
                "per-order contribution is positive",
            ),
        )

    if scorecard.score is not None and scorecard.score < 70:
        _append_unique(reasons, "Commercial Score is below 70")
    if scorecard.score_48h is not None and scorecard.score_48h < 70:
        _append_unique(reasons, "48H Score is below 70")
    if not reasons:
        reasons.append("scores do not yet meet the TEST_NOW threshold")
    return WATCHLIST, BUCKET_WATCHLIST, tuple(reasons)


def decide_offer(offer: Offer, evidence: object) -> DecisionResult:
    """Return one deterministic public readiness decision."""

    gates = evaluate_gates(offer, evidence)
    scorecard = score_offer(offer, evidence, gates=gates)
    readiness_status, bucket, reasons = _decision_for(scorecard, gates)
    return DecisionResult(
        readiness_status=readiness_status,
        bucket=bucket,
        reasons=reasons,
        scorecard=scorecard,
        gates=gates,
    )


__all__ = [
    "DecisionResult",
    "INSUFFICIENT_DATA",
    "NEEDS_VERIFICATION",
    "READINESS_STATUSES",
    "REJECT",
    "REJECT_READINESS",
    "RESEARCH_ONLY",
    "TESTABLE_48H",
    "WATCHLIST",
    "decide_offer",
]
