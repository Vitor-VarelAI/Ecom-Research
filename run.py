#!/usr/bin/env python3
"""Operational deterministic CLI for the Ecom Research pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import unicodedata
from dataclasses import replace
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
from pipeline.adapters.url_sanitize import sanitize_ads_data
from pipeline.delivery import telegram
from pipeline.markets import (
    MarketRegistry,
    MarketRegistryError,
    MarketTarget,
    MarketTargetNotFoundError,
    load_market_registry,
)
from pipeline.matching import product_ads
from pipeline.notifications import NotificationBatch, build_batch, notification_snapshot
from pipeline.output import pack
from pipeline.products import (
    Product,
    ProductNotFoundError,
    ProductRegistry,
    ProductRegistryError,
    load_registry,
)
from pipeline.scoring import score
from pipeline.suggestions import extract_keyword_suggestions, filter_relevant_related_queries

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("run")


# ── Run identity, state and atomic persistence ─────────────────────────────


def _data_dir() -> str:
    return os.path.join(REPO_ROOT, "data")


def _products_path() -> str:
    return os.path.join(REPO_ROOT, "config", "products.json")


def _load_products() -> ProductRegistry:
    """Load the canonical product registry (``config/products.json``), fail-closed.

    The registry is the only active product source; ``config/keywords.txt``
    is a historical bootstrap input and is never read by the runtime.
    """
    return load_registry(_products_path())


def _markets_path() -> str:
    return os.path.join(REPO_ROOT, "config", "markets.json")


def _load_markets() -> MarketRegistry:
    return load_market_registry(_markets_path())


def _run_store() -> run_state.RunStore:
    return run_state.RunStore(_data_dir())


def _digests_dir() -> str:
    return os.path.join(_data_dir(), "digests")


def _csv_hash(
    csv_path: str | None = None,
    products: list[Product | MarketTarget] | None = None,
) -> str | None:
    """Fingerprint every CSV + sidecar selected by this logical action."""
    if csv_path is not None:
        if not os.path.isfile(csv_path):
            return None
        entries = [{
            "path": os.path.basename(csv_path),
            "csv_sha256": run_state.file_sha256(csv_path),
            "metadata_sha256": run_state.file_sha256(f"{csv_path}.meta.json"),
        }]
    else:
        entries = []
        for product in products or []:
            market = product.market if isinstance(product, MarketTarget) else None
            path = _find_latest_csv(market)
            entries.append({
                "product_id": product.product_id,
                "market_id": getattr(product, "market_id", "PT"),
                "path": os.path.basename(path) if path else None,
                "csv_sha256": run_state.file_sha256(path) if path else None,
                "metadata_sha256": run_state.file_sha256(f"{path}.meta.json") if path else None,
            })
        if not entries:
            path = _find_latest_csv()
            if not path:
                return None
            entries = [{
                "path": os.path.basename(path),
                "csv_sha256": run_state.file_sha256(path),
                "metadata_sha256": run_state.file_sha256(f"{path}.meta.json"),
            }]
    return run_state.sha256_hex(json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


# Modes whose action consumes the Keyword Planner CSV.  Wednesday and monthly
# never read the CSV, so their run identity must stay stable when unrelated
# CSV files change.
CSV_CONSUMING_MODES = frozenset({"keyword", "all", "sunday"})


def _compute_run_id(
    mode: str,
    products: list[Product | MarketTarget],
    csv_path: str | None = None,
) -> str:
    """Deterministic identity: date + mode + ordered target descriptors + CSV hash + scoring version + matcher version.

    Each descriptor is the canonical ``(product_id, market_id, research_keyword)`` tuple:
    changing a target's local research query changes the identity (creating a new
    run), while a product name-only or alias-only change does not.  The CSV
    hash is included only when the action actually consumes the CSV
    (``keyword``, ``all`` and ``sunday`` — including Sunday dispatched via
    ``--auto``, which executes the same ``sunday`` action).  Wednesday/monthly
    identity stays stable when unrelated CSV files change.  A direct
    ``--sunday`` and ``--auto`` on a Sunday resolve to the same run id.
    """
    csv_sha = _csv_hash(csv_path, products) if mode in CSV_CONSUMING_MODES else None
    return run_state.compute_run_id(
        logical_date=date.today().isoformat(),
        mode=mode,
        products=[
            {
                "product_id": product.product_id,
                "market_id": getattr(product, "market_id", "PT"),
                "research_keyword": product.research_keyword,
            }
            for product in products
        ],
        csv_sha256=csv_sha,
        scoring_version=score.SCORING_VERSION,
        matcher_version=product_ads.MATCHER_VERSION,
    )


def _target_key(target: Product | MarketTarget | dict[str, Any]) -> tuple[str, str]:
    if isinstance(target, dict):
        return str(target.get("product_id", "")), str(target.get("market_id") or "PT")
    return target.product_id, getattr(target, "market_id", "PT")


def _begin_manifest(
    run_id: str,
    mode: str,
    products: list[Product | MarketTarget],
    csv_path: str | None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "state": "running",
        "mode": mode,
        "logical_date": date.today().isoformat(),
        "products": [
            {
                "product_id": product.product_id,
                "market_id": getattr(product, "market_id", "PT"),
                "name": product.name,
                "research_keyword": product.research_keyword,
            }
            for product in products
        ],
        "keywords": [product.research_keyword for product in products],
        "csv_sha256": _csv_hash(csv_path) if mode in CSV_CONSUMING_MODES else None,
        "scoring_version": score.SCORING_VERSION,
        "matcher_version": product_ads.MATCHER_VERSION,
        "matching_schema_version": product_ads.MATCHING_SCHEMA_VERSION,
        "created_at": run_state.utc_now_iso(),
        "completed_at": None,
        "results": [],
        "digest": {
            "text": "",
            "hash": None,
            "delivery": {"status": "pending", "schema_version": 2, "items_total": 0, "items_sent": 0, "errors": []},
        },
        "artifacts": {"md": [], "json": []},
    }


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    keep = ("keyword", "product_id", "market_id", "market", "product_name",
            "research_keyword", "md_path", "score_result", "preserved", "error",
            "status", "changes", "advertiser_deltas", "ad_cohort", "market_signals",
            "suggested_keywords")
    compact = {key: result.get(key) for key in keep}
    # Minimal credential-free, URL-free notification snapshot so a completed
    # run can rebuild its exact batch for a later --digest without rerunning
    # any source (see pipeline.notifications.builder.notification_snapshot).
    compact["notification_snapshot"] = notification_snapshot(result)
    return compact


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


def _noop_delivery(status: str, items_total: int) -> dict[str, Any]:
    """v2-shaped report for runs that never call the network."""
    return {
        "status": status,
        "schema_version": 2,
        "items_total": items_total,
        "items_sent": 0,
        "errors": [],
    }


def deliver_notification_batch(batch: NotificationBatch, *, send: bool) -> dict[str, Any]:
    """Print the full digest exactly once, then call the network only when
    ``send`` is true.  Delivery failures never print a second copy: the
    stdout trail, the v2 delivery state and the manifest share the same
    audit text (``batch.audit_text``)."""
    print(batch.audit_text)
    if not send:
        return _noop_delivery("skipped", len(batch.items))
    return telegram.deliver_batch(batch, state_id=batch.run_id, state_dir=_digests_dir())


def _rebuild_batch(manifest: dict[str, Any]) -> NotificationBatch:
    """Reconstruct the exact deterministic batch from persisted compact
    results (safe notification snapshots) — zero source calls.  The batch
    audit text is forced to the persisted digest so historical manifests
    without a snapshot still degrade safely to the original trail."""
    logical_date = manifest.get("logical_date") or date.today().isoformat()
    try:
        run_date = date.fromisoformat(logical_date)
    except ValueError:
        run_date = date.today()
    batch = build_batch(
        mode=manifest["mode"],
        run_id=manifest["run_id"],
        results=list(manifest.get("results") or []),
        run_date=run_date,
    )
    digest = (manifest.get("digest") or {}).get("text", "")
    if digest:
        batch = replace(batch, audit_text=digest)
    return batch


def _reuse_run(manifest: dict[str, Any], send: bool) -> list[dict[str, Any]]:
    """Reuse a completed run: persisted digest/artifacts, zero adapter calls.

    If ``send`` is true and the persisted delivery is not already successful,
    the exact deterministic batch is rebuilt from the persisted sanitized
    result data (no paid sources) and delivered; a successful delivery is
    never re-sent and no message call happens."""
    digest = (manifest.get("digest") or {}).get("text", "")
    delivery = (manifest.get("digest") or {}).get("delivery") or {}
    if send and delivery.get("status") != "success":
        batch = _rebuild_batch(manifest)
        report = deliver_notification_batch(batch, send=True)
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


def _safe_run_keyword(product: Product | MarketTarget, csv_path: str | None = None, mode: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Per-target exception containment shared by --all, --sunday and --auto.

    Adapters still receive only ``product.research_keyword``.  Technical
    exceptions are marked ``status="technical_fatal"`` so the batch
    checkpoint can tell them apart from expected source failures (an Apify
    failure still produces a DADOS INSUFICIENTES pack) and retry only the
    fatal products on the next invocation.
    """
    try:
        result = run_keyword(product, csv_path=csv_path, mode=mode, run_id=run_id)
        result["status"] = "ok"
        return result
    except Exception as exc:
        error_type = type(exc).__name__
        logger.error("Erro fatal sanitizado em product_id=%s (tipo=%s)", product.product_id, error_type)
        market = product.market if isinstance(product, MarketTarget) else None
        return {
            "product_id": product.product_id,
            **({"market_id": market.market_id, "market": market.as_dict()} if market else {}),
            "product_name": product.name,
            "research_keyword": product.research_keyword,
            "keyword": product.research_keyword,
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
        "matcher_version": product_ads.MATCHER_VERSION,
        "matching_schema_version": product_ads.MATCHING_SCHEMA_VERSION,
    }


