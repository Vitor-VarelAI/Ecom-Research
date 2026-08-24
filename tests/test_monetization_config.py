"""Strict config validation matrix for ``config/affiliate_offers.json``.

Every malformed shape fails closed to ``None`` (monetization disabled)
without raising and without leaking raw config/errors; the committed
empty-but-valid config loads as a valid empty catalog (the gate then yields
nothing).
"""

from __future__ import annotations

import json
import logging

import pytest

from pipeline.monetization import config as monetization_config
from tests.fixtures.monetization_helpers import (
    PRODUCT_ID_MOCHILA,
    candidate_offer,
    offer,
    product_config,
    write_affiliate_config,
    write_raw_config,
)


def _valid_payload() -> dict:
    return {"schema_version": 1, "products": [product_config()]}


# ── Valid shapes ────────────────────────────────────────────────────────────


def test_empty_products_config_is_valid_empty_catalog(tmp_path):
    catalog = write_affiliate_config(tmp_path, [])
    assert catalog is not None
    assert catalog.schema_version == 1
    assert catalog.products == ()


def test_single_percentage_offer_loads(tmp_path):
    catalog = write_affiliate_config(tmp_path, [product_config()])
    assert catalog is not None
    product = catalog.products[0]
    assert product.product_id == PRODUCT_ID_MOCHILA
    assert product.market == "PT"
    assert product.approved is True
    assert product.offers[0].offer_id == "mochila-pt-primary"
    assert product.offers[0].role == "primary"
    assert product.offers[0].commission_currency is None


def test_fixed_offer_requires_and_keeps_currency(tmp_path):
    payload = _valid_payload()
    payload["products"][0]["offers"] = [offer(commission_type="fixed", commission_value=2.5, commission_currency="EUR")]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert catalog.products[0].offers[0].commission_currency == "EUR"


def test_two_products_and_both_roles_load(tmp_path):
    primary = offer()
    fallback = offer(
        offer_id="mochila-pt-fallback",
        role="trust_fallback",
        merchant="Merchant Y",
        affiliate_url_env="MOCHILA_FALLBACK_TAG",
    )
    catalog = write_affiliate_config(tmp_path, [product_config(offers=[primary, fallback])])
    assert catalog is not None
    assert [entry.product_id for entry in catalog.products] == [PRODUCT_ID_MOCHILA]


def test_market_case_insensitive_in_shipping_markets(tmp_path):
    payload = _valid_payload()
    payload["products"][0]["market"] = "pt"
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert catalog.products[0].market == "pt"


# ── Lifecycle-aware nullability ────────────────────────────────────────────


@pytest.mark.parametrize("status", ["candidate", "applied", "rejected"])
def test_incomplete_status_with_all_nullable_fields_null_loads(tmp_path, status):
    """candidate/applied/rejected may express every optional fact as JSON
    null (unknown) while the identity fields stay mandatory."""
    catalog = write_affiliate_config(
        tmp_path, [product_config(offers=[candidate_offer(status=status)])]
    )
    assert catalog is not None
    offer_entry = catalog.products[0].offers[0]
    assert offer_entry.status == status
    assert offer_entry.offer_id == "mochila-pt-candidate"
    assert offer_entry.role == "primary"
    assert offer_entry.merchant == "Merchant X"
    for name in (
        "destination_url", "affiliate_url_env", "commission_type",
        "commission_value", "commission_currency", "cookie_duration_days",
        "payout_eligibility", "shipping_markets", "asset_rights",
        "traffic_policy", "evidence_url", "checked_at", "valid_until",
    ):
        assert getattr(offer_entry, name) is None, name


@pytest.mark.parametrize("status", ["active", "approved"])
def test_active_or_approved_requires_complete_fields(tmp_path, status):
    """approved/active demand every field complete: an all-null offer is
    invalid for them exactly as under the legacy strict schema."""
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(status=status)]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None


def test_approved_offer_with_complete_fields_loads_but_stays_inert(tmp_path):
    """A fully validated approved offer loads; the gate never emits it
    (only active emits)."""
    catalog = write_affiliate_config(tmp_path, [product_config(offers=[offer(status="approved")])])
    assert catalog is not None
    assert catalog.products[0].offers[0].status == "approved"


