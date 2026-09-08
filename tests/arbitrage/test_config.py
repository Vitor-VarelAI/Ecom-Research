from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.arbitrage import ArbitrageConfigError, load_config
from pipeline.arbitrage.models import (
    COMPETITION_KEYS,
    CREATIVE_SCORE_KEYS,
    ECONOMICS_KEYS,
    EVIDENCE_KEYS,
    FACT_KEYS,
    FX_RATE_KEYS,
    LANDING_KEYS,
    LINK_KEYS,
    META_STRUCTURE_KEYS,
    MONETIZATION_MODELS,
    OFFER_KEYS,
    PERMISSIONS_KEYS,
    PERMISSION_STATES,
    PRODUCT_TYPES,
    TOP_LEVEL_KEYS,
    TRENDS_KEYS,
    US_READINESS_KEYS,
)


def _write(tmp_path: Path, payload: str | dict) -> Path:
    path = tmp_path / "offers.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _evidence(source_ref: str, *, url: str | None = "https://source.example/page") -> dict:
    return {
        "source_ref": source_ref,
        "source_type": "web",
        "url": url,
        "stable_ref": None if url else "pack-2026-09-07",
        "observed_at": "2026-09-07",
        "captured_at": "2026-09-07T12:00:00Z",
        "pack_id": "pack-2026-09-07",
        "content_sha256": "a" * 64,
        "excerpt": "Observed from the cited source.",
    }


def _fact(value, *source_refs: str, status: str = "KNOWN") -> dict:
    return {"value": value, "status": status, "source_refs": list(source_refs)}


def _null_fields(fields: set[str]) -> dict:
    return {field: None for field in sorted(fields)}


def _offer(
    *,
    offer_id: str = "offer-demo-1",
    monetization_model: str = "affiliate",
    evidence: list[dict] | None = None,
) -> dict:
    refs = ["src-1"]
    evidence = evidence or [_evidence("src-1")]
    return {
        "offer_id": offer_id,
        "name": "Demo offer",
        "brand": "Demo brand",
        "market_id": "US",
        "research_keyword": "demo product",
        "enabled": True,
        "monetization_model": monetization_model,
        "product_type": "physical",
        "links": {
            "program_url": _fact("https://partner.example/program", *refs),
            "offer_url": _fact("https://brand.example/offer", *refs),
            "landing_url": _fact("https://brand.example/offer/landing", *refs),
            "ad_links": _fact(["https://ads.example/ad-1"], *refs),
        },
        "economics": {
            "retail_price": _fact(49.0, *refs),
            "aov": _fact(54.0, *refs),
            "affiliate_commission_pct": _fact(20, *refs),
            "cpa_payout": _fact(18.0, *refs),
            "expected_payout_per_order": _fact(16.0, *refs),
            "recurring_commission": _fact(5.0, *refs),
            "cookie_days": _fact(30, *refs),
            "wholesale_cost": _fact(12.0, *refs),
            "gross_spread": _fact(37.0, *refs),
            "monthly_fees": _fact(0.0, *refs),
            "minimum_order": _fact(1, *refs),
            "refund_rate": _fact(4.5, *refs),
            "refund_policy": _fact("30-day returns accepted.", *refs),
            "payment_fees": _fact(2.0, *refs),
            "fulfilment_cost": _fact(6.0, *refs),
            "shipping_cost": _fact(4.0, *refs),
            "expected_return_cost": _fact(3.0, *refs),
            "other_variable_cost": _fact(1.0, *refs),
            "expected_cpa": _fact(10.0, *refs),
            "currency": _fact("USD", *refs),
        },
        "permissions": {
            field: _fact("YES", *refs)
            for field in sorted(PERMISSIONS_KEYS)
        },
        "us_readiness": {
            "us_warehouse": _fact(True, *refs),
            "us_fulfilment": _fact(True, *refs),
            "delivery_min_days": _fact(3, *refs),
            "delivery_max_days": _fact(7, *refs),
            "us_returns_address": _fact("123 Main St, Austin, TX", *refs),
            "shipping_cost": _fact(4.0, *refs),
            "checkout_in_usd": _fact(True, *refs),
        },
        "landing": {
            "functional_score": _fact(9, *refs),
            "quality_score": _fact(8.5, *refs),
        },
        "creative_scores": {
            field: _fact(7, *refs)
            for field in sorted(CREATIVE_SCORE_KEYS)
        },
        "meta_structure": {
            "advertiser_count": _fact(4, *refs),
            "active_ads": _fact(20, *refs),
            "oldest_ad_days": _fact(48, *refs),
            "ads_older_than_30d": _fact(8, *refs),
            "creative_concept_count": _fact(5, *refs),
            "variants_per_concept": _fact(4, *refs),
            "video_share": _fact(60, *refs),
            "image_share": _fact(30, *refs),
            "carousel_share": _fact(10, *refs),
            "ugc_share": _fact(70, *refs),
            "polished_share": _fact(30, *refs),
            "hooks": _fact(["problem", "demo"], *refs),
            "observed_offers": _fact(["20% off"], *refs),
            "landing_pages": _fact(["https://brand.example/offer/landing"], *refs),
            "ad_links": _fact(["https://ads.example/ad-1"], *refs),
        },
        "trends": {"demand_score": _fact(8, *refs)},
        "competition": {"structure_score": _fact(6, *refs)},
        "evidence": evidence,
    }


