"""Batch resumability: per-keyword checkpoints, technical-fatal retry,
deferred Telegram delivery, and no paid calls for successful keywords."""

from __future__ import annotations

import json
import os

import run
from tests.fixtures.products_helpers import product, write_registry

ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
       "saturation": "baixa"}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def _install_stack(monkeypatch, tmp_path, boom_attempts_before_success=1):
    """Full stack where ``boom`` raises ``RuntimeError`` for the first
    ``boom_attempts_before_success`` invocations, then succeeds.  Counts
    adapter calls per product keyword.  Writes the product registry under
    the tmp root and returns the resolved products."""
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    registry = write_registry(tmp_path, ["ok", "boom"])
    products = list(registry.products)
    calls = {"ok_apify": 0, "boom_apify": 0, "ok_trends": 0, "boom_trends": 0}
    state = {"boom_attempts": 0}

    def apify(keyword):
        if keyword == "boom":
            state["boom_attempts"] += 1
            calls["boom_apify"] += 1
            if state["boom_attempts"] <= boom_attempts_before_success:
                raise RuntimeError("falha explosiva no Apify")
            return dict(ADS)
        calls["ok_apify"] += 1
        return dict(ADS)

    def trends(keyword, *a, **k):
        calls["ok_trends" if keyword == "ok" else "boom_trends"] += 1
        return dict(TRENDS)

    monkeypatch.setattr(run.apify_ads, "fetch", apify)
    monkeypatch.setattr(run.trends, "fetch", trends)
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: dict(CSV))
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")
    return calls, generated, state, products


def _install_telegram(monkeypatch):
    delivered = []
    monkeypatch.setattr(
        run.telegram,
        "deliver_batch",
        lambda batch, state_id, state_dir: delivered.append((batch, state_id, state_dir))
        or {
            "delivered": True,
            "status": "success",
            "schema_version": 2,
            "items_total": len(batch.items),
            "items_sent": len(batch.items),
            "errors": [],
        },
    )
    return delivered


def test_technical_fatal_is_marked_and_distinct_from_source_failure():
    # A fatal result is explicitly marked, unlike an expected source failure
    # (which still produces a pack / preserved outcome).
    fatal = {"keyword": "k", "error": "boom", "status": "technical_fatal"}
    assert run._is_technical_fatal(fatal)
    assert not run._is_technical_fatal({"keyword": "k", "status": "ok", "md_path": "x.md"})
    assert not run._is_technical_fatal({"keyword": "k", "preserved": True, "error": "fonte offline"})
    assert not run._is_technical_fatal(None)


def test_safe_run_keyword_marks_technical_fatal(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run, "run_keyword", boom)
    result = run._safe_run_keyword(product("kw"))
    assert result["status"] == "technical_fatal"
    # Every result — including technical_fatal — carries canonical identity
    # plus the current research query.
    assert result["keyword"] == "kw"
    assert result["product_id"] == product("kw").product_id
    assert result["product_name"] == "kw"
    assert result["research_keyword"] == "kw"
    assert result["score_result"]["label"] == "DADOS INSUFICIENTES"


def test_safe_run_keyword_marks_success(monkeypatch):
    monkeypatch.setattr(run, "run_keyword", lambda *a, **k: {"keyword": "kw", "md_path": "p.md"})
    result = run._safe_run_keyword(product("kw"))
    assert result["status"] == "ok"
    assert result["md_path"] == "p.md"


