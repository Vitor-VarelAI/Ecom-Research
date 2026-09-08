from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal

import pytest

from pipeline.arbitrage.evidence import (
    CONTRADICTED,
    KNOWN,
    STALE,
    UNKNOWN,
    OfferEvidence,
    ResolvedGroup,
    ResolvedSignal,
)
from pipeline.arbitrage.gates import (
    CONTRADICTED as GATE_CONTRADICTED,
    FAIL,
    PASS,
    UNKNOWN as GATE_UNKNOWN,
    GateReport,
    evaluate_gates,
)
from pipeline.arbitrage.models import (
    ArbitrageConfig,
    Competition,
    CreativeScores,
    Economics,
    Landing,
    Links,
    MetaStructure,
    Offer,
    Permissions,
    Trends,
    USReadiness,
)


def _signal(value=None, state: str = UNKNOWN) -> ResolvedSignal:
    return ResolvedSignal(value, state)


def _group(name: str, **values) -> ResolvedGroup:
    return ResolvedGroup.from_mapping(
        name,
        {field: _signal(value, KNOWN) for field, value in values.items()},
    )


def _evidence(
    *,
    program_url="https://partner.example/program",
    landing_url="https://shop.example/landing",
    ad_links=("https://meta.example/ad-1",),
    meta_ad="YES",
    tiktok_ad="NO",
    google_ad="NO",
    custom_creatives="YES",
    us_traffic="YES",
    payout=20,
    aov=100,
    commission=20,
    product_type="physical",
    functional_score=8,
    us_fulfilment=True,
    delivery_max_days=7,
    shipping_cost=5,
    returns_address="123 Main St",
    checkout_in_usd=True,
    signal_states: dict[tuple[str, str], str] | None = None,
    permission_values: dict[str, str] | None = None,
) -> OfferEvidence:
    permission_values = permission_values or {}
    values = {
        ("links", "program_url"): program_url,
        ("links", "landing_url"): landing_url,
        ("links", "ad_links"): ad_links,
        ("economics", "expected_payout_per_order"): payout,
        ("economics", "aov"): aov,
        ("economics", "affiliate_commission_pct"): commission,
        ("permissions", "meta_ads"): meta_ad,
        ("permissions", "tiktok_ads"): tiktok_ad,
        ("permissions", "google_ads"): google_ad,
        ("permissions", "custom_creatives"): custom_creatives,
        ("permissions", "us_traffic"): us_traffic,
        ("permissions", "brand_name_use"): permission_values.get("brand_name_use", "YES"),
        ("permissions", "brand_assets"): permission_values.get("brand_assets", "YES"),
        ("permissions", "brand_bidding"): permission_values.get("brand_bidding", "YES"),
        ("permissions", "map_policy"): permission_values.get("map_policy", "YES"),
        ("landing", "functional_score"): functional_score,
        ("us_readiness", "us_fulfilment"): us_fulfilment,
        ("us_readiness", "delivery_max_days"): delivery_max_days,
        ("us_readiness", "shipping_cost"): shipping_cost,
        ("us_readiness", "us_returns_address"): returns_address,
        ("us_readiness", "checkout_in_usd"): checkout_in_usd,
    }
    signal_states = signal_states or {}
    groups = {}
    for name in (
        "links",
        "economics",
        "permissions",
        "us_readiness",
        "landing",
        "creative_scores",
        "meta_structure",
        "trends",
        "competition",
    ):
        group_values = {}
        for (group_name, field), value in values.items():
            if group_name != name:
                continue
            default_state = UNKNOWN if value is None else KNOWN
            state = signal_states.get((group_name, field), default_state)
            group_values[field] = _signal(value, state)
        groups[name] = _group(name, **{}) if not group_values else ResolvedGroup.from_mapping(
            name, group_values
        )
    return OfferEvidence(
        offer_id="offer-1",
        market_id="US",
        research_keyword="demo",
        links=groups["links"],
        economics=groups["economics"],
        permissions=groups["permissions"],
        us_readiness=groups["us_readiness"],
        landing=groups["landing"],
        creative_scores=groups["creative_scores"],
        meta_structure=groups["meta_structure"],
        trends=groups["trends"],
        competition=groups["competition"],
        fx_rate=_signal(Decimal("0.92"), KNOWN),
        selected_pack=None,
    )


