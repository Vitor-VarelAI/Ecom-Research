"""Regression tests: non-2xx provider bodies feed sanitized, bounded diagnostics.

The three adapters previously discarded non-2xx bodies, so a root cause
looked identical (a bare HTTP status) whether the provider rejected the
token, the quota, or the request shape.  These tests mock documented
provider error bodies and prove the returned error carries
source/status/message, sanitized (tokens/URLs redacted) and bounded, and
that nothing raw (body, token, token-bearing URL) is returned or logged.
"""

from __future__ import annotations

import json

from pipeline import quota
from pipeline.adapters import apify_ads, serp_seo, trends

TRENDS_OK = {
    "interest_over_time": {
        "timeline_data": [
            {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
            {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
        ],
    },
    "interest_by_region": [],
}


def _isolate_quota(monkeypatch, tmp_path):
    """Point the real quota counter at a temp file (never touch data/quota.json)."""
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(quota, "QUOTA_FILE", str(quota_file))


class Response:
    """Minimal requests.Response stand-in: status plus raw text body."""

    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


# ── Apify (documented error body: nested ``error.message``) ────────────────

def test_apify_non_2xx_nested_error_message_is_parsed(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    body = {"error": {"type": "InvalidInput", "message": "Invalid 'urls' input: keyword too long"}}
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(400, json.dumps(body)))

    result = apify_ads.fetch("mochila")

    assert result["error"] == "Apify: HTTP 400 — Invalid 'urls' input: keyword too long"


def test_apify_non_2xx_string_error_is_parsed(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(500, json.dumps({"error": "Actor run failed"})))

    assert apify_ads.fetch("mochila")["error"] == "Apify: HTTP 500 — Actor run failed"


def test_apify_non_2xx_html_body_is_safe_fallback(monkeypatch):
    """A non-JSON dump (e.g. an HTML gateway page) never leaks into the error."""
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(502, "<html><body>Bad Gateway</body></html>"))

    result = apify_ads.fetch("mochila")

    assert result["error"] == "Apify: HTTP 502"
    assert "Bad Gateway" not in json.dumps(result)
    assert "html" not in json.dumps(result)


def test_apify_known_token_in_body_is_redacted(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "known-token-abc123")
    body = {"error": {"message": "Authentication failed for token known-token-abc123"}}
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(401, json.dumps(body)))

    result = apify_ads.fetch("mochila")

    assert "known-token-abc123" not in json.dumps(result)
    assert "Authentication failed" in result["error"]


def test_apify_token_bearing_url_is_redacted_even_without_env_match(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "other-token")
    body = {
        "error": {
            "message": "Upstream error at https://api.apify.com/v2/acts/x/run-sync-get-dataset-items?token=zzz9qqq"
        }
    }
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(502, json.dumps(body)))

    result = apify_ads.fetch("mochila")

    assert "api.apify.com" not in json.dumps(result)
    assert "zzz9qqq" not in json.dumps(result)
    assert "[removido]" in result["error"]


def test_provider_message_is_truncated_to_bounded_length(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    long_message = "diagnóstico " * 80  # ~1.3KB of provider noise
    monkeypatch.setattr(apify_ads.requests, "post", lambda *a, **k: Response(500, json.dumps({"error": long_message})))

    result = apify_ads.fetch("mochila")

    assert result["error"].startswith("Apify: HTTP 500 — ")
    assert result["error"].endswith("…")
    assert len(result["error"]) < 200  # bounded fragment, never a body dump


# ── SerpAPI Trends (documented error body: string ``error``) ───────────────

def test_trends_non_2xx_parses_sanitized_message(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    body = {"error": "Invalid API key. Please specify a valid api_key."}
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(401, json.dumps(body)))

    result = trends.fetch("mochila")

    assert result["error"] == "SerpAPI Trends: HTTP 401 — Invalid API key. Please specify a valid api_key."
    assert "sekrit-key" not in json.dumps(result)
    assert "sekrit-key" not in caplog.text


def test_trends_non_2xx_redacts_token_and_token_bearing_url(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    body = {"error": "Invalid API key: sekrit-key — see https://serpapi.com/search?api_key=sekrit-key"}
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(401, json.dumps(body)))

    result = trends.fetch("mochila")

    assert "sekrit-key" not in json.dumps(result)
    assert "serpapi.com" not in json.dumps(result)
    assert "Invalid API key" in result["error"]
    assert "[removido]" in result["error"]


def test_trends_403_keeps_fallback_when_no_body_message(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(403, ""))

    result = trends.fetch("mochila")

    assert result["error"] == "SerpAPI Trends: chave inválida ou quota esgotada (HTTP 403)"


def test_trends_403_with_body_message_uses_sanitized_message(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    body = {"error": "Account doesn't exist or is not active."}
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(403, json.dumps(body)))

    result = trends.fetch("mochila")

    assert result["error"] == "SerpAPI Trends: HTTP 403 — Account doesn't exist or is not active."


def test_trends_200_with_error_body_is_sanitized(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(200, json.dumps({"error": "Invalid API key: sekrit-key"})))

    result = trends.fetch("mochila", timeseries_only=True)

    assert result["error"] == "SerpAPI Trends: Invalid API key: [removido]"
    assert "sekrit-key" not in json.dumps(result)


def test_fetch_related_non_2xx_parses_sanitized_message(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    body = {"error": "Insufficient balance. Please top up your account."}
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(402, json.dumps(body)))

    result = trends.fetch_related("mochila")

    assert result["error"] == "SerpAPI RELATED_QUERIES: HTTP 402 — Insufficient balance. Please top up your account."


def test_trends_date_parameter_is_documented_form(monkeypatch, tmp_path):
    """The timeseries request must use the documented ``today 3-m`` date form."""
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs["params"])
        return Response(200, json.dumps(TRENDS_OK))

    monkeypatch.setattr(trends.requests, "get", get)

    trends.fetch("mochila", timeseries_only=True)

    assert calls[0]["date"] == "today 3-m"


# ── SerpAPI SERP ────────────────────────────────────────────────────────────

def test_serp_seo_non_2xx_parses_sanitized_message(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    body = {"error": "Invalid API key. Please specify a valid api_key."}
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: Response(401, json.dumps(body)))

    result = serp_seo.fetch("mochila")

    assert result["error"] == "SerpAPI SERP: HTTP 401 — Invalid API key. Please specify a valid api_key."


def test_serp_seo_200_with_error_body_is_sanitized(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "sekrit-key")
    monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: Response(200, json.dumps({"error": "Invalid API key: sekrit-key"})))

    result = serp_seo.fetch("mochila")

    assert result["error"] == "SerpAPI SERP: Invalid API key: [removido]"
    assert "sekrit-key" not in json.dumps(result)
