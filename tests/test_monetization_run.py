"""Run integration for the monetization gate (Part B wiring).

``run_keyword`` attaches ``result["monetization"]`` after source states are
computed and passes it to ``pack.generate`` only when eligible; the score and
run identity never change.  Missing/invalid config is a no-op (disabled).
Affiliate payloads never reach run manifests, digests or notification
content — packs are the only carrier in this slice.
"""

from __future__ import annotations

import json

import run as run_module
from pipeline.scoring import score
from tests.fixtures.ads_helpers import classified_ads_data
from tests.fixtures.monetization_helpers import write_affiliate_config
from tests.fixtures.products_helpers import product, stable_id, write_registry

TRENDS = {"direction": "subida", "slope_pct": 100.0, "seasonal": False, "top_geos": [], "related_queries": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}
CSV = {"volume": 100000, "cpc": 0.5, "related_keywords": []}
ENV_VALUE = "SECRET_VALUE_123"


def _strong_ads():
    """Matched ads with days_active=180 so the score is a solid AVANÇAR.
    Both the candidate list and the top_ads projection carry the long
    longevity so a direct ``score.compute`` and the run's re-classification
    agree."""
    candidate = classified_ads_data()["candidates"][0]
    candidate["days_active"] = 180
    return classified_ads_data(candidates=[candidate], top_ads=[candidate])


def _install_run_stack(monkeypatch, ads_result=None):
    """Positive matched-ads stack; returns the list of pack.generate kwargs."""
    generated = []
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda keyword: dict(ads_result or _strong_ads()))
    monkeypatch.setattr(run_module.trends, "fetch", lambda keyword: dict(TRENDS))
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: dict(SEO))
    monkeypatch.setattr(
        run_module.landing_price, "fetch_prices_from_ads", lambda data: {},
    )
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: dict(CSV))
    monkeypatch.setattr(run_module.pack, "generate",
                        lambda **kwargs: generated.append(kwargs) or "pack.md")
    return generated


def _eligible_config():
    return [{
        "product_id": stable_id("mochila personalizada"),
        "market": "PT",
        "approved": True,
        "offers": [{
            "offer_id": "mochila-pt-primary",
            "role": "primary",
            "merchant": "Merchant X",
            "status": "active",
            "destination_url": "https://merchant.example/mochila-personalizada",
            "affiliate_url_env": "MOCHILA_AFFILIATE_TAG",
            "commission_type": "percentage",
            "commission_value": 5.0,
            "commission_currency": None,
            "cookie_duration_days": 30,
            "payout_eligibility": "60 dias após confirmação de venda",
            "shipping_markets": ["PT"],
            "asset_rights": "imagens fornecidas pelo merchant",
            "traffic_policy": "sem branded keywords",
            "evidence_url": "https://merchant.example/affiliate-program",
            "checked_at": "2026-08-24",
            "valid_until": "2030-12-31",
        }],
    }]


def test_missing_config_causes_no_regression(monkeypatch, tmp_path):
    """Missing ``config/affiliate_offers.json``: monetization disabled (None),
    the research output stays byte-identical to the plain score."""
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))  # no config dir/file
    generated = _install_run_stack(monkeypatch)
    result = run_module.run_keyword(product("mochila personalizada"))
    expected = score.compute(TRENDS, CSV, _strong_ads())
    assert result["score_result"] == expected
    assert result["monetization"] is None
    assert generated[0]["monetization"] is None


def test_malformed_config_disables_without_breaking_run(monkeypatch, tmp_path):
    """Malformed config (duplicate active primaries) fails closed to None and
    the research run completes normally."""
    from tests.fixtures.monetization_helpers import write_raw_config

    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    write_raw_config(tmp_path, {
        "schema_version": 1,
        "products": [{
            "product_id": stable_id("mochila personalizada"),
            "market": "PT", "approved": True,
            "offers": [
                {"offer_id": "p1", "role": "primary", "merchant": "M", "status": "active",
                 "destination_url": "https://a.example/p", "affiliate_url_env": "A",
                 "commission_type": "percentage", "commission_value": 5.0,
                 "commission_currency": None, "cookie_duration_days": 30,
                 "payout_eligibility": "p", "shipping_markets": ["PT"],
                 "asset_rights": "a", "traffic_policy": "t",
                 "evidence_url": "https://a.example/e", "checked_at": "2026-01-01",
                 "valid_until": "2030-12-31"},
                {"offer_id": "p2", "role": "primary", "merchant": "M", "status": "active",
                 "destination_url": "https://b.example/p", "affiliate_url_env": "B",
                 "commission_type": "percentage", "commission_value": 5.0,
                 "commission_currency": None, "cookie_duration_days": 30,
                 "payout_eligibility": "p", "shipping_markets": ["PT"],
                 "asset_rights": "a", "traffic_policy": "t",
                 "evidence_url": "https://b.example/e", "checked_at": "2026-01-01",
                 "valid_until": "2030-12-31"},
            ],
        }],
    })
    generated = _install_run_stack(monkeypatch)
    result = run_module.run_keyword(product("mochila personalizada"))
    expected = score.compute(TRENDS, CSV, _strong_ads())
    assert result["score_result"] == expected
    assert result["monetization"] is None
    assert generated[0]["monetization"] is None