def _config(*offers: dict, fx_rates: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "base_currency": "EUR",
        "fx_rates": fx_rates or [],
        "offers": list(offers),
    }


def _error(tmp_path: Path, payload: str | dict) -> str:
    with pytest.raises(ArbitrageConfigError) as caught:
        load_config(_write(tmp_path, payload))
    return str(caught.value)


def test_committed_config_is_valid_and_empty():
    config = load_config(Path(__file__).parents[2] / "config" / "offers.json")
    assert config.schema_version == 1
    assert config.base_currency == "EUR"
    assert config.fx_rates == ()
    assert config.offers == ()


def test_complete_representative_offer_loads_immutably(tmp_path):
    payload = _config(
        _offer(),
        fx_rates=[
            {
                "currency": "USD",
                "eur_per_unit": 0.92,
                "source_ref": "fx-usd-1",
                "observed_at": "2026-09-07",
            }
        ],
    )
    config = load_config(_write(tmp_path, payload))
    offer = config.offers[0]

    assert offer.offer_id == "offer-demo-1"
    assert offer.links.program_url.value == "https://partner.example/program"
    assert offer.links.ad_links.value == ("https://ads.example/ad-1",)
    assert offer.meta_structure.hooks.value == ("problem", "demo")
    assert offer.evidence_registry[0].source_ref == "src-1"
    assert config.fx_rates[0].eur_per_unit == 0.92
    with pytest.raises((AttributeError, TypeError)):
        offer.enabled = False
    with pytest.raises(AttributeError):
        offer.evidence[0].source_ref = "other"


def test_every_monetization_model_is_accepted(tmp_path):
    for index, model in enumerate(MONETIZATION_MODELS):
        config = load_config(_write(tmp_path, _config(_offer(offer_id=f"offer-{index}", monetization_model=model))))
        assert config.offers[0].monetization_model == model


def test_exact_field_inventory_is_pinned():
    payload = _config(_offer())
    offer = payload["offers"][0]
    assert set(payload) == TOP_LEVEL_KEYS
    assert set(payload["fx_rates"][0]) == FX_RATE_KEYS if payload["fx_rates"] else True
    assert set(offer) == OFFER_KEYS
    assert set(offer["links"]) == LINK_KEYS
    assert set(offer["economics"]) == ECONOMICS_KEYS
    assert set(offer["permissions"]) == PERMISSIONS_KEYS
    assert set(offer["us_readiness"]) == US_READINESS_KEYS
    assert set(offer["landing"]) == LANDING_KEYS
    assert set(offer["creative_scores"]) == CREATIVE_SCORE_KEYS
    assert set(offer["meta_structure"]) == META_STRUCTURE_KEYS
    assert set(offer["trends"]) == TRENDS_KEYS
    assert set(offer["competition"]) == COMPETITION_KEYS
    assert set(offer["evidence"][0]) == EVIDENCE_KEYS
    assert set(offer["links"]["program_url"]) == FACT_KEYS


