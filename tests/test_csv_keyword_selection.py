"""Regressão do fix de selecção por keyword.

Um único export do Keyword Planner partilhado por um batch deve servir cada
keyword com os SEUS métricas — nunca a linha de maior volume — e uma keyword
pedida que não exista no export deve dar erro explícito, não herdar outra
linha.  Sem chamadas reais à rede: todos os adapters são mockados, apenas o
parse do CSV é real.
"""

from __future__ import annotations

import run
from tests.fixtures.products_helpers import write_registry

ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
       "saturation": "baixa"}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}

EXPORT = (
    "Keyword,Avg. monthly searches,Competition,Top of page bid (low range)\n"
    "mochila personalizada,5400,0.65,0.42\n"
    "mochila escolar,3200,0.80,0.35\n"
    "mochila viagem,2100,0.45,0.28\n"
    "mochila bebé,1800,0.30,0.22\n"
)


def _write_export(tmp_path) -> str:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    path = data_dir / "planner.csv"
    path.write_text(EXPORT, encoding="utf-8")
    return str(path)


def _install_stack(monkeypatch, tmp_path):
    """Only the CSV parsing is real; every network/external adapter is stubbed."""
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: dict(ADS))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: dict(TRENDS))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")


def test_shared_export_serves_distinct_keywords(monkeypatch, tmp_path):
    """Duas keywords no mesmo batch: cada uma recebe o volume/CPC da SUA linha
    do mesmo export — a de volume baixo não herda a de maior volume."""
    csv_path = _write_export(tmp_path)
    _install_stack(monkeypatch, tmp_path)
    registry = write_registry(tmp_path, ["mochila personalizada", "mochila viagem"])
    products = list(registry.products)

    results = run._run_all(products, csv_path=csv_path, send=False)

    assert [r["keyword"] for r in results] == ["mochila personalizada", "mochila viagem"]
    assert results[0]["csv_data"]["volume"] == 5400
    assert results[0]["csv_data"]["cpc"] == 0.42
    assert results[1]["csv_data"]["volume"] == 2100  # own metrics, not 5400
    assert results[1]["csv_data"]["cpc"] == 0.28
    # O resultado de cada keyword é específico, não o mesmo dict partilhado.
    assert results[0]["csv_data"] is not results[1]["csv_data"]


def test_missing_keyword_in_batch_errors_explicitly(monkeypatch, tmp_path):
    """Keyword pedida ausente do export: o resultado do batch tem erro explícito
    que nomeia a keyword — nunca as métricas de outra linha."""
    csv_path = _write_export(tmp_path)
    _install_stack(monkeypatch, tmp_path)
    registry = write_registry(tmp_path, ["mochila personalizada", "keyword inexistente"])
    products = list(registry.products)

    results = run._run_all(products, csv_path=csv_path, send=False)

    assert results[0]["csv_data"]["volume"] == 5400
    missing = results[1]["csv_data"]
    assert "error" in missing
    assert "keyword inexistente" in missing["error"]
