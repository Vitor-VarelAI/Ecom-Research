from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from pipeline.arbitrage.evidence import KNOWN, STALE, OfferEvidence, ResolvedGroup, ResolvedSignal
from pipeline.arbitrage.gates import PASS
from pipeline.arbitrage.scoring import (
    SCORING_VERSION,
    WEIGHTS,
    _aggregate,
    calculate_economics,
    score_offer,
)

from tests.arbitrage.test_gates import _evidence, _offer


def _group(name: str, **values) -> ResolvedGroup:
    return ResolvedGroup.from_mapping(
        name,
        {field: ResolvedSignal(value, KNOWN) for field, value in values.items()},
    )


def _complete_evidence(
    *,
    model: str = "affiliate",
    selected_creative_values: dict[str, float] | None = None,
    missing: tuple[str, ...] = (),
    fx: Decimal = Decimal("0.92"),
    monthly_fees: int | float = 12,
) -> tuple[object, object]:
    base = _evidence()
    economics_values = (
        {
            "expected_payout_per_order": 20,
            "aov": 100,
            "affiliate_commission_pct": 20,
            "payment_fees": 2,
            "other_variable_cost": 1,
            "expected_return_cost": 3,
            "expected_cpa": 10,
            "monthly_fees": monthly_fees,
        }
        if model == "affiliate"
        else {
            "aov": 100,
            "retail_price": 90,
            "refund_rate": 10,
            "wholesale_cost": 30,
            "fulfilment_cost": 5,
            "shipping_cost": 4,
            "payment_fees": 2,
            "expected_return_cost": 3,
            "other_variable_cost": 1,
            "expected_cpa": 20,
            "monthly_fees": monthly_fees,
            "minimum_order": 1,
        }
    )
    for field in missing:
        economics_values.pop(field, None)
    creative_values = {
        field: 7
        for field in (
            "visual_transformation",
            "hookability",
            "problem_awareness",
            "demo_potential",
            "ugc_potential",
            "storytelling_potential",
            "ai_creative_potential",
            "before_after_potential",
            "angle_breadth",
            "creative_gap",
        )
    }
    if selected_creative_values:
        creative_values.update(selected_creative_values)
    evidence = replace(
        base,
        economics=_group("economics", **economics_values),
        creative_scores=_group("creative_scores", **creative_values),
        landing=_group("landing", functional_score=8, quality_score=8),
        meta_structure=_group(
            "meta_structure",
            advertiser_count=4,
            active_ads=20,
            oldest_ad_days=48,
            ads_older_than_30d=8,
            creative_concept_count=5,
            variants_per_concept=4,
        ),
        us_readiness=_group(
            "us_readiness",
            us_warehouse=True,
            us_fulfilment=True,
            delivery_max_days=7,
            shipping_cost=4,
            us_returns_address="123 Main St",
            checkout_in_usd=True,
        ),
        trends=_group("trends", demand_score=8),
        competition=_group("competition", structure_score=6),
        fx_rate=ResolvedSignal(fx, KNOWN),
    )
    return _offer(model=model), evidence


def test_affiliate_economics_uses_decimal_fx_and_excludes_monthly_fee() -> None:
    offer, evidence = _complete_evidence()

    result = calculate_economics(offer, evidence)

    assert result.state == KNOWN
    assert result.selected_payout_eur == Decimal("18.40")
    assert result.contribution_eur == Decimal("12.88")
    assert result.break_even_cpa_eur == Decimal("12.88")
    assert result.headroom == Decimal("1.4")
    assert result.economics_score == 50
    assert result.excluded_monthly_fees_eur == Decimal("11.04")


def test_reseller_economics_applies_refund_rate_before_costs() -> None:
    offer, evidence = _complete_evidence(model="reseller")

    result = calculate_economics(offer, evidence)

    assert result.sale_price_eur == Decimal("92.00")
    assert result.contribution_eur == Decimal("41.40")
    assert result.headroom == Decimal("2.25")
    assert result.economics_score == 100