@pytest.mark.parametrize(
    "mutate",
    [
        # Present-but-malformed candidate fields must fail closed.
        lambda o: o.__setitem__("destination_url", "https://x.example/p?q=1"),
        lambda o: o.__setitem__("destination_url", "ftp://x.example/p"),
        lambda o: o.__setitem__("affiliate_url_env", "MY TAG"),
        lambda o: o.__setitem__("commission_type", "per-click"),
        lambda o: o.__setitem__("commission_value", 0),
        lambda o: o.__setitem__("commission_value", float("inf")),
        lambda o: o.__setitem__("commission_currency", "eur"),
        lambda o: o.__setitem__("commission_currency", "EU"),
        lambda o: o.__setitem__("cookie_duration_days", 0),
        lambda o: o.__setitem__("cookie_duration_days", 30.5),
        lambda o: o.__setitem__("payout_eligibility", " "),
        lambda o: o.__setitem__("shipping_markets", []),
        lambda o: o.__setitem__("shipping_markets", ["PT", ""]),
        lambda o: o.__setitem__("asset_rights", "\n"),
        lambda o: o.__setitem__("traffic_policy", "  "),
        lambda o: o.__setitem__("evidence_url", "https://x.example/e#top"),
        lambda o: o.__setitem__("checked_at", "not-a-date"),
        lambda o: o.__setitem__("valid_until", "2026-13-40"),
    ],
)
def test_malformed_present_candidate_fields_fail_closed(tmp_path, mutate):
    payload = _valid_payload()
    candidate = candidate_offer()
    mutate(candidate)
    payload["products"][0]["offers"] = [candidate]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None


def test_commission_type_and_value_must_both_be_present_or_both_null(tmp_path):
    # type present, value null -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(commission_type="percentage")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # value present, type null -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(commission_value=5.0)]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # currency alone (no commission) -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(commission_currency="USD")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None


def test_fixed_requires_currency_and_percentage_forbids_currency(tmp_path):
    # fixed without currency -> invalid (legacy rule)
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(
        commission_type="fixed", commission_value=2.5, commission_currency=None)]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # percentage with a currency -> invalid (cross-field rule)
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(
        commission_type="percentage", commission_value=5.0, commission_currency="USD")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # fixed with currency -> valid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(
        commission_type="fixed", commission_value=2.5, commission_currency="USD")]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert catalog.products[0].offers[0].commission_currency == "USD"


def test_evidence_url_and_dates_are_all_or_none(tmp_path):
    # evidence_url present, dates null -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(evidence_url="https://x.example/e")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # checked_at present alone -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(checked_at="2026-08-24")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # valid_until present alone -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(valid_until="2026-11-22")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # all three present but checked_at > valid_until -> invalid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(
        evidence_url="https://x.example/e", checked_at="2026-12-01", valid_until="2026-11-22")]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # all three present and ordered -> valid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer(
        evidence_url="https://x.example/e", checked_at="2026-08-24", valid_until="2026-11-22")]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert catalog.products[0].offers[0].checked_at.isoformat() == "2026-08-24"


def test_nullable_shipping_markets_must_cover_market_when_present(tmp_path):
    # null shipping markets on a candidate -> valid
    payload = _valid_payload()
    payload["products"][0]["offers"] = [candidate_offer()]
    assert write_affiliate_config(tmp_path, payload["products"]) is not None
    # present but missing the operator market -> invalid
    payload = _valid_payload()
    payload["products"][0]["market"] = "US"
    payload["products"][0]["offers"] = [candidate_offer(shipping_markets=("DE",))]
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None
    # present and covering the market -> valid
    payload = _valid_payload()
    payload["products"][0]["market"] = "US"
    payload["products"][0]["offers"] = [candidate_offer(shipping_markets=("US", "CA"))]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert catalog.products[0].offers[0].shipping_markets == ("US", "CA")


