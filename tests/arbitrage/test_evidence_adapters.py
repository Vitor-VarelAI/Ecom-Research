from __future__ import annotations

import ast
import copy
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from pipeline.arbitrage.adapters.landing import derive_landing
from pipeline.arbitrage.adapters.meta import derive_meta
from pipeline.arbitrage.adapters.trends import demand_score, derive_trends
from pipeline.arbitrage.evidence import (
    CONTRADICTED,
    KNOWN,
    STALE,
    UNKNOWN,
    ArbitrageConfig,
    build_offer_evidence,
    convert_to_eur,
    latest_pack,
    resolve_candidates,
    resolve_fact,
    resolve_fx,
    ResolvedSignal,
)
from pipeline.arbitrage.models import (
    Competition,
    CreativeScores,
    Economics,
    Evidence,
    Fact,
    FxRate,
    Landing,
    Links,
    MetaStructure,
    Offer,
    Permissions,
    Trends,
    USReadiness,
)

AS_OF = date(2026, 9, 7)


def _evidence(
    source_ref: str,
    *,
    source_type: str = "merchant_page",
    observed_at: date = AS_OF,
) -> Evidence:
    return Evidence(
        source_ref=source_ref,
        source_type=source_type,
        url="https://source.example/page",
        stable_ref=None,
        observed_at=observed_at.isoformat(),
        captured_at=f"{observed_at.isoformat()}T12:00:00Z",
        pack_id=None,
        content_sha256="a" * 64,
        excerpt="Observed value.",
    )


def _fact(value, ref: str = "src-1", *, status: str = KNOWN) -> Fact:
    return Fact(value=value, status=status, source_refs=(ref,))


def _offer(
    *,
    evidence: tuple[Evidence, ...] = (_evidence("src-1"),),
    meta: MetaStructure | None = None,
    trends: Trends | None = None,
    landing: Landing | None = None,
    currency: Fact | None = None,
) -> Offer:
    empty_links = Links(None, None, None, None)
    empty_economics = Economics(
        retail_price=None,
        aov=None,
        affiliate_commission_pct=None,
        cpa_payout=None,
        expected_payout_per_order=None,
        recurring_commission=None,
        cookie_days=None,
        wholesale_cost=None,
        gross_spread=None,
        monthly_fees=None,
        minimum_order=None,
        refund_rate=None,
        refund_policy=None,
        payment_fees=None,
        fulfilment_cost=None,
        shipping_cost=None,
        expected_return_cost=None,
        other_variable_cost=None,
        expected_cpa=None,
        currency=currency,
    )
    empty_permissions = Permissions(None, None, None, None, None, None, None, None, None)
    empty_readiness = USReadiness(None, None, None, None, None, None, None)
    empty_landing = landing or Landing(None, None)
    empty_creative = CreativeScores(None, None, None, None, None, None, None, None, None, None)
    empty_meta = meta or MetaStructure(
        advertiser_count=None,
        active_ads=None,
        oldest_ad_days=None,
        ads_older_than_30d=None,
        creative_concept_count=None,
        variants_per_concept=None,
        video_share=None,
        image_share=None,
        carousel_share=None,
        ugc_share=None,
        polished_share=None,
        hooks=None,
        observed_offers=None,
        landing_pages=None,
        ad_links=None,
    )
    empty_trends = trends or Trends(None)
    return Offer(
        offer_id="offer-1",
        name="Demo offer",
        brand="Demo brand",
        market_id="US",
        research_keyword="demo keyword",
        enabled=True,
        monetization_model="affiliate",
        product_type="physical",
        links=empty_links,
        economics=empty_economics,
        permissions=empty_permissions,
        us_readiness=empty_readiness,
        landing=empty_landing,
        creative_scores=empty_creative,
        meta_structure=empty_meta,
        trends=empty_trends,
        competition=Competition(None),
        evidence=evidence,
    )


def _config(offer: Offer, *, fx_rates: tuple[FxRate, ...] = ()) -> ArbitrageConfig:
    return ArbitrageConfig(1, "EUR", fx_rates, (offer,))


