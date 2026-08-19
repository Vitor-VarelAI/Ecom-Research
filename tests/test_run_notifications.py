"""Slice 3: run-level NotificationBatch wiring (builder + delivery).

Covers the binding integration contract:

* exact mode shapes (keyword / sunday / all / wednesday / monthly) with
  stable order and stable item ids, including rebuild from the persisted
  notification snapshot;
* the exact ``deliver_batch`` call (state_id=run_id, state_dir=_digests_dir)
  and the absence of any legacy single-text ``deliver`` call;
* stdout digest-once semantics for send=False / send=True / delivery failure;
* completed successful reruns and send=False -> send=True reconstruction
  with zero source calls and no resend;
* deferred technical batches never calling the network;
* manifest/snapshot freedom from tokens, chat ids, URLs and raw payloads;
* the Wednesday pulse facts surviving into rendered and audit text;
* v2-shaped manifest delivery (no legacy chunks fields) and byte-exact
  preservation of the legacy plain digest.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

import run
from pipeline.notifications import (
    KIND_PRODUCT,
    KIND_SUMMARY,
    assert_semantic_parity,
    notification_snapshot,
    render_html,
    render_rich,
    stable_product_id,
    visible_text,
)
from pipeline.output import pack

RUN_DATE = date(2026, 8, 16)

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


def _result(keyword: str, label: str = "OBSERVAR", score: int = 50) -> dict:
    """A full run result dict, as produced by the keyword pipeline."""
    return {
        "keyword": keyword,
        "score_result": {"score": score, "label": label, "breakdown": {}},
        "ads_data": dict(ADS),
        "trends_data": dict(TRENDS),
        "csv_data": dict(CSV),
        "seo_data": dict(SEO),
        "landing_price_data": {"price_range": {"min": 10, "max": 20}},
        "suggested_keywords": [{"keyword": f"{keyword} extra", "score": 10, "status": "proposed"}],
    }


def _wednesday_result(keyword: str) -> dict:
    result = _result(keyword)
    result.update({
        "changes": ["+2 ads novos", "-1 ads removidos"],
        "ad_cohort": {
            "ad_retention_pct": 50.0,
            "advertiser_retention_pct": 66.7,
            "signals": ["high_churn", "concentrated_market"],
        },
        "advertiser_deltas": ["p1: 2→3 variantes"],
    })
    return result


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
    monkeypatch.setattr(run, "_csv_data", lambda path, keyword: dict(CSV))
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")
    return calls


def _install_deliver_batch(monkeypatch, *, fail: bool = False):
    """Record every deliver_batch call; report success (or failure)."""
    delivered = []

    def deliver_batch(batch, state_id=None, state_dir=None, renderer=None, on_item_sent=None):
        delivered.append({"batch": batch, "state_id": state_id, "state_dir": state_dir, "renderer": renderer})
        if fail:
            return {"delivered": False, "status": "error", "schema_version": 2,
                    "items_total": len(batch.items), "items_sent": 0,
                    "errors": ["http_error"], "renderer": None, "fallback_used": False}
        return {"delivered": True, "status": "success", "schema_version": 2,
                "items_total": len(batch.items), "items_sent": len(batch.items),
                "errors": [], "renderer": None, "fallback_used": False}

    monkeypatch.setattr(run.telegram, "deliver_batch", deliver_batch)
    return delivered


def _completed_manifest(tmp_path, keywords):
    return run._run_store().load(run._compute_run_id("all", keywords, None))


# ── 1) Mode shapes, stable order, stable item ids ─────────────────────────


def test_keyword_mode_is_one_product_without_summary():
    batch = run.build_batch(mode="keyword", run_id="k1", results=[_result("mochila")], run_date=RUN_DATE)
    assert [item.kind for item in batch.items] == [KIND_PRODUCT]
    assert len(batch.items) == 1
    assert batch.items[0].product == stable_product_id("mochila")
    assert batch.items[0].order == 0


def test_keyword_mode_rejects_zero_or_multiple_products():
    with pytest.raises(ValueError, match="exatamente um resultado"):
        run.build_batch(mode="keyword", run_id="k0", results=[], run_date=RUN_DATE)
    with pytest.raises(ValueError, match="exatamente um resultado"):
        run.build_batch(
            mode="keyword",
            run_id="k2",
            results=[_result("mochila"), _result("bolsa")],
            run_date=RUN_DATE,
        )


def test_sunday_and_all_make_summary_then_one_product_per_result():
    for mode in ("sunday", "all"):
        batch = run.build_batch(mode=mode, run_id="r1", results=[_result("mochila"), _result("bolsa")], run_date=RUN_DATE)
        assert [item.kind for item in batch.items] == [KIND_SUMMARY, KIND_PRODUCT, KIND_PRODUCT]
        assert [item.product for item in batch.items[1:]] == [
            stable_product_id("mochila"), stable_product_id("bolsa"),
        ]
        assert [item.order for item in batch.items] == [0, 1, 2]


def test_wednesday_and_monthly_make_summary_then_products_in_order():
    for mode in ("wednesday", "monthly"):
        results = [_result("mochila"), _result("bolsa")]
        if mode == "wednesday":
            results = [_wednesday_result("mochila"), _wednesday_result("bolsa")]
        batch = run.build_batch(mode=mode, run_id="r1", results=results, run_date=RUN_DATE)
        assert [item.kind for item in batch.items] == [KIND_SUMMARY, KIND_PRODUCT, KIND_PRODUCT]
        assert [item.product for item in batch.items[1:]] == [
            stable_product_id("mochila"), stable_product_id("bolsa"),
        ]


def test_stable_order_and_item_ids_across_rebuilds():
    results = [_result("mochila"), _result("bolsa")]
    first = run.build_batch(mode="sunday", run_id="stable-1", results=results, run_date=RUN_DATE)
    second = run.build_batch(mode="sunday", run_id="stable-1", results=results, run_date=RUN_DATE)
    assert first.items == second.items
    assert [item.item_id for item in first.items] == [item.item_id for item in second.items]
    assert first.audit_text == second.audit_text


def test_rebuild_from_notification_snapshots_is_identical():
    """The persisted snapshot (ads/trends safe projection) rebuilds the exact
    same items, ids, order and audit text — no source calls involved."""
    results = [_result("mochila"), _result("bolsa")]
    fresh = run.build_batch(mode="sunday", run_id="snap-1", results=results, run_date=RUN_DATE)
    compact = [
        dict(result, ads_data=(notification_snapshot(result) or {}).get("ads_data"),
             trends_data=(notification_snapshot(result) or {}).get("trends_data"))
        for result in results
    ]
    rebuilt = run.build_batch(mode="sunday", run_id="snap-1", results=compact, run_date=RUN_DATE)
    assert rebuilt.items == fresh.items
    assert rebuilt.audit_text == fresh.audit_text


def test_legacy_results_without_snapshot_degrade_to_placeholders_without_sources():
    """A historical compact result with no snapshot rebuilds safely: score and
    label survive, missing evidence becomes placeholders, nothing crashes."""
    legacy = [{"keyword": "mochila", "score_result": {"score": 50, "label": "OBSERVAR"}}]
    batch = run.build_batch(mode="sunday", run_id="legacy-1", results=legacy, run_date=RUN_DATE)
    assert len(batch.items) == 2
    item = batch.items[1]
    assert item.product == stable_product_id("mochila")
    audit = item.audit_text
    assert "mochila" in audit and "OBSERVAR" in audit


# ── Exact legacy digest preservation ──────────────────────────────────────


def test_keyword_batch_audit_is_the_exact_legacy_digest_line():
    result = _result("mochila")
    batch = run.build_batch(mode="keyword", run_id="k2", results=[result], run_date=RUN_DATE)
    expected = pack.generate_digest_line(
        "mochila", result["score_result"], result["ads_data"],
        trends_data=result["trends_data"], run_date=RUN_DATE,
    )
    assert batch.audit_text == expected


def test_sunday_batch_audit_joins_title_and_per_result_digest_lines():
    r1, r2 = _result("mochila"), _result("bolsa")
    batch = run.build_batch(mode="sunday", run_id="s2", results=[r1, r2], run_date=RUN_DATE)
    lines = [f"DOMINGO — {RUN_DATE.isoformat()}"]
    for result in (r1, r2):
        lines.append(pack.generate_digest_line(
            result["keyword"], result["score_result"], result["ads_data"],
            trends_data=result["trends_data"], run_date=RUN_DATE,
        ))
        lines.append("Sugestões (aprovação manual): " + ", ".join(
            item["keyword"] for item in result["suggested_keywords"][:3]
        ))
    assert batch.audit_text == "\n".join(lines)


def test_monthly_preserved_result_audit_matches_legacy_line():
    preserved = {"keyword": "mochila", "preserved": True, "error": "Trends offline",
                 "score_result": {"score": 50, "label": "OBSERVAR"}}
    batch = run.build_batch(mode="monthly", run_id="m1", results=[preserved], run_date=RUN_DATE)
    assert batch.audit_text == "RE-SCORE MENSAL — 2026-08-16\nmochila — fonte falhou; pack anterior preservado (Trends offline)"


# ── 2) Exact deliver_batch call, no legacy deliver ────────────────────────


def test_run_calls_deliver_batch_with_run_id_and_digests_dir(monkeypatch, tmp_path):
    _install_full_stack(monkeypatch, tmp_path)
    delivered = _install_deliver_batch(monkeypatch)
    legacy = []
    monkeypatch.setattr(run.telegram, "deliver", lambda *a, **k: legacy.append(a) or {"delivered": True})

    run._run_all(["mochila", "bolsa"], send=True)

    assert len(delivered) == 1
    call = delivered[0]
    assert call["state_id"] == run._compute_run_id("all", ["mochila", "bolsa"], None)
    assert call["state_dir"] == run._digests_dir()
    assert call["renderer"] is None  # TELEGRAM_RENDERER selection stays inside deliver_batch
    assert legacy == []  # the production single-text deliver path is gone


def test_every_result_producing_mode_uses_deliver_batch(monkeypatch, tmp_path):
    """keyword / sunday / wednesday / monthly all route through deliver_batch
    with state_id=run_id and state_dir=_digests_dir."""
    _install_full_stack(monkeypatch, tmp_path)
    delivered = _install_deliver_batch(monkeypatch)

    run._run_keyword_action("mochila", send=True)
    assert len(delivered) == 1
    assert delivered[0]["state_dir"] == run._digests_dir()
    assert delivered[0]["state_id"] == run._compute_run_id("keyword", ["mochila"], None)

    # A different run (sunday) is a fresh invocation: it delivers too, with the
    # same explicit v1->v2 state location.
    run._run_sunday(send=True)
    assert len(delivered) == 2
    assert all(call["state_dir"] == run._digests_dir() for call in delivered)
    assert delivered[1]["state_id"] == run._compute_run_id("sunday", ["mochila", "bolsa"], None)


# ── 3) Digest printed exactly once; network only when send=True ───────────


def test_send_false_makes_no_call_and_prints_digest_once(monkeypatch, tmp_path, capsys):
    _install_full_stack(monkeypatch, tmp_path)
    delivered = _install_deliver_batch(monkeypatch)

    run._run_all(["mochila", "bolsa"], send=False)
    out = capsys.readouterr().out
    assert delivered == []
    manifest = _completed_manifest(tmp_path, ["mochila", "bolsa"])
    assert out.count(manifest["digest"]["text"]) == 1
    assert "RESUMO DO DIA" in out


def test_send_true_prints_digest_once_and_calls_once(monkeypatch, tmp_path, capsys):
    _install_full_stack(monkeypatch, tmp_path)
    delivered = _install_deliver_batch(monkeypatch)

    run._run_all(["mochila", "bolsa"], send=True)
    out = capsys.readouterr().out
    assert len(delivered) == 1
    manifest = _completed_manifest(tmp_path, ["mochila", "bolsa"])
    assert out.count(manifest["digest"]["text"]) == 1


def test_delivery_failure_prints_digest_once_not_twice(monkeypatch, tmp_path, capsys):
    _install_full_stack(monkeypatch, tmp_path)
    delivered = _install_deliver_batch(monkeypatch, fail=True)

    run._run_all(["mochila", "bolsa"], send=True)
    out = capsys.readouterr().out
    assert len(delivered) == 1
    manifest = _completed_manifest(tmp_path, ["mochila", "bolsa"])
    assert manifest["digest"]["delivery"]["status"] == "error"
    assert out.count(manifest["digest"]["text"]) == 1  # no fallback second copy


# ── 4) Rerun gates: zero sources, zero resend, deterministic rebuild ──────


def test_completed_success_rerun_makes_zero_source_and_zero_message_calls(monkeypatch, tmp_path):
    calls = _install_full_stack(monkeypatch, tmp_path)
    delivered = _install_deliver_batch(monkeypatch)

    run._run_all(["mochila", "bolsa"], send=True)
    assert calls["apify"] == 2 and calls["trends"] == 2
    assert len(delivered) == 1

    run._run_all(["mochila", "bolsa"], send=True)
    assert calls["apify"] == 2 and calls["trends"] == 2  # zero source calls
    assert len(delivered) == 1  # zero message calls


def test_send_false_then_send_true_reconstructs_same_batch_without_sources(monkeypatch, tmp_path):
    calls = _install_full_stack(monkeypatch, tmp_path)
    run._run_all(["mochila", "bolsa"], send=False)
    assert calls["apify"] == 2

    delivered = _install_deliver_batch(monkeypatch)
    run._run_all(["mochila", "bolsa"], send=True)

    assert calls["apify"] == 2 and calls["trends"] == 2  # zero source calls
    assert len(delivered) == 1
    batch = delivered[0]["batch"]
    assert [item.kind for item in batch.items] == [KIND_SUMMARY, KIND_PRODUCT, KIND_PRODUCT]
    assert [item.product for item in batch.items[1:]] == [
        stable_product_id("mochila"), stable_product_id("bolsa"),
    ]
    manifest = _completed_manifest(tmp_path, ["mochila", "bolsa"])
    assert batch.audit_text == manifest["digest"]["text"]  # same deterministic batch

    # A later send=True on the completed run never resends.
    run._run_all(["mochila", "bolsa"], send=True)
    assert len(delivered) == 1


# ── 5) Technical-fatal batches stay deferred and never send ───────────────


def test_technical_fatal_batch_never_calls_deliver_batch(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    delivered = _install_deliver_batch(monkeypatch)

    def safe(keyword, csv_path=None, mode=None, run_id=None):
        if keyword == "boom":
            return {"keyword": keyword, "error": "explosão", "status": "technical_fatal",
                    "score_result": {"score": 0, "label": "DADOS INSUFICIENTES"}}
        return {"keyword": keyword, "status": "ok", "md_path": "pack.md",
                "score_result": {"score": 50, "label": "OBSERVAR"},
                "ads_data": dict(ADS), "trends_data": dict(TRENDS),
                "suggested_keywords": []}

    monkeypatch.setattr(run, "_safe_run_keyword", safe)
    results = run._run_all(["ok", "boom"], send=True)

    assert delivered == []  # MUST NOT call deliver_batch
    assert results[1]["status"] == "technical_fatal"
    manifest = run._run_store().load(run._compute_run_id("all", ["ok", "boom"], None))
    assert manifest["state"] == "completed_with_errors"
    delivery = manifest["digest"]["delivery"]
    assert delivery["status"] == "deferred"
    assert delivery["schema_version"] == 2
    assert delivery["items_total"] == 3 and delivery["items_sent"] == 0
    out = capsys.readouterr().out
    assert out.count(manifest["digest"]["text"]) == 1  # printed and persisted


# ── 6) Snapshot and manifest safety ───────────────────────────────────────


def test_notification_snapshot_drops_urls_credentials_and_raw_payloads():
    hostile = {
        "keyword": "mochila",
        "ads_data": {
            "total_ads": 3,
            "saturation": "alta",
            "advertisers": [
                {"page_id": "p1", "page_name": "Loja", "active_ads": 2,
                 "landing_url": "https://user:pass@shop.example/x?api_key=TOPSECRET#frag",
                 "link": "https://www.facebook.com/ads/library/?id=1"},
            ],
            "top_ads": [{"ad_archive_id": "a1", "landing_url": "https://raw.example/secret"}],
            "chat_id": "chat-SUPERSECRET-id",
        },
        "trends_data": {
            "direction": "subida", "slope_pct": 10.0, "seasonal": True, "peak": "2026-12-01",
            "related_queries": [{"keyword": "x"}],
            "private_url": "https://trends.example/private",
        },
    }
    snapshot = notification_snapshot(hostile)
    blob = json.dumps(snapshot)
    for needle in (
        "TOPSECRET", "user:pass", "api_key", "#frag", "chat-SUPERSECRET-id",
        "api.telegram.org", "/bot", "https://", "top_ads", "related_queries",
        "private_url", "landing_url",
    ):
        assert needle not in blob, f"segredo/URL no snapshot: {needle!r}"
    assert snapshot == {
        "ads_data": {"advertisers": [{"page_name": "Loja", "active_ads": 2}],
                     "saturation": "alta", "total_ads": 3},
        "trends_data": {"direction": "subida", "slope_pct": 10.0, "seasonal": True, "peak": "2026-12-01"},
    }


def test_completed_manifest_snapshot_and_delivery_state_are_safe(monkeypatch, tmp_path):
    _install_full_stack(monkeypatch, tmp_path)
    run._run_all(["mochila", "bolsa"], send=False)

    manifest = _completed_manifest(tmp_path, ["mochila", "bolsa"])
    delivery = manifest["digest"]["delivery"]
    assert "chunks_total" not in delivery and "chunks_sent" not in delivery
    assert delivery["status"] == "skipped"
    assert delivery["schema_version"] == 2
    assert delivery["items_total"] == 3 and delivery["items_sent"] == 0
    for result in manifest["results"]:
        snapshot = result.get("notification_snapshot") or {}
        blob = json.dumps(snapshot)
        for needle in ("https://", "api.telegram.org", "/bot", "chat_id", "token"):
            assert needle not in blob, f"segredo no snapshot do manifesto: {needle!r}"


# ── 7) Wednesday pulse facts survive into rendered and audit text ─────────


def test_wednesday_product_keeps_retention_churn_and_advertiser_deltas():
    result = _wednesday_result("mochila")
    batch = run.build_batch(mode="wednesday", run_id="w7", results=[result], run_date=RUN_DATE)
    item = batch.items[1]

    # Audit text: changes, ad retention, churn signal and advertiser deltas.
    assert "retenção ads 50.0%" in item.audit_text
    assert "high_churn" in item.audit_text
    assert "p1: 2→3 variantes" in item.audit_text
    assert "+2 ads novos" in item.audit_text

    # Rendered content: both retentions, signals and deltas are visible.
    for fragment in (render_rich(item), render_html(item)):
        text = visible_text(fragment["payload"]["rich_message"]["html"] if "rich_message" in fragment["payload"] else fragment["payload"]["text"])
        assert "Retenção ads" in text and "50.0%" in text
        assert "Retenção anunciantes" in text and "66.7%" in text
        assert "high_churn" in text and "concentrated_market" in text
        assert "p1: 2→3 variantes" in text
    assert_semantic_parity(item)


def test_wednesday_digest_preserves_existing_pulse_format():
    result = _wednesday_result("mochila")
    batch = run.build_batch(mode="wednesday", run_id="w8", results=[result], run_date=RUN_DATE)
    assert batch.audit_text == (
        f"PULSE QUARTA — {RUN_DATE.isoformat()}\n"
        "mochila: +2 ads novos, -1 ads removidos | retenção ads 50.0% | "
        "Sinais: high_churn, concentrated_market | p1: 2→3 variantes"
    )


# ── 8) Placeholders where data is absent ──────────────────────────────────


def test_product_content_uses_explicit_placeholders_for_absent_data():
    result = _result("mochila")
    batch = run.build_batch(mode="keyword", run_id="k8", results=[result], run_date=RUN_DATE)
    item = batch.items[0]
    for fragment in (render_rich(item), render_html(item)):
        text = visible_text(fragment["payload"]["rich_message"]["html"] if "rich_message" in fragment["payload"] else fragment["payload"]["text"])
        assert "FORNECEDOR EM FALTA" in text       # supplier placeholder
        assert "POR VALIDAR" in text               # shipping/margin placeholder
        assert "DOCUMENTAÇÃO EM FALTA" in text     # legal placeholder
        assert "PESQUISA NECESSÁRIA" in text       # legal research placeholder
    # No invented evidence: no URLs, buttons or workflow gates in the content.
    blob = json.dumps({**render_html(item), **render_rich(item)})
    for needle in ("https://", "callback_data", "reply_markup", "AVANÇAR PARA PESQUISA DE FORNECEDOR"):
        assert needle not in blob, f"conteúdo inventado: {needle!r}"