def test_exact_keys_still_required_for_nullable_candidate(tmp_path):
    """Null must be explicit: omitting an optional key is still invalid, and
    identity fields can never be null for any status."""
    for field in (
        "destination_url", "affiliate_url_env", "commission_type",
        "commission_value", "commission_currency", "cookie_duration_days",
        "payout_eligibility", "shipping_markets", "asset_rights",
        "traffic_policy", "evidence_url", "checked_at", "valid_until",
    ):
        payload = _valid_payload()
        candidate = candidate_offer()
        del candidate[field]
        payload["products"][0]["offers"] = [candidate]
        assert monetization_config.load(write_raw_config(tmp_path, payload)) is None, field
    for field in ("offer_id", "role", "merchant", "status"):
        payload = _valid_payload()
        candidate = candidate_offer()
        candidate[field] = None
        payload["products"][0]["offers"] = [candidate]
        assert monetization_config.load(write_raw_config(tmp_path, payload)) is None, field


def test_multiple_candidate_primaries_are_valid(tmp_path):
    """Only multiple ACTIVE offers per role are forbidden; several candidate
    primaries are a legitimate research state."""
    payload = _valid_payload()
    payload["products"][0]["offers"] = [
        candidate_offer(offer_id="cand-1"),
        candidate_offer(offer_id="cand-2"),
    ]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert len(catalog.products[0].offers) == 2


def test_active_plus_null_candidate_same_role_is_valid(tmp_path):
    payload = _valid_payload()
    payload["products"][0]["offers"] = [
        offer(offer_id="primary-1", affiliate_url_env="TAG_A"),
        candidate_offer(offer_id="primary-2"),
    ]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert len(catalog.products[0].offers) == 2


# ── Malformed -> None (fail-closed) ────────────────────────────────────────


def test_missing_file_is_none(tmp_path):
    assert monetization_config.load(tmp_path / "config" / "affiliate_offers.json") is None


def test_invalid_json_is_none(tmp_path):
    path = write_raw_config(tmp_path, "not json {")
    assert monetization_config.load(path) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"extra": 1}),
        lambda payload: payload.__setitem__("schema_version", 2),
        lambda payload: payload.__setitem__("products", {}),
        lambda payload: payload.__setitem__("products", [{"product_id": PRODUCT_ID_MOCHILA}]),
        lambda payload: payload["products"].__setitem__(0, {"product_id": PRODUCT_ID_MOCHILA, "market": "PT", "approved": True, "offers": [], "extra": 1}),
        lambda payload: payload["products"][0].__setitem__("product_id", "short"),
        lambda payload: payload["products"].append(payload["products"][0]),  # duplicate product_id
        lambda payload: payload["products"][0].__setitem__("market", "   "),
        lambda payload: payload["products"][0].__setitem__("approved", "yes"),
        lambda payload: payload["products"][0].__setitem__("offers", {}),
        lambda payload: payload["products"][0]["offers"].__setitem__(0, {"offer_id": "o1"}),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("offer_id", "has space"),
        lambda payload: payload["products"][0]["offers"].append(payload["products"][0]["offers"][0]),  # duplicate offer_id
        lambda payload: payload["products"][0]["offers"][0].__setitem__("role", "secondary"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("merchant", ""),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("status", "live"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_type", "per-click"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_value", 0),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_value", -1.5),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_value", float("inf")),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_value", True),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_currency", "eur"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("commission_currency", "EU"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("cookie_duration_days", 0),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("cookie_duration_days", 30.5),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("payout_eligibility", " "),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("shipping_markets", []),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("shipping_markets", ["PT", ""]),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("asset_rights", "  "),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("traffic_policy", "\n"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("checked_at", "not-a-date"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("valid_until", "2026-13-40"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("checked_at", "2027-01-01"),  # checked_at > valid_until
        lambda payload: payload["products"][0]["offers"][0].__setitem__("affiliate_url_env", "MY TAG"),
        lambda payload: payload["products"][0]["offers"][0].__setitem__("affiliate_url_env", "1STARTS_WITH_DIGIT"),
        lambda payload: payload["products"][0].__setitem__("market", "DE"),  # market outside shipping_markets
    ],
)
def test_malformed_config_fails_closed(tmp_path, mutate):
    payload = _valid_payload()
    mutate(payload)
    path = write_raw_config(tmp_path, payload)
    assert monetization_config.load(path) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("destination_url", "https://merchant.example/p?utm_source=tracked"),
        ("destination_url", "https://user:pass@merchant.example/p"),
        ("destination_url", "https://merchant.example/p#fragment"),
        ("destination_url", "ftp://merchant.example/p"),
        ("destination_url", "https://merchant.example:99999/p"),
        ("destination_url", "https://merchant.example/p\x07"),
        ("evidence_url", "https://merchant.example/evidence?ref=abc"),
        ("evidence_url", "https://user@merchant.example/evidence"),
        ("evidence_url", "https://merchant.example/evidence#top"),
    ],
)
def test_url_rules_fail_closed(tmp_path, field, value):
    payload = _valid_payload()
    payload["products"][0]["offers"][0][field] = value
    path = write_raw_config(tmp_path, payload)
    assert monetization_config.load(path) is None