def test_missing_required_cost_is_unknown_and_never_zero() -> None:
    offer, evidence = _complete_evidence(
        missing=("other_variable_cost",),
    )

    result = calculate_economics(offer, evidence)

    assert result.contribution_eur is None
    assert result.economics_score is None
    assert "other_variable_cost" in result.missing_fields


def test_monthly_fee_is_excluded_from_commercial_economics_but_can_be_unknown() -> None:
    offer, evidence = _complete_evidence(monthly_fees=0)
    result = calculate_economics(offer, evidence)

    assert result.state == KNOWN
    assert result.excluded_monthly_fees_eur == Decimal("0.00")

    missing_offer, missing_evidence = _complete_evidence(
        missing=("monthly_fees",),
    )
    missing_result = calculate_economics(missing_offer, missing_evidence)
    missing_card = score_offer(missing_offer, missing_evidence)

    assert missing_result.contribution_eur == Decimal("12.88")
    assert missing_result.missing_fields == ()
    assert missing_card.score is not None
    assert missing_card.score_48h is None
    assert missing_card.components["economics"] == 50


def test_reseller_family_requires_minimum_order_for_public_score() -> None:
    for model in ("reseller", "dropshipping", "direct_fulfilment"):
        offer, evidence = _complete_evidence(
            model=model,
            missing=("minimum_order",),
        )

        result = calculate_economics(offer, evidence)
        card = score_offer(offer, evidence)

        assert result.contribution_eur is None
        assert card.score is None
        assert "minimum_order" in result.missing_fields


def test_stale_fx_blocks_financial_calculation() -> None:
    offer, evidence = _complete_evidence()
    evidence = replace(evidence, fx_rate=ResolvedSignal(Decimal("0.92"), STALE))

    result = calculate_economics(offer, evidence)

    assert result.state == STALE
    assert result.contribution_eur is None


def test_creative_rubrics_distinguish_commercial_and_48h() -> None:
    offer, evidence = _complete_evidence(
        selected_creative_values={
            "visual_transformation": 10,
            "problem_awareness": 10,
            "ai_creative_potential": 10,
            "before_after_potential": 10,
        }
        | {
            "hookability": 0,
            "demo_potential": 0,
            "ugc_potential": 0,
            "storytelling_potential": 0,
            "angle_breadth": 0,
            "creative_gap": 0,
        }
    )

    card = score_offer(offer, evidence)

    assert card.version == SCORING_VERSION
    assert card.components["creative"] == 50
    assert card.components_48h["creative"] == 0
    assert card.score is not None
    assert card.score_48h is not None
    assert card.score != card.score_48h


def test_weights_are_exact_and_unknown_weight_is_not_renormalised() -> None:
    assert dict(WEIGHTS) == {
        "economics": 25,
        "creative": 20,
        "paid": 15,
        "meta": 15,
        "landing": 10,
        "us": 5,
        "trends": 5,
        "competition": 5,
    }
    offer, evidence = _complete_evidence()
    evidence = replace(
        evidence,
        competition=ResolvedGroup.from_mapping(
            "competition",
            {"structure_score": ResolvedSignal(None, "UNKNOWN")},
        ),
    )

    card = score_offer(offer, evidence)

    assert card.score is None
    assert card.diagnostic_score < 100
    assert card.known_weight == 95
    assert "competition" in card.unknown_components


def test_final_aggregate_uses_round_half_up() -> None:
    diagnostic, known_weight, unknown, _ = _aggregate(
        {
            "economics": 0,
            "creative": 0,
            "paid": 50,
            "meta": 0,
            "landing": 25,
            "us": 0,
            "trends": 25,
            "competition": 25,
        }
    )

    assert diagnostic == 13
    assert known_weight == 100
    assert unknown == ()


def test_every_reported_component_stays_on_the_five_point_band() -> None:
    offer, evidence = _complete_evidence()

    card = score_offer(offer, evidence)
    allowed = {0, 25, 50, 75, 100}

    assert set(card.components.values()) <= allowed
    assert set(card.components_48h.values()) <= allowed


