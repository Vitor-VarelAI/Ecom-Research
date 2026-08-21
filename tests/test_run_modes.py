"""Cost and reuse contracts for run.py modes."""

from __future__ import annotations

import json
from datetime import date, timedelta

import run
from tests.fixtures.ads_helpers import classified_ads_data
from tests.fixtures.products_helpers import product, stable_id as run_module_stable_id, write_registry


ADS = {
    "source": "Facebook Ad Library via Apify",
    "total_ads": 1,
    "ad_ids": ["a1"],
    "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30, "landing_url": "https://shop.example"}],
    "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
    "saturation": "baixa",
}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def test_apify_failure_continues_paid_sources_without_false_descartar(monkeypatch):
    calls = {"apify": 0, "trends": 0, "seo": 0, "landing": 0}

    def apify(keyword):
        calls["apify"] += 1
        return {"error": "Apify offline"}

    def trends(keyword):
        calls["trends"] += 1
        return TRENDS

    def seo(keyword):
        calls["seo"] += 1
        return SEO

    monkeypatch.setattr(run.apify_ads, "fetch", apify)
    monkeypatch.setattr(run.trends, "fetch", trends)
    monkeypatch.setattr(run.serp_seo, "fetch", seo)
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: CSV)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    result = run.run_keyword(product("mochila"))

    assert calls == {"apify": 1, "trends": 1, "seo": 1, "landing": 0}
    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert result["keyword"] == "mochila"
    assert result["product_id"] == product("mochila").product_id


def test_zero_ads_skips_seo_and_landing_but_fetches_related_once(monkeypatch):
    calls = {"apify": 0, "related": 0, "seo": 0, "landing": 0}

    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: calls.__setitem__("apify", calls["apify"] + 1) or {**ADS, "total_ads": 0, "ad_ids": [], "top_ads": [], "advertisers": []})
    monkeypatch.setattr(run.trends, "fetch_related", lambda keyword: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": [{"keyword": "mochila escolar", "score": 100}]})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda keyword: calls.__setitem__("seo", 1))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", 1))
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: CSV)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    result = run.run_keyword(product("mochila"))

    assert calls == {"apify": 1, "related": 1, "seo": 0, "landing": 0}
    assert result["score_result"]["label"] == "DESCARTAR"
    assert result["suggested_keywords"][0]["status"] == "proposed"


def test_run_keyword_filters_irrelevant_related_queries_end_to_end(monkeypatch, tmp_path):
    """The product-relevance filter runs once over a copy of trends_data
    before suggestions and pack rendering: a related query sharing only the
    geographic token (``volta a portugal 2026`` for ``camisolas portugal``)
    must never surface in suggestions, pack Markdown, pack JSON or digest —
    and the bounded audit (raw/retained counts, head token, policy) is
    persisted in the run artifact without the excluded query text."""
    packs_dir, data_dir = tmp_path / "packs", tmp_path / "data"
    packs_dir.mkdir()
    data_dir.mkdir()

    def fake_paths(keyword, run_date=None, mode=None, run_id=None, product_id=None):
        run_date = run_date or date.today()
        slug = run.pack._slugify(keyword)
        base = f"{run_date.isoformat()}--{slug}"
        return str(packs_dir / f"{base}.md"), str(data_dir / f"{base}.json")

    monkeypatch.setattr(run.pack, "_pack_paths", fake_paths)
    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: {**ADS, "total_ads": 5})
    monkeypatch.setattr(
        run.trends, "fetch",
        lambda keyword: {
            "direction": "subida", "slope_pct": 10.0, "seasonal": False, "top_geos": [],
            "related_queries": [
                {"keyword": "camisola portugal preta", "score": 100},
                {"keyword": "volta a portugal 2026", "score": 90},
                {"keyword": "camisolas de algodão", "score": 80},
            ],
        },
    )
    monkeypatch.setattr(run.serp_seo, "fetch", lambda keyword: SEO)
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 10, "max": 20}})
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: CSV)

    result = run.run_keyword(product("camisolas portugal"))

    # Suggestions: ranking preserved within retained candidates, proposed.
    assert [s["keyword"] for s in result["suggested_keywords"]] == [
        "camisola portugal preta",
        "camisolas de algodão",
    ]
    assert all(s["status"] == "proposed" for s in result["suggested_keywords"])

    # Pack JSON: filtered list + bounded audit, never the excluded query text.
    json_path = result["md_path"].replace("/packs/", "/data/").replace(".md", ".json")
    raw = json.load(open(json_path, encoding="utf-8"))
    rendered = [q["keyword"] for q in raw["trends"]["related_queries"]]
    assert "volta a portugal 2026" not in rendered
    audit = raw["trends"]["related_queries_filter"]
    assert audit["raw_count"] == 3
    assert audit["retained_count"] == 2
    assert audit["head_token"] == "camisolas"
    assert "volta a portugal 2026" not in json.dumps(raw)

    # Pack Markdown renders only retained queries.
    content = open(result["md_path"], encoding="utf-8").read()
    assert "volta a portugal 2026" not in content
    assert "camisola portugal preta" in content
    assert "camisolas de algodão" in content

    # Digest uses the filtered suggestions (built via the notification builder,
    # the single source of the plain digest).
    batch = run.build_batch(mode="all", run_id="test", results=[result], title="TESTE", run_date=date.today())
    digest = batch.audit_text
    assert "volta a portugal 2026" not in digest
    assert "camisola portugal preta" in digest


