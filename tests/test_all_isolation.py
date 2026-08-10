"""--all per-keyword exception isolation, shared with Sunday's loop."""

from __future__ import annotations

import pytest

import run

ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
       "saturation": "baixa"}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def _boom(*args, **kwargs):
    raise RuntimeError("falha explosiva")


def test_safe_run_keyword_contains_exceptions(monkeypatch):
    monkeypatch.setattr(run, "run_keyword", _boom)
    result = run._safe_run_keyword("kw")
    assert result["keyword"] == "kw"
    assert "error" in result
    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"


def test_all_isolates_one_failing_keyword(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: calls.append(kw) or (dict(ADS) if kw == "ok" else _boom()))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: dict(TRENDS))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: dict(CSV))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_all(["ok", "boom"], send=False)

    assert len(results) == 2  # the batch survives the failing keyword
    assert results[0]["keyword"] == "ok"
    assert results[1]["keyword"] == "boom"
    assert "error" in results[1]
    assert results[1]["score_result"]["label"] == "DADOS INSUFICIENTES"
    out = capsys.readouterr().out
    assert "DADOS INSUFICIENTES" in out  # the digest is still produced


def test_sunday_loop_uses_the_same_isolation(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "keywords.txt").write_text("ok\nboom\n", encoding="utf-8")
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: dict(ADS) if kw == "ok" else _boom())
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: dict(TRENDS))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: dict(CSV))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_sunday(send=False)

    assert len(results) == 2
    assert results[1]["keyword"] == "boom"
    assert results[1]["score_result"]["label"] == "DADOS INSUFICIENTES"
