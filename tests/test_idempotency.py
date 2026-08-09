"""Idempotency: completed reruns reuse artifacts without paid-source calls."""

from __future__ import annotations

import json
from datetime import date as real_date

import pytest

import run

ADS = {
    "source": "Facebook Ad Library via Apify",
    "total_ads": 1,
    "ad_ids": ["a1"],
    "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
    "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
    "saturation": "baixa",
}
TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def _write_previous(tmp_path, keyword="mochila", label="OBSERVAR"):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    previous = {
        "keyword": keyword,
        "date": real_date.today().isoformat(),
        "score": {"score": 50, "label": label, "breakdown": {}},
        "trends": TRENDS,
        "keyword_planner": CSV,
        "facebook_ads": ADS,
        "seo": SEO,
        "landing_price": {"price_range": {"min": 10, "max": 20}},
        "suggested_keywords": [{"keyword": "mochila escolar", "score": 10, "status": "proposed"}],
    }
    (data_dir / f"{real_date.today().isoformat()}--mochila.json").write_text(json.dumps(previous), encoding="utf-8")


def _install_full_stack(monkeypatch, tmp_path):
    """Point run at tmp_path, stub every adapter and count paid calls."""
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "keywords.txt").write_text("mochila\nbolsa\n", encoding="utf-8")
    calls = {"apify": 0, "trends": 0, "seo": 0, "landing": 0, "related": 0}
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: calls.__setitem__("apify", calls["apify"] + 1) or dict(ADS))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: calls.__setitem__("trends", calls["trends"] + 1) or dict(TRENDS))
    monkeypatch.setattr(run.trends, "fetch_related", lambda *a, **k: calls.__setitem__("related", calls["related"] + 1) or {"related_queries": []})
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: calls.__setitem__("seo", calls["seo"] + 1) or dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: calls.__setitem__("landing", calls["landing"] + 1) or {"price_range": {"min": 10, "max": 20}})
    monkeypatch.setattr(run, "_csv_data", lambda path: dict(CSV))
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")
    return calls, generated


def _sunday():
    return real_date(2026, 7, 19)  # Sunday


def test_completed_sunday_rerun_makes_no_adapter_calls(monkeypatch, tmp_path):
    calls, _ = _install_full_stack(monkeypatch, tmp_path)
    first = run._run_sunday(send=False)
    assert calls["apify"] == 2 and calls["trends"] == 2
    second = run._run_sunday(send=False)
    assert calls == {"apify": 2, "trends": 2, "seo": 2, "landing": 2, "related": 0}
    assert [r["keyword"] for r in second] == ["mochila", "bolsa"]


def test_completed_all_rerun_makes_no_adapter_calls(monkeypatch, tmp_path):
    calls, _ = _install_full_stack(monkeypatch, tmp_path)
    run._run_all(["mochila", "bolsa"], send=False)
    assert calls["apify"] == 2
    run._run_all(["mochila", "bolsa"], send=False)
    assert calls["apify"] == 2


def test_completed_keyword_rerun_makes_no_adapter_calls(monkeypatch, tmp_path, capsys):
    calls, _ = _install_full_stack(monkeypatch, tmp_path)
    run._run_keyword_action("mochila", send=False)
    assert calls["apify"] == 1
    run._run_keyword_action("mochila", send=False)
    assert calls["apify"] == 1


def test_completed_wednesday_rerun_makes_no_apify_calls(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify_calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: apify_calls.append(kw) or dict(ADS))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")
    run._run_wednesday(send=False)
    assert apify_calls == ["mochila"]
    run._run_wednesday(send=False)
    assert apify_calls == ["mochila"]


def test_completed_monthly_rerun_makes_no_trends_calls(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    trends_calls = []
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: trends_calls.append((a, k)) or dict(TRENDS))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")
    run._run_monthly(send=False)
    assert len(trends_calls) == 1
    run._run_monthly(send=False)
    assert len(trends_calls) == 1