def test_eligible_run_attaches_monetization_without_touching_score(monkeypatch, tmp_path):
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    write_affiliate_config(tmp_path, _eligible_config())
    monkeypatch.setenv("MOCHILA_AFFILIATE_TAG", ENV_VALUE)
    generated = _install_run_stack(monkeypatch)
    result = run_module.run_keyword(product("mochila personalizada"))

    expected = score.compute(TRENDS, CSV, _strong_ads())
    assert result["score_result"]["label"] == "AVANÇAR"
    assert result["score_result"] == expected
    monetization = result["monetization"]
    assert monetization is not None
    assert monetization["market"] == "PT"
    assert monetization["offers"][0]["role"] == "primary"
    assert monetization["offers"][0]["affiliate_link_ref"] == "MOCHILA_AFFILIATE_TAG"
    # The tracked env VALUE never leaks into the result payload.
    assert ENV_VALUE not in json.dumps(result, ensure_ascii=False)
    # pack.generate received exactly the same emission, and the score_result
    # handed to the pack is unchanged.
    assert generated[0]["monetization"] is monetization
    assert generated[0]["score_result"] == expected


def test_candidates_only_config_keeps_output_identical_to_disabled(monkeypatch, tmp_path):
    """A candidates-only config (no active offer, nulls everywhere) behaves
    exactly like the missing config: monetization stays None and the research
    output is unchanged (pack/run unchanged when candidates only)."""
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    _install_run_stack(monkeypatch)
    baseline = run_module.run_keyword(product("mochila personalizada"))

    write_affiliate_config(tmp_path, [{
        "product_id": stable_id("mochila personalizada"),
        "market": "PT", "approved": True,
        "offers": [{
            "offer_id": "cand-1", "role": "primary", "merchant": "M",
            "status": "candidate",
            "destination_url": None, "affiliate_url_env": None,
            "commission_type": None, "commission_value": None,
            "commission_currency": None, "cookie_duration_days": None,
            "payout_eligibility": None, "shipping_markets": None,
            "asset_rights": None, "traffic_policy": None,
            "evidence_url": None, "checked_at": None, "valid_until": None,
        }],
    }])
    _install_run_stack(monkeypatch)
    with_config = run_module.run_keyword(product("mochila personalizada"))
    for key in ("score_result", "trends_data", "csv_data", "ads_data",
                "seo_data", "landing_price_data", "suggested_keywords", "md_path"):
        assert with_config[key] == baseline[key], key
    assert with_config["monetization"] is None


def test_monetization_configuration_never_changes_research_output(monkeypatch, tmp_path):
    """Running the same keyword with and without an eligible monetization
    config yields identical research output; only the additive key differs."""
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    _install_run_stack(monkeypatch)
    baseline = run_module.run_keyword(product("mochila personalizada"))

    write_affiliate_config(tmp_path, _eligible_config())
    monkeypatch.setenv("MOCHILA_AFFILIATE_TAG", ENV_VALUE)
    _install_run_stack(monkeypatch)
    with_config = run_module.run_keyword(product("mochila personalizada"))

    for key in ("score_result", "trends_data", "csv_data", "ads_data",
                "seo_data", "landing_price_data", "suggested_keywords", "md_path"):
        assert with_config[key] == baseline[key], key
    assert baseline["monetization"] is None
    assert with_config["monetization"] is not None


def test_finish_manifest_never_stores_monetization(monkeypatch, tmp_path):
    """Even the full-result (compact=False) manifest path strips the
    monetization payload — affiliate metadata/URLs are pack-only."""
    result = {
        "keyword": "mochila personalizada",
        "monetization": {
            "disclosure": "D",
            "market": "PT",
            "offers": [{"destination_url": "https://merchant.example/x"}],
        },
    }
    manifest = run_module._begin_manifest("sunday", "run123", [product("mochila personalizada")], None)
    finished = run_module._finish_manifest(
        manifest, [result], "digest", {"status": "skipped", "schema_version": 2, "items_total": 0, "items_sent": 0, "errors": []},
        compact=False,
    )
    stored = finished["results"][0]
    assert "monetization" not in stored
    blob = json.dumps(finished, ensure_ascii=False)
    assert "merchant.example" not in blob
    assert "monetization" not in blob


def test_batch_manifest_and_digest_never_carry_affiliate_data(monkeypatch, tmp_path, capsys):
    """End-to-end Sunday batch with eligible monetization: the run manifest
    (checkpoint + final) and the stdout digest never contain the affiliate
    payload, its URLs or the tracked env VALUE."""
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    write_registry(tmp_path, ["mochila personalizada"])
    write_affiliate_config(tmp_path, _eligible_config())
    monkeypatch.setenv("MOCHILA_AFFILIATE_TAG", ENV_VALUE)
    _install_run_stack(monkeypatch)

    results = run_module._run_sunday(send=False)

    assert len(results) == 1
    assert results[0]["monetization"] is not None
    assert results[0]["monetization"]["offers"][0]["destination_url"] == "https://merchant.example/mochila-personalizada"

    manifests = [json.loads(p.read_text(encoding="utf-8")) for p in (tmp_path / "data" / "runs").glob("*.json")]
    assert len(manifests) == 1
    blob = json.dumps(manifests[0], ensure_ascii=False)
    assert "monetization" not in blob
    assert "merchant.example" not in blob
    assert ENV_VALUE not in blob
    assert "affiliate_link_ref" not in blob

    digest = capsys.readouterr().out
    assert "merchant.example" not in digest
    assert "MOCHILA_AFFILIATE_TAG" not in digest
    assert ENV_VALUE not in digest
