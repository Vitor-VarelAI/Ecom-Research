"""Market-scoped product resolution through the public CLI."""

from __future__ import annotations

import json

import run
from pipeline.output import pack
from tests.fixtures.products_helpers import write_registry


def test_manual_gb_market_resolves_local_product_query(monkeypatch, tmp_path):
    write_registry(tmp_path, [
        {"keyword": "velas aromáticas", "aliases": ["velas"]},
    ])
    markets = {
        "schema_version": 1,
        "markets": [
            {
                "market_id": "PT",
                "name": "Portugal",
                "currency": "EUR",
                "ads_country": "PT",
                "trends_geo": "PT",
                "serp_gl": "pt",
                "serp_hl": "pt",
                "serp_location": "Portugal",
                "product_queries": {"258053734570": "velas aromáticas"},
            },
            {
                "market_id": "GB",
                "name": "United Kingdom",
                "currency": "GBP",
                "ads_country": "GB",
                "trends_geo": "GB",
                "serp_gl": "uk",
                "serp_hl": "en",
                "serp_location": "United Kingdom",
                "product_queries": {"258053734570": "scented candles"},
            },
        ],
    }
    (tmp_path / "config" / "markets.json").write_text(
        json.dumps(markets), encoding="utf-8"
    )
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    captured = []
    monkeypatch.setattr(
        run,
        "_run_keyword_action",
        lambda target, **kwargs: captured.append(
            (target.product_id, target.market_id, target.research_keyword)
        ),
    )

    run.main(["velas", "--market", "GB"])

    assert captured == [("258053734570", "GB", "scented candles")]


