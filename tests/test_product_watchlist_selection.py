"""TDD slice 3 — product-aware Wednesday/monthly watchlist selection.

Selection is grouped by canonical product identity (``product_id``), not by
the raw pack ``keyword``: a pack's canonical id is ``product_id`` when
present, else the read-only legacy resolution of its ``keyword`` through the
registry.  A renamed product (old keyword now an alias, new
``research_keyword``) survives the rename with full watchlist and
previous-pack continuity, and no historical artifact is ever rewritten.
"""

from __future__ import annotations

import json
from datetime import date

import run
from pipeline.products import ProductNotFoundError
from tests.fixtures.products_helpers import stable_id, write_registry

SCORE_OBSERVAR = {"score": 50, "label": "OBSERVAR", "breakdown": {}}
SCORE_INSUF = {"score": 0, "label": "DADOS INSUFICIENTES", "breakdown": {}}


def _legacy_pack(keyword: str, pack_date: str, score: dict) -> dict:
    """A historical pack: only the old query string, no product identity."""
    return {
        "keyword": keyword,
        "date": pack_date,
        "score": score,
        "generated_at": f"{pack_date}T08:00:00+00:00",
    }


def _canonical_pack(keyword: str, product_id: str, pack_date: str, score: dict) -> dict:
    """A new pack: carries product_id plus the current research query."""
    return {
        "keyword": keyword,
        "product_id": product_id,
        "date": pack_date,
        "score": score,
        "run_id": f"run-{product_id}-{pack_date}",
        "artifact_complete": True,
        "generated_at": f"{pack_date}T08:00:00+00:00",
    }


def _write_pack(tmp_path, filename: str, payload: dict) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_renamed_product_old_keyword_in_aliases_survives_selection(monkeypatch, tmp_path):
    """(a) A product whose research query was renamed (old keyword ``mochila``
    is now an alias of ``mochila escolar``) keeps its watchlist slot and its
    prior cohort baseline: the legacy pack resolves through the alias."""
    _write_pack(tmp_path, "2026-06-15--mochila.json",
                _legacy_pack("mochila", "2026-06-15", SCORE_OBSERVAR))
    registry = write_registry(tmp_path, [
        {"keyword": "mochila escolar", "name": "mochila personalizada", "aliases": ["mochila"]},
    ])
    product = registry.products[0]
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    watchlist = run._find_packs_by_label(["OBSERVAR"], registry=registry)

    assert [p.product_id for p in watchlist] == [stable_id("mochila escolar")]
    assert watchlist[0].research_keyword == "mochila escolar"
    previous = run._load_previous_pack(product, registry=registry)
    assert previous is not None
    assert previous["keyword"] == "mochila"  # the historical query is untouched
    assert previous["score"]["label"] == "OBSERVAR"


def test_canonical_pack_matches_by_product_id_even_when_keyword_differs(monkeypatch, tmp_path):
    """(b) A pack carrying ``product_id`` is selected by product identity even
    when its ``keyword`` does not match the product's research query and does
    not resolve to anything on its own."""
    pid = stable_id("mochila escolar")
    _write_pack(tmp_path, "2026-06-15--mochila.json",
                _canonical_pack("mochila", pid, "2026-06-15", SCORE_OBSERVAR))
    registry = write_registry(tmp_path, [{"keyword": "mochila escolar"}])
    product = registry.products[0]
    assert product.research_keyword == "mochila escolar"
    assert_not_found(registry, "mochila")  # keyword alone cannot resolve
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    watchlist = run._find_packs_by_label(["OBSERVAR"], registry=registry)
    assert [p.product_id for p in watchlist] == [pid]
    previous = run._load_previous_pack(product, registry=registry)
    assert previous is not None and previous["product_id"] == pid


def test_mixed_legacy_and_canonical_packs_collapse_to_single_newest(monkeypatch, tmp_path):
    """(c) A legacy pack and a canonical pack for the same product collapse
    into a single watchlist slot; the previous pack is the newest complete
    one, whichever identity form it carries."""
    pid = stable_id("mochila escolar")
    _write_pack(tmp_path, "2026-06-15--mochila.json",
                _legacy_pack("mochila", "2026-06-15", SCORE_OBSERVAR))
    _write_pack(tmp_path, "2026-09-01--mochila-escolar.json",
                _canonical_pack("mochila escolar", pid, "2026-09-01", SCORE_OBSERVAR))
    registry = write_registry(tmp_path, [
        {"keyword": "mochila escolar", "aliases": ["mochila"]},
    ])
    product = registry.products[0]
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    watchlist = run._find_packs_by_label(["OBSERVAR"], registry=registry)
    assert [p.product_id for p in watchlist] == [pid]  # one slot, not two
    previous = run._load_previous_pack(product, registry=registry)
    assert previous["date"] == "2026-09-01"
    assert previous["product_id"] == pid