def test_partial_run_retries_only_failed_keyword_and_delivers_once(monkeypatch, tmp_path):
    """One product succeeds, one raises: the second invocation calls only the
    failed product (no paid calls for the successful one), Telegram receives
    no partial digest and exactly one final digest."""
    calls, generated, state, products = _install_stack(monkeypatch, tmp_path, boom_attempts_before_success=1)
    delivered = _install_telegram(monkeypatch)

    first = run._run_all(products, send=True)

    # First invocation: partial batch — no Telegram, deferred delivery, stdout.
    assert first[0]["status"] == "ok"
    assert first[1]["status"] == "technical_fatal"
    assert delivered == []  # no partial digest was Telegram-delivered
    assert calls["ok_apify"] == 1 and calls["boom_apify"] == 1

    store = run._run_store()
    run_id = run._compute_run_id("all", products, None)
    manifest = store.load(run_id)
    assert manifest["state"] == "completed_with_errors"
    assert manifest["digest"]["delivery"]["status"] == "deferred"
    assert manifest["digest"]["text"]  # the partial digest was persisted
    # Full results are kept for the resume so the final digest can be rebuilt.
    assert manifest["results"][0]["keyword"] == "ok"
    assert manifest["results"][1]["status"] == "technical_fatal"

    second = run._run_all(products, send=True)

    # Only the failed product was retried; the successful one was reused.
    assert calls["ok_apify"] == 1
    assert calls["boom_apify"] == 2
    assert calls["ok_trends"] == 1 and calls["boom_trends"] == 1
    assert [r["keyword"] for r in second] == ["ok", "boom"]
    assert all(r["status"] == "ok" for r in second)

    # Exactly one final digest after the failed product succeeded.
    assert len(delivered) == 1
    batch, state_id, state_dir = delivered[0]
    assert state_id == run_id
    assert state_dir == run._digests_dir()
    assert "RESUMO DO DIA" in batch.audit_text
    assert "boom" in batch.audit_text and "ok" in batch.audit_text

    manifest = store.load(run_id)
    assert manifest["state"] == "completed"
    assert manifest["digest"]["delivery"]["status"] == "success"
    # Completed manifests are compacted again.
    assert "status" in manifest["results"][0]

    # A third invocation reuses the completed run: zero paid calls, no resend.
    run._run_all(products, send=True)
    assert calls["boom_apify"] == 2 and calls["ok_apify"] == 1
    assert len(delivered) == 1


def test_persistent_technical_failure_stays_deferred_and_prints(monkeypatch, tmp_path, capsys):
    """If the failed product keeps failing, the run stays
    ``completed_with_errors`` with deferred delivery and the digest goes to
    stdout — no partial Telegram digest ever leaves."""
    calls, generated, state, products = _install_stack(monkeypatch, tmp_path, boom_attempts_before_success=99)
    delivered = _install_telegram(monkeypatch)

    first = run._run_all(products, send=True)
    assert first[1]["status"] == "technical_fatal"
    out = capsys.readouterr().out
    assert "DADOS INSUFICIENTES" in out  # persisted/printed to stdout

    second = run._run_all(products, send=True)
    assert second[1]["status"] == "technical_fatal"
    assert calls["ok_apify"] == 1  # never re-spent the successful product
    assert delivered == []  # still no partial Telegram digest

    store = run._run_store()
    run_id = run._compute_run_id("all", products, None)
    assert store.load(run_id)["state"] == "completed_with_errors"
    assert store.load(run_id)["digest"]["delivery"]["status"] == "deferred"


def test_sunday_batch_uses_same_resume_semantics(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    write_registry(tmp_path, ["ok", "boom"])
    calls, generated, state, products = _install_stack(monkeypatch, tmp_path, boom_attempts_before_success=1)

    first = run._run_sunday(send=False)
    assert first[1]["status"] == "technical_fatal"

    second = run._run_sunday(send=False)
    assert calls["ok_apify"] == 1 and calls["boom_apify"] == 2
    assert all(r["status"] == "ok" for r in second)
    store = run._run_store()
    run_id = run._compute_run_id("sunday", products, None)
    assert store.load(run_id)["state"] == "completed"


def test_checkpoint_is_saved_after_each_keyword(monkeypatch, tmp_path):
    """The manifest is atomically saved after every product result, so a
    mid-batch crash leaves a resume point with the finished products."""
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    registry = write_registry(tmp_path, ["a", "b", "c"])
    products = list(registry.products)
    attempts = {"n": 0}

    def safe(product, csv_path=None, mode=None, run_id=None):
        attempts["n"] += 1
        if attempts["n"] == 2:
            raise KeyboardInterrupt()  # crash between checkpoints
        return {"product_id": product.product_id, "keyword": product.research_keyword,
                "status": "ok", "score_result": {"score": 50, "label": "OBSERVAR"}, "md_path": "pack.md"}

    monkeypatch.setattr(run, "_safe_run_keyword", safe)
    try:
        run._run_all(products, send=False)
    except KeyboardInterrupt:
        pass

    store = run._run_store()
    run_id = run._compute_run_id("all", products, None)
    manifest = store.load(run_id)
    assert manifest["state"] == "failed"
    assert [r["keyword"] for r in manifest["results"]] == ["a"]  # checkpointed

    # Resume retries only what is missing.
    results = run._run_all(products, send=False)
    assert [r["keyword"] for r in results] == ["a", "b", "c"]
    assert attempts["n"] == 4  # a (reused) + b + c retried
    assert store.load(run_id)["state"] == "completed"