def test_direct_and_auto_sunday_share_run_identity(monkeypatch, tmp_path):
    calls, _ = _install_full_stack(monkeypatch, tmp_path)

    class FakeDate:
        @classmethod
        def today(cls):
            return _sunday()

    monkeypatch.setattr(run, "date", FakeDate)
    run._run_sunday(send=False)
    assert calls["apify"] == 2
    results = run._auto_dispatch(send=False)
    assert calls["apify"] == 2  # auto reused the direct-run manifest
    assert [r["keyword"] for r in results] == ["mochila", "bolsa"]


def test_run_without_digest_then_digest_delivers_without_rerun(monkeypatch, tmp_path):
    calls, _ = _install_full_stack(monkeypatch, tmp_path)
    run._run_sunday(send=False)
    assert calls["apify"] == 2

    delivered = []
    monkeypatch.setattr(
        run.telegram, "deliver",
        lambda text, state_id=None, state_dir=None, on_chunk_sent=None: delivered.append((text, state_id))
        or {"delivered": True, "status": "success", "chunks_total": 1, "chunks_sent": 1, "errors": []},
    )
    results = run._run_sunday(send=True)
    assert calls["apify"] == 2  # research NOT rerun for delivery
    assert len(delivered) == 1
    digest_text, state_id = delivered[0]
    assert "DOMINGO" in digest_text
    assert state_id == run._compute_run_id("sunday", ["mochila", "bolsa"], None)
    # A later --digest call on the same completed run never re-sends.
    run._run_sunday(send=True)
    assert len(delivered) == 1


def test_interrupted_running_state_is_finalized_without_reduplication(monkeypatch, tmp_path):
    calls, _ = _install_full_stack(monkeypatch, tmp_path)
    first = run._run_sunday(send=False)
    assert calls["apify"] == 2 and calls["trends"] == 2
    # Simulate a crash between the last checkpoint and the finalize: rewrite
    # the completed manifest back to "running".  All keywords already reached
    # a terminal outcome, so the rerun finalizes the run WITHOUT re-executing
    # any paid source.
    store = run._run_store()
    run_id = run._compute_run_id("sunday", ["mochila", "bolsa"], None)
    manifest = store.load(run_id)
    manifest["state"] = "running"
    store.save(run_id, manifest)
    second = run._run_sunday(send=False)
    assert calls == {"apify": 2, "trends": 2, "seo": 2, "landing": 2, "related": 0}
    assert [r["keyword"] for r in second] == ["mochila", "bolsa"]
    assert store.load(run_id)["state"] == "completed"


def test_mid_batch_crash_resumes_only_missing_keywords(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: calls.append(kw) or dict(ADS))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: dict(TRENDS))
    monkeypatch.setattr(run.serp_seo, "fetch", lambda kw: dict(SEO))
    monkeypatch.setattr(run.landing_price, "fetch_prices_from_ads", lambda data: {"price_range": {"min": 1, "max": 2}})
    monkeypatch.setattr(run, "_csv_data", lambda path: dict(CSV))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    attempts = {"n": 0}

    def safe(keyword, csv_path=None, mode=None, run_id=None):
        attempts["n"] += 1
        if attempts["n"] == 2:
            raise KeyboardInterrupt()  # crash between the first checkpoint and the second
        return {"keyword": keyword, "status": "ok", "score_result": {"score": 50, "label": "OBSERVAR"}, "md_path": "pack.md"}

    monkeypatch.setattr(run, "_safe_run_keyword", safe)
    with pytest.raises(KeyboardInterrupt):
        run._run_all(["mochila", "bolsa"], send=False)

    store = run._run_store()
    run_id = run._compute_run_id("all", ["mochila", "bolsa"], None)
    manifest = store.load(run_id)
    assert manifest["state"] == "failed"
    assert [r["keyword"] for r in manifest["results"]] == ["mochila"]  # checkpoint preserved

    # Resume: only the missing keyword is retried; the checkpointed one is reused.
    results = run._run_all(["mochila", "bolsa"], send=False)
    assert [r["keyword"] for r in results] == ["mochila", "bolsa"]
    assert attempts["n"] == 3  # one retried keyword, not a full rerun
    assert store.load(run_id)["state"] == "completed"