def _pack(
    *,
    pack_date: str = "2026-09-06",
    market_id: str = "US",
    keyword: str = "demo keyword",
    completed_at: str = "2026-09-06T10:00:00+00:00",
    run_id: str | None = None,
    artifact_complete: bool | None = None,
) -> dict:
    payload = {
        "date": pack_date,
        "market_id": market_id,
        "keyword": keyword,
        "research_keyword": keyword,
        "completed_at": completed_at,
        "generated_at": completed_at,
        "facebook_ads": {
            "candidates": [
                {
                    "advertiser": "One advertiser",
                    "page_id": "page-1",
                    "days_active": 31,
                    "link": "https://www.facebook.com/ads/library/?id=1",
                    "landing_url": "https://shop.example/product",
                    "match": {"label": "matched"},
                },
                {
                    "advertiser": "Two advertiser",
                    "page_id": "page-2",
                    "days_active": 10,
                    "link": "https://www.facebook.com/ads/library/?id=2",
                    "landing_url": "https://shop.example/product",
                    "match": {"label": "rejected"},
                },
            ]
        },
        "trends": {"slope_90d_pct": 100.0},
        "keyword_planner": {"volume": 500},
        "landing_price": {"price_range": {"min": 10, "max": 20, "currency": "USD"}},
    }
    if run_id is not None:
        payload["run_id"] = run_id
        payload["artifact_complete"] = artifact_complete
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (7, KNOWN),
        (8, STALE),
        (14, KNOWN),
        (15, STALE),
        (30, KNOWN),
        (31, STALE),
    ],
)
def test_freshness_boundaries_are_inclusive(age: int, expected: str) -> None:
    observed = AS_OF - timedelta(days=age)
    signal = resolve_fact(
        _fact(12),
        (_evidence("src-1", observed_at=observed),),
        AS_OF,
        max_age_days=age if age in (7, 14, 30) else age - 1,
    )
    assert signal.state == expected


def test_future_fact_is_invalid_and_contradiction_stays_contradicted() -> None:
    future = _evidence("src-1", observed_at=AS_OF + timedelta(days=1))
    assert resolve_fact(_fact(12), (future,), AS_OF, max_age_days=30).state == STALE
    contradicted = _fact(None, "src-1", status=CONTRADICTED)
    assert resolve_fact(contradicted, (future,), AS_OF).state == CONTRADICTED


def test_unknown_does_not_become_zero_and_same_priority_disagreement_is_explicit() -> None:
    assert resolve_fact(None, (), AS_OF).value is None
    result = resolve_candidates(
        (
            # Official terms wins over a lower-priority merchant page.
            # The tie below remains a contradiction.
            ResolvedSignal(
                1, KNOWN, ("a",), ("merchant_page",)
            ),
            ResolvedSignal(
                2, KNOWN, ("b",), ("merchant_page",)
            ),
        )
    )
    assert result.state == CONTRADICTED
    assert result.value is None


def test_source_priority_is_deterministic() -> None:
    result = resolve_candidates(
        (
            ResolvedSignal(2, KNOWN, ("merchant",), ("merchant_page",)),
            ResolvedSignal(1, KNOWN, ("official",), ("official_terms",)),
        )
    )
    assert result.state == KNOWN
    assert result.value == 1


def test_fx_missing_stale_and_eur_identity() -> None:
    offer = _offer()
    config = _config(
        offer,
        fx_rates=(FxRate("USD", 0.92, "fx-usd", "2026-08-01"),),
    )
    assert resolve_fx("EUR", config, AS_OF).value == 1.0
    assert resolve_fx("GBP", config, AS_OF).state == UNKNOWN
    assert resolve_fx("USD", config, AS_OF).state == STALE
    assert convert_to_eur(10, "USD", config, AS_OF) is None


