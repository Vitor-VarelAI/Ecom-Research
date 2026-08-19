"""Source state classification: valid/missing/stale/failed and provenance policies."""

from __future__ import annotations

from pipeline import provenance
from pipeline.adapters import trends
from tests.fixtures.products_helpers import product
import run as run_module

ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 3, "ad_ids": ["a1"],
       "top_ads": [], "advertisers": [], "saturation": "baixa"}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}
LANDING = {"price_range": {"min": 10, "max": 20}}


def test_provenance_source_state_classification():
    assert provenance.source_state(origin="fresh", reason="x")["status"] == "valid"
    assert provenance.source_state(origin="reused", reason="x")["status"] == "stale"
    assert provenance.source_state(origin="not_attempted", reason="x")["status"] == "missing"
    assert provenance.source_state(origin="error", reason="x")["status"] == "failed"
    forced = provenance.source_state(origin="not_attempted", reason="csv ausente", force_status="missing")
    assert forced["status"] == "missing"


def test_policies_are_credential_free_and_snapshot_is_isolated():
    policies = provenance.snapshot()
    assert policies["apify_ads"]["retries"] == 0
    assert policies["trends"]["ttl_days"] == 30
    assert policies["trends"]["timeout_s"] == trends.REQUEST_TIMEOUT == 60
    assert policies["landing_price"]["max_fetches"] == 3
    serialized = str(policies)
    assert "token" not in serialized and "Bearer" not in serialized and "http" not in serialized.replace("timeout", "").replace("url", "")


def test_full_run_all_sources_valid():
    result = {"ads_data": ADS, "trends_data": TRENDS, "csv_data": CSV, "seo_data": SEO,
              "landing_price_data": LANDING, "suggested_keywords": [{"keyword": "x", "status": "proposed"}]}
    states = run_module._source_states("keyword", result)
    assert states["apify_ads"]["status"] == "valid"
    assert states["trends"]["status"] == "valid"
    assert states["keyword_csv"]["status"] == "valid"
    assert states["serp_seo"]["status"] == "valid"
    assert states["landing_price"]["status"] == "valid"
    assert states["suggestions"]["status"] == "valid"
    assert states["ad_cohort"]["status"] == "missing"


def test_zero_ad_apify_result_is_valid():
    zero = {**ADS, "total_ads": 0, "ad_ids": [], "top_ads": [], "advertisers": []}
    result = {"ads_data": zero, "trends_data": TRENDS, "csv_data": CSV, "seo_data": SEO, "landing_price_data": None}
    states = run_module._source_states("keyword", result)
    assert states["apify_ads"]["status"] == "valid"
    assert states["landing_price"]["status"] == "missing"


def test_failed_apify_is_failed_and_skipped_sources_are_missing():
    result = {"ads_data": {"error": "Apify offline"}, "trends_data": {"error": "SerpAPI não executada — modo sem rede"},
              "csv_data": CSV, "seo_data": {"error": "SEO não executado — modo sem rede"}, "landing_price_data": None}
    states = run_module._source_states("keyword", result)
    assert states["apify_ads"]["status"] == "failed"
    assert states["trends"]["status"] == "missing"
    assert states["serp_seo"]["status"] == "missing"


def test_missing_csv_is_missing_not_failed():
    result = {"ads_data": ADS, "trends_data": TRENDS,
              "csv_data": {"error": "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner"},
              "seo_data": SEO, "landing_price_data": LANDING}
    states = run_module._source_states("keyword", result)
    assert states["keyword_csv"]["status"] == "missing"


def test_wednesday_reuses_evidence_as_stale():
    result = {"ads_data": ADS, "trends_data": TRENDS, "csv_data": CSV, "seo_data": SEO,
              "landing_price_data": LANDING, "suggested_keywords": [], "ad_cohort": {"signals": []}}
    states = run_module._source_states("wednesday", result, {"date": "2026-07-15"})
    assert states["trends"]["status"] == "stale"
    assert states["trends"]["generated_at"] == "2026-07-15T00:00:00+00:00"
    assert states["keyword_csv"]["status"] == "stale"
    assert states["serp_seo"]["status"] == "stale"
    assert states["landing_price"]["status"] == "stale"
    assert states["apify_ads"]["status"] == "valid"
    assert states["ad_cohort"]["status"] == "valid"


