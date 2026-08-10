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
        assert "_cached_at" not in result1  # fresh adapter output has no cache metadata
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


class TestTrendsRequestContract:
    """Asserts the exact SerpAPI request for the Trends timeseries call."""

    def test_timeseries_request_url_params_and_timeout(self, monkeypatch, tmp_path):
        """Regression: the corrected ``date=today 3-m`` request needs the 60s
        timeout to complete against SerpAPI instead of timing out at 15s.

        A live call at REQUEST_TIMEOUT=15 timed out without charging quota;
        the same call succeeded at 60s and charged exactly one call.  The
        exact URL, params and timeout are pinned here.
        """
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
        calls = []

        class MockResp:
            status_code = 200

            @staticmethod
            def json():
                return SERPAPI_RESPONSE_JSON

        def mock_get(url, params=None, timeout=None, **kwargs):
            calls.append((url, params, timeout))
            return MockResp()

        monkeypatch.setattr(trends.requests, "get", mock_get)
        _isolate_quota(monkeypatch, tmp_path)

        result = trends.fetch("mochila-contract", timeseries_only=True)

        assert calls == [(
            trends.SERPAPI_URL,
            {
                "engine": "google_trends",
                "q": "mochila-contract",
                "date": "today 3-m",
                "api_key": "fake-key",
            },
            60,
        )]
        assert "error" not in result