def _write_previous(tmp_path, keyword="mochila", label="OBSERVAR"):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    previous = {
        "keyword": keyword,
        "date": date.today().isoformat(),
        "score": {"score": 50, "label": label, "breakdown": {}},
        "trends": TRENDS,
        "keyword_planner": CSV,
        "facebook_ads": classified_ads_data(),
        "seo": SEO,
        "landing_price": {"price_range": {"min": 10, "max": 20}},
        "suggested_keywords": [{"keyword": "mochila escolar", "score": 10, "status": "proposed"}],
    }
    (data_dir / f"{date.today().isoformat()}--mochila.json").write_text(json.dumps(previous), encoding="utf-8")
    write_registry(tmp_path, ["mochila"])


def test_wednesday_uses_one_apify_and_reuses_everything(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify_calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: apify_calls.append(keyword) or {**ADS, "ad_ids": ["a2"]})
    monkeypatch.setattr(run.trends, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Trends called")))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SEO called")))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("landing called")))
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run._run_wednesday(send=False)

    assert apify_calls == ["mochila"]
    assert generated[0]["trends_data"] == TRENDS
    assert generated[0]["landing_price_data"] == {"price_range": {"min": 10, "max": 20}}
    assert results[0]["changes"] == ["+1 ads novos", "-1 ads removidos"]


def test_wednesday_emits_ad_cohort_to_pack_and_results(monkeypatch, tmp_path, capsys):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(run.apify_ads, "fetch", lambda keyword: {**ADS, "ad_ids": ["a2"]})
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run._run_wednesday(send=False)

    cohort = results[0]["ad_cohort"]
    assert generated[0]["ad_cohort"] is cohort
    assert cohort["baseline_date"] == date.today().isoformat()
    assert cohort["previous_ads"] == ["a1"]
    assert cohort["current_ads"] == ["a2"]
    assert cohort["retained_ads"] == []
    assert cohort["new_ads"] == ["a2"]
    assert cohort["removed_ads"] == ["a1"]
    assert cohort["ad_retention_pct"] == 0.0
    assert cohort["advertiser_retention_pct"] == 100.0
    assert cohort["top_advertiser_share_pct"] == 100.0
    assert cohort["signals"] == ["high_churn", "concentrated_market"]
    # (7) pulse products resolve through the registry and carry identity.
    assert results[0]["product_id"] == run_module_stable_id("mochila")
    assert results[0]["research_keyword"] == "mochila"
    assert generated[0]["product_id"] == run_module_stable_id("mochila")
    digest = capsys.readouterr().out
    assert "retenção ads 0.0%" in digest
    assert "Sinais: high_churn, concentrated_market" in digest


def _ads(ad_ids, advertisers, total_ads):
    return {"total_ads": total_ads, "ad_ids": ad_ids, "advertisers": advertisers}


def _adv(page_id, active_ads):
    return {"page_id": page_id, "page_name": f"Loja {page_id}", "active_ads": active_ads}


