"""config/products.json is the only active product source and is read-only at
runtime; config/keywords.txt is a historical bootstrap input that is never
read or mutated by the runtime."""

from __future__ import annotations

import run
from tests.fixtures.products_helpers import write_registry

ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
       "saturation": "baixa"}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def _install_adapters(monkeypatch):
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: dict(ADS))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: dict(TRENDS))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: dict(CSV))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")


def test_registry_read_does_not_mutate_products_json_or_keywords_txt(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    keywords = config_dir / "keywords.txt"
    original = "# keep this\nmochila\n"
    keywords.write_text(original, encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    write_registry(tmp_path, ["mochila", "bolsa"])
    products_before = (config_dir / "products.json").read_bytes()

    assert [p.research_keyword for p in run._load_products().products] == ["mochila", "bolsa"]
    assert (config_dir / "products.json").read_bytes() == products_before
    assert keywords.read_text(encoding="utf-8") == original


def test_runtime_sunday_never_reads_or_mutates_keywords_txt(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    write_registry(tmp_path, ["mochila", "bolsa"])
    config_dir = tmp_path / "config"
    keywords = config_dir / "keywords.txt"
    original = "# legacy bootstrap input\nmochila\nbolsa\n"
    keywords.write_text(original, encoding="utf-8")
    _install_adapters(monkeypatch)

    results = run._run_sunday(send=False)

    assert [r["research_keyword"] for r in results] == ["mochila", "bolsa"]
    assert keywords.read_text(encoding="utf-8") == original