def test_run_identity_distinguishes_markets_even_with_the_same_query(monkeypatch, tmp_path):
    write_registry(tmp_path, [{"keyword": "scented candles"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    pt = payload["markets"][0]
    payload["markets"].append({
        **pt,
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    product = run._load_products().resolve("scented candles")
    markets = run._load_markets()

    pt_id = run._compute_run_id("keyword", [markets.target(product, "PT")])
    gb_id = run._compute_run_id("keyword", [markets.target(product, "GB")])

    assert pt_id != gb_id


def test_run_propagates_one_market_to_all_live_sources(monkeypatch, tmp_path):
    write_registry(tmp_path, [{"keyword": "scented candles"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    payload["markets"][0].update({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    product = run._load_products().resolve("scented candles")
    target = run._load_markets().target(product, "GB")
    calls = []
    generated = {}

    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword, *, market: calls.append(("ads", market)) or {"error": "offline"})
    monkeypatch.setattr(run.trends, "fetch", lambda keyword, *, market: calls.append(("trends", market)) or {"error": "offline"})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda keyword, *, market: calls.append(("serp", market)) or {"error": "offline"})
    monkeypatch.setattr(
        run,
        "_csv_data",
        lambda path, keyword, market: calls.append(("csv", market)) or {"error": "missing"},
    )
    monkeypatch.setattr(
        run.pack,
        "generate",
        lambda **kwargs: generated.update(kwargs) or str(tmp_path / "pack.md"),
    )

    result = run.run_keyword(target)

    assert [name for name, _ in calls] == ["ads", "trends", "serp", "csv"]
    assert all(market is target.market for _, market in calls)
    assert result["research_keyword"] == "scented candles"
    assert result["market_id"] == "GB"
    assert generated["market"] == {
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
    }


def test_registry_builds_configured_product_market_matrix(tmp_path):
    registry = write_registry(tmp_path, [
        {"keyword": "velas aromáticas"},
        {"keyword": "caneca personalizada"},
    ])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")

    targets = run.load_market_registry(markets_path).targets_for(registry.active_products())

    assert [(target.market_id, target.research_keyword) for target in targets] == [
        ("PT", "velas aromáticas"),
        ("PT", "caneca personalizada"),
        ("GB", "scented candles"),
    ]


def test_all_mode_dispatches_the_configured_market_matrix(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [
        {"keyword": "velas aromáticas"},
        {"keyword": "caneca personalizada"},
    ])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    captured = []
    monkeypatch.setattr(
        run,
        "_run_all",
        lambda targets, csv_path=None, send=True: captured.extend(targets) or [],
    )

    run.main(["--all"])

    assert [(target.market_id, target.research_keyword) for target in captured] == [
        ("PT", "velas aromáticas"),
        ("PT", "caneca personalizada"),
        ("GB", "scented candles"),
    ]


def test_auto_receives_validated_product_and_market_registries(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "velas aromáticas"}])
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    captured = {}
    monkeypatch.setattr(
        run,
        "_auto_dispatch",
        lambda *, send, csv_path, registry, markets: captured.update(
            registry=registry, markets=markets
        ) or [],
    )

    run.main(["--auto"])

    assert captured["registry"].products == registry.products
    assert [market.market_id for market in captured["markets"].markets] == ["PT"]


def test_previous_pack_selection_is_isolated_by_market(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "velas aromáticas"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    markets = run.load_market_registry(markets_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    base = {
        "product_id": candles.product_id,
        "score": {"label": "OBSERVAR", "score": 50},
        "artifact_complete": True,
    }
    (data_dir / "pt.json").write_text(json.dumps({
        **base, "run_id": "pt-run", "market_id": "PT",
        "keyword": "velas aromáticas", "research_keyword": "velas aromáticas",
        "date": "2026-08-01", "generated_at": "2026-08-01T08:00:00+00:00",
    }), encoding="utf-8")
    (data_dir / "gb.json").write_text(json.dumps({
        **base, "run_id": "gb-run", "market_id": "GB",
        "keyword": "scented candles", "research_keyword": "scented candles",
        "date": "2026-08-02", "generated_at": "2026-08-02T08:00:00+00:00",
    }), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    previous = run._load_previous_pack(
        markets.target(candles, "PT"), registry=registry
    )

    assert previous["run_id"] == "pt-run"


def test_watchlist_keeps_same_product_once_per_market(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "velas aromáticas"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    markets = run.load_market_registry(markets_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for market_id, keyword in (("PT", "velas aromáticas"), ("GB", "scented candles")):
        (data_dir / f"{market_id}.json").write_text(json.dumps({
            "product_id": candles.product_id,
            "market_id": market_id,
            "keyword": keyword,
            "date": "2026-08-02",
            "score": {"label": "OBSERVAR", "score": 50},
            "run_id": f"{market_id}-run",
            "artifact_complete": True,
        }), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    targets = run._find_packs_by_label(
        ["OBSERVAR"], registry=registry, markets=markets
    )

    assert [(target.product_id, target.market_id) for target in targets] == [
        (candles.product_id, "PT"),
        (candles.product_id, "GB"),
    ]


def test_wednesday_refresh_uses_the_target_market(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "velas aromáticas"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    markets = run.load_market_registry(markets_path)
    target = markets.target(candles, "GB")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        run,
        "_find_packs_by_label",
        lambda labels, max_age_days=None, registry=None, markets=None: [target],
    )
    previous_ads = run.apify_ads._normalise("scented candles", [])
    previous_ads.update({
        "evidence_scope": "market", "query_country": "GB",
        "matching_schema_version": run.product_ads.MATCHING_SCHEMA_VERSION,
    })
    previous = {
        "date": "2026-08-01",
        "facebook_ads": previous_ads,
        "trends": {"market_id": "GB", "query_geo": "GB"},
        "keyword_planner": {"market_id": "GB", "currency": "GBP"},
        "seo": {"market_id": "GB", "query_gl": "uk", "query_hl": "en", "query_location": "United Kingdom"},
        "score": {"score": 50, "label": "OBSERVAR"},
        "suggested_keywords": [],
    }
    monkeypatch.setattr(run, "_load_previous_pack", lambda target, registry=None: previous)
    captured = []

    def fetch(keyword, *, market):
        captured.append(market)
        fresh = run.apify_ads._normalise(keyword, [])
        fresh.update({"evidence_scope": "market", "query_country": "GB"})
        return fresh

    monkeypatch.setattr(run.apify_ads, "fetch", fetch)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: str(tmp_path / "pack.md"))
    batch = type("Batch", (), {"audit_text": "digest", "items": []})()
    monkeypatch.setattr(run, "build_batch", lambda **kwargs: batch)
    monkeypatch.setattr(
        run,
        "deliver_notification_batch",
        lambda batch, send: run._noop_delivery("not_requested", 0),
    )

    results = run._run_wednesday(send=False, registry=registry, markets=markets)

    assert captured == [target.market]
    assert results[0]["market_id"] == "GB"


def test_monthly_refresh_uses_the_target_market(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "scented candles"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    payload["markets"][0].update({
        "market_id": "GB", "name": "United Kingdom", "currency": "GBP",
        "ads_country": "GB", "trends_geo": "GB", "serp_gl": "uk",
        "serp_hl": "en", "serp_location": "United Kingdom",
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    markets = run.load_market_registry(markets_path)
    target = markets.target(registry.resolve("scented candles"), "GB")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        run,
        "_find_packs_by_label",
        lambda labels, max_age_days=None, registry=None, markets=None: [target],
    )
    previous_ads = run.apify_ads._normalise("scented candles", [])
    previous_ads.update({
        "evidence_scope": "market", "query_country": "GB",
        "matching_schema_version": run.product_ads.MATCHING_SCHEMA_VERSION,
    })
    previous = {
        "date": "2026-08-01",
        "facebook_ads": previous_ads,
        "trends": {"market_id": "GB", "query_geo": "GB"},
        "keyword_planner": {
            "volume": 1000, "cpc": 1.0, "related_keywords": [],
            "market_id": "GB", "currency": "GBP",
        },
        "seo": {
            "market_id": "GB", "query_gl": "uk", "query_hl": "en",
            "query_location": "United Kingdom",
        },
        "score": {"score": 50, "label": "OBSERVAR"},
        "suggested_keywords": [],
    }
    monkeypatch.setattr(run, "_load_previous_pack", lambda target, registry=None: previous)
    captured = []

    def fetch(keyword, *, timeseries_only, force_refresh, market):
        captured.append(market)
        return {
            "direction": "subida", "slope_pct": 25, "seasonal": False,
            "related_queries": [], "market_id": "GB", "query_geo": "GB",
        }

    monkeypatch.setattr(run.trends, "fetch", fetch)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: str(tmp_path / "pack.md"))
    batch = type("Batch", (), {"audit_text": "digest", "items": []})()
    monkeypatch.setattr(run, "build_batch", lambda **kwargs: batch)
    monkeypatch.setattr(
        run,
        "deliver_notification_batch",
        lambda batch, send: run._noop_delivery("not_requested", 0),
    )

    results = run._run_monthly(send=False, registry=registry, markets=markets)

    assert captured == [target.market]
    assert results[0]["market_id"] == "GB"


def test_market_signal_labels_require_confirmed_local_evidence():
    trends_data = {
        "slope_30d_pct": 25,
        "slope_90d_pct": 5,
        "related_queries": [{"keyword": "luxury scented candles", "score": 40}],
    }
    ads_data = {
        "advertisers": [
            {"page_id": "a", "oldest_ad_days": 45},
            {"page_id": "b", "oldest_ad_days": 20},
            {"page_id": "c", "oldest_ad_days": 10},
        ],
    }
    cohort = {"ad_retention_pct": 80}

    assert run._market_signal_labels(trends_data, ads_data, cohort) == [
        "EMERGENTE", "DURÁVEL",
    ]
    assert run._market_signal_labels(trends_data, ads_data, None) == ["DURÁVEL"]


def test_notification_labels_and_identity_are_market_specific():
    result = {
        "product_id": "258053734570",
        "market_id": "GB",
        "product_name": "velas aromáticas",
        "research_keyword": "scented candles",
        "keyword": "scented candles",
        "score_result": {"score": 80, "label": "AVANÇAR"},
        "ads_data": {"advertisers": [], "total_ads": 3, "saturation": "baixa"},
        "trends_data": {"direction": "subida", "slope_pct": 25, "seasonal": False},
        "suggested_keywords": [],
        "market_signals": ["EMERGENTE", "DURÁVEL"],
    }

    batch = run.build_batch(mode="keyword", run_id="run123", results=[result])
    item = batch.items[0]

    assert item.title == "scented candles · GB"
    assert item.product == "258053734570:GB"
    assert any(field.value == "AVANÇAR GB" for field in item.header_fields)
    assert "AVANÇAR GB" in item.audit_text
    assert "Mercado: GB" in item.audit_text
    assert "Prova GB:" in item.audit_text
    assert "decisão manual" not in item.audit_text
    assert "Prova global" not in item.audit_text
    assert any(section.heading == "Evidência GB" for section in item.sections)
    assert any(
        section.heading == "Sinais GB"
        and section.fields[0].value == "EMERGENTE, DURÁVEL"
        for section in item.sections
    )


def test_run_scoped_pack_paths_separate_markets_for_the_same_product():
    pt = pack._pack_paths(
        "velas aromáticas", mode="sunday", run_id="run123",
        product_id="258053734570", market_id="PT",
    )
    gb = pack._pack_paths(
        "scented candles", mode="sunday", run_id="run123",
        product_id="258053734570", market_id="GB",
    )

    assert pt != gb
    assert "--pt--" in pt[1]
    assert "--gb--" in gb[1]


def test_sunday_dispatches_the_configured_market_matrix(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [
        {"keyword": "velas aromáticas"},
        {"keyword": "caneca personalizada"},
    ])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    markets = run.load_market_registry(markets_path)
    captured = []
    monkeypatch.setattr(
        run,
        "_run_batch",
        lambda mode, targets, csv_path, send: captured.extend(targets) or [],
    )

    run._run_sunday(send=False, registry=registry, markets=markets)

    assert [(target.market_id, target.research_keyword) for target in captured] == [
        ("PT", "velas aromáticas"),
        ("PT", "caneca personalizada"),
        ("GB", "scented candles"),
    ]


def test_batch_keeps_same_product_results_separate_by_market(monkeypatch, tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "velas aromáticas"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    candles = registry.resolve("velas aromáticas")
    payload["markets"].append({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
        "product_queries": {candles.product_id: "scented candles"},
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    targets = list(run.load_market_registry(markets_path).targets_for(registry.active_products()))
    monkeypatch.setattr(
        run,
        "_safe_run_keyword",
        lambda target, **kwargs: {
            "product_id": target.product_id,
            "market_id": target.market_id,
            "product_name": target.name,
            "research_keyword": target.research_keyword,
            "keyword": target.research_keyword,
            "score_result": {"score": 50, "label": "OBSERVAR"},
            "ads_data": {"advertisers": []},
        },
    )
    batch = type("Batch", (), {"audit_text": "digest", "items": []})()
    monkeypatch.setattr(run, "build_batch", lambda **kwargs: batch)
    monkeypatch.setattr(
        run,
        "deliver_notification_batch",
        lambda batch, send: run._noop_delivery("not_requested", 0),
    )

    results = run._run_batch("all", targets, csv_path=None, send=False)

    assert [result["market_id"] for result in results] == ["PT", "GB"]


def test_automatic_csv_selection_uses_matching_market_sidecar(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for filename, market_id, currency, volume in (
        ("2026-08-01-pt.csv", "PT", "EUR", 1000),
        ("2026-08-02-gb.csv", "GB", "GBP", 5000),
    ):
        path = data_dir / filename
        path.write_text(
            "Keyword,Avg. monthly searches,Top of page bid (low range)\n"
            f"scented candles,{volume},1.0\n",
            encoding="utf-8",
        )
        (data_dir / f"{filename}.meta.json").write_text(
            json.dumps({"market_id": market_id, "currency": currency}),
            encoding="utf-8",
        )
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    pt = type("Market", (), {"market_id": "PT", "currency": "EUR"})()
    gb = type("Market", (), {"market_id": "GB", "currency": "GBP"})()

    assert run._csv_data(None, "scented candles", pt)["volume"] == 1000
    assert run._csv_data(None, "scented candles", gb)["volume"] == 5000


def test_global_ads_cannot_produce_a_market_decision(monkeypatch, tmp_path):
    write_registry(tmp_path, [{"keyword": "scented candles"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    payload["markets"][0].update({
        "market_id": "GB",
        "name": "United Kingdom",
        "currency": "GBP",
        "ads_country": "GB",
        "trends_geo": "GB",
        "serp_gl": "uk",
        "serp_hl": "en",
        "serp_location": "United Kingdom",
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    product = run._load_products().resolve("scented candles")
    target = run._load_markets().target(product, "GB")
    global_ads = run.apify_ads._normalise("scented candles", [])

    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword, *, market: global_ads)
    monkeypatch.setattr(
        run.trends,
        "fetch_related",
        lambda keyword, *, market: {
            "related_queries": [], "market_id": "GB", "query_geo": "GB",
        },
    )
    monkeypatch.setattr(
        run,
        "_csv_data",
        lambda path, keyword, market: {
            "volume": 1000, "cpc": 1.0, "related_keywords": [],
            "market_id": "GB", "currency": "GBP",
        },
    )
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: str(tmp_path / "pack.md"))

    result = run.run_keyword(target)

    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert "geográfica" in result["score_result"]["reason"]


def test_csv_boundary_propagates_market_to_keyword_planner(monkeypatch):
    market = type("Market", (), {"market_id": "GB", "currency": "GBP"})()
    captured = {}
    monkeypatch.setattr(
        run.keyword_csv,
        "parse",
        lambda path, *, keyword, market: captured.update(
            path=path, keyword=keyword, market=market
        ) or {"volume": 100},
    )

    result = run._csv_data("planner.csv", "scented candles", market)

    assert result == {"volume": 100}
    assert captured == {
        "path": "planner.csv",
        "keyword": "scented candles",
        "market": market,
    }


def _gb_target(tmp_path):
    registry = write_registry(tmp_path, [{"keyword": "scented candles"}])
    markets_path = tmp_path / "config" / "markets.json"
    payload = json.loads(markets_path.read_text(encoding="utf-8"))
    payload["markets"][0].update({
        "market_id": "GB", "name": "United Kingdom", "currency": "GBP",
        "ads_country": "GB", "trends_geo": "GB", "serp_gl": "uk",
        "serp_hl": "en", "serp_location": "United Kingdom",
    })
    markets_path.write_text(json.dumps(payload), encoding="utf-8")
    markets = run.load_market_registry(markets_path)
    return registry, markets, markets.target(registry.resolve("scented candles"), "GB")


def test_technical_failure_keeps_gb_target_identity(monkeypatch, tmp_path):
    _, _, target = _gb_target(tmp_path)
    monkeypatch.setattr(
        run,
        "run_keyword",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = run._safe_run_keyword(target)

    assert result["status"] == "technical_fatal"
    assert result["market_id"] == "GB"
    assert result["market"] == target.market.as_dict()
    assert run._target_key(result) == (target.product_id, "GB")


def test_scored_market_fails_closed_when_trends_or_serp_fails(tmp_path):
    _, _, target = _gb_target(tmp_path)
    market = target.market
    errors = run._market_scope_errors(
        market,
        {"total_ads": 3, "evidence_scope": "market", "query_country": "GB"},
        {"error": "Trends offline"},
        {"market_id": "GB", "currency": "GBP"},
        {"error": "SERP offline"},
    )

    assert errors == [
        "Trends indisponível para decisão local",
        "SERP indisponível para decisão local",
    ]


def test_market_baseline_requires_the_current_local_query(monkeypatch, tmp_path):
    registry, _, target = _gb_target(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "old-query.json").write_text(json.dumps({
        "run_id": "old-run",
        "artifact_complete": True,
        "product_id": target.product_id,
        "market_id": "GB",
        "research_keyword": "luxury candles",
        "keyword": "luxury candles",
        "date": "2026-08-01",
        "score": {"score": 50, "label": "OBSERVAR"},
    }), encoding="utf-8")
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    assert run._load_previous_pack(target, registry=registry) is None


def test_monthly_preserved_gb_result_keeps_market_scope(monkeypatch, tmp_path):
    registry, markets, target = _gb_target(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        run,
        "_find_packs_by_label",
        lambda labels, max_age_days=None, registry=None, markets=None: [target],
    )
    previous_ads = run.apify_ads._normalise("scented candles", [])
    previous_ads["matching_schema_version"] = run.product_ads.MATCHING_SCHEMA_VERSION
    monkeypatch.setattr(run, "_load_previous_pack", lambda *args, **kwargs: {
        "date": "2026-08-01",
        "facebook_ads": previous_ads,
        "score": {"score": 50, "label": "OBSERVAR"},
    })
    monkeypatch.setattr(run.trends, "fetch", lambda *args, **kwargs: {"error": "offline"})
    monkeypatch.setattr(
        run,
        "deliver_notification_batch",
        lambda batch, send: run._noop_delivery("not_requested", len(batch.items)),
    )

    results = run._run_monthly(send=False, registry=registry, markets=markets)

    assert results[0]["preserved"] is True
    assert results[0]["market_id"] == "GB"
    assert results[0]["market"] == target.market.as_dict()


def test_market_scoped_ads_are_labeled_local_in_pack():
    rendered = pack._format_ads_section({
        "evidence_scope": "market",
        "query_country": "GB",
        "total_ads": 3,
    })

    assert "Evidência local (GB)" in rendered
    assert "Evidência global" not in rendered
