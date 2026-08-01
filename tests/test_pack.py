"""Pack schema tests for manual-market and advisory output."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date

import pytest

from pipeline.output import pack
from pipeline.scoring import score


TRENDS_DATA = {
    "direction": "subida",
    "slope_pct": 15.5,
    "seasonal": True,
    "peak": "2026-11",
    "top_geos": [{"geo": "PT", "value": 100}, {"geo": "ES", "value": 72}, {"geo": "BR", "value": 45}],
    "related_queries": [{"keyword": "mochila escolar", "score": 120}],
}
ADS_DATA = {
    "source": "Facebook Ad Library via Apify",
    "total_ads": 12,
    "top_ads": [
        {"ad_archive_id": "1", "advertiser": "MochilasXPTO", "page_id": "p1", "days_active": 180, "link": "https://www.facebook.com/ads/library/?id=1", "landing_url": "https://shop.example/1", "countries": ["PT", "ES"]},
        {"ad_archive_id": "2", "advertiser": "BagMaster", "page_id": "p2", "days_active": 90, "link": "https://www.facebook.com/ads/library/?id=2", "landing_url": "https://shop.example/2", "countries": ["FR"]},
    ],
    "advertisers": [{"page_id": "p1", "page_name": "MochilasXPTO", "active_ads": 6, "oldest_ad_days": 180}],
    "market_proven": True,
    "copy_examples": ["Mochila personalizada premium"],
    "saturation": "baixa",
}
CSV_DATA = {"volume": 5400, "cpc": 0.35, "related_keywords": [], "total_keywords": 1}
SEO_DATA = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


@pytest.fixture
def temp_pack_dirs(monkeypatch):
    tmp = tempfile.mkdtemp()
    packs_dir, data_dir = os.path.join(tmp, "packs"), os.path.join(tmp, "data")
    os.makedirs(packs_dir)
    os.makedirs(data_dir)

    def temp_paths(keyword, run_date=None):
        run_date = run_date or date.today()
        slug = pack._slugify(keyword)
        return os.path.join(packs_dir, f"{run_date.isoformat()}--{slug}.md"), os.path.join(data_dir, f"{run_date.isoformat()}--{slug}.json")

    monkeypatch.setattr(pack, "_pack_paths", temp_paths)
    return tmp


def test_pack_has_manual_decision_market_and_advisory_suggestions(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate(
        "mochila personalizada",
        TRENDS_DATA,
        CSV_DATA,
        ADS_DATA,
        result,
        seo_data=SEO_DATA,
        landing_price_data={"price_range": {"min": 29.99, "max": 59.99}},
        suggested_keywords=[{"keyword": "mochila escolar", "score": 120, "status": "proposed"}],
    )
    content = open(path, encoding="utf-8").read()
    assert "Mercado:   decisão manual" in content
    assert "## Sinais por mercado — decisão manual" in content
    assert "| country | Trends demand | advertisers observed | ads observed |" in content
    assert all(f"| {country} |" in content for country in ("PT", "ES", "FR", "BR"))
    assert "## Sugestões de keywords — aprovação manual" in content
    assert "Nada foi adicionado a config/keywords.txt" in content
    assert "AVANÇAR: preparar ad-machine após aprovação manual" in content
    assert "Facebook Ad Library via Apify" in content


def test_raw_json_uses_facebook_ads_only(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    pack.generate("test", TRENDS_DATA, CSV_DATA, ADS_DATA, result)
    raw_path = next(os.path.join(temp_pack_dirs, "data", name) for name in os.listdir(os.path.join(temp_pack_dirs, "data")))
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert raw["facebook_ads"] == ADS_DATA
    assert set(raw) >= {"facebook_ads", "suggested_keywords"}
    assert raw["suggested_keywords"] == []


def test_incomplete_recommendation_is_not_descartar(temp_pack_dirs):
    result = {"score": 0, "label": "DADOS INSUFICIENTES", "breakdown": {}}
    path = pack.generate("test", {"error": "offline"}, {"error": "missing"}, {"error": "Apify offline"}, result)
    content = open(path, encoding="utf-8").read()
    assert "Vender?    DADOS INSUFICIENTES" in content
    assert "repetir quando o Apify estiver disponível" in content
    assert "DESCARTAR" not in content


def test_digest_does_not_choose_a_country():
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    digest = pack.generate_digest_line("mochila", result, ADS_DATA, trends_data=TRENDS_DATA)
    assert "Mercado: decisão manual" in digest
    assert "Onde?" not in digest


COHORT = {
    "baseline_date": "2026-07-08",
    "current_date": "2026-07-15",
    "previous_ads": ["a1", "a2", "a3", "a4"],
    "current_ads": ["a2", "a3", "a4", "a5"],
    "retained_ads": ["a2", "a3", "a4"],
    "new_ads": ["a5"],
    "removed_ads": ["a1"],
    "ad_retention_pct": 75.0,
    "previous_advertisers": ["p1", "p2", "p3"],
    "current_advertisers": ["p1", "p2", "p3"],
    "retained_advertisers": ["p1", "p2", "p3"],
    "new_advertisers": [],
    "lost_advertisers": [],
    "advertiser_retention_pct": 100.0,
    "top_advertiser_share_pct": 50.0,
    "signals": ["durable_demand"],
}


def test_cohort_renders_markdown_section_and_persists_json(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate(
        "mochila personalizada",
        TRENDS_DATA,
        CSV_DATA,
        ADS_DATA,
        result,
        ad_cohort=COHORT,
    )
    content = open(path, encoding="utf-8").read()
    assert "## Persistência de mercado — pulse quarta (advisory)" in content
    assert "- Janela: 2026-07-08 → 2026-07-15" in content
    assert "- Retenção de ads: 75.0% (3 de 4) | novos: +1 | removidos: -1" in content
    assert "- Retenção de anunciantes: 100.0% (3 de 3) | novos: +0 | perdidos: -0" in content
    assert "- Concentração: maior anunciante com 50.0% das variantes atuais" in content
    assert "- Sinais: durable_demand" in content

    raw_path = next(os.path.join(temp_pack_dirs, "data", name) for name in os.listdir(os.path.join(temp_pack_dirs, "data")))
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert raw["ad_cohort"] == COHORT


def test_no_cohort_renders_fallback_and_omits_json_key(temp_pack_dirs):
    result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
    path = pack.generate("mochila", TRENDS_DATA, CSV_DATA, ADS_DATA, result)
    content = open(path, encoding="utf-8").read()
    assert "## Persistência de mercado — pulse quarta (advisory)" in content
    assert "sem baseline" in content

    raw_path = next(os.path.join(temp_pack_dirs, "data", name) for name in os.listdir(os.path.join(temp_pack_dirs, "data")))
    raw = json.load(open(raw_path, encoding="utf-8"))
    assert "ad_cohort" not in raw
