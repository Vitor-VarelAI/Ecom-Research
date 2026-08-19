"""Run identity: CSV hash participates only when the action consumes CSV.

``keyword``/``all``/``sunday`` (including Sunday via --auto) consume the
Keyword Planner CSV, so their run id tracks the CSV content.  Wednesday and
monthly never read the CSV, so unrelated CSV changes must not change their
identity; a direct --sunday and --auto on a Sunday share the same run id.
Identity is computed over ordered product descriptors (product_id +
research_keyword), so a query rename changes the run id while a product
name-only or alias-only change does not.
"""

from __future__ import annotations

from pipeline import run_state
import run as run_module
from tests.fixtures.products_helpers import product


def _write_csv(tmp_path, content):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    csv_path = data_dir / "planner.csv"
    csv_path.write_text(content, encoding="utf-8")
    return csv_path


def test_wednesday_and_monthly_identity_ignore_csv_presence_and_content(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    products = [product("mochila")]

    for mode in ("wednesday", "monthly"):
        with_explicit = run_module._compute_run_id(mode, products, str(csv_path))
        without = run_module._compute_run_id(mode, products, None)
        # An unrelated CSV file changes/arrives: identity stays stable.
        csv_path.write_text("completely different content\n", encoding="utf-8")
        after_change = run_module._compute_run_id(mode, products, str(csv_path))
        assert with_explicit == without == after_change, mode


def test_keyword_all_sunday_identity_tracks_csv_content(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    products = [product("mochila")]

    for mode in ("keyword", "all", "sunday"):
        csv_path.write_text("kw,volume\nmochila,100\n", encoding="utf-8")
        base = run_module._compute_run_id(mode, products, str(csv_path))
        csv_path.write_text("kw,volume\nmochila,999\n", encoding="utf-8")
        changed = run_module._compute_run_id(mode, products, str(csv_path))
        assert base != changed, mode  # CSV content is part of the identity


def test_missing_csv_gives_different_identity_than_present_csv(monkeypatch, tmp_path):
    empty_root = tmp_path / "empty"
    monkeypatch.setattr(run_module, "REPO_ROOT", str(empty_root))
    products = [product("mochila")]
    no_csv = run_module._compute_run_id("sunday", products, None)

    # A CSV appears: the sunday identity changes (it consumes CSV)…
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    with_csv = run_module._compute_run_id("sunday", products, str(csv_path))
    assert no_csv != with_csv


def test_query_rename_changes_run_id_name_or_alias_only_does_not(monkeypatch, tmp_path):
    """(a) vertical slice: renaming the research query changes the run id;
    a product name-only or alias-only change keeps it."""
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    base = product("mochila personalizada", name="mochila personalizada")
    renamed_query = product("mochila escolar", name="mochila personalizada")
    renamed_name = product("mochila personalizada", name="Mochilas Premium")
    alias_only = product("mochila personalizada", aliases=("mochila", "bolsas"))

    original = run_module._compute_run_id("all", [base], None)
    assert run_module._compute_run_id("all", [renamed_query], None) != original
    assert run_module._compute_run_id("all", [renamed_name], None) == original
    assert run_module._compute_run_id("all", [alias_only], None) == original

    # Order matters: the same two products in a different order is a new run.
    other = product("caneca personalizada")
    ordered = run_module._compute_run_id("all", [base, other], None)
    swapped = run_module._compute_run_id("all", [other, base], None)
    assert ordered != swapped


def test_run_id_canonical_payload_is_explicit_products_json():
    products = [
        {"product_id": "e942e8bf7f30", "research_keyword": "mochila personalizada"},
        {"product_id": "be385ea0e349", "research_keyword": "camisola portugal"},
    ]
    canonical = run_state.compute_run_id(
        logical_date="2026-08-02",
        mode="sunday",
        products=products,
        csv_sha256=None,
        scoring_version="1.0.0",
    )
    expected = run_state.sha256_hex(
        '{"csv_sha256":null,"date":"2026-08-02","mode":"sunday","products":'
        '[{"product_id":"e942e8bf7f30","research_keyword":"mochila personalizada"},'
        '{"product_id":"be385ea0e349","research_keyword":"camisola portugal"}],'
        '"scoring_version":"1.0.0"}'
    )
    assert canonical == expected