def test_null_unknowns_have_no_evidence_and_contradiction_is_explicit(tmp_path):
    offer = _offer(evidence=[_evidence("src-1"), _evidence("src-2")])
    offer["economics"] = _null_fields(ECONOMICS_KEYS)
    offer["economics"]["retail_price"] = _fact(
        None, "src-1", "src-2", status="CONTRADICTED"
    )
    config = load_config(_write(tmp_path, _config(offer)))
    assert config.offers[0].economics.aov is None
    assert config.offers[0].economics.refund_policy is None
    assert config.offers[0].economics.retail_price.status == "CONTRADICTED"
    assert config.offers[0].economics.retail_price.value is None


def test_unknown_values_remain_null_without_source_refs(tmp_path):
    offer = _offer()
    offer["links"]["landing_url"] = None
    config = load_config(_write(tmp_path, _config(offer)))
    assert config.offers[0].links.landing_url is None


@pytest.mark.parametrize("product_type", PRODUCT_TYPES)
def test_every_product_type_is_accepted(tmp_path, product_type):
    offer = _offer()
    offer["product_type"] = product_type
    assert load_config(_write(tmp_path, _config(offer))).offers[0].product_type == product_type


@pytest.mark.parametrize(
    "bad",
    [
        {"schema_version": 2, "base_currency": "EUR", "fx_rates": [], "offers": []},
        {"schema_version": 1, "base_currency": "USD", "fx_rates": [], "offers": []},
        {"schema_version": 1, "base_currency": "EUR", "fx_rates": {}, "offers": []},
        {"schema_version": 1, "base_currency": "EUR", "fx_rates": [], "offers": {}, "extra": 1},
    ],
)
def test_top_level_shape_is_strict(tmp_path, bad):
    assert "invalid arbitrage config" in _error(tmp_path, bad)


def test_unknown_keys_and_duplicate_ids_fail_closed(tmp_path):
    offer = _offer()
    offer["unexpected"] = None
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))
    assert "invalid arbitrage config" in _error(
        tmp_path, _config(_offer(), _offer(offer_id="offer-demo-1"))
    )
    duplicated_evidence = [_evidence("src-1"), _evidence("src-1")]
    assert "invalid arbitrage config" in _error(
        tmp_path, _config(_offer(evidence=duplicated_evidence))
    )
    duplicate_fx = [
        {
            "currency": "USD",
            "eur_per_unit": 0.92,
            "source_ref": "fx-usd-1",
            "observed_at": "2026-09-07",
        },
        {
            "currency": "USD",
            "eur_per_unit": 0.91,
            "source_ref": "fx-usd-2",
            "observed_at": "2026-09-07",
        },
    ]
    assert "invalid arbitrage config" in _error(
        tmp_path, _config(_offer(), fx_rates=duplicate_fx)
    )


def test_duplicate_json_keys_are_rejected(tmp_path):
    raw = '{"schema_version":1,"base_currency":"EUR","fx_rates":[],"offers":[],"offers":[]}'
    assert "JSON" in _error(tmp_path, raw)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda offer: offer["economics"].update(
            {"affiliate_commission_pct": _fact(101, "src-1")}
        ),
        lambda offer: offer["landing"].update({"quality_score": _fact(11, "src-1")}),
        lambda offer: offer["meta_structure"].update(
            {"video_share": _fact(80, "src-1"), "image_share": _fact(30, "src-1")}
        ),
        lambda offer: offer["us_readiness"].update(
            {
                "delivery_min_days": _fact(8, "src-1"),
                "delivery_max_days": _fact(3, "src-1"),
            }
        ),
    ],
)
def test_invalid_ranges_and_cross_field_states_fail(tmp_path, mutate):
    offer = _offer()
    mutate(offer)
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