class TestTrendsMixedScoreSort:
    """Scores mistos numéricos/"Breakout" ordenam deterministicamente sem
    TypeError — Breakout primeiro, depois numéricos descendente."""

    def _fetch_with_rising(self, monkeypatch, tmp_path, rising):
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
        responses = {
            "interest_over_time": {"timeline_data": [
                {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
                {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
            ]},
            "interest_by_region": [],
            "related_queries": {"rising": rising},
        }
        calls = {"n": 0}

        class MockResp:
            status_code = 200

            @staticmethod
            def json():
                return responses

        def mock_get(url, params=None, timeout=None, **kwargs):
            calls["n"] += 1
            return MockResp()

        monkeypatch.setattr(trends.requests, "get", mock_get)
        _isolate_quota(monkeypatch, tmp_path)
        return trends.fetch("mix-sort-test")

    def test_mixed_breakout_and_numeric_sorts_deterministically(self, monkeypatch, tmp_path):
        result = self._fetch_with_rising(monkeypatch, tmp_path, [
            {"query": "mochila premium", "value": "Breakout"},
            {"query": "mochila escolar", "value": 300},
            {"query": "mochila viagem", "value": 150},
            {"query": "mochila gaming", "value": 800},
        ])
        assert "error" not in result
        scores = [item["score"] for item in result["related_queries"]]
        assert scores == ["Breakout", 800, 300, 150]  # Breakout primeiro, numéricos desc.

    def test_numeric_only_still_sorted_descending(self, monkeypatch, tmp_path):
        result = self._fetch_with_rising(monkeypatch, tmp_path, [
            {"query": "mochila a", "value": 100},
            {"query": "mochila b", "value": 900},
            {"query": "mochila c", "value": 250},
        ])
        assert [item["score"] for item in result["related_queries"]] == [900, 250, 100]

    def test_scores_missing_never_crash(self, monkeypatch, tmp_path):
        result = self._fetch_with_rising(monkeypatch, tmp_path, [
            {"query": "mochila a"},
            {"query": "mochila b", "value": None},
            "não-dict",
        ])
        assert "error" not in result
        assert isinstance(result["related_queries"], list)


class TestTrendsChargesCallsActuallyMade:
    """Quota cobra as calls realmente feitas: a 2ª call só é cobrada quando o
    pedido foi enviado (exceção de rede = call nunca feita = sem cobrança)."""

    def test_second_call_network_error_not_charged(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
        _isolate_quota(monkeypatch, tmp_path)
        calls = {"n": 0}

        class MockResp:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "interest_over_time": {"timeline_data": [
                        {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
                        {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
                    ]},
                    "interest_by_region": [],
                }

        def mock_get(url, params=None, timeout=None, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise trends.requests.ConnectionError("2ª call falhou antes de enviar")
            return MockResp()

        monkeypatch.setattr(trends.requests, "get", mock_get)
        result = trends.fetch("charge-2nd-fail")

        assert "error" not in result  # timeseries ok, related degrada graciosamente
        assert quota.get_usage()["calls"] == 1  # só a timeseries (feita) é cobrada

    def test_first_call_http_error_is_charged_call_made(self, monkeypatch, tmp_path):
        """HTTP de erro na 1ª call: a call foi realmente feita (a SerpAPI
        conta-a), por isso a quota é cobrada mesmo sem sucesso."""
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
        _isolate_quota(monkeypatch, tmp_path)

        class MockResp:
            status_code = 429
            text = ""

        monkeypatch.setattr(trends.requests, "get", lambda *a, **k: MockResp())
        result = trends.fetch("charge-http-error")

        assert "error" in result
        assert quota.get_usage()["calls"] == 1  # call feita → cobrada


class TestTrendsNonDictRoots:
    """HTTP-200 com raiz não-dict (lista/string/null) nunca faz ``.get``/``in``
    sobre a raiz: erro estruturado sanitizado na timeseries, lista vazia
    graciosa nas related."""

    def _fetch_raw(self, monkeypatch, tmp_path, payload):
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
        _isolate_quota(monkeypatch, tmp_path)

        class MockResp:
            status_code = 200

            @staticmethod
            def json():
                return payload

        monkeypatch.setattr(trends.requests, "get", lambda *a, **k: MockResp())
        return trends.fetch("root-check")

    @pytest.mark.parametrize("bad_root", [[], None, "texto", 42])
    def test_timeseries_non_dict_root_returns_structured_error(self, monkeypatch, tmp_path, bad_root):
        result = self._fetch_raw(monkeypatch, tmp_path, bad_root)
        assert "error" in result
        assert "raiz não é objeto JSON" in result["error"]

    def test_related_non_dict_root_returns_graceful_empty(self, monkeypatch, tmp_path):
        result = self._fetch_raw(monkeypatch, tmp_path, [])
        assert "error" in result  # timeseries first — the root is shared

    def test_fetch_related_non_dict_root_is_graceful(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SERPAPI_KEY", "fake-key")
        monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
        _isolate_quota(monkeypatch, tmp_path)

        class MockResp:
            status_code = 200

            @staticmethod
            def json():
                return ["not-a-dict"]

        monkeypatch.setattr(trends.requests, "get", lambda *a, **k: MockResp())
        result = trends.fetch_related("root-rel")
        assert "error" in result
        assert "resposta inválida" in result["error"]


class TestTrendsMalformedTimeline:
    """Timeline/region malformed (non-dict items, non-dict nested values,
    NaN/Infinity) nunca crasham nem fabricam valores de tendência: degradam
    para o erro estruturado de dados insuficientes ou, quando há pontos
    válidos suficientes, usam só valores finitos."""

    def _parse(self, timeline=None, regions=None):
        payload: dict = {"interest_over_time": {"timeline_data": timeline or []}}
        if regions is not None:
            payload["interest_by_region"] = regions
        return trends._parse_serpapi_response(payload)

    def test_non_dict_point_skipped_to_insufficient_data(self):
        result = self._parse(
            timeline=[
                "não-dict",
                {"date": "2026-04-14", "values": [{"extracted_value": 1}]},
            ]
        )
        assert "error" in result  # erro estruturado de dados insuficientes
        assert "Trends:" in result["error"]

    def test_values_entry_not_a_dict_never_crashes(self):
        """vals[0] string (o defeito original: .get sobre str → AttributeError)
        degrada para o erro estruturado, nunca crasha."""
        result = self._parse(
            timeline=[
                {"date": "2026-04-14", "values": ["100"]},
                {"date": "2026-04-21", "values": [{"extracted_value": 5}]},
            ]
        )
        assert "error" in result  # só 1 ponto válido

    def test_nan_infinity_values_degrade_to_structured_error(self):
        result = self._parse(
            timeline=[
                {"date": "2026-04-14", "values": [{"extracted_value": float("nan")}]},
                {"date": "2026-04-21", "values": [{"extracted_value": float("inf")}]},
            ]
        )
        assert "error" in result

    def test_mixed_valid_and_malformed_uses_only_finite_values(self):
        result = self._parse(
            timeline=[
                {"date": "2026-04-14", "values": [{"extracted_value": 10}]},
                {"date": "2026-04-21", "values": [{"extracted_value": "10"}]},      # string numérica aceite
                {"date": "2026-04-28", "values": [{"extracted_value": float("nan")}]},  # ignorado
                {"date": "2026-05-05", "values": [{"extracted_value": {"x": 1}}]},   # shape inválido ignorado
                {"date": "2026-05-12", "values": [{"extracted_value": True}]},       # bool ignorado
            ]
        )
        assert "error" not in result
        assert result["direction"] == "estável"
        # Os dois pontos válidos (10, 10) dão média plana → slope 0.
        assert result["slope_pct"] == 0.0
        assert result["seasonal"] is False

    def test_bool_values_never_accepted(self):
        result = self._parse(
            timeline=[
                {"date": "2026-04-14", "values": [{"extracted_value": True}]},
                {"date": "2026-04-21", "values": [{"extracted_value": False}]},
            ]
        )
        assert "error" in result

    def test_peak_label_tracks_the_valid_point_not_the_timeline_index(self):
        """O pico é ancorado ao ponto real (não a um índice deslocado): um
        ponto malformado no início não rouba o pico para a data errada."""
        result = self._parse(
            timeline=[
                {"date": "lixo", "values": ["não-dict"]},                       # descartado
                {"date": "2026-05-01", "values": [{"extracted_value": 5}]},
                {"date": "2026-05-02", "values": [{"extracted_value": 90}]},    # pico real
                {"date": "2026-05-03", "values": [{"extracted_value": 8}]},
            ]
        )
        assert result["seasonal"] is True
        assert result["peak"] == "2026-05-02"

    def test_region_non_dict_and_non_finite_values_are_dropped(self):
        result = self._parse(
            timeline=[
                {"date": "2026-04-14", "values": [{"extracted_value": 10}]},
                {"date": "2026-04-21", "values": [{"extracted_value": 20}]},
            ],
            regions=[
                {"geo": "PT", "value": "100"},            # string numérica → aceite
                {"geo": "ES", "value": float("nan")},     # NaN → descartado
                {"geo": "FR", "value": float("inf")},     # Inf → descartado
                "não-dict",                               # não-dict → descartado
                {"geo": "DE", "value": {"x": 1}},         # shape inválido → descartado
                {"value": 42},                            # sem geo → descartado
            ],
        )
        assert "error" not in result
        assert [item["geo"] for item in result["top_geos"]] == ["PT"]
        assert result["top_geos"][0]["value"] == 100.0
