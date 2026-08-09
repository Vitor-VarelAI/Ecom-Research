"""
Testes do adapter Google Trends (SerpAPI).

Usa fixture com resposta JSON simulada da SerpAPI.
Sem chamadas reais à rede.
"""

import json
import os
import tempfile
import time

import pytest

os.environ.pop("SERPAPI_KEY", None)

from pipeline import quota
from pipeline.adapters import trends


# ── Fixture: resposta simulada da SerpAPI Google Trends ────────────────────

SERPAPI_RESPONSE_JSON = {
    "search_metadata": {"status": "Success"},
    "interest_over_time": {
        "timeline_data": [
            {"date": "2026-04-14", "values": [{"query": "mochila personalizada", "extracted_value": 50}]},
            {"date": "2026-04-21", "values": [{"query": "mochila personalizada", "extracted_value": 50}]},
            {"date": "2026-04-28", "values": [{"query": "mochila personalizada", "extracted_value": 51}]},
            {"date": "2026-05-05", "values": [{"query": "mochila personalizada", "extracted_value": 49}]},
            {"date": "2026-05-12", "values": [{"query": "mochila personalizada", "extracted_value": 50}]},
            {"date": "2026-05-19", "values": [{"query": "mochila personalizada", "extracted_value": 51}]},
            {"date": "2026-05-26", "values": [{"query": "mochila personalizada", "extracted_value": 50}]},
            {"date": "2026-06-02", "values": [{"query": "mochila personalizada", "extracted_value": 49}]},
            {"date": "2026-06-09", "values": [{"query": "mochila personalizada", "extracted_value": 51}]},
            {"date": "2026-06-16", "values": [{"query": "mochila personalizada", "extracted_value": 50}]},
        ],
    },
    "interest_by_region": [
        {"geo": "PT", "value": "100"},
        {"geo": "BR", "value": "82"},
        {"geo": "ES", "value": "45"},
    ],
}

# Resposta com subida sazonal (pico no meio, depois regresso)
SERPAPI_SEASONAL_JSON = {
    "interest_over_time": {
        "timeline_data": [
            {"date": "2026-04-14", "values": [{"query": "natal", "extracted_value": 5}]},
            {"date": "2026-04-28", "values": [{"query": "natal", "extracted_value": 6}]},
            {"date": "2026-05-12", "values": [{"query": "natal", "extracted_value": 7}]},
            {"date": "2026-05-26", "values": [{"query": "natal", "extracted_value": 30}]},
            {"date": "2026-06-09", "values": [{"query": "natal", "extracted_value": 80}]},
            {"date": "2026-06-23", "values": [{"query": "natal", "extracted_value": 90}]},
            {"date": "2026-06-30", "values": [{"query": "natal", "extracted_value": 85}]},
            {"date": "2026-07-07", "values": [{"query": "natal", "extracted_value": 20}]},
            {"date": "2026-07-10", "values": [{"query": "natal", "extracted_value": 10}]},
            {"date": "2026-07-14", "values": [{"query": "natal", "extracted_value": 5}]},
        ],
    },
    "interest_by_region": [],
}


# ── Tests ──────────────────────────────────────────────────────────────────


def _isolate_quota(monkeypatch, tmp_path):
    """Point the real quota counter at a temp file (never touch data/quota.json)."""
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))
    return quota_file


