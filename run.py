#!/usr/bin/env python3
"""Operational deterministic CLI for the Ecom Research pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import unicodedata
from datetime import date, timedelta
from typing import Any

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO_ROOT)

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(REPO_ROOT, ".env"))
except ImportError:
    pass

from pipeline import provenance, quota, run_state
from pipeline.adapters import apify_ads, keyword_csv, landing_price, serp_seo, trends
from pipeline.delivery import telegram
from pipeline.output import pack
from pipeline.scoring import score
from pipeline.suggestions import extract_keyword_suggestions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run")


# ── Run identity, state and atomic persistence ─────────────────────────────


def _data_dir() -> str:
    return os.path.join(REPO_ROOT, "data")


def _run_store() -> run_state.RunStore:
    return run_state.RunStore(_data_dir())


def _digests_dir() -> str:
    return os.path.join(_data_dir(), "digests")


def _csv_hash(csv_path: str | None = None) -> str | None:
    """SHA-256 of the CSV actually consumed, or None when no CSV is used."""
    path = csv_path or _find_latest_csv()
    if not path or not os.path.isfile(path):
        return None
    return run_state.file_sha256(path)


# Modes whose action consumes the Keyword Planner CSV.  Wednesday and monthly
# never read the CSV, so their run identity must stay stable when unrelated
# CSV files change.
CSV_CONSUMING_MODES = frozenset({"keyword", "all", "sunday"})


def _compute_run_id(mode: str, keywords: list[str], csv_path: str | None = None) -> str:
    """Deterministic identity: date + mode + ordered keywords + CSV hash + scoring version.

    The CSV hash is included only when the action actually consumes the CSV
    (``keyword``, ``all`` and ``sunday`` — including Sunday dispatched via
    ``--auto``, which executes the same ``sunday`` action).  Wednesday/monthly
    identity stays stable when unrelated CSV files change.  A direct
    ``--sunday`` and ``--auto`` on a Sunday resolve to the same run id.
    """
    csv_sha = _csv_hash(csv_path) if mode in CSV_CONSUMING_MODES else None
    return run_state.compute_run_id(
        logical_date=date.today().isoformat(),
        mode=mode,
        keywords=keywords,
        csv_sha256=csv_sha,
        scoring_version=score.SCORING_VERSION,
    )


def _begin_manifest(run_id: str, mode: str, keywords: list[str], csv_path: str | None) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "state": "running",
        "mode": mode,
        "logical_date": date.today().isoformat(),
        "keywords": list(keywords),
        "csv_sha256": _csv_hash(csv_path) if mode in CSV_CONSUMING_MODES else None,
        "scoring_version": score.SCORING_VERSION,
        "created_at": run_state.utc_now_iso(),
        "completed_at": None,
        "results": [],
        "digest": {
            "text": "",
            "hash": None,
            "delivery": {"status": "pending", "chunks_total": 0, "chunks_sent": 0, "errors": []},
        },
        "artifacts": {"md": [], "json": []},
    }


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    keep = ("keyword", "md_path", "score_result", "preserved", "error", "status", "changes",
            "advertiser_deltas", "ad_cohort", "suggested_keywords")
    return {key: result.get(key) for key in keep}


def _finish_manifest(
    manifest: dict[str, Any],
    results: list[dict[str, Any]],
    digest: str,
    delivery: dict[str, Any],
    *,
    state: str = "completed",
    compact: bool = True,
) -> dict[str, Any]:
    md_files = sorted({str(r["md_path"]) for r in results if r.get("md_path")})
    json_files = [
        os.path.join(_data_dir(), os.path.basename(md).replace(".md", ".json")) for md in md_files
    ]
    completed = run_state.utc_now_iso()
    stored_results = [_compact_result(r) for r in results] if compact else [dict(r) for r in results]
    manifest.update(
        {
            "state": state,
            "completed_at": completed,
            "results": stored_results,
            "digest": {
                "text": digest,
                "hash": run_state.sha256_hex(digest),
                "delivery": delivery,
            },
            "artifacts": {"md": md_files, "json": json_files},
        }
    )
    return manifest


def _manifest_completed(manifest: dict[str, Any] | None) -> bool:
    return bool(manifest and manifest.get("state") == "completed")


def _deliver_digest(text: str, run_id: str, send: bool) -> dict[str, Any]:
    """Deliver the digest with persisted state; stdout is the fallback."""
    if not send:
        print(text)
        return {"status": "skipped", "chunks_total": 0, "chunks_sent": 0, "errors": []}
    report = telegram.deliver(text, state_id=run_id, state_dir=_digests_dir())
    if not report["delivered"]:
        print(text)
    return report


def _reuse_run(manifest: dict[str, Any], send: bool) -> list[dict[str, Any]]:
    """Reuse a completed run: persisted digest/artifacts, zero adapter calls."""
    digest = (manifest.get("digest") or {}).get("text", "")
    delivery = (manifest.get("digest") or {}).get("delivery") or {}
    if send and delivery.get("status") != "success":
        report = _deliver_digest(digest, manifest["run_id"], send=True)
        manifest["digest"]["delivery"] = report
        _run_store().save(manifest["run_id"], manifest)
    elif not send:
        print(digest)
    logger.info("run_state: run %s já concluído (%s); artefactos reutilizados",
                manifest.get("run_id", "")[:12], manifest.get("mode", "?"))
    return list(manifest.get("results") or [])


def _is_technical_fatal(result: dict[str, Any] | None) -> bool:
    """True when a keyword result was a technical exception, not an expected
    source failure.  Expected failures still produce a pack (``md_path``) or a
    preserved outcome (``preserved``); a technical fatal produced neither and
    is the only thing a batch rerun must retry."""
    return bool(result and result.get("status") == "technical_fatal")


def _safe_run_keyword(keyword: str, csv_path: str | None = None, mode: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Per-keyword exception containment shared by --all, --sunday and --auto.

    Technical exceptions are marked ``status="technical_fatal"`` so the batch
    checkpoint can tell them apart from expected source failures (an Apify
    failure still produces a DADOS INSUFICIENTES pack) and retry only the
    fatal keywords on the next invocation.
    """
    try:
        result = run_keyword(keyword, csv_path=csv_path, mode=mode, run_id=run_id)
        result["status"] = "ok"
        return result
    except Exception as exc:
        error_type = type(exc).__name__
        logger.error("Erro fatal sanitizado em keyword=%r (tipo=%s)", keyword, error_type)
        return {
            "keyword": keyword,
            "error": f"Erro técnico inesperado ({error_type})",
            "status": "technical_fatal",
            "score_result": {"score": 0, "label": "DADOS INSUFICIENTES"},
        }