def test_ipv6_url_keeps_brackets_when_canonicalized(tmp_path):
    payload = _valid_payload()
    url = "https://[2001:4860:4860::8888]:8443/product"
    payload["products"][0]["offers"][0]["destination_url"] = url
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert catalog.products[0].offers[0].destination_url == url


def test_duplicate_json_keys_fail_closed(tmp_path):
    payload = json.dumps(_valid_payload(), ensure_ascii=False)
    payload = payload.replace(
        '"affiliate_url_env": "MOCHILA_AFFILIATE_TAG"',
        '"affiliate_url_env": "SAFE_TAG", "affiliate_url_env": "OVERRIDE_TAG"',
    )
    path = tmp_path / "affiliate_offers.json"
    path.write_text(payload, encoding="utf-8")
    assert monetization_config.load(path) is None


def test_fixed_commission_without_currency_fails_closed(tmp_path):
    payload = _valid_payload()
    payload["products"][0]["offers"][0] = offer(commission_type="fixed", commission_value=2.5, commission_currency=None)
    path = write_raw_config(tmp_path, payload)
    assert monetization_config.load(path) is None


def test_percentage_offer_with_currency_fails_closed(tmp_path):
    """A present percentage commission must carry a null currency (cross-field
    rule, also under the legacy active schema)."""
    payload = _valid_payload()
    payload["products"][0]["offers"][0]["commission_currency"] = "USD"
    assert monetization_config.load(write_raw_config(tmp_path, payload)) is None


def test_two_active_offers_same_role_fails_closed(tmp_path):
    payload = _valid_payload()
    payload["products"][0]["offers"] = [
        offer(offer_id="primary-1", role="primary", affiliate_url_env="TAG_A"),
        offer(offer_id="primary-2", role="primary", affiliate_url_env="TAG_B"),
    ]
    path = write_raw_config(tmp_path, payload)
    assert monetization_config.load(path) is None


def test_active_plus_candidate_same_role_is_valid(tmp_path):
    """At most one ACTIVE per role: a candidate sibling does not violate it."""
    payload = _valid_payload()
    payload["products"][0]["offers"] = [
        offer(offer_id="primary-1", role="primary", affiliate_url_env="TAG_A"),
        offer(offer_id="primary-2", role="primary", affiliate_url_env="TAG_B", status="candidate"),
    ]
    catalog = write_affiliate_config(tmp_path, payload["products"])
    assert catalog is not None
    assert len(catalog.products[0].offers) == 2


def test_duplicate_offer_id_across_products_fails_closed(tmp_path):
    payload = _valid_payload()
    second = product_config(product_id="be385ea0e349")
    second["offers"][0]["offer_id"] = "mochila-pt-primary"  # collides with first product
    payload["products"].append(second)
    path = write_raw_config(tmp_path, payload)
    assert monetization_config.load(path) is None


def test_load_never_raises_and_leaks_nothing(tmp_path, caplog):
    """Fail-closed is silent: the only output is one sanitized log line, and
    the malformed payload contents never surface in logs."""
    path = write_raw_config(tmp_path, {"schema_version": 1, "products": [{"secret_marker": "LEAK_ME_123"}]})
    with caplog.at_level(logging.WARNING, logger="monetization.config"):
        result = monetization_config.load(path)
    assert result is None
    assert "LEAK_ME_123" not in caplog.text
    assert "monetização desativada" in caplog.text
