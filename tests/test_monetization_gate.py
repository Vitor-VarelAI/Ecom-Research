"""Pure gate matrix: eligibility conditions and deterministic emission.

The gate returns None unless *every* condition holds; when it emits, it emits
up to two offers (active primary, then active trust_fallback) with validated
metadata, sanitized public URLs and the env NAME as ``affiliate_link_ref`` —
never the tracked affiliate URL value, never a commission-based ranking.
"""

from __future__ import annotations

from datetime import date

import pytest

from pipeline.monetization import gate
from tests.fixtures.monetization_helpers import (
    PRODUCT_ID_MOCHILA,
    catalog_model,
    offer,
    offer_model,
    product_model,
    write_affiliate_config,
)

TODAY = date(2026, 10, 1)
ENV = {"MOCHILA_AFFILIATE_TAG": "SECRET_VALUE_123"}


def _result(**overrides) -> dict:
    base = {
        "product_id": PRODUCT_ID_MOCHILA,
        "score_result": {"score": 95, "label": "AVANÇAR"},
        "ads_data": {"total_ads": 3, "matched_count": 3, "ad_ids": ["a1"]},
        "csv_data": {"volume": 5400},
    }
    base.update(overrides)
    return base


def _source_states(**overrides) -> dict:
    base = {
        "apify_ads": {"status": "valid", "origin": "fresh"},
        "trends": {"status": "valid", "origin": "fresh"},
        "keyword_csv": {"status": "valid", "origin": "fresh"},
    }
    base.update(overrides)
    return base


def _env_get(name: str) -> str | None:
    return ENV.get(name)


def _evaluate(**overrides):
    kwargs = {
        "result": _result(),
        "source_states": _source_states(),
        "catalog": catalog_model(),
        "today": TODAY,
        "env_get": _env_get,
    }
    kwargs.update(overrides)
    return gate.evaluate(**kwargs)


# ── Happy path ─────────────────────────────────────────────────────────────


def test_eligible_emits_primary_with_validated_bounded_payload():
    emission = _evaluate()
    assert emission is not None
    assert emission["disclosure"]
    assert emission["market"] == "PT"
    assert len(emission["offers"]) == 1
    primary = emission["offers"][0]
    assert primary["role"] == "primary"
    assert primary["offer_id"] == "mochila-pt-primary"
    assert primary["merchant"] == "Merchant X"
    assert primary["commission_type"] == "percentage"
    assert primary["commission_value"] == 5.0
    assert primary["commission_currency"] is None
    assert primary["cookie_duration_days"] == 30
    assert primary["destination_url"] == "https://merchant.example/mochila-personalizada"
    assert primary["evidence_url"] == "https://merchant.example/affiliate-program"
    assert primary["affiliate_link_ref"] == "MOCHILA_AFFILIATE_TAG"


def test_eligible_end_to_end_through_real_loader(tmp_path):
    catalog = write_affiliate_config(tmp_path, [{
        "product_id": PRODUCT_ID_MOCHILA, "market": "PT", "approved": True,
        "offers": [offer()],
    }])
    emission = _evaluate(catalog=catalog)
    assert emission is not None
    assert emission["offers"][0]["affiliate_link_ref"] == "MOCHILA_AFFILIATE_TAG"


def test_both_roles_emit_in_operator_order():
    catalog = catalog_model(products=(
        product_model(offers=(
            offer_model(offer_id="f", role="trust_fallback", affiliate_url_env="MOCHILA_FALLBACK_TAG"),
            offer_model(),  # primary
        )),
    ))
    env = {"MOCHILA_AFFILIATE_TAG": "v1", "MOCHILA_FALLBACK_TAG": "v2"}
    emission = _evaluate(catalog=catalog, env_get=env.get)
    assert [offer["role"] for offer in emission["offers"]] == ["primary", "trust_fallback"]
    assert emission["offers"][0]["offer_id"] == "mochila-pt-primary"
    assert emission["offers"][1]["offer_id"] == "f"


def test_fallback_only_emits_fallback():
    catalog = catalog_model(products=(
        product_model(offers=(
            offer_model(offer_id="f", role="trust_fallback", affiliate_url_env="MOCHILA_FALLBACK_TAG"),
        )),
    ))
    emission = _evaluate(catalog=catalog, env_get={"MOCHILA_FALLBACK_TAG": "v2"}.get)
    assert emission is not None
    assert [offer["role"] for offer in emission["offers"]] == ["trust_fallback"]


def test_emission_is_deterministic():
    first = _evaluate()
    second = _evaluate()
    assert first == second


# ── Score / completeness ────────────────────────────────────────────────────


@pytest.mark.parametrize("label", ["OBSERVAR", "DESCARTAR", "DADOS INSUFICIENTES"])
def test_non_avançar_label_is_none(label):
    assert _evaluate(result=_result(score_result={"score": 50, "label": label})) is None