def _find_latest_csv(market: Any | None = None) -> str | None:
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    files = sorted(
        (name for name in os.listdir(data_dir) if name.endswith(".csv")),
        reverse=True,
    )
    for filename in files:
        path = os.path.join(data_dir, filename)
        if market is None or keyword_csv._market_metadata(path, market) is not None:
            return path
    return None


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


def _pack_product_id(data: dict[str, Any], registry: ProductRegistry) -> str | None:
    """Canonical product identity of a pack: the explicit ``product_id`` when
    present, else the read-only legacy resolution of its ``keyword``.  A pack
    whose identity cannot be established (orphaned legacy keyword, unknown
    product_id) is ``None`` and is ignored by every selector — never crashes,
    never evicts evidence."""
    product_id = data.get("product_id")
    if isinstance(product_id, str) and product_id:
        return product_id
    keyword = data.get("keyword", "")
    if not keyword:
        return None
    try:
        return registry.resolve(keyword).product_id
    except ProductNotFoundError:
        return None


def _find_packs_by_label(
    labels: list[str],
    max_age_days: int | None = None,
    registry: ProductRegistry | None = None,
    markets: MarketRegistry | None = None,
) -> list[Product | MarketTarget]:
    """Watchlist selection grouped by canonical product identity.

    Every complete pack maps to the product_id of its canonical product
    (``product_id`` when present, else the legacy ``keyword`` resolved through
    the registry), so a renamed product — old keyword now an alias — keeps its
    slot and its previous-pack continuity.  Orphan packs (legacy keyword that
    resolves to nothing) are skipped.  All existing eviction semantics hold per
    product: newest complete pack wins, DADOS INSUFICIENTES never evicts older
    labeled evidence, cutoff by date, malformed packs ignored.
    """
    registry = registry or _load_products()
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return []
    cutoff = date.today() - timedelta(days=max_age_days) if max_age_days is not None else None
    labeled: dict[Any, tuple[tuple[str, str, str], str]] = {}
    overall: dict[Any, tuple[tuple[str, str, str], str]] = {}
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
        if not data.get("keyword", "") or (cutoff is not None and pack_date < cutoff):
            continue
        product_id = _pack_product_id(data, registry)
        if product_id is None:
            continue  # orphaned: no product identity to group under
        if markets is None:
            target_key: Any = product_id
        else:
            market_id = data.get("market_id")
            if not isinstance(market_id, str):
                continue  # packs sem scope são históricos, não evidência local
            target_key = (product_id, market_id)
        key = _pack_sort_key(data, filename)
        overall[target_key] = max(overall.get(target_key, (("", "", ""), "")), (key, filename))
        label = data.get("score", {}).get("label", "")
        if label in labels:
            labeled[target_key] = max(labeled.get(target_key, (("", "", ""), "")), (key, filename))

    selected: set[Any] = set()
    for target_key in labeled:
        overall_key, overall_file = overall[target_key]
        overall_data = _read_pack_json(os.path.join(data_dir, overall_file)) or {}
        if not _pack_is_complete(overall_data):
            continue
        overall_label = overall_data.get("score", {}).get("label", "")
        if overall_label in labels:
            selected.add(target_key)  # the newest pack carries a requested label
        elif overall_label == "DADOS INSUFICIENTES":
            # A failed run must not evict real evidence: keep the older labeled pack.
            selected.add(target_key)
        # else: newest pack carries another real label (e.g. AVANÇAR) — follows it.
    if markets is not None:
        return [
            target for target in markets.targets_for(registry.products)
            if _target_key(target) in selected
        ]
    # Registry order, never keyword sort order; unknown product_ids drop out.
    return [product for product in registry.products if product.product_id in selected]


