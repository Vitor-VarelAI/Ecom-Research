"""Cache validation: finite timestamps, error payloads rejected, shape checks.

Malformed valid JSON is a miss — never a crash, never a suppressed refresh.
Legacy valid cache entries stay compatible; the 30-day TTL is unchanged.
"""

from __future__ import annotations

import json
import math
import os
import time

import pytest

from pipeline import quota
from pipeline.adapters import serp_seo, trends

GOOD_TRENDS = {"direction": "estável", "slope_pct": 0.0, "seasonal": False,
               "peak": None, "top_geos": [], "related_queries": []}
GOOD_SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}
GOOD_RELATED = {"related_queries": []}


class Response:
    def __init__(self, payload):
        self.status_code = 200
        self.payload = payload

    def json(self):
        return self.payload


def _isolate_quota(monkeypatch, tmp_path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))
    monkeypatch.setattr(serp_seo.quota, "QUOTA_FILE", str(quota_file))


@pytest.mark.parametrize("bad_ts", [True, False, "123", None, float("nan"), float("inf"), float("-inf")])
def test_trends_cached_at_rejects_bool_nonfinite_and_non_numeric(monkeypatch, tmp_path, bad_ts):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    cache_path = trends._cache_path("kw-ts")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**GOOD_TRENDS, "_cached_at": bad_ts}, f, allow_nan=True)
    assert trends._read_cache("kw-ts") is None  # miss, not a crash, not a fresh hit


def test_trends_cached_at_nan_is_not_served(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    cache_path = trends._cache_path("kw-nan")
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write('{"direction": "estável", "slope_pct": 0.0, "related_queries": [], "_cached_at": NaN}')
    assert trends._read_cache("kw-nan") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"direction": 5, "slope_pct": 0.0, "related_queries": []},           # non-string direction
        {"direction": "", "slope_pct": 0.0, "related_queries": []},          # blank direction
        {"direction": "estável", "slope_pct": "x", "related_queries": []},   # non-numeric slope
        {"direction": "estável", "slope_pct": True, "related_queries": []},  # bool slope
        {"direction": "estável", "slope_pct": float("nan"), "related_queries": []},
        {"direction": "estável", "slope_pct": 0.0, "related_queries": "x"},  # non-list related
        {"direction": "estável", "slope_pct": 0.0},                          # missing related
        {"direction": "estável", "slope_pct": 0.0, "related_queries": [], "error": "boom"},
    ],
)
def test_trends_full_cache_shape_validation(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    cache_path = trends._cache_path("kw-shape")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**payload, "_cached_at": time.time()}, f)
    assert trends._read_cache("kw-shape") is None  # miss, never a crash


def test_trends_full_cache_valid_legacy_entry_still_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    cache_path = trends._cache_path("kw-valid")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**GOOD_TRENDS, "_cached_at": time.time()}, f)
    cached = trends._read_cache("kw-valid")
    assert cached is not None
    assert cached["direction"] == "estável"
    assert "_cached_at" not in trends.fetch("kw-valid")  # served and metadata-stripped


@pytest.mark.parametrize(
    "payload",
    [
        {"related_queries": "x"},
        {"related_queries": {}},
        {},
        {"related_queries": [], "error": "boom"},
    ],
)
def test_trends_related_cache_shape_validation(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    cache_path = trends._related_cache_path("kw-rel")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**payload, "_cached_at": time.time()}, f)
    assert trends._read_related_cache("kw-rel") is None


def test_trends_related_cache_valid_empty_list_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    cache_path = trends._related_cache_path("kw-rel-ok")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**GOOD_RELATED, "_cached_at": time.time()}, f)
    cached = trends._read_related_cache("kw-rel-ok")
    assert cached == {"related_queries": []}


@pytest.mark.parametrize(
    "payload",
    [
        {"organic_results": "x", "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"},
        {"organic_results": [], "ads_count": "n", "paa_questions": [], "seo_opportunity": "alta"},
        {"organic_results": [], "ads_count": True, "paa_questions": [], "seo_opportunity": "alta"},
        {"organic_results": [], "ads_count": float("inf"), "paa_questions": [], "seo_opportunity": "alta"},
        {"organic_results": [], "ads_count": 0, "paa_questions": "x", "seo_opportunity": "alta"},
        {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": ""},
        {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": 5},
        {"organic_results": [], "ads_count": 0, "paa_questions": []},
        {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta", "error": "boom"},
    ],
)
def test_seo_cache_shape_validation(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))
    cache_path = serp_seo._cache_path("kw-seo")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**payload, "_cached_at": time.time()}, f)
    assert serp_seo._read_cache("kw-seo") is None


@pytest.mark.parametrize("bad_ts", [True, False, "123", None, float("nan"), float("inf")])
def test_seo_cached_at_rejects_bool_nonfinite_and_non_numeric(monkeypatch, tmp_path, bad_ts):
    monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))
    cache_path = serp_seo._cache_path("kw-seo-ts")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**GOOD_SEO, "_cached_at": bad_ts}, f, allow_nan=True)
    assert serp_seo._read_cache("kw-seo-ts") is None  # miss, not a crash, not a fresh hit


def test_seo_cache_valid_legacy_entry_still_hits(monkeypatch, tmp_path):
    monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))
    cache_path = serp_seo._cache_path("kw-seo-ok")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({**GOOD_SEO, "_cached_at": time.time()}, f)
    cached = serp_seo._read_cache("kw-seo-ok")
    assert cached["seo_opportunity"] == "alta"
    assert "_cached_at" not in serp_seo.fetch("kw-seo-ok")


def test_error_payloads_are_removed_from_all_three_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(serp_seo, "CACHE_DIR", str(tmp_path))
    full = trends._cache_path("kw-err-full")
    related = trends._related_cache_path("kw-err-rel")
    seo = serp_seo._cache_path("kw-err-seo")
    for path, payload in ((full, {"error": "x", "_cached_at": time.time()}),
                          (related, {"error": "x", "_cached_at": time.time()}),
                          (seo, {"error": "x", "_cached_at": time.time()})):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    assert trends._read_cache("kw-err-full") is None
    assert trends._read_related_cache("kw-err-rel") is None
    assert serp_seo._read_cache("kw-err-seo") is None
    assert not os.path.exists(full)
    assert not os.path.exists(related)
    assert not os.path.exists(seo)


def test_malformed_shape_cache_is_a_miss_not_a_suppressed_refresh(monkeypatch, tmp_path):
    """A shape-invalid cache entry must not suppress a refresh: fetch() calls
    the API and returns fresh data."""
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    _isolate_quota(monkeypatch, tmp_path)
    cache_path = trends._cache_path("kw-miss-refresh")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"direction": "estável", "slope_pct": 0.0, "related_queries": "garbage",
                   "_cached_at": time.time()}, f)

    calls = []
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: calls.append(a) or Response({
        "interest_over_time": {"timeline_data": [
            {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
            {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
        ]},
        "interest_by_region": [],
    }))

    result = trends.fetch("kw-miss-refresh")
    assert result["direction"] == "estável"  # API was consulted, no suppressed refresh
    assert calls  # the API was actually hit
    assert "error" not in result
