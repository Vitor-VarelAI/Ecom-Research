"""
Testes do adapter Meta Ad Library.

Usa fixture com resposta JSON simulada da Meta API.
Sem chamadas reais à rede.
"""

import json
import os

import pytest

# Forçar .env vazio antes de importar
os.environ.pop("META_ACCESS_TOKEN", None)

from pipeline.adapters import meta_ads

# ── Fixture: resposta simulada da Meta Ad Library API ──────────────────────

META_RESPONSE_JSON = {
    "data": [
        {
            "id": "123456",
            "ad_creation_time": "2026-06-01T00:00:00+0000",
            "ad_creative_bodies": [
                "Mochila personalizada com o teu nome — entrega em 48h!",
                "A mochila que toda a gente quer. Personaliza já.",
            ],
            "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=123456",
            "page_name": "MochilasXPTO",
            "delivery_by_region": [
                {"region": "PT", "percentage": 45},
                {"region": "ES", "percentage": 30},
            ],
        },
        {
            "id": "789012",
            "ad_creation_time": "2026-04-15T00:00:00+0000",
            "ad_creative_bodies": [
                "As mochilas mais resistentes do mercado — garantia 5 anos.",
            ],
            "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=789012",
            "page_name": "BagMaster",
            "delivery_by_region": [
                {"region": "EU", "percentage": 60},
            ],
        },
        {
            "id": "345678",
            "ad_creation_time": "2026-04-01T00:00:00+0000",
            "ad_creative_bodies": [
                "Mochila leve, cabe tudo — 30% desconto esta semana!",
            ],
            "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=345678",
            "page_name": "MochilasXPTO",
            "delivery_by_region": [],
        },
    ],
    "paging": {"cursors": {}, "next": None},
}

META_EMPTY_JSON = {"data": [], "paging": {"cursors": {}, "next": None}}


# ── Tests ──────────────────────────────────────────────────────────────────


class TestMetaAdsWithoutToken:
    """Sem META_ACCESS_TOKEN definida, deve devolver erro graciosamente."""

    def test_no_token_returns_error(self, monkeypatch):
        monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
        result = meta_ads.fetch("mochila personalizada")
        assert "error" in result
        assert "META_ACCESS_TOKEN" in result["error"]


class TestMetaAdsParse:
    """Testa o parsing da resposta JSON (sem chamadas reais)."""

    def test_parse_response(self, monkeypatch):
        """Simula uma resposta bem-sucedida da API."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")

        # Mock requests.get
        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return META_RESPONSE_JSON

        def mock_get(url, params, timeout):
            return MockResponse()

        monkeypatch.setattr(meta_ads.requests, "get", mock_get)

        result = meta_ads.fetch("mochila personalizada")

        # Deve ter dados, não erro
        assert "error" not in result or result.get("total_ads", 0) > 0
        assert result["total_ads"] == 3
        assert result["saturation"] == "baixa"  # 3 < 10

        # Top 3 ads por longevidade
        assert len(result["top_ads"]) == 3
        # Mais antigo primeiro: ad_creation_time 2026-04-01
        assert result["top_ads"][0]["advertiser"] == "MochilasXPTO"  # criado 2026-04-01
        assert result["top_ads"][0]["days_active"] > result["top_ads"][2]["days_active"]

        # Copy angles
        assert len(result["copy_angles"]) >= 1

    def test_empty_response(self, monkeypatch):
        """Resposta vazia da API (zero ads)."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")

        class MockResponse:
            status_code = 200

            @staticmethod
            def json():
                return META_EMPTY_JSON

        monkeypatch.setattr(meta_ads.requests, "get", lambda url, params, timeout: MockResponse())

        result = meta_ads.fetch("keyword inexistente")
        assert result["total_ads"] == 0
        assert result["top_ads"] == []
        assert result["saturation"] == "baixa"
        assert "error" in result  # zero ads é tratado como erro informativo

    def test_http_error(self, monkeypatch):
        """Erro HTTP deve ser tratado graciosamente."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")

        class MockResponse:
            status_code = 403
            text = "Forbidden"

        monkeypatch.setattr(meta_ads.requests, "get", lambda url, params, timeout: MockResponse())

        result = meta_ads.fetch("test")
        assert "error" in result
        assert "403" in result["error"]

    def test_network_error(self, monkeypatch):
        """Erro de rede deve ser tratado graciosamente."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")

        def mock_get_raise(url, params, timeout):
            raise meta_ads.requests.ConnectionError("Timeout")

        monkeypatch.setattr(meta_ads.requests, "get", mock_get_raise)

        result = meta_ads.fetch("test")
        assert "error" in result
        assert "Erro de rede" in result["error"]