def test_latest_pack_is_exact_deterministic_read_only_selection(tmp_path: Path) -> None:
    _write_json(tmp_path / "quota.json", {"month": "2026-09", "calls": 1})
    _write_json(tmp_path / "malformed.json", "{")
    _write_json(tmp_path / "wrong-market.json", _pack(market_id="GB"))
    _write_json(tmp_path / "wrong-keyword.json", _pack(keyword="other"))
    _write_json(
        tmp_path / "incomplete.json",
        _pack(run_id="run-incomplete", artifact_complete=False),
    )
    _write_json(
        tmp_path / "future.json",
        _pack(pack_date="2026-09-08"),
    )
    runs = tmp_path / "runs"
    runs.mkdir()
    _write_json(runs / "manifest.json", _pack(run_id="manifest", artifact_complete=True))
    _write_json(
        tmp_path / "older.json",
        _pack(completed_at="2026-09-06T09:00:00+00:00"),
    )
    _write_json(
        tmp_path / "newer.json",
        _pack(completed_at="2026-09-06T11:00:00+00:00"),
    )
    before = sorted(path.name for path in tmp_path.rglob("*") if path.is_file())
    selected = latest_pack(tmp_path, "US", "demo keyword", AS_OF)
    after = sorted(path.name for path in tmp_path.rglob("*") if path.is_file())
    assert selected is not None
    assert selected.metadata.filename == "newer.json"
    assert before == after


def test_meta_derives_only_matched_permitted_fields() -> None:
    pack = _pack()
    signals = derive_meta(pack, source_ref="pack:test", observed_at="2026-09-06")
    assert set(signals) == {
        "advertiser_count",
        "active_ads",
        "oldest_ad_days",
        "ads_older_than_30d",
        "ad_links",
        "landing_pages",
    }
    assert signals["advertiser_count"].value == 1
    assert signals["active_ads"].value == 1
    assert signals["ads_older_than_30d"].value == 1
    assert signals["landing_pages"].value == ("https://shop.example/product",)
    assert "permissions" not in signals
    assert "creative_concept_count" not in signals


def test_trends_volume_bands_and_no_volume_cap() -> None:
    assert demand_score({"slope_90d_pct": 100}, {}) <= 5
    assert demand_score({"slope_90d_pct": 100}, {"volume": 50}) <= 5
    assert demand_score({"slope_90d_pct": 100}, {"volume": 50_000}) > 5
    assert derive_trends(
        {"trends": {"slope_90d_pct": 100}, "keyword_planner": {"error": "missing"}}
    )["demand_score"].value <= 5


def test_landing_functionality_does_not_infer_quality() -> None:
    signals = derive_landing(_pack())
    assert signals["functional_score"].value == 10.0
    assert "quality_score" not in signals
    unsafe = copy.deepcopy(_pack())
    unsafe["facebook_ads"]["candidates"][0]["landing_url"] = "http://127.0.0.1/private"
    assert derive_landing(unsafe) == {}


def test_build_prefers_configured_values_and_fills_only_absent_adapter_fields(tmp_path: Path) -> None:
    evidence = (_evidence("src-1", source_type="official_terms"),)
    offer = _offer(
        evidence=evidence,
        meta=MetaStructure(
            advertiser_count=_fact(99),
            active_ads=None,
            oldest_ad_days=None,
            ads_older_than_30d=None,
            creative_concept_count=None,
            variants_per_concept=None,
            video_share=None,
            image_share=None,
            carousel_share=None,
            ugc_share=None,
            polished_share=None,
            hooks=None,
            observed_offers=None,
            landing_pages=None,
            ad_links=None,
        ),
        trends=Trends(_fact(8)),
        landing=Landing(None, None),
    )
    _write_json(tmp_path / "pack.json", _pack())
    before = copy.deepcopy(_pack())
    resolved = build_offer_evidence(offer, _config(offer), AS_OF, tmp_path)
    assert resolved.meta_structure.advertiser_count.value == 99
    assert resolved.meta_structure.active_ads.value == 1
    assert resolved.trends.demand_score.value == 8
    assert resolved.landing.functional_score.state == KNOWN
    assert resolved.landing.quality_score.state == UNKNOWN
    assert json.loads(json.dumps(before)) == _pack()
    assert resolved.selected_pack is not None


def test_owned_modules_make_no_network_imports() -> None:
    forbidden = {"requests", "httpx", "aiohttp", "urllib.request", "socket"}
    paths = [
        Path(__file__).parents[2] / "pipeline/arbitrage/evidence.py",
        Path(__file__).parents[2] / "pipeline/arbitrage/adapters/meta.py",
        Path(__file__).parents[2] / "pipeline/arbitrage/adapters/trends.py",
        Path(__file__).parents[2] / "pipeline/arbitrage/adapters/landing.py",
    ]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert forbidden.isdisjoint(imports), (path, imports)
