"""Committed copper-bottles candidate catalog (Part B, source-gated).

The committed ``config/affiliate_offers.json`` must load through the strict
loader as exactly one unapproved copper product (``433621eb147d``, market
``US``) with five ``candidate`` offers, zero env references, zero active
offers — so the gate stays dark even under an otherwise eligible AVANÇAR
result, and no tracked URL or health claim can reach any rendered content.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pipeline.monetization import config as monetization_config
from pipeline.monetization import gate

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "affiliate_offers.json"
PRODUCT_ID = "433621eb147d"


def _load_committed():
    catalog = monetization_config.load(CONFIG_PATH)
    assert catalog is not None, "committed config must stay valid"
    return catalog


def _eligible_result() -> dict:
    return {
        "product_id": PRODUCT_ID,
        "score_result": {"score": 95, "label": "AVANÇAR"},
        "ads_data": {"total_ads": 3, "matched_count": 3, "ad_ids": ["a1"]},
        "csv_data": {"volume": 5400},
    }


def _valid_source_states() -> dict:
    return {
        "apify_ads": {"status": "valid", "origin": "fresh"},
        "trends": {"status": "valid", "origin": "fresh"},
        "keyword_csv": {"status": "valid", "origin": "fresh"},
    }


def test_committed_catalog_loads_with_schema_version_1():
    catalog = _load_committed()
    assert catalog.schema_version == 1


def test_committed_catalog_has_exactly_one_unapproved_copper_product():
    catalog = _load_committed()
    assert len(catalog.products) == 1
    product = catalog.products[0]
    assert product.product_id == PRODUCT_ID
    assert product.market == "US"
    assert product.approved is False


def test_committed_catalog_has_five_candidates_with_expected_roles():
    catalog = _load_committed()
    offers = catalog.products[0].offers
    assert len(offers) == 5
    assert all(entry.status == "candidate" for entry in offers)
    assert [entry.role for entry in offers].count("primary") == 4
    assert [entry.role for entry in offers].count("trust_fallback") == 1
    assert {entry.offer_id for entry in offers} == {
        "pur-copper-primary",
        "sama-homes-primary",
        "copperwater-primary",
        "amazon-us-trust-fallback",
        "copper-utensil-online-primary",
    }


def test_only_unambiguous_pur_commission_is_recorded():
    offers = {offer.offer_id: offer for offer in _load_committed().products[0].offers}
    assert offers["pur-copper-primary"].commission_value == 15.0
    for offer_id in (
        "sama-homes-primary",
        "copperwater-primary",
        "amazon-us-trust-fallback",
        "copper-utensil-online-primary",
    ):
        assert offers[offer_id].commission_type is None
        assert offers[offer_id].commission_value is None


def test_committed_catalog_has_no_env_references_and_no_active_offers():
    catalog = _load_committed()
    for entry in catalog.products[0].offers:
        assert entry.affiliate_url_env is None
        assert entry.status != "active"


def test_committed_catalog_gate_returns_none_under_eligible_avançar():
    """Even an otherwise fully eligible AVANÇAR result emits nothing: the
    product is unapproved and no offer is active (the gate stays dark)."""
    catalog = _load_committed()
    emission = gate.evaluate(
        result=_eligible_result(),
        source_states=_valid_source_states(),
        catalog=catalog,
        today=date(2026, 8, 24),
        env_get=lambda name: "value",
    )
    assert emission is None


def test_committed_catalog_contains_no_tracked_url_values():
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    serialized = json.dumps(raw).casefold()
    for product in raw["products"]:
        for offer in product["offers"]:
            assert offer["affiliate_url_env"] is None
    for needle in ("utm_", "?tag=", "?ref=", "affiliate-tag"):
        assert needle not in serialized


def test_committed_config_prose_records_no_health_claims():
    """Merchant URL slugs may contain product vocabulary such as
    ``ayurvedic``; operator-authored prose must not repeat health claims."""
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    prose_fields = ("merchant", "payout_eligibility", "asset_rights", "traffic_policy")
    prose = " ".join(
        str(offer.get(field) or "")
        for product in raw["products"]
        for offer in product["offers"]
        for field in prose_fields
    ).casefold()
    for needle in ("anti-aging", "kills bacteria", "immune", "health benefit", "oligodynamic"):
        assert needle not in prose