def _offer(
    *,
    model="affiliate",
    product_type="physical",
    enabled=True,
    market_id="US",
) -> Offer:
    def empty(cls):
        return cls(**{field.name: None for field in fields(cls)})

    return Offer(
        offer_id="offer-1",
        name="Demo",
        brand="Brand",
        market_id=market_id,
        research_keyword="demo",
        enabled=enabled,
        monetization_model=model,
        product_type=product_type,
        links=empty(Links),
        economics=empty(Economics),
        permissions=empty(Permissions),
        us_readiness=empty(USReadiness),
        landing=empty(Landing),
        creative_scores=empty(CreativeScores),
        meta_structure=empty(MetaStructure),
        trends=empty(Trends),
        competition=empty(Competition),
        evidence=(),
    )


def test_core_offer_passes_all_required_gates() -> None:
    report = evaluate_gates(_offer(), _evidence())

    assert report.all_pass
    assert report.overall_status == PASS
    assert report.google_ads.status == FAIL
    assert report.google_ads.blocking is False
    assert report.commercial_rights.status == PASS


@pytest.mark.parametrize(
    ("field", "value", "state", "expected"),
    [
        ("brand_name_use", "NO", KNOWN, PASS),
        ("brand_assets", "NOT_APPLICABLE", KNOWN, PASS),
        ("brand_bidding", "NEEDS_VERIFICATION", KNOWN, GATE_UNKNOWN),
        ("map_policy", "YES", STALE, GATE_UNKNOWN),
        ("brand_name_use", "YES", CONTRADICTED, GATE_CONTRADICTED),
    ],
)
def test_commercial_rights_gate_requires_known_policy_states(
    field: str,
    value: str,
    state: str,
    expected: str,
) -> None:
    report = evaluate_gates(
        _offer(),
        _evidence(
            signal_states={("permissions", field): state},
            permission_values={field: value},
        ),
    )
    if state == KNOWN:
        permissions = getattr(report, "commercial_rights")
        assert permissions.status == expected
    else:
        assert report.commercial_rights.status == expected


def test_commercial_rights_value_is_applied_for_known_state() -> None:
    evidence = _evidence()
    evidence = replace(
        evidence,
        permissions=ResolvedGroup.from_mapping(
            "permissions",
            {
                "meta_ads": _signal("YES", KNOWN),
                "tiktok_ads": _signal("NO", KNOWN),
                "google_ads": _signal("NO", KNOWN),
                "custom_creatives": _signal("YES", KNOWN),
                "us_traffic": _signal("YES", KNOWN),
                "brand_name_use": _signal("NO", KNOWN),
                "brand_assets": _signal("YES", KNOWN),
                "brand_bidding": _signal("YES", KNOWN),
                "map_policy": _signal("YES", KNOWN),
            },
        ),
    )

    report = evaluate_gates(_offer(), evidence)

    assert report.commercial_rights.status == PASS


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (UNKNOWN, GATE_UNKNOWN),
        (STALE, GATE_UNKNOWN),
        (CONTRADICTED, GATE_CONTRADICTED),
    ],
)
def test_program_access_never_passes_without_fresh_known_proof(
    state: str, expected: str
) -> None:
    report = evaluate_gates(
        _offer(),
        _evidence(signal_states={("links", "program_url"): state}),
    )

    assert report.monetization_access.status == expected
    assert report.monetization_access.evidence_state == state


def test_disabled_offer_is_unresolved_and_unsupported_model_is_rejected() -> None:
    disabled = evaluate_gates(_offer(enabled=False), _evidence())
    unsupported = evaluate_gates(
        _offer(model="unsupported"),
        _evidence(),
    )

    assert disabled.monetization_access.status == GATE_UNKNOWN
    assert "disabled" in disabled.monetization_access.reason
    assert unsupported.monetization_access.status == FAIL


@pytest.mark.parametrize(
    ("model", "kwargs", "expected"),
    [
        ("affiliate", {"payout": 0, "aov": None, "commission": None}, FAIL),
        ("affiliate", {"payout": 0.01, "aov": None, "commission": None}, PASS),
        ("affiliate", {"payout": None, "aov": 100, "commission": 20}, PASS),
        ("reseller", {"payout": None, "aov": 100}, GATE_UNKNOWN),
    ],
)
def test_payout_and_spread_boundaries(model: str, kwargs: dict, expected: str) -> None:
    evidence = _evidence(**kwargs)
    if model == "reseller":
        evidence = _evidence(**kwargs)
        economics = ResolvedGroup.from_mapping(
            "economics",
            {
                "aov": _signal(100, KNOWN),
                "wholesale_cost": _signal(None, UNKNOWN),
            },
        )
        evidence = OfferEvidence(
            **{
                field.name: (
                    economics
                    if field.name == "economics"
                    else getattr(evidence, field.name)
                )
                for field in fields(OfferEvidence)
            }
        )
    report = evaluate_gates(_offer(model=model), evidence)

    assert report.payout_spread.status == expected


