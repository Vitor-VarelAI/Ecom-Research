"""Cost and reuse contracts for run.py modes."""

from __future__ import annotations

import json
from datetime import date, timedelta

import run


ADS = {
    "source": "Facebook Ad Library via Apify",
    "total_ads": 1,
    "ad_ids": ["a1"],
    "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30, "landing_url": "https://shop.example"}],
    "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
    "saturation": "baixa",
}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def test_apify_failure_continues_paid_sources_without_false_descartar(monkeypatch):
    calls = {"apify": 0, "trends": 0, "seo": 0, "landing": 0}

    def apify(keyword):
        calls["apify"] += 1
        return {"error": "Apify offline"}

    def trends(keyword):
        calls["trends"] += 1
        return TRENDS

    def seo(keyword):
        calls["seo"] += 1
        return SEO

    monkeypatch.setattr(run.apify_ads, "fetch", apify)
    monkeypatch.setattr(run.trends, "fetch", trends)
    monkeypatch.setattr(run.serp_seo, "fetch", seo)
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run, "_csv_data", lambda path: CSV)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    result = run.run_keyword("mochila")

    assert calls == {"apify": 1, "trends": 1, "seo": 1, "landing": 0}
    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"


def test_zero_ads_skips_seo_and_landing_but_fetches_related_once(monkeypatch):
    calls = {"apify": 0, "related": 0, "seo": 0, "landing": 0}

    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: calls.__setitem__("apify", calls["apify"] + 1) or {**ADS, "total_ads": 0, "ad_ids": [], "top_ads": [], "advertisers": []})
    monkeypatch.setattr(run.trends, "fetch_related", lambda keyword: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": [{"keyword": "mochila escolar", "score": 100}]})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda keyword: calls.__setitem__("seo", 1))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run, "_csv_data", lambda path: CSV)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    result = run.run_keyword("mochila")

    assert calls == {"apify": 1, "related": 1, "seo": 0, "landing": 0}
    assert result["score_result"]["label"] == "DESCARTAR"
    assert result["suggested_keywords"][0]["status"] == "proposed"


def _write_previous(tmp_path, keyword="mochila", label="OBSERVAR"):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    previous = {
        "keyword": keyword,
        "date": date.today().isoformat(),
        "score": {"score": 50, "label": label, "breakdown": {}},
        "trends": TRENDS,
        "keyword_planner": CSV,
        "facebook_ads": ADS,
        "seo": SEO,
        "landing_price": {"price_range": {"min": 10, "max": 20}},
        "suggested_keywords": [{"keyword": "mochila escolar", "score": 10, "status": "proposed"}],
    }
    (data_dir / f"{date.today().isoformat()}--mochila.json").write_text(json.dumps(previous), encoding="utf-8")


def test_wednesday_uses_one_apify_and_reuses_everything(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify_calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: apify_calls.append(keyword) or {**ADS, "ad_ids": ["a2"]})
    monkeypatch.setattr(run.trends, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Trends called")))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SEO called")))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("landing called")))
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run._run_wednesday(send=False)

    assert apify_calls == ["mochila"]
    assert generated[0]["trends_data"] == TRENDS
    assert generated[0]["landing_price_data"] == {"price_range": {"min": 10, "max": 20}}
    assert results[0]["changes"] == ["+1 ads novos", "-1 ads removidos"]


def test_wednesday_emits_ad_cohort_to_pack_and_results(monkeypatch, tmp_path, capsys):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: {**ADS, "ad_ids": ["a2"]})
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run._run_wednesday(send=False)

    cohort = results[0]["ad_cohort"]
    assert generated[0]["ad_cohort"] is cohort
    assert cohort["baseline_date"] == date.today().isoformat()
    assert cohort["previous_ads"] == ["a1"]
    assert cohort["current_ads"] == ["a2"]
    assert cohort["retained_ads"] == []
    assert cohort["new_ads"] == ["a2"]
    assert cohort["removed_ads"] == ["a1"]
    assert cohort["ad_retention_pct"] == 0.0
    assert cohort["advertiser_retention_pct"] == 100.0
    assert cohort["top_advertiser_share_pct"] == 100.0
    assert cohort["signals"] == ["high_churn", "concentrated_market"]
    digest = capsys.readouterr().out
    assert "retenção ads 0.0%" in digest
    assert "Sinais: high_churn, concentrated_market" in digest


def _ads(ad_ids, advertisers, total_ads):
    return {"total_ads": total_ads, "ad_ids": ad_ids, "advertisers": advertisers}


def _adv(page_id, active_ads):
    return {"page_id": page_id, "page_name": f"Loja {page_id}", "active_ads": active_ads}


