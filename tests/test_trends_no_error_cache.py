"""Trends: error payloads are never cached; legacy error cache entries are misses."""

from __future__ import annotations

import json
import os
import time

from pipeline import quota
from pipeline.adapters import trends

GOOD_RESPONSE = {
    "interest_over_time": {
        "timeline_data": [
            {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
            {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
            {"date": "2026-04-28", "values": [{"extracted_value": 51}]},
            {"date": "2026-05-05", "values": [{"extracted_value": 49}]},
        ],
    },
    "interest_by_region": [],
}
BAD_RESPONSE = {  # single timeline point -> parse error
    "interest_over_time": {"timeline_data": [{"date": "2026-01-01", "values": [{"extracted_value": 5}]}]},
}


class Response:
    def __init__(self, payload):
        self.status_code = 200
        self.payload = payload

    def json(self):
        return self.payload


def _prepare(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(payload))
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))


def test_parse_error_is_never_cached(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, BAD_RESPONSE)
    result = trends.fetch("kw-error-cache")
    assert "error" in result
    assert not os.path.exists(trends._cache_path("kw-error-cache"))


def test_write_cache_skips_error_payloads(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    trends._write_cache("kw-x", {"error": "boom"})
    assert not os.path.exists(trends._cache_path("kw-x"))


def test_write_cache_still_caches_valid_results(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    trends._write_cache("kw-y", {"direction": "subida", "slope_pct": 5.0, "related_queries": []})
    assert os.path.exists(trends._cache_path("kw-y"))


def test_legacy_cached_error_is_a_miss_and_is_removed(monkeypatch, tmp_path):
    _prepare(monkeypatch, tmp_path, GOOD_RESPONSE)
    path = trends._cache_path("kw-legacy-error")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"error": "Trends: dados insuficientes", "_cached_at": time.time()}, f)

    result = trends.fetch("kw-legacy-error")

    assert "error" not in result  # API was consulted; stale error cache ignored
    assert result["direction"] == "estável"
    # The legacy error payload was replaced by a fresh valid cache entry.
    cached = json.load(open(path, encoding="utf-8"))
    assert "error" not in cached


def test_error_result_does_not_poison_the_next_fetch(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    state = {"payload": BAD_RESPONSE}
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: Response(state["payload"]))
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))

    assert "error" in trends.fetch("kw-recover")
    state["payload"] = GOOD_RESPONSE
    # Same day, same keyword: the error must NOT be cached, so the retry hits the API.
    assert "error" not in trends.fetch("kw-recover")