def test_wednesday_failed_apify_cohort_missing():
    result = {"ads_data": {"error": "Apify offline"}}
    states = run_module._source_states("wednesday", result, {"date": "2026-07-15"})
    assert states["apify_ads"]["status"] == "failed"
    assert states["ad_cohort"]["status"] == "missing"


def test_monthly_trends_failed_others_stale():
    result = {"trends_data": {"error": "Trends offline"}, "ads_data": ADS}
    states = run_module._source_states("monthly", result, {"date": "2026-07-15"})
    assert states["trends"]["status"] == "failed"
    assert states["apify_ads"]["status"] == "stale"
    assert states["keyword_csv"]["status"] == "stale"
    assert states["ad_cohort"]["status"] == "missing"


def test_monthly_success_trends_valid():
    result = {"trends_data": TRENDS, "ads_data": ADS}
    states = run_module._source_states("monthly", result, {"date": "2026-07-15"})
    assert states["trends"]["status"] == "valid"
    assert states["apify_ads"]["status"] == "stale"


def test_zero_ad_related_only_is_valid_even_when_result_list_empty():
    """A zero-ads run calls Related Queries only; an empty result list is
    still a valid read — never ``timeseries + related``, never not_attempted."""
    zero = {**ADS, "total_ads": 0, "ad_ids": [], "top_ads": [], "advertisers": []}
    result = {"ads_data": zero, "trends_data": {"related_queries": []}, "csv_data": CSV,
              "seo_data": SEO, "landing_price_data": None, "suggested_keywords": []}
    states = run_module._source_states("keyword", result)
    assert states["trends"]["status"] == "valid"
    assert states["trends"]["origin"] == "fresh"
    assert "related_queries_only" in states["trends"]["reason"]
    assert "timeseries + related" not in states["trends"]["reason"]
    # The suggestions source request happened even though the list is empty.
    assert states["suggestions"]["status"] == "valid"


def test_zero_ad_run_end_to_end_records_related_queries_only(monkeypatch):
    """A successful zero-ad run calls Related Queries only, and the generated
    pack provenance records a valid ``related_queries_only`` operation."""
    calls = {"apify": 0, "related": 0, "seo": 0, "landing": 0}
    monkeypatch.setattr(
        run_module.apify_ads, "fetch",
        lambda keyword: calls.__setitem__("apify", calls["apify"] + 1)
        or {**ADS, "total_ads": 0, "ad_ids": [], "top_ads": [], "advertisers": []},
    )
    monkeypatch.setattr(
        run_module.trends, "fetch_related",
        lambda keyword: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": []},
    )
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda keyword: calls.__setitem__("seo", 1))
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: CSV)
    generated = []
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    result = run_module.run_keyword(product("mochila"))
    states = generated[0]["source_states"]

    assert calls == {"apify": 1, "related": 1, "seo": 0, "landing": 0}
    assert result["score_result"]["label"] == "DESCARTAR"
    assert states["trends"]["status"] == "valid"
    assert "related_queries_only" in states["trends"]["reason"]
    assert "timeseries + related" not in states["trends"]["reason"]
    assert states["suggestions"]["status"] == "valid"
    assert states["suggestions"]["origin"] == "fresh"


def test_timeseries_full_run_keeps_timeseries_related_reason():
    result = {"ads_data": ADS, "trends_data": {**TRENDS, "related_queries": []},
              "csv_data": CSV, "seo_data": SEO, "landing_price_data": LANDING,
              "suggested_keywords": []}
    states = run_module._source_states("keyword", result)
    assert states["trends"]["reason"] == "timeseries"
    assert states["trends"]["status"] == "valid"
    assert states["suggestions"]["status"] == "valid"


def test_skipped_serpapi_trends_stays_not_attempted():
    result = {"ads_data": ADS,
              "trends_data": {"error": "SerpAPI não executada — modo sem rede"},
              "csv_data": CSV, "seo_data": {"error": "SEO não executado — modo sem rede"},
              "landing_price_data": None, "suggested_keywords": []}
    states = run_module._source_states("keyword", result)
    assert states["trends"]["status"] == "missing"
    assert states["suggestions"]["status"] == "missing"