def _load_previous_pack(
    product: Product | MarketTarget,
    registry: ProductRegistry | None = None,
) -> dict[str, Any] | None:
    """Newest complete non-failed pack of the product's canonical identity.

    Matches by product_id: either the pack carries ``product_id`` equal to
    ``product.product_id``, or its legacy ``keyword`` resolves through the
    registry to that same product.  DADOS INSUFICIENTES packs are not evidence
    for the next incremental mode.  Returns the pack JSON unchanged — legacy
    packs are read, never rewritten.
    """
    registry = registry or _load_products()
    data_dir = os.path.join(REPO_ROOT, "data")
    if not os.path.isdir(data_dir):
        return None
    best: tuple[tuple[str, str, str], str] | None = None
    for filename in os.listdir(data_dir):
        if not filename.endswith(".json") or filename.startswith("quota"):
            continue
        data = _read_pack_json(os.path.join(data_dir, filename))
        if data is None or not _pack_is_complete(data):
            continue
        if _pack_product_id(data, registry) != product.product_id:
            continue
        if isinstance(product, MarketTarget) and (
            data.get("market_id") != product.market_id
            or data.get("research_keyword") != product.research_keyword
        ):
            continue
        if (data.get("score", {}).get("label", "")) == "DADOS INSUFICIENTES":
            continue  # failed-run packs are not evidence for the next incremental mode
        key = _pack_sort_key(data, filename)
        if best is None or key > best[0]:
            best = (key, filename)
    if best is None:
        return None
    return _read_pack_json(os.path.join(data_dir, best[1]))


def _csv_data(
    csv_path: str | None,
    keyword: str,
    market: Any | None = None,
) -> dict[str, Any]:
    """Parse do export do Keyword Planner seleccionando a linha da keyword
    pedida (matching exacto normalizado); erro explícito se a keyword não
    existir no CSV — nunca as métricas de outra linha."""
    path = csv_path or _find_latest_csv(market)
    if path:
        return keyword_csv.parse(path, keyword=keyword, market=market)
    return {"error": "Nenhum CSV encontrado em data/ — exporta do Google Keyword Planner e coloca em data/"}


