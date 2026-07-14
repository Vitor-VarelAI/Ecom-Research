"""
Testes do gerador de pack (Delta C e D).

Verifica que o pack gerado contém:
- Bloco DECISÃO com 5 linhas
- Tabela Onde vender
- Formatação correta
"""

import os
import tempfile
import json
from datetime import date
from unittest.mock import patch

import pytest

from pipeline.output import pack
from pipeline.scoring import score

# ── Fixture data ───────────────────────────────────────────────────────────

TRENDS_DATA = {
    "direction": "subida",
    "slope_pct": 15.5,
    "seasonal": True,
    "peak": "2026-11",
    "top_geos": [
        {"geo": "PT", "value": 100},
        {"geo": "ES", "value": 72},
        {"geo": "BR", "value": 45},
    ],
    "related_queries": [
        {"keyword": "mochila escolar personalizada", "score": 120},
    ],
}

ADS_DATA = {
    "total_ads": 12,
    "top_ads": [
        {
            "advertiser": "MochilasXPTO",
            "days_active": 180,
            "reach_eu": 45,
            "link": "https://www.facebook.com/ads/library/?id=1",
            "delivery_by_region": [
                {"region": "PT", "percentage": 45},
                {"region": "ES", "percentage": 30},
            ],
        },
        {
            "advertiser": "BagMaster",
            "days_active": 90,
            "reach_eu": 60,
            "link": "https://www.facebook.com/ads/library/?id=2",
            "delivery_by_region": [
                {"region": "EU", "percentage": 60},
                {"region": "FR", "percentage": 20},
            ],
        },
    ],
    "advertisers": [
        {"page_name": "MochilasXPTO", "active_ads": 6, "oldest_ad_days": 180},
        {"page_name": "BagMaster", "active_ads": 2, "oldest_ad_days": 90},
    ],
    "market_proven": True,
    "copy_angles": ["Mochila personalizada premium"],
    "saturation": "baixa",
}

CSV_DATA = {
    "volume": 5400,
    "cpc": 0.35,
    "related_keywords": [
        {"keyword": "mochila personalizada", "volume": 5400},
        {"keyword": "mochila com nome", "volume": 2400},
    ],
    "total_keywords": 15,
}

SEO_DATA = {
    "organic_results": [
        {"domain": "mochilas.pt", "position": 1, "title": "Mochilas Personalizadas"},
    ],
    "ads_count": 3,
    "paa_questions": ["Como personalizar mochila?"],
    "seo_opportunity": "média",
}

LANDING_PRICE_DATA = {
    "price_range": {
        "min": 29.99,
        "max": 59.99,
        "currency": "EUR",
        "sources": 2,
    },
}


@pytest.fixture
def temp_pack_dirs(monkeypatch):
    """Redireciona packs/ e data/ para dirs temporários."""
    tmp = tempfile.mkdtemp()
    packs_dir = os.path.join(tmp, "packs")
    data_dir = os.path.join(tmp, "data")
    os.makedirs(packs_dir, exist_ok=True)
    os.makedirs(data_dir, exist_ok=True)

    # Monkeypatch the _pack_paths function to use temp dirs
    original_pack_paths = pack._pack_paths

    def _temp_pack_paths(keyword, run_date=None):
        if run_date is None:
            run_date = date.today()
        slug = pack._slugify(keyword)
        date_str = run_date.isoformat()
        md_path = os.path.join(packs_dir, f"{date_str}--{slug}.md")
        json_path = os.path.join(data_dir, f"{date_str}--{slug}.json")
        return md_path, json_path

    monkeypatch.setattr(pack, "_pack_paths", _temp_pack_paths)
    return tmp


class TestDecisionBlock:
    """Testa o bloco DECISÃO no pack."""

    def test_decision_has_all_five_lines(self, temp_pack_dirs):
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)

        md_path = pack.generate(
            keyword="mochila personalizada",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ADS_DATA,
            score_result=score_result,
            seo_data=SEO_DATA,
            landing_price_data=LANDING_PRICE_DATA,
        )

        assert os.path.exists(md_path)

        with open(md_path, "r") as f:
            content = f.read()

        # Verificar cabeçalho DECISÃO
        assert "## DECISÃO" in content
        assert "mochila personalizada — DECISÃO" in content

        # Verificar as 5 linhas
        assert "Vender?" in content
        assert "Onde?" in content
        assert "Preço:" in content
        assert "Prova:" in content
        assert "Risco:" in content

        # Verificar conteúdos específicos
        assert "SIM" in content  # AVANÇAR → SIM
        assert "lançar antes de 2026-11" in content  # seasonal
        assert "€29.99" in content  # price min
        assert "€59.99" in content  # price max
        assert "MochilasXPTO" in content  # top advertiser
        assert "6 variantes ativas" in content
        assert "180 dias" in content

    def test_decision_without_price_data(self, temp_pack_dirs):
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)

        md_path = pack.generate(
            keyword="test",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ADS_DATA,
            score_result=score_result,
            landing_price_data={"error": "landings não legíveis"},
        )

        with open(md_path, "r") as f:
            content = f.read()

        assert "Preço:     sem dados" in content

    def test_decision_without_advertisers(self, temp_pack_dirs):
        ads_no_adv = {
            **ADS_DATA,
            "advertisers": [],
            "market_proven": False,
        }
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ads_no_adv)

        md_path = pack.generate(
            keyword="test",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ads_no_adv,
            score_result=score_result,
        )

        with open(md_path, "r") as f:
            content = f.read()

        assert "sem escala detetada" in content

    def test_descartar_shows_nao(self, temp_pack_dirs):
        trends_bad = {"error": "sem dados"}
        csv_bad = {"error": "sem dados"}
        ads_bad = {"total_ads": 0, "top_ads": [], "advertisers": [],
                    "market_proven": False, "copy_angles": [], "saturation": "baixa"}

        score_result = score.compute(trends_bad, csv_bad, ads_bad)
        assert score_result["label"] == "DESCARTAR"

        md_path = pack.generate(
            keyword="test",
            trends_data=trends_bad,
            csv_data=csv_bad,
            ads_data=ads_bad,
            score_result=score_result,
        )

        with open(md_path, "r") as f:
            content = f.read()

        assert "Vender?    NÃO" in content