def test_known_zero_gross_spread_is_not_overridden_by_derived_positive() -> None:
    evidence = _evidence()
    zero_direct = replace(
        evidence,
        economics=_group(
            "economics",
            gross_spread=0,
            aov=100,
            wholesale_cost=20,
        ),
    )
    no_direct = replace(
        evidence,
        economics=_group(
            "economics",
            aov=100,
            wholesale_cost=20,
        ),
    )

    assert evaluate_gates(_offer(model="reseller"), zero_direct).payout_spread.status == FAIL
    assert evaluate_gates(_offer(model="reseller"), no_direct).payout_spread.status == PASS


@pytest.mark.parametrize(
    ("meta", "tiktok", "expected"),
    [
        ("YES", "NO", PASS),
        ("NO", "YES", PASS),
        ("NO", "NO", FAIL),
        ("NEEDS_VERIFICATION", "NO", GATE_UNKNOWN),
    ],
)
def test_paid_social_matrix(meta: str, tiktok: str, expected: str) -> None:
    report = evaluate_gates(
        _offer(),
        _evidence(meta_ad=meta, tiktok_ad=tiktok),
    )

    assert report.paid_social.status == expected


def test_custom_creatives_is_separate_hard_gate_and_google_is_advisory() -> None:
    report = evaluate_gates(
        _offer(),
        _evidence(custom_creatives="NO", google_ad="NO"),
    )

    assert report.custom_creatives.status == FAIL
    assert report.google_ads.status == FAIL
    assert report.overall_status == FAIL


def test_us_traffic_is_only_applicable_in_us_mode() -> None:
    non_us = evaluate_gates(
        _offer(market_id="PT"),
        _evidence(),
    )
    unresolved = evaluate_gates(
        _offer(),
        _evidence(us_traffic="NEEDS_VERIFICATION"),
    )

    assert non_us.us_traffic.status == PASS
    assert non_us.us_traffic.applicable is False
    assert unresolved.us_traffic.status == GATE_UNKNOWN


@pytest.mark.parametrize(
    ("product_type", "expected"),
    [
        ("digital", PASS),
        ("service", PASS),
        ("physical", PASS),
    ],
)
def test_landing_ad_links_and_nonphysical_fulfilment(product_type: str, expected: str) -> None:
    report = evaluate_gates(
        _offer(product_type=product_type),
        _evidence(product_type=product_type),
    )

    assert report.landing.status == PASS
    assert report.ad_links.status == PASS
    assert report.physical_us_fulfilment.status == expected


def test_missing_ad_links_are_unknown_not_reject() -> None:
    report = evaluate_gates(_offer(), _evidence(ad_links=()))

    assert report.ad_links.status == GATE_UNKNOWN
    assert report.overall_status == GATE_UNKNOWN


def test_meta_structure_ad_links_are_an_acceptable_fallback() -> None:
    evidence = _evidence(ad_links=())
    evidence = replace(
        evidence,
        meta_structure=ResolvedGroup.from_mapping(
            "meta_structure",
            {
                "ad_links": _signal(("https://meta.example/ad-2",), KNOWN),
            },
        ),
    )

    report = evaluate_gates(_offer(), evidence)

    assert report.ad_links.status == PASS


def test_landing_functional_zero_and_false_fulfilment_fail() -> None:
    landing = evaluate_gates(
        _offer(),
        _evidence(functional_score=0),
    )
    fulfilment = evaluate_gates(
        _offer(),
        _evidence(us_fulfilment=False),
    )

    assert landing.landing.status == FAIL
    assert landing.landing.blocking is False
    assert fulfilment.physical_us_fulfilment.status == FAIL


@pytest.mark.parametrize(
    ("days", "expected"),
    [(10, PASS), (11, FAIL)],
)
def test_dropshipping_delivery_limit(days: int, expected: str) -> None:
    report = evaluate_gates(
        _offer(model="dropshipping"),
        _evidence(delivery_max_days=days),
    )

    assert report.physical_us_fulfilment.status == expected


def test_stale_and_contradicted_are_distinct_in_report() -> None:
    report = evaluate_gates(
        _offer(),
        _evidence(
            signal_states={
                ("links", "program_url"): STALE,
                ("permissions", "us_traffic"): CONTRADICTED,
            }
        ),
    )

    assert report.monetization_access.status == GATE_UNKNOWN
    assert report.stale_gates == ("monetization_access",)
    assert report.us_traffic.status == GATE_CONTRADICTED
    assert report.contradicted_gates == ("us_traffic",)
    assert report.overall_status == GATE_CONTRADICTED