def _incomplete_score(
    trends_data: dict[str, Any],
    csv_data: dict[str, Any],
    ads_data: dict[str, Any],
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """Keep the unchanged numeric formula while preventing a false verdict."""
    result = score.compute(trends_data, csv_data, ads_data)
    result["label"] = "DADOS INSUFICIENTES"
    result["reason"] = reason or (
        "Facebook Ad Library via Apify indisponível; repetir quando a fonte estiver disponível"
    )
    result["incomplete"] = True
    return result


def _market_signal_labels(
    trends_data: dict[str, Any],
    ads_data: dict[str, Any],
    cohort: dict[str, Any] | None = None,
) -> list[str]:
    def number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    advertisers = [
        advertiser for advertiser in ads_data.get("advertisers", [])
        if isinstance(advertiser, dict)
    ]
    labels: list[str] = []
    slope_30d = number(trends_data.get("slope_30d_pct"))
    slope_90d = number(trends_data.get("slope_90d_pct"))
    related = trends_data.get("related_queries")
    if (
        cohort is not None
        and slope_30d is not None and slope_30d >= 20
        and slope_90d is not None and slope_90d >= 0
        and isinstance(related, list) and bool(related)
        and len(advertisers) >= 3
    ):
        labels.append("EMERGENTE")

    retention = number(cohort.get("ad_retention_pct")) if cohort else None
    oldest = [
        number(advertiser.get("oldest_ad_days")) for advertiser in advertisers
    ]
    if (retention is not None and retention >= 70) or any(
        days is not None and days >= 30 for days in oldest
    ):
        labels.append("DURÁVEL")
    return labels


def _market_scope_errors(
    market: Any,
    ads_data: dict[str, Any],
    trends_data: dict[str, Any],
    csv_data: dict[str, Any],
    seo_data: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if "error" not in ads_data and (
        ads_data.get("evidence_scope") != "market"
        or ads_data.get("query_country") != market.ads_country
    ):
        errors.append("Ads sem scope local comprovado")
    scored_market = "error" not in ads_data and bool(ads_data.get("total_ads", 0))
    if "error" in trends_data:
        if scored_market:
            errors.append("Trends indisponível para decisão local")
    elif (
        trends_data.get("market_id") != market.market_id
        or trends_data.get("query_geo") != market.trends_geo
    ):
        errors.append("Trends sem geo local comprovado")
    if "error" in csv_data or (
        csv_data.get("market_id") != market.market_id
        or csv_data.get("currency") != market.currency
    ):
        errors.append("Keyword Planner sem mercado/moeda comprovados")
    if "error" in seo_data:
        if scored_market:
            errors.append("SERP indisponível para decisão local")
    elif (
        seo_data.get("market_id") != market.market_id
        or seo_data.get("query_gl") != market.serp_gl
        or seo_data.get("query_hl") != market.serp_hl
        or seo_data.get("query_location") != market.serp_location
    ):
        errors.append("SERP sem localização local comprovada")
    return errors


def _forced_zero_score(reason: str | None = None) -> dict[str, Any]:
    return {
        "score": 0,
        "label": "DESCARTAR",
        "breakdown": {
            "trend": 0.0,
            "volume": 0.0,
            "ad_longevity": 0.0,
            "saturacao_invertida": 0.0,
        },
        "reason": reason or "zero ads ativos após filtragem determinística",
    }


_ZERO_CANDIDATES_REASON = "zero candidatos canónicos ativos após filtragem determinística"
_ZERO_MATCHED_REJECTED_REASON = "zero matched (todos os candidatos rejeitados)"
_NEEDS_REVIEW_REASON = "zero matched; candidatos ambíguos (needs_review) — revisão manual necessária"


def run_keyword(
    product: Product | MarketTarget,
    csv_path: str | None = None,
    skip_serpapi: bool = False,
    mode: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run one full product pipeline with exactly one Apify invocation.

    Adapters receive only ``product.research_keyword``; the result carries
    the canonical identity (``product_id``/``product_name``/
    ``research_keyword``) plus ``keyword`` = the current research query.
    ``mode``/``run_id`` are additive provenance passed through to the pack.

    Every successful Apify result is classified by the matcher immediately:
    ``total_ads`` is the matched count, and the zero-match policy is
    applied here (zero candidates => DESCARTAR; zero matched with any
    needs_review => DADOS INSUFICIENTES; zero matched with all rejected =>
    DESCARTAR).
    """
    keyword = product.research_keyword
    market = product.market if isinstance(product, MarketTarget) else None

    def csv_data_for_market() -> dict[str, Any]:
        if market is None:
            return _csv_data(csv_path, keyword)
        return _csv_data(csv_path, keyword, market)

    logger.info("=== INÍCIO: product_id=%s keyword=%r ===", product.product_id, keyword)
    ads_data = apify_ads.fetch(keyword, market=market) if market is not None else apify_ads.fetch(keyword)
    apify_failed = "error" in ads_data
    if not apify_failed:
        ads_data = product_ads.classify_ads_data(keyword, ads_data)
    if apify_failed:
        logger.warning("Apify Ads: %s", ads_data["error"])
    else:
        logger.info(
            "Apify Ads: candidatos=%d matched=%d needs_review=%d rejected=%d saturação=%s",
            ads_data.get("candidate_count", 0),
            ads_data.get("matched_count", 0),
            ads_data.get("needs_review_count", 0),
            ads_data.get("rejected_count", 0),
            ads_data.get("saturation"),
        )

    # A source failure must continue the paid sources, but cannot create a
    # negative market decision.  A valid empty result takes the deterministic
    # zero-candidates branch and spends one related-query call for
    # alternatives.  Candidates that exist but never match follow the
    # zero-match policy: any needs_review keeps the decision open
    # (DADOS INSUFICIENTES); every candidate rejected is a real negative
    # signal (DESCARTAR).
    #
    # ``candidate_count`` only exists on new-format results; legacy-shaped
    # passthrough data (test fixtures, already-finalized dicts) falls back
    # to ``total_ads`` as the zero signal, preserving the historical
    # zero-ads semantics.
    if apify_failed:
        landing_data = {"error": "sem dados: Apify indisponível; landing não consultada"}
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            seo_data = {"error": "SEO não executado — modo sem rede"}
        else:
            trends_data = trends.fetch(keyword, market=market) if market is not None else trends.fetch(keyword)
            seo_data = serp_seo.fetch(keyword, market=market) if market is not None else serp_seo.fetch(keyword)
        csv_data = csv_data_for_market()
        score_result = _incomplete_score(trends_data, csv_data, ads_data)
    elif ads_data.get("candidate_count", 0) == 0 and ads_data.get("total_ads", 0) == 0:
        landing_data = {"error": "sem dados: zero candidatos; landing não consultada"}
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
        else:
            trends_data = trends.fetch_related(keyword, market=market) if market is not None else trends.fetch_related(keyword)
        seo_data = {"error": "SEO não executado: zero candidatos ativos"}
        csv_data = csv_data_for_market()
        score_result = _forced_zero_score(_ZERO_CANDIDATES_REASON)
    elif ads_data.get("total_ads", 0) == 0:
        if ads_data.get("needs_review_count", 0) > 0:
            # Ambiguous candidates: no negative decision, keep the gate open.
            landing_data = {"error": "sem dados: zero matched; landing não consultada"}
            if skip_serpapi:
                trends_data = {"error": "SerpAPI não executada — modo sem rede"}
                seo_data = {"error": "SEO não executado — modo sem rede"}
            else:
                trends_data = trends.fetch(keyword, market=market) if market is not None else trends.fetch(keyword)
                seo_data = serp_seo.fetch(keyword, market=market) if market is not None else serp_seo.fetch(keyword)
            csv_data = csv_data_for_market()
            score_result = _incomplete_score(trends_data, csv_data, ads_data, reason=_NEEDS_REVIEW_REASON)
        else:
            landing_data = {"error": "sem dados: zero matched (todos rejeitados); landing não consultada"}
            if skip_serpapi:
                trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            else:
                trends_data = trends.fetch_related(keyword, market=market) if market is not None else trends.fetch_related(keyword)
            seo_data = {"error": "SEO não executado: zero matched"}
            csv_data = csv_data_for_market()
            score_result = _forced_zero_score(_ZERO_MATCHED_REJECTED_REASON)
    else:
        landing_data = landing_price.fetch_prices_from_ads(ads_data)
        if skip_serpapi:
            trends_data = {"error": "SerpAPI não executada — modo sem rede"}
            seo_data = {"error": "SEO não executado — modo sem rede"}
        else:
            trends_data = trends.fetch(keyword, market=market) if market is not None else trends.fetch(keyword)
            seo_data = serp_seo.fetch(keyword, market=market) if market is not None else serp_seo.fetch(keyword)
        csv_data = csv_data_for_market()
        score_result = score.compute(trends_data, csv_data, ads_data)

    if market is not None:
        scope_errors = _market_scope_errors(
            market, ads_data, trends_data, csv_data, seo_data
        )
        if scope_errors:
            score_result = _incomplete_score(
                trends_data,
                csv_data,
                ads_data,
                reason="proveniência geográfica inválida: " + "; ".join(scope_errors),
            )

    # Product-relevance gate: apply the filter once over a *copy* of
    # trends_data before both suggestion extraction and pack rendering
    # (full / zero-ads / related-only branches converge here), so raw related
    # queries sharing only a geographic token can never surface in
    # JSON/Markdown/digest.  The trends cache object is never mutated.
    trends_data = filter_relevant_related_queries(keyword, trends_data)
    suggested = extract_keyword_suggestions(keyword, trends_data)

    # Landing URL privacy: the raw landing URL (userinfo/query/fragment) was
    # consumed above exclusively by ``fetch_prices_from_ads`` for the actual
    # fetch.  From here on — result dict, pack JSON/Markdown, manifests and
    # digests — ads_data is replaced by a deep copy whose ``landing_url`` in
    # both ``top_ads`` and ``candidates`` is sanitized to scheme + host +
    # safe port + path.  The raw object is never mutated and never
    # serialized.
    ads_data = sanitize_ads_data(ads_data)
    result = {
        "product_id": product.product_id,
        "product_name": product.name,
        "research_keyword": keyword,
        "keyword": keyword,
        "score_result": score_result,
        "trends_data": trends_data,
        "csv_data": csv_data,
        "ads_data": ads_data,
        "seo_data": seo_data,
        "landing_price_data": landing_data,
        "suggested_keywords": suggested,
    }
    market_scope = market.as_dict() if market is not None else None
    market_signals = _market_signal_labels(trends_data, ads_data) if market is not None else []
    if market_scope is not None:
        result["market_id"] = market.market_id
        result["market"] = market_scope
        result["market_signals"] = market_signals
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
        product_id=product.product_id,
        product_name=product.name,
        research_keyword=keyword,
        market=market_scope,
        market_signals=market_signals if market is not None else None,
        **_run_metadata(run_mode, run_id or None, _source_states(run_mode, result, None)),
    )
    return result


def _run_batch(
    mode: str,
    products: list[Product | MarketTarget],
    csv_path: str | None,
    send: bool,
) -> list[dict[str, Any]]:
    """Shared Sunday/--all batch loop with per-product checkpoints.

    The manifest is saved atomically after every product result, so a crash
    never loses the work already done.  A rerun of a ``running``/``failed``/
    ``completed_with_errors`` manifest reuses the terminal results (zero paid
    calls for them) and retries only the technical-fatal or missing products;
    resume/done maps are keyed by ``product_id``, not by keyword.

    A batch containing technical fatals is saved as ``completed_with_errors``
    and its digest is *not* Telegram-delivered: the content would change after
    the retry, and the operator would receive two different digests.  Instead
    the digest is printed to stdout with ``delivery.status == "deferred"`` and
    persisted in the manifest, and the final digest is delivered exactly once
    on the first run where every product reaches an intentional terminal
    outcome.  Normal source errors that already produced a pack remain
    terminal and digestable.  Digest order follows the given product order
    (registry order for --all/--sunday).
    """
    run_id = _compute_run_id(mode, products, csv_path)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            return _reuse_run(manifest, send=send)

        target_keys = {_target_key(product) for product in products}
        if manifest is None:
            results: list[dict[str, Any]] = []
            pending: list[Product | MarketTarget] = list(products)
        else:
            results = [
                r for r in (manifest.get("results") or [])
                if isinstance(r, dict) and _target_key(r) in target_keys
                and not _is_technical_fatal(r)
            ]
            done = {_target_key(result) for result in results}
            pending = [product for product in products if _target_key(product) not in done]
            if pending:
                logger.info("run_state: retoma %s — %d produto(s) reutilizado(s), %d a repetir",
                            mode, len(results), len(pending))

        manifest = _begin_manifest(run_id, mode, products, csv_path)
        manifest["results"] = results
        store.save(run_id, manifest)
        logger.info("=== %s: deep run de %d produtos ===", mode.upper(), len(products))
        try:
            for product in pending:
                result = _safe_run_keyword(product, csv_path=csv_path, mode=mode, run_id=run_id)
                results.append(result)
                manifest["results"] = [dict(r) for r in results]  # checkpoint
                store.save(run_id, manifest)

            # Deterministic digest order even after a resume appended retries.
            by_target = {_target_key(result): result for result in results}
            results = [
                by_target[_target_key(product)]
                for product in products
                if _target_key(product) in by_target
            ]

            batch = build_batch(mode=mode, run_id=run_id, results=results, run_date=date.today())
            digest = batch.audit_text
            if any(_is_technical_fatal(r) for r in results):
                # Deferred: never Telegram-deliver a technical-partial digest.
                delivery = _noop_delivery("deferred", len(batch.items))
                print(digest)
                store.save(run_id, _finish_manifest(manifest, results, digest, delivery,
                                                    state="completed_with_errors", compact=False))
            else:
                delivery = deliver_notification_batch(batch, send=send)
                store.save(run_id, _finish_manifest(manifest, results, digest, delivery))
        except BaseException:
            store.save(run_id, {**manifest, "state": "failed"})
            raise
    return results


def _run_sunday(
    csv_path: str | None = None,
    send: bool = True,
    registry: ProductRegistry | None = None,
    markets: MarketRegistry | None = None,
) -> list[dict[str, Any]]:
    registry = registry or _load_products()
    if markets is None:
        products = registry.active_products()
        if not products:
            logger.info("Zero produtos ativos em config/products.json")
            return []
        return _run_batch("sunday", list(products), csv_path, send)
    targets = markets.targets_for(registry.active_products())
    if not targets:
        logger.info("Zero targets ativos em config/products.json + config/markets.json")
        return []
    return _run_batch("sunday", list(targets), csv_path, send)


def _run_wednesday(
    send: bool = True,
    registry: ProductRegistry | None = None,
    markets: MarketRegistry | None = None,
) -> list[dict[str, Any]]:
    registry = registry or _load_products()
    products = _find_packs_by_label(
        ["AVANÇAR", "OBSERVAR"],
        max_age_days=14,
        registry=registry,
        markets=markets,
    )
    if not products:
        logger.info("Zero packs AVANÇAR/OBSERVAR com <14 dias. Nada a pulsar.")
        return []
    run_id = _compute_run_id("wednesday", products, None)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            return _reuse_run(manifest, send=send)
        manifest = _begin_manifest(run_id, "wednesday", products, None)
        store.save(run_id, manifest)
        logger.info("=== QUARTA: pulse Apify em %d targets ===", len(products))
        results: list[dict[str, Any]] = []
        try:
            for product in products:
                keyword = product.research_keyword
                market = product.market if isinstance(product, MarketTarget) else None
                previous = _load_previous_pack(product, registry=registry)
                if not previous:
                    continue
                previous_ads = previous.get("facebook_ads", {})
                if not isinstance(previous_ads, dict) or previous_ads.get("matching_schema_version") != product_ads.MATCHING_SCHEMA_VERSION:
                    # Fail-closed legacy guard: the baseline pack predates
                    # classified candidates, so comparing ad_ids/advertisers
                    # (matched-only semantics) against it would mix
                    # incompatible cohorts.  Skip the pulse, preserve the
                    # pack, never rewrite history.
                    reason = "pack anterior sem matching_schema_version; pulse não executado (coorte incompatível)"
                    logger.warning("Pulse %s: %s", keyword, reason)
                    results.append({
                        "product_id": product.product_id,
                        **({"market_id": market.market_id, "market": market.as_dict()} if market else {}),
                        "product_name": product.name,
                        "research_keyword": keyword,
                        "keyword": keyword,
                        "error": reason,
                        "ads_data": {"error": reason},
                        "preserved": True,
                    })
                    continue
                current_ads = (
                    apify_ads.fetch(keyword, market=market)
                    if market is not None
                    else apify_ads.fetch(keyword)
                )
                if "error" in current_ads:
                    logger.warning("Pulse %s: %s; pack anterior preservado", keyword, current_ads["error"])
                    results.append({
                        "product_id": product.product_id,
                        **({"market_id": market.market_id, "market": market.as_dict()} if market else {}),
                        "product_name": product.name,
                        "research_keyword": keyword,
                        "keyword": keyword,
                        "error": current_ads["error"],
                        "ads_data": current_ads,
                        "preserved": True,
                    })
                    continue

                if market is not None and (
                    current_ads.get("evidence_scope") != "market"
                    or current_ads.get("query_country") != market.ads_country
                ):
                    reason = "Apify devolveu Ads sem scope local comprovado; pack anterior preservado"
                    results.append({
                        "product_id": product.product_id,
                        "market_id": market.market_id,
                        "market": market.as_dict(),
                        "product_name": product.name,
                        "research_keyword": keyword,
                        "keyword": keyword,
                        "error": reason,
                        "ads_data": {"error": reason},
                        "preserved": True,
                    })
                    continue

                current_ads = product_ads.classify_ads_data(keyword, current_ads)
                # Derived-value privacy: both the compatible previous baseline
                # and the freshly classified current ads are sanitized *before*
                # the compare/cohort algebra, so page-name fallback advertiser
                # identities and the changes/advertiser_deltas/cohort they
                # produce can never carry raw embedded URL credentials/query/
                # fragments from a legacy or hostile page_name/advertiser.
                # Landing URL privacy holds too: only the actual fetch may use
                # the raw landing URL; what is persisted in the result/pack/
                # checkpoint is the sanitized copy (scheme + host + safe port
                # + path) — the sanitized current ads continue into result/pack.
                previous_ads = sanitize_ads_data(previous_ads)
                current_ads = sanitize_ads_data(current_ads)
                changes, advertiser_deltas = _compare_ads(previous_ads, current_ads)
                cohort = _ad_cohort(previous_ads, current_ads, baseline_date=previous.get("date"))
                trends_data = previous.get("trends", {})
                csv_data = previous.get("keyword_planner", {})
                seo_data = previous.get("seo", {})
                landing_data = previous.get("landing_price")
                score_result = previous.get("score", {})
                if market is not None:
                    scope_errors = _market_scope_errors(
                        market, current_ads, trends_data, csv_data, seo_data
                    )
                    if scope_errors:
                        score_result = _incomplete_score(
                            trends_data,
                            csv_data,
                            current_ads,
                            reason="proveniência geográfica inválida: " + "; ".join(scope_errors),
                        )
                suggested = previous.get("suggested_keywords", [])
                result = {
                    "product_id": product.product_id,
                    "product_name": product.name,
                    "research_keyword": keyword,
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
                market_scope = market.as_dict() if market is not None else None
                market_signals = (
                    _market_signal_labels(trends_data, current_ads, cohort)
                    if market is not None else []
                )
                if market_scope is not None:
                    result["market_id"] = market.market_id
                    result["market"] = market_scope
                    result["market_signals"] = market_signals
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
                    product_id=product.product_id,
                    product_name=product.name,
                    research_keyword=keyword,
                    market=market_scope,
                    market_signals=market_signals if market is not None else None,
                    ad_cohort=cohort,
                    **_run_metadata("wednesday", run_id, _source_states("wednesday", result, previous)),
                )
                results.append(result)
            batch = build_batch(mode="wednesday", run_id=run_id, results=results, run_date=date.today())
            digest = batch.audit_text
            delivery = deliver_notification_batch(batch, send=send)
            store.save(run_id, _finish_manifest(manifest, results, digest, delivery))
        except Exception:
            store.save(run_id, {**manifest, "state": "failed"})
            raise
    return results


def _run_monthly(
    send: bool = True,
    registry: ProductRegistry | None = None,
    markets: MarketRegistry | None = None,
) -> list[dict[str, Any]]:
    registry = registry or _load_products()
    products = _find_packs_by_label(
        ["OBSERVAR"], max_age_days=None, registry=registry, markets=markets
    )
    if not products:
        logger.info("Zero packs OBSERVAR na watchlist. Nada a re-score.")
        return []
    run_id = _compute_run_id("monthly", products, None)
    store = _run_store()
    with store.lock(run_id):
        manifest = store.load(run_id)
        if _manifest_completed(manifest):
            return _reuse_run(manifest, send=send)
        manifest = _begin_manifest(run_id, "monthly", products, None)
        store.save(run_id, manifest)
        logger.info("=== DIA 1: re-score watchlist de %d targets ===", len(products))
        results: list[dict[str, Any]] = []
        try:
            for product in products:
                keyword = product.research_keyword
                market = product.market if isinstance(product, MarketTarget) else None
                previous = _load_previous_pack(product, registry=registry)
                if not previous:
                    continue
                previous_ads = previous.get("facebook_ads", {})
                if not isinstance(previous_ads, dict) or previous_ads.get("matching_schema_version") != product_ads.MATCHING_SCHEMA_VERSION:
                    # Fail-closed legacy guard: re-scoring a pack produced
                    # under the old ads semantics (no classified candidates)
                    # with the new scoring cohort would reinterpret
                    # incompatible history.  Skip before spending the Trends
                    # call, preserve the pack, never rewrite it.
                    reason = "pack anterior sem matching_schema_version; re-score não executado (semântica de coorte incompatível)"
                    logger.warning("Re-score %s: %s", keyword, reason)
                    results.append({
                        "product_id": product.product_id,
                        **({"market_id": market.market_id, "market": market.as_dict()} if market else {}),
                        "product_name": product.name,
                        "research_keyword": keyword,
                        "keyword": keyword,
                        "error": reason,
                        "preserved": True,
                        "score_result": previous.get("score", {"score": 0, "label": "DADOS INSUFICIENTES"}),
                        "trends_data": {"error": reason},
                        # Fail-closed error-only shape (como no pulse de quarta):
                        # o ``facebook_ads`` histórico cru — que pode conter
                        # landing URLs não sanitizadas de packs legados — nunca
                        # entra no result/manifest/digest.
                        "ads_data": {"error": reason},
                    })
                    continue
                trends_data = (
                    trends.fetch(
                        keyword,
                        timeseries_only=True,
                        force_refresh=True,
                        market=market,
                    )
                    if market is not None
                    else trends.fetch(keyword, timeseries_only=True, force_refresh=True)
                )
                ads_data = previous_ads
                # Landing URL privacy: legacy packs may carry raw landing URLs;
                # what is re-persisted in the new pack is always sanitized
                # (candidates + top_ads).
                ads_data = sanitize_ads_data(ads_data)
                csv_data = previous.get("keyword_planner", {})
                seo_data = previous.get("seo", {})
                landing_data = previous.get("landing_price")
                suggested = previous.get("suggested_keywords", [])
                if "error" in trends_data:
                    # Preserve the previous pack: write no replacement pack, keep
                    # the OBSERVAR watchlist entry alive, report in digest/run state.
                    logger.warning("Re-score %s: %s; pack anterior preservado", keyword, trends_data["error"])
                    results.append({
                        "product_id": product.product_id,
                        **({"market_id": market.market_id, "market": market.as_dict()} if market else {}),
                        "product_name": product.name,
                        "research_keyword": keyword,
                        "keyword": keyword,
                        "error": trends_data["error"],
                        "preserved": True,
                        "score_result": previous.get("score", {"score": 0, "label": "DADOS INSUFICIENTES"}),
                        "trends_data": trends_data,
                        "ads_data": ads_data,
                    })
                    continue
                score_result = score.compute(trends_data, csv_data, ads_data)
                if market is not None:
                    scope_errors = _market_scope_errors(
                        market, ads_data, trends_data, csv_data, seo_data
                    )
                    if scope_errors:
                        score_result = _incomplete_score(
                            trends_data,
                            csv_data,
                            ads_data,
                            reason="proveniência geográfica inválida: " + "; ".join(scope_errors),
                        )
                result = {
                    "product_id": product.product_id,
                    "product_name": product.name,
                    "research_keyword": keyword,
                    "keyword": keyword,
                    "score_result": score_result,
                    "trends_data": trends_data,
                    "ads_data": ads_data,
                    "csv_data": csv_data,
                    "seo_data": seo_data,
                    "landing_price_data": landing_data,
                    "suggested_keywords": suggested,
                }
                market_scope = market.as_dict() if market is not None else None
                market_signals = (
                    _market_signal_labels(trends_data, ads_data)
                    if market is not None else []
                )
                if market_scope is not None:
                    result["market_id"] = market.market_id
                    result["market"] = market_scope
                    result["market_signals"] = market_signals
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
                    product_id=product.product_id,
                    product_name=product.name,
                    research_keyword=keyword,
                    market=market_scope,
                    market_signals=market_signals if market is not None else None,
                    **_run_metadata("monthly", run_id, _source_states("monthly", result, previous)),
                )
                results.append(result)
            batch = build_batch(mode="monthly", run_id=run_id, results=results, run_date=date.today())
            digest = batch.audit_text
            delivery = deliver_notification_batch(batch, send=send)
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

def _run_all(products: list[Product | MarketTarget], csv_path: str | None = None, send: bool = True) -> list[dict[str, Any]]:
    """Full run over all active products, with per-product exception isolation."""
    if not products:
        return []
    return _run_batch("all", products, csv_path, send)


def _auto_dispatch(
    send: bool = True,
    csv_path: str | None = None,
    registry: ProductRegistry | None = None,
    markets: MarketRegistry | None = None,
) -> list[dict[str, Any]]:
    """Dispatch all actions for today; day 1 always runs before weekly pulse."""
    today = date.today()
    is_sunday = today.weekday() == 6
    is_wednesday = today.weekday() == 2
    results: list[dict[str, Any]] = []
    if today.day == 1:
        if registry is not None and markets is not None:
            results.extend(_run_monthly(send=send, registry=registry, markets=markets))
        else:
            results.extend(_run_monthly(send=send))
    if is_sunday:
        if registry is not None and markets is not None:
            results.extend(_run_sunday(
                send=send, csv_path=csv_path, registry=registry, markets=markets
            ))
        else:
            results.extend(_run_sunday(send=send, csv_path=csv_path))
    elif is_wednesday:
        if registry is not None and markets is not None:
            results.extend(_run_wednesday(send=send, registry=registry, markets=markets))
        else:
            results.extend(_run_wednesday(send=send))
    if not results and today.day != 1 and not is_sunday and not is_wednesday:
        logger.info("→ Dia sem ação (%s). Exit 0.", today.strftime("%A"))
    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ECOM-RESEARCH — pipeline determinístico de pesquisa")
    parser.add_argument("keyword", nargs="?", help="Produto a pesquisar (product_id, nome, research_keyword ou alias)")
    parser.add_argument("--all", action="store_true", help="Pesquisar produtos ativos de config/products.json")
    parser.add_argument("--sunday", action="store_true", help="Deep run de domingo (produtos ativos do registry)")
    parser.add_argument("--wednesday", action="store_true", help="Pulse de quarta: um refresh Apify por keyword")
    parser.add_argument("--monthly", action="store_true", help="Re-score mensal: uma call Trends por keyword")
    parser.add_argument("--auto", action="store_true", help="Despachar dia 1, domingo ou quarta")
    parser.add_argument("--csv", help="Caminho para CSV do Keyword Planner")
    parser.add_argument("--market", default="PT", help="Mercado explícito (default: PT)")
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


def _run_keyword_action(product: Product | MarketTarget, csv_path: str | None = None, send: bool = False) -> None:
    """Single-product action with the same idempotency contract as the other modes."""
    keyword = product.research_keyword
    run_id = _compute_run_id("keyword", [product], csv_path)
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
        manifest = _begin_manifest(run_id, "keyword", [product], csv_path)
        store.save(run_id, manifest)
        try:
            result = run_keyword(product, csv_path=csv_path, mode="keyword", run_id=run_id)
            score_result = result["score_result"]
            batch = build_batch(mode="keyword", run_id=run_id, results=[result], run_date=date.today())
            digest = batch.audit_text
            print(f"\n=== {keyword} ===")
            print(f"Score: {score_result.get('score', 0)}/100 ({score_result.get('label', 'DADOS INSUFICIENTES')})")
            print(f"Pack: {result['md_path']}")
            delivery = deliver_notification_batch(batch, send=send)
            store.save(run_id, _finish_manifest(manifest, [result], digest, delivery))
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

    # Fail-closed registry validation: every mode that touches products loads
    # config/products.json before any source/network call, so a missing or
    # invalid registry is a hard exit-2 error, never a silent empty run.
    registry = None
    markets = None
    if mode in ("all", "sunday", "wednesday", "monthly", "keyword", "auto"):
        try:
            registry = _load_products()
            markets = _load_markets()
        except (ProductRegistryError, MarketRegistryError) as exc:
            parser.error(str(exc))

    if mode == "auto":
        _auto_dispatch(
            send=args.digest,
            csv_path=args.csv,
            registry=registry,
            markets=markets,
        )
        return
    if mode == "sunday":
        _run_sunday(csv_path=args.csv, send=args.digest, registry=registry, markets=markets)
        return
    if mode == "wednesday":
        _run_wednesday(send=args.digest, registry=registry, markets=markets)
        return
    if mode == "monthly":
        _run_monthly(send=args.digest, registry=registry, markets=markets)
        return
    if mode == "all":
        targets = markets.targets_for(registry.active_products())
        if not targets:
            parser.error("Zero targets ativos em config/products.json + config/markets.json")
        _run_all(list(targets), csv_path=args.csv, send=args.digest)
        return
    if mode == "none":
        parser.print_help()
        return
    try:
        product = registry.resolve(args.keyword)
        target = markets.target(product, args.market)
    except (ProductNotFoundError, MarketRegistryError, MarketTargetNotFoundError) as exc:
        parser.error(str(exc))
    _run_keyword_action(target, csv_path=args.csv, send=args.digest)


if __name__ == "__main__":
    main()