def _reused_timestamp(previous: dict[str, Any] | None) -> str | None:
    """Timestamp of reused evidence: previous ``generated_at``, then
    ``completed_at``, then date midnight (legacy packs)."""
    if not isinstance(previous, dict):
        return None
    for key in ("generated_at", "completed_at"):
        value = previous.get(key)
        if isinstance(value, str) and value:
            return value
    prev_date = previous.get("date")
    return f"{prev_date}T00:00:00+00:00" if prev_date else None


_TIMESERIES_KEYS = ("direction", "slope_pct", "seasonal", "peak", "top_geos")


def _is_timeseries(trends_data: dict[str, Any]) -> bool:
    """True when the trends evidence includes a real timeseries payload."""
    return isinstance(trends_data, dict) and any(key in trends_data for key in _TIMESERIES_KEYS)


def _source_states(mode: str, result: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify each relevant source as valid/missing/stale/failed for this run."""
    prev_date = (previous or {}).get("date") if isinstance(previous, dict) else None
    reused_ts = _reused_timestamp(previous)
    states: dict[str, Any] = {}
    ads = result.get("ads_data") or {}
    trends_data = result.get("trends_data") or {}
    csv = result.get("csv_data") or {}
    seo = result.get("seo_data") or {}
    landing = result.get("landing_price_data")

    def add(name: str, origin: str, reason: str, *, force_status: str | None = None, timestamp: str | None = None) -> None:
        states[name] = provenance.source_state(origin=origin, reason=reason, force_status=force_status, timestamp=timestamp)

    if mode in ("wednesday", "monthly"):
        stale_reason = f"reusado do pack de {prev_date}" if prev_date else "reusado do pack anterior"
        add("trends", "reused", stale_reason, timestamp=reused_ts)
        add("keyword_csv", "reused", stale_reason, timestamp=reused_ts)
        add("serp_seo", "reused", stale_reason, timestamp=reused_ts)
        add("landing_price", "reused", stale_reason, timestamp=reused_ts)
        add("suggestions", "reused", stale_reason, timestamp=reused_ts)
        if mode == "wednesday":
            if "error" in ads:
                add("apify_ads", "error", f"{ads['error']}")
                add("ad_cohort", "not_attempted", "pulse sem dados Apify; pack anterior preservado")
            else:
                add("apify_ads", "fresh", "refresh Apify do pulse")
                add("ad_cohort", "fresh", "álgebra de retenção sobre ad_ids/advertisers")
        else:
            if "error" in trends_data:
                add("trends", "error", f"{trends_data['error']}")
            else:
                add("trends", "fresh", "timeseries forçada no re-score mensal")
            add("apify_ads", "reused", stale_reason, timestamp=reused_ts)
            add("ad_cohort", "not_attempted", "modo mensal não faz pulse")
        return states

    # Full modes (keyword / all / sunday): everything is attempted fresh.
    if "error" in ads:
        add("apify_ads", "error", f"{ads['error']}")
    else:
        add("apify_ads", "fresh", "leitura válida (zero ads ativos é resultado válido)")
    if "error" in trends_data:
        if "não executada" in trends_data["error"]:
            add("trends", "not_attempted", f"{trends_data['error']}")
        else:
            add("trends", "error", f"{trends_data['error']}")
    elif _is_timeseries(trends_data):
        reason = "timeseries + related queries" if trends_data.get("related_queries") else "timeseries"
        add("trends", "fresh", reason)
    elif "related_queries" in trends_data:
        # Zero-ads flow: only Related Queries were fetched — a valid
        # ``related_queries_only`` operation, even when the list is empty.
        add("trends", "fresh", "related_queries_only (zero ads — timeseries não executada)")
    else:
        add("trends", "not_attempted", "SerpAPI não executada — modo sem rede")
    if "error" in seo:
        if "não executado" in seo["error"]:
            add("serp_seo", "not_attempted", f"{seo['error']}")
        else:
            add("serp_seo", "error", f"{seo['error']}")
    elif seo:
        add("serp_seo", "fresh", "snapshot SERP")
    else:
        add("serp_seo", "not_attempted", "SEO não executado")
    if "error" in csv:
        if "não encontrado" in csv["error"] or "Nenhum CSV" in csv["error"]:
            add("keyword_csv", "not_attempted", f"{csv['error']}", force_status="missing")
        else:
            add("keyword_csv", "error", f"{csv['error']}")
    elif csv:
        add("keyword_csv", "fresh", "parse do export do Keyword Planner")
    else:
        add("keyword_csv", "not_attempted", "sem CSV", force_status="missing")
    if landing is None:
        add("landing_price", "not_attempted", "landing não consultada")
    elif "error" in landing:
        if "não consultada" in landing["error"]:
            add("landing_price", "not_attempted", f"{landing['error']}")
        else:
            add("landing_price", "error", f"{landing['error']}")
    else:
        add("landing_price", "fresh", "preços extraídos das landings dos top ads")
    # The suggestions source was requested whenever Trends was consulted; an
    # empty result list is still a valid read (e.g. zero related queries in
    # the zero-ads flow), so only a genuinely un-attempted/failed Trends
    # leaves suggestions as not_attempted.
    trends_consulted = "error" not in trends_data and (
        _is_timeseries(trends_data) or "related_queries" in trends_data
    )
    if trends_consulted:
        add("suggestions", "fresh", "queries relacionadas da SerpAPI")
    else:
        add("suggestions", "not_attempted", "sem sugestões nesta execução")
    add("ad_cohort", "not_attempted", "modo completo não faz pulse")
    return states


def _run_metadata(mode: str, run_id: str, source_states: dict[str, Any]) -> dict[str, Any]:
    """Credential-free metadata passed to every pack generated in a run."""
    return {
        "mode": mode,
        "run_id": run_id,
        "source_states": source_states,
        "source_policies": provenance.snapshot(),
        "scoring_version": score.SCORING_VERSION,
        "scoring_config": score.scoring_config_snapshot(),
    }


def send_digest(text: str) -> bool:
    """Delegate delivery; the delivery adapter handles stdout fallback."""
    return telegram.send(text)


def _load_keywords() -> list[str]:
    path = os.path.join(REPO_ROOT, "config", "keywords.txt")
    if not os.path.exists(path):
        logger.error("Ficheiro config/keywords.txt não encontrado")
        return []
    with open(path, "r", encoding="utf-8") as source:
        return [line.strip() for line in source if line.strip() and not line.strip().startswith("#")]


def _consume_keywords() -> list[str]:
    """Read active keywords without adding suggestions or changing config."""
    keywords = _load_keywords()
    if keywords:
        logger.info("Lidas %d keywords; config/keywords.txt permanece intacto", len(keywords))
    else:
        logger.warning("Zero keywords ativas em config/keywords.txt")
    return keywords


def _find_latest_csv() -> str | None:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    files = sorted((name for name in os.listdir(data_dir) if name.endswith(".csv")), reverse=True)
    return os.path.join(data_dir, files[0]) if files else None


def _read_pack_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _pack_is_complete(data: dict[str, Any]) -> bool:
    """A pack is usable evidence only when its identity fields are sound: a
    dict ``score`` with a non-empty string ``label``, a non-empty string
    ``keyword`` and a valid ISO ``date``.  Run-scoped packs (with ``run_id``)
    additionally require the ``artifact_complete`` flag.  Malformed legacy
    packs (``score: []``, a blank keyword, a missing date, …) are ignored:
    they can never crash the selectors or evict real evidence."""
    if not isinstance(data, dict):
        return False
    score = data.get("score")
    if not isinstance(score, dict) or not isinstance(score.get("label"), str) or not score["label"]:
        return False
    if not isinstance(data.get("keyword"), str) or not data["keyword"]:
        return False
    pack_date = data.get("date")
    if not isinstance(pack_date, str):
        return False
    try:
        date.fromisoformat(pack_date)
    except ValueError:
        return False
    if data.get("run_id"):
        return data.get("artifact_complete") is True
    return True


def _pack_sort_key(data: dict[str, Any], filename: str) -> tuple[str, str, str]:
    """Deterministic selection: pack date, then generated/completed ts, then path."""
    date_str = str(data.get("date", "") or "")
    stamp = str(data.get("completed_at") or data.get("generated_at") or "")
    return (date_str, stamp, filename)


def _find_packs_by_label(labels: list[str], max_age_days: int | None = None) -> list[str]:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return []
    cutoff = date.today() - timedelta(days=max_age_days) if max_age_days is not None else None
    labeled: dict[str, tuple[tuple[str, str, str], str]] = {}
    overall: dict[str, tuple[tuple[str, str, str], str]] = {}
    for filename in sorted(os.listdir(data_dir)):
        if not filename.endswith(".json") or filename.startswith("quota"):
            continue
        data = _read_pack_json(os.path.join(data_dir, filename))
        if data is None or not _pack_is_complete(data):
            continue
        try:
            pack_date = date.fromisoformat(data.get("date", ""))
        except (TypeError, ValueError):
            continue
        keyword = data.get("keyword", "")
        if not keyword or (cutoff is not None and pack_date < cutoff):
            continue
        key = _pack_sort_key(data, filename)
        overall[keyword] = max(overall.get(keyword, (("", "", ""), "")), (key, filename))
        label = data.get("score", {}).get("label", "")
        if label in labels:
            labeled[keyword] = max(labeled.get(keyword, (("", "", ""), "")), (key, filename))

    keywords: list[str] = []
    for keyword in sorted(labeled):
        overall_key, overall_file = overall[keyword]
        overall_data = _read_pack_json(os.path.join(data_dir, overall_file)) or {}
        if not _pack_is_complete(overall_data):
            continue
        overall_label = overall_data.get("score", {}).get("label", "")
        if overall_label in labels:
            keywords.append(keyword)  # the newest pack carries a requested label
        elif overall_label == "DADOS INSUFICIENTES":
            # A failed run must not evict real evidence: keep the older labeled pack.
            keywords.append(keyword)
        # else: newest pack carries another real label (e.g. AVANÇAR) — follows it.
    return keywords


def _load_previous_pack(keyword: str) -> dict[str, Any] | None:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    best: tuple[tuple[str, str, str], str] | None = None
    for filename in os.listdir(data_dir):
        if not filename.endswith(".json") or filename.startswith("quota"):
            continue
        data = _read_pack_json(os.path.join(data_dir, filename))
        if data is None or not _pack_is_complete(data) or data.get("keyword") != keyword:
            continue
        if (data.get("score", {}).get("label", "")) == "DADOS INSUFICIENTES":
            continue  # failed-run packs are not evidence for the next incremental mode
        key = _pack_sort_key(data, filename)
        if best is None or key > best[0]:
            best = (key, filename)
    if best is None:
        return None
    return _read_pack_json(os.path.join(data_dir, best[1]))


def _csv_data(csv_path: str | None) -> dict[str, Any]:
    path = csv_path or _find_latest_csv()
    if path:
        return keyword_csv.parse(path)
    return {"error": "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner e coloca em data/"}


def _incomplete_score(trends_data: dict[str, Any], csv_data: dict[str, Any], ads_data: dict[str, Any]) -> dict[str, Any]:
    """Keep the unchanged numeric formula while preventing a false verdict."""
    result = score.compute(trends_data, csv_data, ads_data)
    result["label"] = "DADOS INSUFICIENTES"
    result["reason"] = "Facebook Ad Library via Apify indisponível; repetir quando a fonte estiver disponível"
    result["incomplete"] = True
    return result


def _forced_zero_score() -> dict[str, Any]:
    return {
        "score": 0,
        "label": "DESCARTAR",
        "breakdown": {
            "trend": 0.0,
            "volume": 0.0,
            "ad_longevity": 0.0,
            "saturacao_invertida": 0.0,
        },
        "reason": "zero ads ativos após filtragem determinística",
    }


def run_keyword(
    keyword: str,
    csv_path: str | None = None,
    skip_serpapi: bool = False,
    mode: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one full keyword pipeline with exactly one Apify invocation.

    ``mode``/``run_id`` are additive provenance passed through to the pack.
    """
    logger.info("=== INÍCIO: keyword=%r ===", keyword)
    ads_data = apify_ads.fetch(keyword)
    apify_failed = "error" in ads_data
    if apify_failed:
        logger.warning("Apify Ads: %s", ads_data["error"])
    else:
        logger.info("Apify Ads: total=%d saturação=%s", ads_data.get("total_ads", 0), ads_data.get("saturation"))

    # A source failure must continue the paid sources, but cannot create a
    # negative market decision.  A valid empty result takes the deterministic
    # zero-ads branch and spends one related-query call for alternatives.
    if apify_failed:
        landing_data = {"error": "sem dados: Apify indisponível; landing não consultada"}
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            seo_data = {"error": "SEO não executado — modo sem rede"}
        else:
            trends_data = trends.fetch(keyword)
            seo_data = serp_seo.fetch(keyword)
        csv_data = _csv_data(csv_path)
        score_result = _incomplete_score(trends_data, csv_data, ads_data)
    elif ads_data.get("total_ads", 0) == 0:
        landing_data = {"error": "sem dados: zero ads; landing não consultada"}
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
        else:
            trends_data = trends.fetch_related(keyword)
        seo_data = {"error": "SEO não executado: zero ads ativos"}
        csv_data = _csv_data(csv_path)
        score_result = _forced_zero_score()
    else:
        landing_data = landing_price.fetch_prices_from_ads(ads_data)
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            seo_data = {"error": "SEO não executado — modo sem rede"}
        else:
            trends_data = trends.fetch(keyword)
            seo_data = serp_seo.fetch(keyword)
        csv_data = _csv_data(csv_path)
        score_result = score.compute(trends_data, csv_data, ads_data)

    suggested = extract_keyword_suggestions(keyword, trends_data)
    result = {
        "keyword": keyword,
        "score_result": score_result,
        "trends_data": trends_data,
        "csv_data": csv_data,
        "ads_data": ads_data,
        "seo_data": seo_data,
        "landing_price_data": landing_data,
        "suggested_keywords": suggested,
    }
    run_mode = mode or "keyword"
    result["md_path"] = pack.generate(
        keyword=keyword,
        trends_data=trends_data,
        csv_data=csv_data,
        ads_data=ads_data,
        score_result=score_result,
        seo_data=seo_data,
        landing_price_data=landing_data,
        suggested_keywords=suggested,
        run_date=date.today(),
        **_run_metadata(run_mode, run_id or None, _source_states(run_mode, result, None)),
    )
    return result


def _digest_text(results: list[dict[str, Any]], title: str | None = None) -> str:
    lines = [title] if title else []
    for result in results:
        if result.get("error") and result.get("preserved"):
            lines.append(f"{result.get('keyword', '?')} — fonte falhou; pack anterior preservado ({result['error']})")
            continue
        keyword = result.get("keyword", "?")
        score_result = result.get("score_result", {"score": 0, "label": "DADOS INSUFICIENTES"})
        lines.append(pack.generate_digest_line(keyword, score_result, result.get("ads_data", {}), trends_data=result.get("trends_data", {})))
        suggestions = result.get("suggested_keywords", [])[:3]
        if suggestions:
            lines.append("Sugestões (aprovação manual): " + ", ".join(item["keyword"] for item in suggestions))
    return "\n".join(lines)


def _run_batch(
    mode: str,
    keywords: list[str],
    csv_path: str | None,
    send: bool,
    title: str,
) -> list[dict[str, Any]]:
    """Shared Sunday/--all batch loop with per-keyword checkpoints.

    The manifest is saved atomically after every keyword result, so a crash
    never loses the work already done.  A rerun of a ``running``/``failed``/
    ``completed_with_errors`` manifest reuses the terminal results (zero paid
    calls for them) and retries only the technical-fatal or missing keywords.

    A batch containing technical fatals is saved as ``completed_with_errors``
    and its digest is *not* Telegram-delivered: the content would change after
    the retry, and the operator would receive two different digests.  Instead
    the digest is printed to stdout with ``delivery.status == "deferred"`` and
    persisted in the manifest, and the final digest is delivered exactly once
    on the first run where every keyword reaches an intentional terminal
    outcome.  Normal source errors that already produced a pack remain
    terminal and digestable.
    """
    run_id = _compute_run_id(mode, keywords, csv_path)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            return _reuse_run(manifest, send=send)

        if manifest is None:
            results: list[dict[str, Any]] = []
            pending: list[str] = list(keywords)
        else:
            results = [
                r for r in (manifest.get("results") or [])
                if isinstance(r, dict) and r.get("keyword") in keywords and not _is_technical_fatal(r)
            ]
            done = {r.get("keyword") for r in results}
            pending = [kw for kw in keywords if kw not in done]
            if pending:
                logger.info("run_state: retoma %s — %d keyword(s) reutilizadas, %d a repetir",
                            mode, len(results), len(pending))

        manifest = _begin_manifest(run_id, mode, keywords, csv_path)
        manifest["results"] = results
        store.save(run_id, manifest)
        logger.info("=== %s: deep run de %d keywords ===", mode.upper(), len(keywords))
        try:
            for keyword in pending:
                result = _safe_run_keyword(keyword, csv_path=csv_path, mode=mode, run_id=run_id)
                results.append(result)
                manifest["results"] = [dict(r) for r in results]  # checkpoint
                store.save(run_id, manifest)

            # Deterministic digest order even after a resume appended retries.
            by_keyword = {r["keyword"]: r for r in results}
            results = [by_keyword[kw] for kw in keywords if kw in by_keyword]

            digest = _digest_text(results, title)
            if any(_is_technical_fatal(r) for r in results):
                # Deferred: never Telegram-deliver a technical-partial digest.
                delivery = {"status": "deferred", "chunks_total": 0, "chunks_sent": 0, "errors": []}
                print(digest)
                store.save(run_id, _finish_manifest(manifest, results, digest, delivery,
                                                    state="completed_with_errors", compact=False))
            else:
                delivery = _deliver_digest(digest, run_id, send=send)
                store.save(run_id, _finish_manifest(manifest, results, digest, delivery))
        except BaseException:
            store.save(run_id, {**manifest, "state": "failed"})
            raise
    return results


def _run_sunday(csv_path: str | None = None, send: bool = True) -> list[dict[str, Any]]:
    keywords = _consume_keywords()
    if not keywords:
        return []
    return _run_batch("sunday", keywords, csv_path, send, f"DOMINGO — {date.today().isoformat()}")


def _build_wednesday_digest(results: list[dict[str, Any]], date_str: str) -> str:
    lines = [f"PULSE QUARTA — {date_str}"]
    for result in results:
        if result.get("preserved"):
            lines.append(f"{result['keyword']} — Apify falhou; pack anterior preservado")
        else:
            line = f"{result['keyword']}: {', '.join(result.get('changes', []))}"
            cohort = result.get("ad_cohort")
            if cohort:
                retention = cohort.get("ad_retention_pct")
                retention_text = f"{retention:.1f}%" if retention is not None else "sem baseline"
                line += f" | retenção ads {retention_text}"
                signals = cohort.get("signals", [])
                if signals:
                    line += " | Sinais: " + ", ".join(signals)
            if result.get("advertiser_deltas"):
                line += " | " + ", ".join(result["advertiser_deltas"][:2])
            lines.append(line)
    return "\n".join(lines)


def _run_wednesday(send: bool = True) -> list[dict[str, Any]]:
    keywords = _find_packs_by_label(["AVANÇAR", "OBSERVAR"], max_age_days=14)
    if not keywords:
        logger.info("Zero packs AVANÇAR/OBSERVAR com <14 dias. Nada a pulsar.")
        return []
    run_id = _compute_run_id("wednesday", keywords, None)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            return _reuse_run(manifest, send=send)
        manifest = _begin_manifest(run_id, "wednesday", keywords, None)
        store.save(run_id, manifest)
        logger.info("=== QUARTA: pulse Apify em %d keywords ===", len(keywords))
        results: list[dict[str, Any]] = []
        try:
            for keyword in keywords:
                previous = _load_previous_pack(keyword)
                if not previous:
                    continue
                previous_ads = previous.get("facebook_ads", {})
                current_ads = apify_ads.fetch(keyword)
                if "error" in current_ads:
                    logger.warning("Pulse %s: %s; pack anterior preservado", keyword, current_ads["error"])
                    results.append({
                        "keyword": keyword,
                        "error": current_ads["error"],
                        "ads_data": current_ads,
                        "preserved": True,
                    })
                    continue

                changes, advertiser_deltas = _compare_ads(previous_ads, current_ads)
                cohort = _ad_cohort(previous_ads, current_ads, baseline_date=previous.get("date"))
                trends_data = previous.get("trends", {})
                csv_data = previous.get("keyword_planner", {})
                seo_data = previous.get("seo", {})
                landing_data = previous.get("landing_price")
                score_result = previous.get("score", {})
                suggested = previous.get("suggested_keywords", [])
                result = {
                    "keyword": keyword,
                    "score_result": score_result,
                    "ads_data": current_ads,
                    "trends_data": trends_data,
                    "csv_data": csv_data,
                    "seo_data": seo_data,
                    "landing_price_data": landing_data,
                    "suggested_keywords": suggested,
                    "changes": changes,
                    "advertiser_deltas": advertiser_deltas,
                    "ad_cohort": cohort,
                }
                result["md_path"] = pack.generate(
                    keyword=keyword,
                    trends_data=trends_data,
                    csv_data=csv_data,
                    ads_data=current_ads,
                    score_result=score_result,
                    seo_data=seo_data,
                    landing_price_data=landing_data,
                    suggested_keywords=suggested,
                    run_date=date.today(),
                    ad_cohort=cohort,
                    **_run_metadata("wednesday", run_id, _source_states("wednesday", result, previous)),
                )
                results.append(result)
            digest = _build_wednesday_digest(results, date.today().isoformat())
            delivery = _deliver_digest(digest, run_id, send=send)
            store.save(run_id, _finish_manifest(manifest, results, digest, delivery))
        except Exception:
            store.save(run_id, {**manifest, "state": "failed"})
            raise
    return results


def _run_monthly(send: bool = True) -> list[dict[str, Any]]:
    keywords = _find_packs_by_label(["OBSERVAR"], max_age_days=None)
    if not keywords:
        logger.info("Zero packs OBSERVAR na watchlist. Nada a re-score.")
        return []
    run_id = _compute_run_id("monthly", keywords, None)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            return _reuse_run(manifest, send=send)
        manifest = _begin_manifest(run_id, "monthly", keywords, None)
        store.save(run_id, manifest)
        logger.info("=== DIA 1: re-score watchlist de %d keywords ===", len(keywords))
        results: list[dict[str, Any]] = []
        try:
            for keyword in keywords:
                previous = _load_previous_pack(keyword)
                if not previous:
                    continue
                trends_data = trends.fetch(keyword, timeseries_only=True, force_refresh=True)
                ads_data = previous.get("facebook_ads", {})
                csv_data = previous.get("keyword_planner", {})
                seo_data = previous.get("seo", {})
                landing_data = previous.get("landing_price")
                suggested = previous.get("suggested_keywords", [])
                if "error" in trends_data:
                    # Preserve the previous pack: write no replacement pack, keep
                    # the OBSERVAR watchlist entry alive, report in digest/run state.
                    logger.warning("Re-score %s: %s; pack anterior preservado", keyword, trends_data["error"])
                    results.append({
                        "keyword": keyword,
                        "error": trends_data["error"],
                        "preserved": True,
                        "score_result": previous.get("score", {"score": 0, "label": "DADOS INSUFICIENTES"}),
                        "trends_data": trends_data,
                        "ads_data": ads_data,
                    })
                    continue
                score_result = score.compute(trends_data, csv_data, ads_data)
                result = {
                    "keyword": keyword,
                    "score_result": score_result,
                    "trends_data": trends_data,
                    "ads_data": ads_data,
                    "csv_data": csv_data,
                    "seo_data": seo_data,
                    "landing_price_data": landing_data,
                    "suggested_keywords": suggested,
                }
                result["md_path"] = pack.generate(
                    keyword=keyword,
                    trends_data=trends_data,
                    csv_data=csv_data,
                    ads_data=ads_data,
                    score_result=score_result,
                    seo_data=seo_data,
                    landing_price_data=landing_data,
                    suggested_keywords=suggested,
                    run_date=date.today(),
                    **_run_metadata("monthly", run_id, _source_states("monthly", result, previous)),
                )
                results.append(result)
            digest = _digest_text(results, f"RE-SCORE MENSAL — {date.today().isoformat()}")
            delivery = _deliver_digest(digest, run_id, send=send)
            store.save(run_id, _finish_manifest(manifest, results, digest, delivery))
        except Exception:
            store.save(run_id, {**manifest, "state": "failed"})
            raise
    return results


def _fold_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def _advertiser_counts(data: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for advertiser in data.get("advertisers", []) if "error" not in data else []:
        key = str(advertiser.get("page_id") or _fold_name(advertiser.get("page_name")))
        result[key] = int(advertiser.get("active_ads", 0))
    return result


def _advertiser_keys(data: dict[str, Any]) -> list[str]:
    """Stable advertiser identity keys (page_id, else accent-folded name)."""
    return sorted(key for key in _advertiser_counts(data) if key)


def _ad_cohort(
    previous: dict[str, Any],
    current: dict[str, Any],
    baseline_date: str | None = None,
    current_date: str | None = None,
) -> dict[str, Any]:
    """Advisory market-persistence cohort: previous pack + current Apify result only.

    Deterministic set algebra over ad ids and advertiser identities.  Retention
    percentages are None whenever the baseline side has no comparable items;
    signals are advisory and coexist.  Never touches scores or labels.
    """
    old_ids = sorted(set(previous.get("ad_ids", []))) if "error" not in previous else []
    new_ids = sorted(set(current.get("ad_ids", []))) if "error" not in current else []
    old_adv = _advertiser_keys(previous)
    new_adv = _advertiser_keys(current)
    retained_ads = sorted(set(old_ids) & set(new_ids))
    retained_adv = sorted(set(old_adv) & set(new_adv))

    ad_retention_pct = round(len(retained_ads) / len(old_ids) * 100, 1) if old_ids else None
    advertiser_retention_pct = round(len(retained_adv) / len(old_adv) * 100, 1) if old_adv else None

    total_ads = current.get("total_ads", 0) if "error" not in current else 0
    counts = _advertiser_counts(current)
    top_share = round(max(counts.values()) / total_ads * 100, 1) if counts and total_ads else None

    signals: list[str] = []
    if ad_retention_pct is not None and ad_retention_pct >= 50 and len(retained_adv) >= 3:
        signals.append("durable_demand")
    if ad_retention_pct is not None and ad_retention_pct < 50:
        signals.append("high_churn")
    if top_share is not None and top_share >= 60:
        signals.append("concentrated_market")
    if not old_ids and not old_adv:
        signals.append("insufficient_baseline")

    return {
        "baseline_date": baseline_date,
        "current_date": current_date or date.today().isoformat(),
        "previous_ads": old_ids,
        "current_ads": new_ids,
        "retained_ads": retained_ads,
        "new_ads": sorted(set(new_ids) - set(old_ids)),
        "removed_ads": sorted(set(old_ids) - set(new_ids)),
        "ad_retention_pct": ad_retention_pct,
        "previous_advertisers": old_adv,
        "current_advertisers": new_adv,
        "retained_advertisers": retained_adv,
        "new_advertisers": sorted(set(new_adv) - set(old_adv)),
        "lost_advertisers": sorted(set(old_adv) - set(new_adv)),
        "advertiser_retention_pct": advertiser_retention_pct,
        "top_advertiser_share_pct": top_share,
        "signals": signals,
    }


def _compare_ads(previous: dict[str, Any], current: dict[str, Any]) -> tuple[list[str], list[str]]:
    old_ids = set(previous.get("ad_ids", [])) if "error" not in previous else set()
    new_ids = set(current.get("ad_ids", [])) if "error" not in current else set()
    changes: list[str] = []
    if new_ids - old_ids:
        changes.append(f"+{len(new_ids - old_ids)} ads novos")
    if old_ids - new_ids:
        changes.append(f"-{len(old_ids - new_ids)} ads removidos")
    if not changes:
        changes.append("sem alterações nos ad_ids")

    old_adv = _advertiser_counts(previous)
    new_adv = _advertiser_counts(current)
    deltas: list[str] = []
    for key in sorted(set(old_adv) | set(new_adv)):
        if old_adv.get(key, 0) != new_adv.get(key, 0):
            deltas.append(f"{key}: {old_adv.get(key, 0)}→{new_adv.get(key, 0)} variantes")
    return changes, deltas

def _run_all(keywords: list[str], csv_path: str | None = None, send: bool = True) -> list[dict[str, Any]]:
    """Full run over all active keywords, with per-keyword exception isolation."""
    if not keywords:
        return []
    return _run_batch("all", keywords, csv_path, send, f"RESUMO DO DIA — {date.today().isoformat()}")


def _auto_dispatch(send: bool = True, csv_path: str | None = None) -> list[dict[str, Any]]:
    """Dispatch all actions for today; day 1 always runs before weekly pulse."""
    today = date.today()
    is_sunday = today.weekday() == 6
    is_wednesday = today.weekday() == 2
    results: list[dict[str, Any]] = []
    if today.day == 1:
        results.extend(_run_monthly(send=send))
    if is_sunday:
        results.extend(_run_sunday(send=send, csv_path=csv_path))
    elif is_wednesday:
        results.extend(_run_wednesday(send=send))
    if not results and today.day != 1 and not is_sunday and not is_wednesday:
        logger.info("→ Dia sem ação (%s). Exit 0.", today.strftime("%A"))
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ECOM-RESEARCH — pipeline determinístico de pesquisa")
    parser.add_argument("keyword", nargs="?", help="Keyword a pesquisar")
    parser.add_argument("--all", action="store_true", help="Pesquisar keywords de config/keywords.txt")
    parser.add_argument("--sunday", action="store_true", help="Deep run de domingo")
    parser.add_argument("--wednesday", action="store_true", help="Pulse de quarta: um refresh Apify por keyword")
    parser.add_argument("--monthly", action="store_true", help="Re-score mensal: uma call Trends por keyword")
    parser.add_argument("--auto", action="store_true", help="Despachar dia 1, domingo ou quarta")
    parser.add_argument("--csv", help="Caminho para CSV do Keyword Planner")
    parser.add_argument("--digest", action="store_true", help="Enviar digest para Telegram, com fallback stdout")
    return parser


def _mode_consumes_csv(mode: str, today: date | None = None) -> bool:
    """Whether the resolved mode reads the Keyword Planner CSV."""
    if mode in ("all", "sunday", "keyword"):
        return True
    if mode == "auto":
        today = today or date.today()
        return today.weekday() == 6  # auto só consome CSV quando dispara o domingo
    return False


def resolve_mode(args: argparse.Namespace, today: date | None = None) -> dict[str, Any]:
    """Resolve the effective mode with the documented precedence.

    Precedence (unchanged): auto > sunday > wednesday > monthly > all > keyword.
    Shadowed selectors/keywords and an ignored CSV produce deterministic
    stderr warnings, never hard errors.  A supplied CSV that does not exist is
    a hard error only when the resolved mode actually consumes CSV.
    """
    today = today or date.today()
    warnings: list[str] = []

    if args.auto:
        mode = "auto"
    elif args.sunday:
        mode = "sunday"
    elif args.wednesday:
        mode = "wednesday"
    elif args.monthly:
        mode = "monthly"
    elif args.all:
        mode = "all"
    elif args.keyword:
        mode = "keyword"
    else:
        mode = "none"

    shadowed: list[str] = []
    if args.auto:
        shadowed = [flag for flag in ("sunday", "wednesday", "monthly", "all") if getattr(args, flag)]
        if args.keyword:
            shadowed.append("keyword")
    elif args.sunday:
        shadowed = [flag for flag in ("wednesday", "monthly", "all") if getattr(args, flag)]
        if args.keyword:
            shadowed.append("keyword")
    elif args.wednesday:
        shadowed = [flag for flag in ("monthly", "all") if getattr(args, flag)]
        if args.keyword:
            shadowed.append("keyword")
    elif args.monthly:
        shadowed = [flag for flag in ("all",) if getattr(args, flag)]
        if args.keyword:
            shadowed.append("keyword")
    elif args.all and args.keyword:
        shadowed = ["keyword"]
    if shadowed:
        warnings.append(f"Aviso: --{mode} sobrepõe-se a --{' --'.join(shadowed)}; estes são ignorados.")

    csv_fatal: str | None = None
    if args.csv:
        if _mode_consumes_csv(mode, today):
            if not os.path.isfile(args.csv):
                csv_fatal = f"CSV não encontrado: {args.csv}"
        else:
            warnings.append(f"Aviso: --csv ignorado no modo {mode} (não consome CSV).")

    return {"mode": mode, "keyword": args.keyword, "warnings": warnings, "csv_fatal": csv_fatal}


def _run_keyword_action(keyword: str, csv_path: str | None = None, send: bool = False) -> None:
    """Single-keyword action with the same idempotency contract as the other modes."""
    run_id = _compute_run_id("keyword", [keyword], csv_path)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            results = _reuse_run(manifest, send=send)
            result = results[0] if results else {}
            score_result = result.get("score_result", {}) or {}
            print(f"\n=== {keyword} ===")
            print(f"Score: {score_result.get('score', 0)}/100 ({score_result.get('label', 'DADOS INSUFICIENTES')})")
            print(f"Pack: {result.get('md_path', 'reutilizado do run anterior')}")
            return
        manifest = _begin_manifest(run_id, "keyword", [keyword], csv_path)
        store.save(run_id, manifest)
        try:
            result = run_keyword(keyword, csv_path=csv_path, mode="keyword", run_id=run_id)
            score_result = result["score_result"]
            digest_line = pack.generate_digest_line(
                keyword, score_result, result.get("ads_data", {}), trends_data=result.get("trends_data", {})
            )
            print(f"\n=== {keyword} ===")
            print(f"Score: {score_result.get('score', 0)}/100 ({score_result.get('label', 'DADOS INSUFICIENTES')})")
            print(f"Pack: {result['md_path']}")
            if send:
                delivery = _deliver_digest(digest_line, run_id, send=True)
            else:
                delivery = {"status": "skipped", "chunks_total": 0, "chunks_sent": 0, "errors": []}
            store.save(run_id, _finish_manifest(manifest, [result], digest_line, delivery))
        except Exception:
            store.save(run_id, {**manifest, "state": "failed"})
            raise


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    resolved = resolve_mode(args)
    for warning in resolved["warnings"]:
        print(warning, file=sys.stderr)
    if resolved["csv_fatal"]:
        parser.error(resolved["csv_fatal"])
    mode = resolved["mode"]

    if mode == "auto":
        _auto_dispatch(send=args.digest, csv_path=args.csv)
        return
    if mode == "sunday":
        _run_sunday(csv_path=args.csv, send=args.digest)
        return
    if mode == "wednesday":
        _run_wednesday(send=args.digest)
        return
    if mode == "monthly":
        _run_monthly(send=args.digest)
        return
    if mode == "all":
        keywords = _load_keywords()
        if not keywords:
            parser.error("Zero keywords em config/keywords.txt")
        _run_all(keywords, csv_path=args.csv, send=args.digest)
        return
    if mode == "none":
        parser.print_help()
        return
    _run_keyword_action(args.keyword, csv_path=args.csv, send=args.digest)


if __name__ == "__main__":
    main()