@pytest.mark.parametrize(
    "field,value",
    [
        ("monetization_model", "unknown"),
        ("market_id", "not an id"),
        ("research_keyword", ""),
    ],
)
def test_invalid_identity_fields_fail(tmp_path, field, value):
    offer = _offer()
    offer[field] = value
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://brand.example/offer",
        "https://user:pass@brand.example/offer",
        "https://brand.example/offer?token=secret",
        "https://brand.example/offer?",
        "https://brand.example/offer#fragment",
        "https://localhost/offer",
        "https://127.0.0.1/offer",
        "https://brand.example/offer\nsecret",
    ],
)
def test_malformed_urls_fail_closed(tmp_path, url):
    offer = _offer()
    offer["links"]["offer_url"] = _fact(url, "src-1")
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


def test_urls_are_sanitized_to_public_canonical_form(tmp_path):
    offer = _offer()
    offer["links"]["offer_url"] = _fact("HTTPS://BRAND.EXAMPLE/offer///", "src-1")
    config = load_config(_write(tmp_path, _config(offer)))
    assert config.offers[0].links.offer_url.value == "https://brand.example/offer"


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_at", "07-09-2026"),
        ("captured_at", "2026-09-07 12:00:00"),
        ("content_sha256", "not-a-sha"),
    ],
)
def test_malformed_evidence_fields_fail(tmp_path, field, value):
    offer = _offer()
    offer["evidence"][0][field] = value
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


def test_unresolved_fact_reference_fails(tmp_path):
    offer = _offer()
    offer["links"]["offer_url"] = _fact("https://brand.example/offer", "missing")
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


def test_invalid_fact_shapes_fail(tmp_path):
    offer = _offer()
    offer["links"]["offer_url"] = {
        "value": "https://brand.example/offer",
        "status": "KNOWN",
        "source_refs": [],
    }
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


def test_known_refund_policy_must_be_nonblank_text(tmp_path):
    offer = _offer()
    offer["economics"]["refund_policy"] = _fact("   ", "src-1")
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


def test_invalid_fx_rates_fail(tmp_path):
    offer = _offer()
    bad_rates = [
        {
            "currency": "USD",
            "eur_per_unit": 0,
            "source_ref": "fx-usd-1",
            "observed_at": "2026-09-07",
        }
    ]
    assert "invalid arbitrage config" in _error(
        tmp_path, _config(offer, fx_rates=bad_rates)
    )


@pytest.mark.parametrize(
    "bad_currency",
    ["eur", "EURO", "US", "U$D"],
)
def test_malformed_currencies_fail_closed(tmp_path, bad_currency):
    offer = _offer()
    offer["economics"]["currency"] = _fact(bad_currency, "src-1")
    assert "invalid arbitrage config" in _error(tmp_path, _config(offer))


def test_eur_fx_rate_must_be_identity(tmp_path):
    offer = _offer()
    fx_rates = [
        {
            "currency": "EUR",
            "eur_per_unit": 0.99,
            "source_ref": "fx-eur-1",
            "observed_at": "2026-09-07",
        }
    ]
    assert "invalid arbitrage config" in _error(
        tmp_path, _config(offer, fx_rates=fx_rates)
    )


def test_errors_do_not_leak_file_contents_or_secrets(tmp_path):
    secret = "SECRET_FILE_CONTENT_9f1d"
    path = tmp_path / "offers.json"
    path.write_text(f'{{"broken":"{secret}"', encoding="utf-8")
    with pytest.raises(ArbitrageConfigError) as caught:
        load_config(path)
    assert secret not in str(caught.value)
    assert str(path) not in str(caught.value)