def test_dados_insuficientes_never_evicts_older_evidence_by_product(monkeypatch, tmp_path):
    """(d) Under product-aware grouping a failed run still does not evict the
    older labeled evidence of the same product, and the previous pack is the
    real OBSERVAR evidence, not the failed one."""
    pid = stable_id("mochila escolar")
    _write_pack(tmp_path, "2026-06-15--mochila.json",
                _legacy_pack("mochila", "2026-06-15", SCORE_OBSERVAR))
    _write_pack(tmp_path, "2026-07-01--mochila-escolar.json",
                _canonical_pack("mochila escolar", pid, "2026-07-01", SCORE_INSUF))
    registry = write_registry(tmp_path, [
        {"keyword": "mochila escolar", "aliases": ["mochila"]},
    ])
    product = registry.products[0]
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    assert [p.product_id for p in run._find_packs_by_label(["OBSERVAR"], registry=registry)] == [pid]
    previous = run._load_previous_pack(product, registry=registry)
    assert previous["score"]["label"] == "OBSERVAR"
    assert previous["date"] == "2026-06-15"


def test_orphan_legacy_keyword_is_skipped_without_crashing(monkeypatch, tmp_path):
    """(e) A legacy keyword that resolves to no registry product is a genuine
    orphan: it never appears on the watchlist, never crashes the selectors,
    and does not disturb the valid products around it."""
    _write_pack(tmp_path, "2026-06-15--mochila.json",
                _legacy_pack("mochila", "2026-06-15", SCORE_OBSERVAR))
    _write_pack(tmp_path, "2026-06-15--produto-esquecido.json",
                _legacy_pack("produto esquecido", "2026-06-15", SCORE_OBSERVAR))
    registry = write_registry(tmp_path, ["mochila", "bolsa"])
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))

    watchlist = run._find_packs_by_label(["OBSERVAR"], registry=registry)
    assert [p.product_id for p in watchlist] == [stable_id("mochila")]

    # A watchlist made only of orphans is empty, not an error.
    orphan_root = tmp_path / "orphan_only"
    _write_pack(orphan_root, "2026-06-15--so-orphan.json",
                _legacy_pack("completamente sozinho", "2026-06-15", SCORE_OBSERVAR))
    monkeypatch.setattr(run, "REPO_ROOT", str(orphan_root))
    assert run._find_packs_by_label(["OBSERVAR"], registry=registry) == []


def assert_not_found(registry, token) -> None:
    """Assert the token genuinely cannot resolve on its own (precondition)."""
    try:
        registry.resolve(token)
    except ProductNotFoundError:
        return
    raise AssertionError(f"{token!r} unexpectedly resolves in the registry")


def test_renamed_product_keeps_weekly_and_monthly_runs(monkeypatch, tmp_path):
    """End-to-end: the renamed product still drives a Wednesday pulse whose
    adapter input is the new research query and whose baseline is the legacy
    pack — the whole slice-3 contract in one flow."""
    _write_pack(tmp_path, f"{date.today().isoformat()}--mochila.json",
                {**_legacy_pack("mochila", date.today().isoformat(), SCORE_OBSERVAR),
                  "trends": {}, "keyword_planner": {}, "seo": {},
                  "facebook_ads": {"total_ads": 1, "ad_ids": ["a1"], "advertisers": []}})
    registry = write_registry(tmp_path, [
        {"keyword": "mochila escolar", "name": "mochila personalizada", "aliases": ["mochila"]},
    ])
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    apify_calls = []
    monkeypatch.setattr(run.apify_ads, "fetch", lambda kw: apify_calls.append(kw) or {
        "total_ads": 1, "ad_ids": ["a2"], "advertisers": []})
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_wednesday(send=False, registry=registry)

    assert apify_calls == ["mochila escolar"]  # adapter input is the new query
    assert [r["product_id"] for r in results] == [stable_id("mochila escolar")]
    assert results[0]["research_keyword"] == "mochila escolar"
    assert results[0]["keyword"] == "mochila escolar"