def test_ad_cohort_set_math_and_retention():
    previous = _ads(["a1", "a2", "a3", "a4"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)
    current = _ads(["a2", "a3", "a4", "a5"], [_adv("p1", 2), _adv("p2", 1), _adv("p4", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["baseline_date"] == "2026-07-08"
    assert cohort["current_date"] == "2026-07-15"
    assert cohort["previous_ads"] == ["a1", "a2", "a3", "a4"]
    assert cohort["current_ads"] == ["a2", "a3", "a4", "a5"]
    assert cohort["retained_ads"] == ["a2", "a3", "a4"]
    assert cohort["new_ads"] == ["a5"]
    assert cohort["removed_ads"] == ["a1"]
    assert cohort["ad_retention_pct"] == 75.0
    assert cohort["previous_advertisers"] == ["p1", "p2", "p3"]
    assert cohort["current_advertisers"] == ["p1", "p2", "p4"]
    assert cohort["retained_advertisers"] == ["p1", "p2"]
    assert cohort["new_advertisers"] == ["p4"]
    assert cohort["lost_advertisers"] == ["p3"]
    assert cohort["advertiser_retention_pct"] == round(2 / 3 * 100, 1)
    assert cohort["top_advertiser_share_pct"] == 50.0
    assert cohort["signals"] == []


def test_ad_cohort_durable_demand_requires_three_retained_advertisers():
    previous = _ads(["a1", "a2", "a3", "a4"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)
    current = _ads(["a2", "a3", "a4", "a5"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == 75.0
    assert len(cohort["retained_advertisers"]) == 3
    assert cohort["signals"] == ["durable_demand"]


def test_ad_cohort_signals_coexist_durable_and_concentrated():
    previous = _ads(["a1", "a2", "a3", "a4", "a5", "a6"], [_adv("p1", 4), _adv("p2", 1), _adv("p3", 1)], 6)
    current = _ads(["a3", "a4", "a5", "a6", "a7", "a8"], [_adv("p1", 4), _adv("p2", 1), _adv("p3", 1)], 6)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == round(4 / 6 * 100, 1)
    assert cohort["top_advertiser_share_pct"] == round(4 / 6 * 100, 1)
    assert cohort["signals"] == ["durable_demand", "concentrated_market"]


def test_ad_cohort_high_churn_below_fifty():
    previous = _ads(["a1", "a2", "a3", "a4", "a5", "a6"], [_adv("p1", 4), _adv("p2", 1), _adv("p3", 1)], 6)
    current = _ads(["a5", "a6", "a7", "a8"], [_adv("p1", 2), _adv("p2", 1), _adv("p4", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == round(2 / 6 * 100, 1)
    assert cohort["signals"] == ["high_churn"]


def test_ad_cohort_no_baseline_is_insufficient():
    current = _ads(["a1", "a2"], [_adv("p1", 1), _adv("p2", 1)], 2)

    cohort = run._ad_cohort({}, current, baseline_date=None, current_date="2026-07-15")

    assert cohort["ad_retention_pct"] is None
    assert cohort["advertiser_retention_pct"] is None
    assert cohort["retained_ads"] == []
    assert cohort["removed_ads"] == []
    assert cohort["new_ads"] == ["a1", "a2"]
    assert cohort["signals"] == ["insufficient_baseline"]


def test_ad_cohort_partial_baseline_without_ids_keeps_advertiser_metrics():
    previous = _ads([], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)
    current = _ads(["a1", "a2", "a3", "a4"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] is None
    assert cohort["advertiser_retention_pct"] == 100.0
    assert cohort["top_advertiser_share_pct"] == 50.0
    assert cohort["signals"] == []


def test_ad_cohort_handles_error_sources():
    cohort = run._ad_cohort({"error": "Apify offline"}, {"error": "Apify offline"}, baseline_date=None)
    assert cohort["previous_ads"] == []
    assert cohort["current_ads"] == []
    assert cohort["ad_retention_pct"] is None
    assert cohort["top_advertiser_share_pct"] is None
    assert cohort["signals"] == ["insufficient_baseline"]


def test_ad_cohort_zero_current_ads_has_no_share():
    previous = _ads(["a1", "a2"], [_adv("p1", 1), _adv("p2", 1)], 2)
    current = _ads([], [], 0)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == 0.0
    assert cohort["top_advertiser_share_pct"] is None
    assert cohort["signals"] == ["high_churn"]


def test_monthly_has_one_forced_trends_call_and_no_apify_or_landing(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda *args, **kwargs: apify.append(True))
    trends_calls = []
    monkeypatch.setattr(run.trends, "fetch", lambda *args, **kwargs: trends_calls.append((args, kwargs)) or {**TRENDS, "slope_pct": 12})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SEO called")))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("landing called")))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_monthly(send=False)

    assert apify == []
    assert len(trends_calls) == 1
    assert trends_calls[0][1] == {"timeseries_only": True, "force_refresh": True}
    assert results[0]["score_result"]["label"] in {"OBSERVAR", "AVANÇAR", "DESCARTAR"}