class TestOndeVenderTable:
    """Testa a tabela 'Onde vender'."""

    def test_table_present_in_pack(self, temp_pack_dirs):
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)

        md_path = pack.generate(
            keyword="test",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ADS_DATA,
            score_result=score_result,
        )

        with open(md_path, "r") as f:
            content = f.read()

        assert "## Onde vender" in content
        # Tabela markdown
        assert "| País | Procura (Trends 0-100) | Advertisers ativos | Alcance ads |" in content

        # Países base sempre presentes: PT, ES, FR
        assert "| PT |" in content
        assert "| ES |" in content
        assert "| FR |" in content

    def test_includes_trends_top_geos_in_table(self, temp_pack_dirs):
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)

        md_path = pack.generate(
            keyword="test",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ADS_DATA,
            score_result=score_result,
        )

        with open(md_path, "r") as f:
            content = f.read()

        # BR está no top 3 trends → deve aparecer
        assert "| BR |" in content

    def test_table_without_trends_data(self, temp_pack_dirs):
        score_result = score.compute({"error": "no trends"}, CSV_DATA, ADS_DATA)

        md_path = pack.generate(
            keyword="test",
            trends_data={"error": "sem dados de trends"},
            csv_data=CSV_DATA,
            ads_data=ADS_DATA,
            score_result=score_result,
        )

        with open(md_path, "r") as f:
            content = f.read()

        # Tabela ainda deve existir (PT, ES, FR sempre presentes)
        assert "| PT |" in content
        assert "| ES |" in content
        assert "| FR |" in content

    def test_dash_for_missing_data(self, temp_pack_dirs):
        """Células sem dados devem mostrar '—'."""
        ads_no_regions = {
            **ADS_DATA,
            "top_ads": [
                {**ADS_DATA["top_ads"][0], "delivery_by_region": []},
                {**ADS_DATA["top_ads"][1], "delivery_by_region": []},
            ],
        }
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ads_no_regions)

        md_path = pack.generate(
            keyword="test",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ads_no_regions,
            score_result=score_result,
        )

        with open(md_path, "r") as f:
            content = f.read()

        # Alcance deve ser "—" para todos quando não há dados de delivery
        assert "—" in content


class TestOndeVenderHelpers:
    """Testa funções auxiliares da tabela Onde vender."""

    def test_build_rows_includes_base_countries(self):
        rows = pack._build_onde_vender_rows(TRENDS_DATA, ADS_DATA)
        countries = [r["pais"] for r in rows]
        assert "PT" in countries
        assert "ES" in countries
        assert "FR" in countries

    def test_pick_best_country(self):
        rows = pack._build_onde_vender_rows(TRENDS_DATA, ADS_DATA)
        best = pack._pick_best_country(rows)
        # PT tem procura=100 e advertisers moderados (2) → deve ser PT
        assert best == "PT"

    def test_pick_best_country_all_missing(self):
        """Sem dados de trends, devolve o primeiro país da lista."""
        rows = pack._build_onde_vender_rows({"error": "no data"}, {"error": "no data"})
        best = pack._pick_best_country(rows)
        assert best in ["PT", "ES", "FR"]  # um dos base


class TestDigestLine:
    """Testa generate_digest_line com o novo formato DECISÃO."""

    def test_digest_with_ads_data(self):
        sr = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
        line = pack.generate_digest_line(
            "mochila", sr, ads_data=ADS_DATA, trends_data=TRENDS_DATA
        )
        assert "Vender?" in line
        assert "Onde?" in line
        assert "Prova:" in line
        assert "MochilasXPTO" in line

    def test_digest_fallback_without_ads_data(self):
        sr = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
        line = pack.generate_digest_line("mochila", sr)  # sem ads_data
        assert "Score:" in line
        # Formato simples: emoji + keyword + score
        assert "mochila" in line

    def test_digest_with_seasonal_note(self):
        sr = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)
        line = pack.generate_digest_line(
            "mochila", sr, ads_data=ADS_DATA, trends_data=TRENDS_DATA
        )
        assert "lançar antes de 2026-11" in line


class TestJsonOutput:
    """Testa que o JSON inclui os novos campos."""

    def test_json_includes_landing_price(self, temp_pack_dirs):
        score_result = score.compute(TRENDS_DATA, CSV_DATA, ADS_DATA)

        md_path = pack.generate(
            keyword="test",
            trends_data=TRENDS_DATA,
            csv_data=CSV_DATA,
            ads_data=ADS_DATA,
            score_result=score_result,
            landing_price_data=LANDING_PRICE_DATA,
        )

        # Encontrar o JSON
        data_dir = os.path.join(temp_pack_dirs, "data")
        json_files = [f for f in os.listdir(data_dir) if f.endswith(".json")]
        assert len(json_files) == 1

        with open(os.path.join(data_dir, json_files[0]), "r") as f:
            jdata = json.load(f)

        assert "landing_price" in jdata
        assert jdata["landing_price"]["price_range"]["min"] == 29.99
