from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from pipeline.arbitrage.evidence import KNOWN, UNKNOWN, ResolvedGroup, ResolvedSignal
from pipeline.arbitrage.buckets import (
    REJECT,
    RESEARCH_FURTHER,
    TEST_NOW,
    WATCHLIST,
    classify_bucket,
)
from pipeline.arbitrage.decision import (
    INSUFFICIENT_DATA,
    NEEDS_VERIFICATION,
    RESEARCH_ONLY,
    TESTABLE_48H,
    WATCHLIST as READINESS_WATCHLIST,
    decide_offer,
)
from pipeline.arbitrage.gates import FAIL, GateResult, evaluate_gates
from pipeline.arbitrage.scoring import score_offer

from tests.arbitrage.test_gates import _evidence, _offer
from tests.arbitrage.test_scoring import _complete_evidence


@dataclass(frozen=True)
class _Card:
    score: int | None
    score_48h: int | None
    incomplete: bool = False


def _gates():
    return evaluate_gates(_offer(), _evidence())


@pytest.mark.parametrize(
    ("commercial", "h48", "expected"),
    [
        (69, 100, WATCHLIST),
        (70, 70, TEST_NOW),
        (70, 69, WATCHLIST),
        (100, 70, TEST_NOW),
    ],
)
def test_test_now_requires_both_scores_at_least_70(
    commercial: int,
    h48: int,
    expected: str,
) -> None:
    assert classify_bucket(_Card(commercial, h48), _gates()) == expected


def test_fail_precedes_unknown_and_score() -> None:
    gates = replace(
        _gates(),
        monetization_access=GateResult(FAIL, "commercial prohibition"),
    )

    assert classify_bucket(_Card(100, 100), gates) == REJECT


def test_unknown_contradicted_or_incomplete_is_research_further() -> None:
    gates = _gates()
    unknown = replace(
        gates,
        ad_links=GateResult("UNKNOWN", "ad links missing"),
    )
    contradicted = replace(
        gates,
        us_traffic=GateResult("CONTRADICTED", "US traffic disagrees"),
    )

    assert classify_bucket(_Card(100, 100), unknown) == RESEARCH_FURTHER
    assert classify_bucket(_Card(100, 100), contradicted) == RESEARCH_FURTHER
    assert classify_bucket(_Card(None, None), gates) == RESEARCH_FURTHER
    assert classify_bucket(_Card(100, 100, incomplete=True), gates) == RESEARCH_FURTHER


def test_disabled_offer_cannot_become_test_now() -> None:
    offer = _offer(enabled=False)
    _, evidence = _complete_evidence()
    card = score_offer(offer, evidence)

    assert classify_bucket(card) == RESEARCH_FURTHER


def test_public_decision_is_deterministic_and_exposes_readiness_status() -> None:
    offer, evidence = _complete_evidence()

    first = decide_offer(offer, evidence)
    second = decide_offer(offer, evidence)

    assert first.readiness_status == TESTABLE_48H
    assert first.bucket == TEST_NOW
    assert first.reasons == second.reasons
    assert first.reasons == tuple(first.reasons)


def test_non_positive_contribution_cannot_reach_test_now() -> None:
    offer, evidence = _complete_evidence()
    economics = dict(evidence.economics.signals)
    economics["expected_payout_per_order"] = ResolvedSignal(5, KNOWN)
    evidence = replace(
        evidence,
        economics=ResolvedGroup.from_mapping("economics", economics),
    )

    result = decide_offer(offer, evidence)

    assert result.readiness_status == REJECT
    assert result.bucket == REJECT
    assert result.bucket != TEST_NOW


def test_unknown_48h_cost_is_insufficient_data_without_hiding_commercial_score() -> None:
    offer, evidence = _complete_evidence(missing=("monthly_fees",))

    result = decide_offer(offer, evidence)

    assert result.commercial_score is not None
    assert result.score_48h is None
    assert result.readiness_status == INSUFFICIENT_DATA
    assert result.bucket == RESEARCH_FURTHER


def test_commitment_above_50_eur_cannot_be_testable_48h() -> None:
    offer, evidence = _complete_evidence(
        fx=1,
        monthly_fees=51,
    )

    result = decide_offer(offer, evidence)

    assert result.scorecard.components_48h["budget_fit"] == 50
    assert result.readiness_status == READINESS_WATCHLIST
    assert result.bucket == WATCHLIST


@pytest.mark.parametrize(
    ("offer_kwargs", "evidence_kwargs", "status", "bucket"),
    [
        ({"enabled": False}, {}, RESEARCH_ONLY, RESEARCH_FURTHER),
        ({}, {"payout": None, "aov": None, "commission": None}, INSUFFICIENT_DATA, RESEARCH_FURTHER),
        ({}, {"permission_values": {"brand_assets": "NEEDS_VERIFICATION"}}, NEEDS_VERIFICATION, RESEARCH_FURTHER),
        ({}, {"functional_score": 0}, READINESS_WATCHLIST, WATCHLIST),
    ],
)
def test_public_decision_applies_readiness_precedence(
    offer_kwargs: dict,
    evidence_kwargs: dict,
    status: str,
    bucket: str,
) -> None:
    offer = _offer(**offer_kwargs)
    evidence = _complete_evidence()[1]
    if "payout" in evidence_kwargs:
        economics = dict(evidence.economics.signals)
        for field, value in (
            ("expected_payout_per_order", evidence_kwargs["payout"]),
            ("aov", evidence_kwargs["aov"]),
            ("affiliate_commission_pct", evidence_kwargs["commission"]),
        ):
            economics[field] = ResolvedSignal(
                value,
                KNOWN if value is not None else UNKNOWN,
            )
        evidence = replace(
            evidence,
            economics=ResolvedGroup.from_mapping("economics", economics),
        )
    elif "permission_values" in evidence_kwargs:
        permissions = dict(evidence.permissions.signals)
        for field, value in evidence_kwargs["permission_values"].items():
            permissions[field] = ResolvedSignal(value, KNOWN)
        evidence = replace(
            evidence,
            permissions=ResolvedGroup.from_mapping("permissions", permissions),
        )
    elif "functional_score" in evidence_kwargs:
        landing = dict(evidence.landing.signals)
        landing["functional_score"] = ResolvedSignal(
            evidence_kwargs["functional_score"],
            KNOWN,
        )
        evidence = replace(
            evidence,
            landing=ResolvedGroup.from_mapping("landing", landing),
        )

    result = decide_offer(offer, evidence)

    assert result.readiness_status == status
    assert result.bucket == bucket
    assert result.reasons
