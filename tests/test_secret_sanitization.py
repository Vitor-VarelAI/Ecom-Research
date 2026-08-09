"""Request exceptions never leak secrets: sanitized messages in adapters and
nothing reaching pack/run/digest/Telegram state."""

from __future__ import annotations

import json
import os

import requests

from pipeline import quota
from pipeline.adapters import serp_seo, trends


def _isolate_quota(monkeypatch, tmp_path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))
    monkeypatch.setattr(serp_seo.quota, "QUOTA_FILE", str(quota_file))


def test_trends_network_error_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)

    def boom(*args, **kwargs):
        raise requests.ConnectionError("url https://serpapi.com/search?api_key=SECRET broke")

    monkeypatch.setattr(trends.requests, "get", boom)
    result = trends.fetch("kw-secret")

    assert "SECRET" not in json.dumps(result)
    assert "serpapi.com" not in json.dumps(result)
    assert "ConnectionError" in result["error"]  # exception class name only
    assert "SECRET" not in caplog.text
    assert "serpapi.com" not in caplog.text
    assert quota.get_usage()["calls"] == 0  # failure before charge


def test_trends_related_call_inside_fetch_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    responses = [
        type("R", (), {"status_code": 200, "json": lambda self: {
            "interest_over_time": {"timeline_data": [
                {"date": "2026-04-14", "values": [{"extracted_value": 50}]},
                {"date": "2026-04-21", "values": [{"extracted_value": 50}]},
            ]},
            "interest_by_region": [],
        }})(),
    ]

    def get(*args, **kwargs):
        if responses:
            return responses.pop(0)
        raise requests.ConnectionError("api_key=SECRET on related queries")

    monkeypatch.setattr(trends.requests, "get", get)
    result = trends.fetch("kw-secret-2")

    assert "SECRET" not in json.dumps(result)
    assert "SECRET" not in caplog.text
    # Timeseries succeeded; the failed related call degrades gracefully.
    assert result["direction"] == "estável"
    assert result["related_queries"] == []


def test_fetch_related_network_error_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(trends.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.Timeout("api_key=SECRET timed out")))
    result = trends.fetch_related("kw-secret-rel")
    assert "SECRET" not in json.dumps(result)
    assert "SECRET" not in caplog.text
    assert "Timeout" in result["error"]


def test_serp_seo_network_error_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("api_key=SECRET broken")))
    result = serp_seo.fetch("kw-secret-seo")
    assert "SECRET" not in json.dumps(result)
    assert "serpapi.com" not in json.dumps(result)
    assert "ConnectionError" in result["error"]
    assert "SECRET" not in caplog.text
    assert quota.get_usage()["calls"] == 0


def test_http_error_logs_status_only_not_body(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: type("R", (), {
        "status_code": 429,
        "text": '{"error": "api_key=SECRET echoed in body"}',
    })())
    result = serp_seo.fetch("kw-secret-429")
    assert result["error"] == "SerpAPI SERP: HTTP 429"
    assert "SECRET" not in caplog.text
    assert "echoed in body" not in caplog.text


def test_technical_keyword_exception_is_sanitized(monkeypatch, caplog):
    import run as run_module

    monkeypatch.setattr(
        run_module,
        "run_keyword",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("https://provider.test?api_key=SECRET")
        ),
    )

    result = run_module._safe_run_keyword("mochila")

    assert result["error"] == "Erro técnico inesperado (RuntimeError)"
    assert "SECRET" not in json.dumps(result)
    assert "provider.test" not in caplog.text


# ── End-to-end: a secret in a request exception cannot reach pack/run/digest ──

def test_secret_exception_never_reaches_pack_manifest_or_digest(monkeypatch, tmp_path, capsys):
    import run as run_module

    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")  # other suites pop this at import
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "keywords.txt").write_text("mochila\n", encoding="utf-8")
    ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
           "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
           "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
           "saturation": "baixa"}
    CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda kw: dict(ADS))
    monkeypatch.setattr(run_module.trends.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("api_key=SECRET broken")))
    monkeypatch.setattr(run_module.serp_seo.requests, "get", lambda *a, **k: (_ for _ in ()).throw(
        requests.ConnectionError("api_key=SECRET broken")))
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run_module, "_csv_data", lambda path: dict(CSV))
    generated = []
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run_module._run_all(["mochila"], send=False)

    # Run result, pack kwargs, stdout digest and persisted manifest: no secret.
    assert "SECRET" not in json.dumps(results)
    assert "SECRET" not in json.dumps(generated, default=str)
    assert "SECRET" not in capsys.readouterr().out
    store = run_module._run_store()
    run_id = run_module._compute_run_id("all", ["mochila"], None)
    manifest = store.load(run_id)
    assert "SECRET" not in json.dumps(manifest)
    # The sanitized class-name message is what travels inside the result
    # instead of the raw exception text.
    assert results[0]["trends_data"]["error"] == "Erro de rede na SerpAPI (ConnectionError)"
    assert results[0]["seo_data"]["error"] == "Erro de rede na SerpAPI (SERP) (ConnectionError)"