class TestTrendsWithoutKey:
    """Sem SERPAPI_KEY definida, deve devolver erro graciosamente."""

    def test_no_key_returns_error(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        _isolate_quota(monkeypatch, tmp_path)
        # Limpar cache para forçar ida à API
        result = trends.fetch("mochila-test-sem-key")
        assert "error" in result
        assert "SERPAPI_KEY" in result["error"]
        # Nada foi cobrado: a key faltou antes de qualquer call.
        assert quota.get_usage()["calls"] == 0


class TestTrendsParse:
    """Testa o parsing da resposta JSON da SerpAPI."""

    def test_parse_response(self, monkeypatch, tmp_path):
        """Resposta normal com tendência de subida."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)

        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_RESPONSE_JSON

        monkeypatch.setattr(trends.requests, "get", lambda url, params, timeout: MockResponse())

        # Limpar cache para este teste
        cache_path = trends._cache_path("mochila-teste-parse")
        if os.path.exists(cache_path):
            os.remove(cache_path)

        # Também precisamos limpar qualquer cache interna
        result = trends.fetch("mochila-teste-parse")

        # Remover cache depois do teste para não poluir
        if os.path.exists(cache_path):
            os.remove(cache_path)

        assert "error" not in result or result.get("direction") is not None
        # Dados planos → direção "estável"
        assert result["direction"] == "estável"
        # slope próximo de zero (flat)
        assert -5 < result["slope_pct"] < 5
        assert result["seasonal"] is False
        assert len(result["top_geos"]) == 3
        assert result["top_geos"][0]["geo"] == "PT"

    def test_seasonal_detection(self, monkeypatch, tmp_path):
        """Detecção de sazonalidade."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)

        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_SEASONAL_JSON

        monkeypatch.setattr(trends.requests, "get", lambda url, params, timeout: MockResponse())

        cache_path = trends._cache_path("natal-teste-seasonal")
        if os.path.exists(cache_path):
            os.remove(cache_path)

        result = trends.fetch("natal-teste-seasonal")

        if os.path.exists(cache_path):
            os.remove(cache_path)

        assert result["seasonal"] is True
        assert result["peak"] is not None

    def test_network_error(self, monkeypatch, tmp_path):
        """Erro de rede tratado graciosamente."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)

        def mock_get_raise(url, params, timeout):
            raise trends.requests.ConnectionError("DNS failure")

        monkeypatch.setattr(trends.requests, "get", mock_get_raise)

        result = trends.fetch("test-network-error")
        assert "error" in result
        assert "Erro de rede" in result["error"]
        # Falha de rede antes do charge: nada é cobrado.
        assert quota.get_usage()["calls"] == 0


class TestTrendsCache:
    """Testa o sistema de cache em disco."""

    def test_cache_write_and_read(self, monkeypatch, tmp_path):
        """Escreve na cache e verifica que é lida sem chamada à API."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))

        # Mock da API
        call_count = 0

        class MockResp:
            status_code = 200
            @staticmethod
            def json():
                return SERPAPI_RESPONSE_JSON

        def mock_get(url, params=None, timeout=None):
            nonlocal call_count
            call_count += 1
            return MockResp()

        monkeypatch.setattr(trends.requests, "get", mock_get)
        _isolate_quota(monkeypatch, tmp_path)

        # Primeira chamada — deve ir à API (2 calls: timeseries + related)
        result1 = trends.fetch("cache-test-1")
        assert call_count == 2  # 2 API calls (timeseries + related_queries)
        # Dados planos → direção "estável"
        assert result1["direction"] == "estável"
        assert quota.get_usage()["calls"] == 2  # contabilidade real: 2 units

        # Segunda chamada — deve vir da cache, zero chamadas adicionais
        result2 = trends.fetch("cache-test-1")
        assert call_count == 2  # não aumentou
        assert result2["direction"] == "estável"
        assert "_cached_at" not in result2  # metadados internos removidos
        assert quota.get_usage()["calls"] == 2  # cache hit: zero gastos

    def test_cache_expired(self, monkeypatch, tmp_path):
        """Cache expirada (>30 dias) deve forçar nova chamada."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))

        # Escrever cache antiga
        cache_path = trends._cache_path("cache-expired-test")
        trends._write_cache("cache-expired-test", {"direction": "queda", "slope_pct": -10.0, "related_queries": []})
        # Alterar timestamp para 35 dias atrás (>30d TTL)
        with open(cache_path, "r") as f:
            cached = json.load(f)
        cached["_cached_at"] = time.time() - 35 * 24 * 3600
        with open(cache_path, "w") as f:
            json.dump(cached, f)

        class MockResp:
            status_code = 200
            @staticmethod
            def json():
                return SERPAPI_RESPONSE_JSON

        monkeypatch.setattr(trends.requests, "get", lambda url, params=None, timeout=None, **kwargs: MockResp())
        _isolate_quota(monkeypatch, tmp_path)

        result = trends.fetch("cache-expired-test")
        # Cache expirada → foi buscar dados frescos com direção "estável"
        assert result["direction"] == "estável"


class TestTrendsTimeseriesOnly:
    """Testa o modo timeseries_only (1 call, sem related_queries)."""

    def test_timeseries_only_spends_one(self, monkeypatch, tmp_path):
        """timeseries_only=True faz só 1 call, não 2."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))

        call_count = 0

        class MockResp:
            status_code = 200
            @staticmethod
            def json():
                return SERPAPI_RESPONSE_JSON

        def mock_get(url, params=None, timeout=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockResp()

        monkeypatch.setattr(trends.requests, "get", mock_get)
        _isolate_quota(monkeypatch, tmp_path)

        result = trends.fetch("ts-only-test", timeseries_only=True)

        assert call_count == 1  # só timeseries, sem related_queries
        assert quota.get_usage()["calls"] == 1  # 1 quota unit
        assert result["related_queries"] == []
        assert result["direction"] == "estável"


class TestTrendsForceRefresh:
    """Testa o modo force_refresh (ignora cache)."""

    def test_force_refresh_ignores_cache(self, monkeypatch, tmp_path):
        """force_refresh=True ignora cache válida e chama a API."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))

        call_count = 0

        class MockResp:
            status_code = 200
            @staticmethod
            def json():
                return SERPAPI_RESPONSE_JSON

        def mock_get(url, params=None, timeout=None, **kwargs):
            nonlocal call_count
            call_count += 1
            return MockResp()

        monkeypatch.setattr(trends.requests, "get", mock_get)
        _isolate_quota(monkeypatch, tmp_path)

        # Primeira call — popula cache
        trends.fetch("force-refresh-test")
        assert call_count == 2  # timeseries + related

        # Segunda call sem force_refresh — cache hit
        trends.fetch("force-refresh-test")
        assert call_count == 2  # não aumentou

        # Terceira call com force_refresh — ignora cache
        trends.fetch("force-refresh-test", force_refresh=True)
        assert call_count == 4  # +2 (timeseries + related)