def test_ad_cohort_set_math_and_retention():
    previous = _ads(["a1", "a2", "a3", "a4"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)
    current = _ads(["a2", "a3", "a4", "a5"], [_adv("p1", 2), _adv("p2", 1), _adv("p4", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["baseline_date"] == "2026-07-08"
    assert cohort["current_date"] == "2026-07-15"
    assert cohort["previous_ads"] == ["a1", "a2", "a3", "a4"]
    assert cohort["current_ads"] == ["a2", "a3", "a4", "a5"]
    assert cohort["retained_ads"] == ["a2", "a3", "a4"]
    assert cohort["new_ads"] == ["a5"]
    assert cohort["removed_ads"] == ["a1"]
    assert cohort["ad_retention_pct"] == 75.0
    assert cohort["previous_advertisers"] == ["p1", "p2", "p3"]
    assert cohort["current_advertisers"] == ["p1", "p2", "p4"]
    assert cohort["retained_advertisers"] == ["p1", "p2"]
    assert cohort["new_advertisers"] == ["p4"]
    assert cohort["lost_advertisers"] == ["p3"]
    assert cohort["advertiser_retention_pct"] == round(2 / 3 * 100, 1)
    assert cohort["top_advertiser_share_pct"] == 50.0
    assert cohort["signals"] == []


def test_ad_cohort_durable_demand_requires_three_retained_advertisers():
    previous = _ads(["a1", "a2", "a3", "a4"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)
    current = _ads(["a2", "a3", "a4", "a5"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == 75.0
    assert len(cohort["retained_advertisers"]) == 3
    assert cohort["signals"] == ["durable_demand"]


def test_ad_cohort_signals_coexist_durable_and_concentrated():
    previous = _ads(["a1", "a2", "a3", "a4", "a5", "a6"], [_adv("p1", 4), _adv("p2", 1), _adv("p3", 1)], 6)
    current = _ads(["a3", "a4", "a5", "a6", "a7", "a8"], [_adv("p1", 4), _adv("p2", 1), _adv("p3", 1)], 6)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == round(4 / 6 * 100, 1)
    assert cohort["top_advertiser_share_pct"] == round(4 / 6 * 100, 1)
    assert cohort["signals"] == ["durable_demand", "concentrated_market"]


def test_ad_cohort_high_churn_below_fifty():
    previous = _ads(["a1", "a2", "a3", "a4", "a5", "a6"], [_adv("p1", 4), _adv("p2", 1), _adv("p3", 1)], 6)
    current = _ads(["a5", "a6", "a7", "a8"], [_adv("p1", 2), _adv("p2", 1), _adv("p4", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == round(2 / 6 * 100, 1)
    assert cohort["signals"] == ["high_churn"]


def test_ad_cohort_no_baseline_is_insufficient():
    current = _ads(["a1", "a2"], [_adv("p1", 1), _adv("p2", 1)], 2)

    cohort = run._ad_cohort({}, current, baseline_date=None, current_date="2026-07-15")

    assert cohort["ad_retention_pct"] is None
    assert cohort["advertiser_retention_pct"] is None
    assert cohort["retained_ads"] == []
    assert cohort["removed_ads"] == []
    assert cohort["new_ads"] == ["a1", "a2"]
    assert cohort["signals"] == ["insufficient_baseline"]


def test_ad_cohort_partial_baseline_without_ids_keeps_advertiser_metrics():
    previous = _ads([], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)
    current = _ads(["a1", "a2", "a3", "a4"], [_adv("p1", 2), _adv("p2", 1), _adv("p3", 1)], 4)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] is None
    assert cohort["advertiser_retention_pct"] == 100.0
    assert cohort["top_advertiser_share_pct"] == 50.0
    assert cohort["signals"] == []


def test_ad_cohort_handles_error_sources():
    cohort = run._ad_cohort({"error": "Apify offline"}, {"error": "Apify offline"}, baseline_date=None)
    assert cohort["previous_ads"] == []
    assert cohort["current_ads"] == []
    assert cohort["ad_retention_pct"] is None
    assert cohort["top_advertiser_share_pct"] is None
    assert cohort["signals"] == ["insufficient_baseline"]


def test_ad_cohort_zero_current_ads_has_no_share():
    previous = _ads(["a1", "a2"], [_adv("p1", 1), _adv("p2", 1)], 2)
    current = _ads([], [], 0)

    cohort = run._ad_cohort(previous, current, baseline_date="2026-07-08", current_date="2026-07-15")

    assert cohort["ad_retention_pct"] == 0.0
    assert cohort["top_advertiser_share_pct"] is None
    assert cohort["signals"] == ["high_churn"]


def test_monthly_has_one_forced_trends_call_and_no_apify_or_landing(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda *args, **kwargs: apify.append(True))
    trends_calls = []
    monkeypatch.setattr(run.trends, "fetch", lambda *args, **kwargs: trends_calls.append((args, kwargs)) or {**TRENDS, "slope_pct": 12})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("SEO called")))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("landing called")))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_monthly(send=False)

    assert apify == []
    assert len(trends_calls) == 1
    assert trends_calls[0][1] == {"timeseries_only": True, "force_refresh": True}
    assert results[0]["score_result"]["label"] in {"OBSERVAR", "AVANÇAR", "DESCARTAR"}


# ── Política zero-match (total_ads = matched) e classificação no run ───────


def _adapter_result(keyword, *, matched=0, needs_review=0, rejected=0):
    """A new-format adapter result (unclassified canonical candidates +
    counts).  run_keyword classifies it with the real matcher."""
    candidates = []

    def mk(ad_id, commercial, geo, weak, copy="Mochila premium", days_active=30):
        return {
            "ad_archive_id": ad_id,
            "advertiser": "Loja",
            "page_id": "p1",
            "days_active": days_active,
            "reported_active_time": f"{days_active} days",
            "link": f"https://www.facebook.com/ads/library/?id={ad_id}",
            "landing_url": f"https://shop.example/{ad_id}",
            "copy": copy,
            "publisher_platforms": ["facebook"],
            "countries": ["PT"],
            "match_evidence": {"commercial": commercial, "geo": geo, "weak": weak},
        }

    for i in range(matched):
        candidates.append(mk(
            f"m{i}",
            "mochilas personalizadas premium",
            "mochilas personalizadas",
            "mochila personalizada premium envio rapido",
            copy="Mochila personalizada premium",
        ))
    for i in range(needs_review):
        candidates.append(mk(
            f"r{i}",
            "mochilas escolares com divisoes",
            "mochilas escolares",
            "mochila personalizada escolar com bolso",
            copy="Mochila escolar com divisões",
        ))
    for i in range(rejected):
        candidates.append(mk(
            f"x{i}",
            "concentrador de oxigenio portatil",
            "concentrador de oxigenio",
            "concentrador portatil com mochila incluida",
            copy="Concentrador com mochila incluída",
        ))
    total = len(candidates)
    return {
        "source": "Facebook Ad Library via Apify",
        "query": keyword,
        "evidence_scope": "global",
        "query_country": "ALL",
        "sample_limit": 100,
        "sample_capped": False,
        "raw_ads_count": total,
        "filtered_out_count": 0,
        "filtered_inactive_count": 0,
        "filtered_irrelevant_count": 0,
        "candidate_count": total,
        "duplicates_removed": 0,
        "relevance_policy": "gate1",
        "matching_schema_version": 1,
        "candidates": candidates,
    }


def _install_zero_match_stack(monkeypatch, ads_result):
    calls = {"apify": 0, "trends": 0, "related": 0, "seo": 0, "landing": 0}
    monkeypatch.setattr(run.apify_ads, "fetch",
                        lambda kw: calls.__setitem__("apify", calls["apify"] + 1) or ads_result)
    monkeypatch.setattr(run.trends, "fetch",
                        lambda kw: calls.__setitem__("trends", calls["trends"] + 1) or TRENDS)
    monkeypatch.setattr(run.trends, "fetch_related",
                        lambda kw: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": []})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: calls.__setitem__("seo", calls["seo"] + 1) or SEO)
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads",
                        lambda data: calls.__setitem__("landing", calls["landing"] + 1))
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: CSV)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")
    return calls


def test_run_keyword_classifies_candidates_and_total_ads_is_matched(monkeypatch):
    """A result com candidatos é classificado pelo matcher no run: total_ads é
    a contagem matched, as projeções vêm só dos matched."""
    ads_result = _adapter_result("mochila personalizada", matched=2, needs_review=1, rejected=1)
    calls = _install_zero_match_stack(monkeypatch, ads_result)

    result = run.run_keyword(product("mochila personalizada"))

    assert calls == {"apify": 1, "trends": 1, "seo": 1, "landing": 1, "related": 0}
    ads = result["ads_data"]
    assert ads["candidate_count"] == 4
    assert ads["matched_count"] == 2
    assert ads["needs_review_count"] == 1
    assert ads["rejected_count"] == 1
    assert ads["total_ads"] == 2
    assert ads["ad_ids"] == ["m0", "m1"]
    assert [ad["ad_archive_id"] for ad in ads["top_ads"]] == ["m0", "m1"]
    assert ads["matcher_version"]
    assert ads["matching_schema_version"] == 1
    labels = {c["ad_archive_id"]: c["match"]["label"] for c in ads["candidates"]}
    assert labels == {"m0": "matched", "m1": "matched", "r0": "needs_review", "x0": "rejected"}
    assert result["score_result"]["label"] in {"AVANÇAR", "OBSERVAR", "DESCARTAR"}


def test_zero_candidates_is_descartar_with_related_queries(monkeypatch):
    """Zero candidatos canónicos ativos => DESCARTAR determinístico (sem
    decisão negativa sobre evidência ambígua porque não há evidência)."""
    calls = _install_zero_match_stack(monkeypatch, _adapter_result("mochila personalizada"))
    result = run.run_keyword(product("mochila personalizada"))

    assert calls == {"apify": 1, "trends": 0, "related": 1, "seo": 0, "landing": 0}
    assert result["score_result"]["label"] == "DESCARTAR"
    assert result["score_result"]["score"] == 0


def test_zero_matched_with_needs_review_is_dados_insuficientes(monkeypatch):
    """Zero matched com candidatos needs_review => DADOS INSUFICIENTES: a
    evidência é ambígua e nenhuma decisão negativa pode ser tomada."""
    calls = _install_zero_match_stack(monkeypatch, _adapter_result("mochila personalizada", needs_review=2))
    result = run.run_keyword(product("mochila personalizada"))

    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"
    assert result["score_result"]["incomplete"] is True
    assert "needs_review" in result["score_result"]["reason"]
    assert calls == {"apify": 1, "trends": 1, "seo": 1, "landing": 0, "related": 0}
    assert result["ads_data"]["needs_review_count"] == 2
    assert result["ads_data"]["total_ads"] == 0


def test_zero_matched_all_rejected_is_descartar(monkeypatch):
    """Zero matched com todos os candidatos rejeitados => DESCARTAR: sinal
    negativo real (a coorte da Ad Library não prova o produto)."""
    calls = _install_zero_match_stack(monkeypatch, _adapter_result("mochila personalizada", rejected=2))
    result = run.run_keyword(product("mochila personalizada"))

    assert result["score_result"]["label"] == "DESCARTAR"
    assert result["score_result"]["score"] == 0
    assert "rejeitados" in result["score_result"]["reason"]
    assert calls == {"apify": 1, "trends": 0, "related": 1, "seo": 0, "landing": 0}
    assert result["ads_data"]["rejected_count"] == 2


# ── Guardas legadas mensal/quarta (fail-closed, nunca reescrever história) ─


def _write_legacy_previous(tmp_path):
    """A previous pack whose facebook_ads predates matching_schema_version."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    previous = {
        "keyword": "mochila",
        "date": date.today().isoformat(),
        "score": {"score": 50, "label": "OBSERVAR", "breakdown": {}},
        "trends": TRENDS,
        "keyword_planner": CSV,
        "facebook_ads": ADS,
        "seo": SEO,
        "landing_price": {"price_range": {"min": 10, "max": 20}},
        "suggested_keywords": [],
    }
    (data_dir / f"{date.today().isoformat()}--mochila.json").write_text(json.dumps(previous), encoding="utf-8")
    write_registry(tmp_path, ["mochila"])


def test_wednesday_skips_legacy_baseline_pack_fail_closed(monkeypatch, tmp_path, capsys):
    """Um pack anterior sem matching_schema_version não pode servir de
    baseline de coorte: o pulse é saltado (zero calls Apify), o pack é
    preservado e o motivo é explícito — nunca se reescreve história."""
    _write_legacy_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify_calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: apify_calls.append(kw) or classified_ads_data())
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_wednesday(send=False)

    assert apify_calls == []
    assert len(results) == 1
    assert results[0]["preserved"] is True
    assert "matching_schema_version" in results[0]["error"]
    out = capsys.readouterr().out
    assert "matching_schema_version" in out


def test_monthly_skips_legacy_baseline_pack_fail_closed(monkeypatch, tmp_path, capsys):
    """Um pack anterior sem matching_schema_version nunca é re-scored sob a
    semântica nova: salta antes da call Trends (zero gastos), preserva o
    pack e reporta o motivo."""
    _write_legacy_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    trends_calls = []
    monkeypatch.setattr(run.trends, "fetch",
                        lambda *a, **k: trends_calls.append((a, k)) or TRENDS)
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_monthly(send=False)

    assert trends_calls == []
    assert len(results) == 1
    assert results[0]["preserved"] is True
    assert "matching_schema_version" in results[0]["error"]
    out = capsys.readouterr().out
    assert "matching_schema_version" in out