def test_paid_component_requires_known_commercial_rights_without_penalising_no() -> None:
    offer, evidence = _complete_evidence()
    permissions = _group(
        "permissions",
        meta_ads="YES",
        tiktok_ads="NO",
        google_ads="NO",
        custom_creatives="YES",
        us_traffic="YES",
        brand_name_use="NO",
        brand_assets="YES",
        brand_bidding="YES",
        map_policy="YES",
    )
    card_with_known_restriction = score_offer(
        offer,
        replace(evidence, permissions=permissions),
    )
    unresolved_permissions = replace(
        permissions,
        signals=tuple(
            (name, ResolvedSignal("NEEDS_VERIFICATION", KNOWN))
            if name == "brand_assets"
            else (name, signal)
            for name, signal in permissions.signals
        ),
    )
    card_with_unresolved_right = score_offer(
        offer,
        replace(evidence, permissions=unresolved_permissions),
    )

    assert card_with_known_restriction.components["paid"] == 75
    assert card_with_unresolved_right.components["paid"] is None
    assert card_with_unresolved_right.score is None


def test_google_ads_unknown_does_not_hide_known_meta_tiktok_paid_readiness() -> None:
    offer, evidence = _complete_evidence()
    permissions = _group(
        "permissions",
        meta_ads="YES",
        tiktok_ads="NO",
        google_ads=None,
        custom_creatives="YES",
        us_traffic="YES",
        brand_name_use="YES",
        brand_assets="YES",
        brand_bidding="YES",
        map_policy="YES",
    )

    card = score_offer(offer, replace(evidence, permissions=permissions))

    assert card.components["paid"] == 75
    assert card.score is not None


def test_landing_functional_zero_is_a_score_penalty_not_a_public_reject() -> None:
    offer, evidence = _complete_evidence()
    evidence = replace(
        evidence,
        landing=_group("landing", functional_score=0, quality_score=8),
    )

    card = score_offer(offer, evidence)

    assert card.components["landing"] == 0
    assert card.score is not None
    assert card.gates.landing.status != PASS


def test_inventory_moq_changes_48h_budget_readiness() -> None:
    low_offer, low_evidence = _complete_evidence(model="reseller")
    high_offer, high_evidence = _complete_evidence(model="reseller")
    high_evidence = replace(
        high_evidence,
        economics=_group(
            "economics",
            aov=100,
            retail_price=90,
            refund_rate=10,
            wholesale_cost=30,
            fulfilment_cost=5,
            shipping_cost=4,
            payment_fees=2,
            expected_return_cost=3,
            other_variable_cost=1,
            expected_cpa=20,
            monthly_fees=12,
            minimum_order=100,
        ),
    )

    low_card = score_offer(low_offer, low_evidence)
    high_card = score_offer(high_offer, high_evidence)

    assert low_card.components_48h["budget_fit"] > high_card.components_48h["budget_fit"]
    assert low_card.score_48h != high_card.score_48h


@pytest.mark.parametrize(
    ("monthly_fees", "expected"),
    [
        (20, 100),
        (50, 75),
        (100, 50),
        (250, 25),
        (251, 0),
    ],
)
def test_48h_budget_fit_uses_absolute_euro_commitment(
    monthly_fees: int,
    expected: int,
) -> None:
    offer, evidence = _complete_evidence(
        fx=Decimal("1"),
        monthly_fees=monthly_fees,
    )

    card = score_offer(offer, evidence)

    assert card.components_48h["budget_fit"] == expected


def test_public_score_requires_all_gates_and_all_components() -> None:
    offer, evidence = _complete_evidence()
    blocked = replace(
        evidence,
        links=ResolvedGroup.from_mapping(
            "links",
            {
                "program_url": ResolvedSignal(
                    "https://partner.example/program",
                    STALE,
                ),
                "landing_url": ResolvedSignal(
                    "https://shop.example/landing",
                    KNOWN,
                ),
                "ad_links": ResolvedSignal(
                    ("https://meta.example/ad-1",),
                    KNOWN,
                ),
            },
        ),
    )

    card = score_offer(offer, blocked)

    assert card.score is None
    assert card.score_48h is None
    assert card.gates.monetization_access.status != PASS
