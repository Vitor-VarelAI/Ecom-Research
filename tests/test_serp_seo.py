"""
Testes do adapter Google SERP snapshot (SerpAPI).

Usa fixture com resposta JSON simulada da SerpAPI.
Sem chamadas reais à rede.
"""

import json
import os

import pytest

os.environ.pop("SERPAPI_KEY", None)

from pipeline import quota
from pipeline.adapters import serp_seo

# ── Fixture: resposta simulada da SerpAPI Google SERP ──────────────────────

SERPAPI_SEO_JSON = {
    "search_metadata": {"status": "Success"},
    "organic_results": [
        {"position": 1, "title": "Mochilas Personalizadas — Cria a Tua | LojaXPTO", "link": "https://www.lojaxpto.pt/mochilas-personalizadas"},
        {"position": 2, "title": "Mochila Personalizada com Nome — Entrega 48h", "link": "https://www.mochilaslegais.pt/"},
        {"position": 3, "title": "Mochila Personalizada | Amazon.es", "link": "https://www.amazon.es/mochila-personalizada/s?k=mochila+personalizada"},
        {"position": 4, "title": "Mochilas Personalizadas à Tua Medida — Blog Criativo", "link": "https://www.blogcriativo.pt/diy/mochilas-personalizadas"},
        {"position": 5, "title": "Mochila Personalizada Escolar — Worten.pt", "link": "https://www.worten.pt/mochilas-escolares-personalizadas"},
        {"position": 6, "title": "Mochilas Bordadas — Atelier Portugal", "link": "https://www.atelierportugal.com/mochilas-bordadas"},
        {"position": 7, "title": "Mochila Personalizada — Fnac.pt", "link": "https://www.fnac.pt/mochila-personalizada"},
        {"position": 8, "title": "As 10 Melhores Mochilas Personalizadas 2026", "link": "https://www.reviewssite.com/melhores-mochilas-personalizadas"},
        {"position": 9, "title": "Mochila com Foto — Print&Go", "link": "https://www.printandgo.pt/mochila-com-foto"},
        {"position": 10, "title": "Mochilas Personalizadas Portugal | Guia Completo", "link": "https://www.guiacompleto.pt/mochilas-personalizadas-portugal"},
    ],
    "ads": [
        {"position": 1, "title": "Mochila Personalizada — Anúncio"},
        {"position": 2, "title": "Mochilas Baratas — Anúncio"},
    ],
    "related_questions": [
        {"question": "Quanto custa personalizar uma mochila?"},
        {"question": "Onde comprar mochilas personalizadas em Portugal?"},
        {"question": "Qual o melhor material para mochila personalizada?"},
        {"question": "Como limpar mochila personalizada?"},
    ],
}

# Resposta SEM ads
SERPAPI_SEO_NO_ADS_JSON = {
    "organic_results": [
        {"position": 1, "title": "Blog sobre mochilas", "link": "https://www.blogmochilas.com/"},
        {"position": 2, "title": "Guia de mochilas artesanais", "link": "https://www.artesanato.pt/mochilas/"},
    ],
    "related_questions": [],
}

# Resposta dominada por marketplaces
SERPAPI_SEO_MARKETPLACES_JSON = {
    "organic_results": [
        {"position": 1, "title": "Produto X", "link": "https://www.amazon.es/produto-x"},
        {"position": 2, "title": "Produto X", "link": "https://www.elcorteingles.es/produto-x"},
        {"position": 3, "title": "Produto X", "link": "https://www.worten.pt/produto-x"},
        {"position": 4, "title": "Produto X", "link": "https://www.fnac.pt/produto-x"},
        {"position": 5, "title": "Produto X", "link": "https://www.continente.pt/produto-x"},
    ],
}


# ── Tests ──────────────────────────────────────────────────────────────────


def _isolate_quota(monkeypatch, tmp_path):
    """Point the real quota counter at a temp file (never touch data/quota.json)."""
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(serp_seo.quota, "QUOTA_FILE", str(quota_file))
    return quota_file


class TestSerpSEOParse:
    """Testa o parsing da resposta JSON da SerpAPI Google SERP."""

    def test_parse_response(self, monkeypatch, tmp_path):
        """Resposta normal com orgânicos, ads e PAA."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))

        # Mock da SerpAPI
        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_SEO_JSON

        monkeypatch.setattr(serp_seo.requests, "get", lambda url, params, timeout: MockResponse())
        _isolate_quota(monkeypatch, tmp_path)

        result = serp_seo.fetch("mochila personalizada")

        assert "error" not in result
        assert len(result["organic_results"]) == 10
        assert result["organic_results"][0]["domain"] == "lojaxpto.pt"
        assert result["ads_count"] == 2
        assert len(result["paa_questions"]) == 3
        assert result["paa_questions"][0] == "Quanto custa personalizar uma mochila?"
        assert result["seo_opportunity"] in ("alta", "média", "baixa")
        assert quota.get_usage()["calls"] == 1  # 1 call cobrada após sucesso

    def test_no_ads_high_opportunity(self, monkeypatch, tmp_path):
        """SERP sem ads e sem marketplaces → oportunidade alta."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))

        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_SEO_NO_ADS_JSON

        monkeypatch.setattr(serp_seo.requests, "get", lambda url, params, timeout: MockResponse())
        _isolate_quota(monkeypatch, tmp_path)

        result = serp_seo.fetch("keyword artesanal")

        assert result["ads_count"] == 0
        assert result["seo_opportunity"] == "alta"
        assert result["paa_questions"] == []

    def test_marketplace_domination(self, monkeypatch, tmp_path):
        """SERP dominada por marketplaces → oportunidade baixa."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))

        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_SEO_MARKETPLACES_JSON

        monkeypatch.setattr(serp_seo.requests, "get", lambda url, params, timeout: MockResponse())
        _isolate_quota(monkeypatch, tmp_path)

        result = serp_seo.fetch("produto competitivo")

        assert result["seo_opportunity"] == "baixa"
        assert len(result["organic_results"]) == 5

    def test_no_key_returns_error(self, monkeypatch, tmp_path):
        """Sem SERPAPI_KEY deve devolver erro."""
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        _isolate_quota(monkeypatch, tmp_path)
        result = serp_seo.fetch("test-sem-key")
        assert "error" in result
        assert "SERPAPI_KEY" in result["error"]
        assert quota.get_usage()["calls"] == 0

    def test_cache_hit(self, monkeypatch, tmp_path):
        """Cache hit — zero chamadas à API."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))

        call_count = 0

        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_SEO_JSON

        def mock_get(url, params=None, timeout=None):
            nonlocal call_count
            call_count += 1
            return MockResponse()

        monkeypatch.setattr(serp_seo.requests, "get", mock_get)
        _isolate_quota(monkeypatch, tmp_path)

        # Primeira call — vai à API
        result1 = serp_seo.fetch("cache-seo-test")
        assert call_count == 1
        assert quota.get_usage()["calls"] == 1

        # Segunda call — cache hit
        result2 = serp_seo.fetch("cache-seo-test")
        assert call_count == 1  # não aumentou
        assert "_cached_at" not in result2
        assert result2["ads_count"] == 2
        assert quota.get_usage()["calls"] == 1  # cache hit: zero gastos
