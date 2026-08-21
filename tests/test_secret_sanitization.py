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


def test_http_error_returns_sanitized_message_logs_status_only(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")
    _isolate_quota(monkeypatch, tmp_path)
    monkeypatch.setattr(serp_seo.requests, "get", lambda *a, **k: type("R", (), {
        "status_code": 429,
        "text": '{"error": "api_key=SECRET echoed in body"}',
    })())
    result = serp_seo.fetch("kw-secret-429")
    # The provider message field is parsed, sanitized (credential pair
    # redacted) and bounded — never the raw body, token or URL.
    assert result["error"] == "SerpAPI SERP: HTTP 429 — [removido] echoed in body"
    assert "SECRET" not in json.dumps(result)
    assert "SECRET" not in caplog.text
    assert "echoed in body" not in caplog.text


def test_technical_keyword_exception_is_sanitized(monkeypatch, caplog):
    import run as run_module
    from tests.fixtures.products_helpers import product

    monkeypatch.setattr(
        run_module,
        "run_keyword",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("https://provider.test?api_key=SECRET")
        ),
    )

    result = run_module._safe_run_keyword(product("mochila"))

    assert result["error"] == "Erro técnico inesperado (RuntimeError)"
    assert "SECRET" not in json.dumps(result)
    assert "provider.test" not in caplog.text


# ── End-to-end: a secret in a request exception cannot reach pack/run/digest ──

def test_secret_exception_never_reaches_pack_manifest_or_digest(monkeypatch, tmp_path, capsys):
    import run as run_module
    from tests.fixtures.products_helpers import write_registry

    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("SERPAPI_KEY", "fake-key")  # other suites pop this at import
    registry = write_registry(tmp_path, ["mochila"])
    products = list(registry.products)
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
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: dict(CSV))
    generated = []
    monkeypatch.setattr(run_module.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run_module._run_all(products, send=False)

    # Run result, pack kwargs, stdout digest and persisted manifest: no secret.
    assert "SECRET" not in json.dumps(results)
    assert "SECRET" not in json.dumps(generated, default=str)
    assert "SECRET" not in capsys.readouterr().out
    store = run_module._run_store()
    run_id = run_module._compute_run_id("all", products, None)
    manifest = store.load(run_id)
    assert "SECRET" not in json.dumps(manifest)
    # The sanitized class-name message is what travels inside the result
    # instead of the raw exception text.
    assert results[0]["trends_data"]["error"] == "Erro de rede na SerpAPI (ConnectionError)"
    assert results[0]["seo_data"]["error"] == "Erro de rede na SerpAPI (SERP) (ConnectionError)"


# ── sanitize_ads_data: deep-copy + candidatos + top_ads, sem mutação ────────


def _ads_with_raw_urls():
    from tests.fixtures.ads_helpers import classified_ads_data
    raw = "https://user:pass@shop.example/produto?api_key=TOPSECRET&utm_source=fb#frag"
    ads = classified_ads_data()
    ads["top_ads"] = [{**ads["top_ads"][0], "landing_url": raw}]
    ads["candidates"] = [{**ads["candidates"][0], "landing_url": raw}]
    return ads


def test_sanitize_ads_data_deep_copies_and_sanitizes_candidates_and_top_ads():
    from pipeline.adapters.url_sanitize import sanitize_ads_data
    ads = _ads_with_raw_urls()
    snapshot = json.dumps(ads, ensure_ascii=False)

    clean = sanitize_ads_data(ads)

    # O input nunca é mutado (nem os anúncios nem os dicionários aninhados).
    assert json.dumps(ads, ensure_ascii=False) == snapshot
    assert clean is not ads
    assert clean["candidates"] is not ads["candidates"]
    assert clean["candidates"][0] is not ads["candidates"][0]
    assert clean["top_ads"][0] is not ads["top_ads"][0]
    # Ambos os sítios ficam sanitizados.
    assert clean["candidates"][0]["landing_url"] == "https://shop.example/produto"
    assert clean["top_ads"][0]["landing_url"] == "https://shop.example/produto"
    blob = json.dumps(clean, ensure_ascii=False)
    assert "TOPSECRET" not in blob and "user:pass" not in blob and "utm_source=fb" not in blob
    # A referência pública da Ad Library (link com ad-ID) sobrevive intacta —
    # só a landing_url é sanitizada.
    assert clean["candidates"][0]["link"] == "https://www.facebook.com/ads/library/?id=a1"
    assert "facebook.com/ads/library" in blob


def test_run_keyword_persists_only_sanitized_candidate_landing_urls(monkeypatch, tmp_path):
    """O result e o pack JSON nunca carregam a URL crua de um candidato
    (userinfo/query/fragment) — candidates e top_ads são sanitizados na
    mesma mudança antes de qualquer serialização."""
    import run as run_module
    from tests.fixtures.ads_helpers import classified_ads_data
    from tests.fixtures.products_helpers import product

    packs_dir, data_dir = tmp_path / "packs", tmp_path / "data"
    packs_dir.mkdir()
    data_dir.mkdir()
    raw_url = "https://shop.example/produto?api_key=TOPSECRET&utm_source=fb#frag"

    def fake_paths(keyword, run_date=None, mode=None, run_id=None, product_id=None):
        from datetime import date as _date
        run_date = run_date or _date.today()
        slug = run_module.pack._slugify(keyword)
        base = f"{run_date.isoformat()}--{slug}"
        return str(packs_dir / f"{base}.md"), str(data_dir / f"{base}.json")

    ads = classified_ads_data()
    ads["top_ads"] = [{**ads["top_ads"][0], "landing_url": raw_url}]
    ads["candidates"] = [{**ads["candidates"][0], "landing_url": raw_url}]
    consumed = []

    def fetch_prices(data):
        consumed.append(data["top_ads"][0]["landing_url"])
        return {"price_range": {"min": 10.0, "max": 20.0, "currency": "EUR", "sources": 1}}

    monkeypatch.setattr(run_module.pack, "_pack_paths", fake_paths)
    monkeypatch.setattr(run_module.apify_ads, "fetch", lambda kw: ads)
    monkeypatch.setattr(run_module.trends, "fetch", lambda kw: {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []})
    monkeypatch.setattr(run_module.serp_seo, "fetch", lambda kw: {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"})
    monkeypatch.setattr(run_module.landing_price, "fetch_prices_from_ads", fetch_prices)
    monkeypatch.setattr(run_module, "_csv_data", lambda path, keyword: {"volume": 1000, "cpc": 1, "related_keywords": []})

    result = run_module.run_keyword(product("mochila"))

    # O fetch consumiu a URL crua; o que é persistido está sanitizado.
    assert consumed == [raw_url]
    persisted = result["ads_data"]["candidates"][0]["landing_url"]
    assert persisted == "https://shop.example/produto"
    json_path = result["md_path"].replace("/packs/", "/data/").replace(".md", ".json")
    raw_pack = open(json_path, encoding="utf-8").read()
    assert "https://shop.example/produto" in raw_pack
    assert "TOPSECRET" not in raw_pack
    assert "api_key=TOPSECRET" not in raw_pack
    assert "utm_source=fb" not in raw_pack