def test_incomplete_result_is_none():
    assert _evaluate(result=_result(score_result={"score": 95, "label": "AVANÇAR", "incomplete": True})) is None


def test_missing_score_result_is_none():
    assert _evaluate(result={"product_id": PRODUCT_ID_MOCHILA, "ads_data": {}, "csv_data": {}}) is None


# ── Critical sources ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["apify_ads", "trends", "keyword_csv"])
def test_invalid_critical_source_is_none(name):
    states = _source_states(**{name: {"status": "failed", "origin": "error"}})
    assert _evaluate(source_states=states) is None


def test_missing_critical_source_state_is_none():
    states = _source_states()
    del states["keyword_csv"]
    assert _evaluate(source_states=states) is None


# ── Ads evidence ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("ads", [
    {"total_ads": 0, "matched_count": 0},
    {"total_ads": 3, "matched_count": 0},
    {"error": "Apify offline"},
    {},
    {"total_ads": "3", "matched_count": 3},
    {"total_ads": 3, "matched_count": True},
])
def test_no_matched_ads_is_none(ads):
    assert _evaluate(result=_result(ads_data=ads)) is None


# ── CSV volume ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("csv_data", [
    {"error": "Keyword 'x' não encontrada no CSV"},
    {},
    {"volume": None},
    {"volume": -1},
    {"volume": float("inf")},
    {"volume": True},
])
def test_bad_volume_is_none(csv_data):
    assert _evaluate(result=_result(csv_data=csv_data)) is None


def test_zero_volume_is_acceptable():
    assert _evaluate(result=_result(csv_data={"volume": 0})) is not None


# ── Catalog / product eligibility ───────────────────────────────────────────


def test_no_catalog_is_none():
    assert _evaluate(catalog=None) is None


def test_empty_catalog_is_none():
    assert _evaluate(catalog=catalog_model(products=())) is None


def test_unknown_product_id_is_none():
    assert _evaluate(result=_result(product_id="be385ea0e349")) is None


def test_unapproved_product_is_none():
    catalog = catalog_model(products=(product_model(approved=False),))
    assert _evaluate(catalog=catalog) is None


# ── Offer-level eligibility ─────────────────────────────────────────────────


def test_no_active_offer_is_none():
    catalog = catalog_model(products=(product_model(offers=(offer_model(status="candidate"),)),))
    assert _evaluate(catalog=catalog) is None


def test_expired_offer_is_none():
    catalog = catalog_model(products=(product_model(offers=(offer_model(valid_until=date(2026, 9, 30)),)),))
    assert _evaluate(catalog=catalog) is None


def test_valid_until_equals_today_is_acceptable():
    catalog = catalog_model(products=(product_model(offers=(offer_model(valid_until=TODAY),)),))
    assert _evaluate(catalog=catalog) is not None


@pytest.mark.parametrize("env_value", [None, "", "   "])
def test_missing_or_empty_env_is_none(env_value):
    env = {"MOCHILA_AFFILIATE_TAG": env_value}
    assert _evaluate(env_get=env.get) is None


def test_mixed_active_and_inactive_emits_only_active():
    catalog = catalog_model(products=(product_model(offers=(
        offer_model(offer_id="cand", status="candidate", affiliate_url_env="CAND_TAG"),
        offer_model(offer_id="exp", status="active", valid_until=date(2026, 9, 1), affiliate_url_env="EXP_TAG"),
        offer_model(offer_id="env-missing", status="active", affiliate_url_env="UNSET_TAG"),
        offer_model(),  # the only eligible one
    )),))
    emission = _evaluate(catalog=catalog)
    assert emission is not None
    assert [offer["offer_id"] for offer in emission["offers"]] == ["mochila-pt-primary"]


# ── No env value leakage, no ranking by commission ─────────────────────────


def test_env_value_never_leaks_into_emission():
    emission = _evaluate()
    assert "SECRET_VALUE_123" not in str(emission)
    # Only the NAME travels, as affiliate_link_ref.
    assert emission["offers"][0]["affiliate_link_ref"] == "MOCHILA_AFFILIATE_TAG"


def test_higher_commission_fallback_never_outranks_primary():
    """Role is operator-chosen: a fallback with a higher commission cannot
    displace the primary, and no ranking by commission exists."""
    catalog = catalog_model(products=(product_model(offers=(
        offer_model(offer_id="p", role="primary", commission_value=1.0),
        offer_model(offer_id="f", role="trust_fallback", commission_value=99.0,
                    affiliate_url_env="MOCHILA_FALLBACK_TAG"),
    )),))
    env = {"MOCHILA_AFFILIATE_TAG": "v1", "MOCHILA_FALLBACK_TAG": "v2"}
    emission = _evaluate(catalog=catalog, env_get=env.get)
    assert [offer["role"] for offer in emission["offers"]] == ["primary", "trust_fallback"]
    assert emission["offers"][0]["offer_id"] == "p"
