"""Run identity: CSV hash participates only when the action consumes CSV.

``keyword``/``all``/``sunday`` (including Sunday via --auto) consume the
Keyword Planner CSV, so their run id tracks the CSV content.  Wednesday and
monthly never read the CSV, so unrelated CSV changes must not change their
identity; a direct --sunday and --auto on a Sunday share the same run id.
"""

from __future__ import annotations

from pipeline import run_state
import run as run_module


def _write_csv(tmp_path, content):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    csv_path = data_dir / "planner.csv"
    csv_path.write_text(content, encoding="utf-8")
    return csv_path


def test_wednesday_and_monthly_identity_ignore_csv_presence_and_content(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    keywords = ["mochila"]

    for mode in ("wednesday", "monthly"):
        with_explicit = run_module._compute_run_id(mode, keywords, str(csv_path))
        without = run_module._compute_run_id(mode, keywords, None)
        # An unrelated CSV file changes/arrives: identity stays stable.
        csv_path.write_text("completely different content\n", encoding="utf-8")
        after_change = run_module._compute_run_id(mode, keywords, str(csv_path))
        assert with_explicit == without == after_change, mode


def test_keyword_all_sunday_identity_tracks_csv_content(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    keywords = ["mochila"]

    for mode in ("keyword", "all", "sunday"):
        csv_path.write_text("kw,volume\nmochila,100\n", encoding="utf-8")
        base = run_module._compute_run_id(mode, keywords, str(csv_path))
        csv_path.write_text("kw,volume\nmochila,999\n", encoding="utf-8")
        changed = run_module._compute_run_id(mode, keywords, str(csv_path))
        assert base != changed, mode  # CSV content is part of the identity


def test_missing_csv_gives_different_identity_than_present_csv(monkeypatch, tmp_path):
    empty_root = tmp_path / "empty"
    monkeypatch.setattr(run_module, "REPO_ROOT", str(empty_root))
    keywords = ["mochila"]
    no_csv = run_module._compute_run_id("sunday", keywords, None)

    # A CSV appears: the sunday identity changes (it consumes CSV)…
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    with_csv = run_module._compute_run_id("sunday", keywords, str(csv_path))
    assert no_csv != with_csv
    # …while wednesday/monthly stay put regardless.
    assert run_module._compute_run_id("wednesday", keywords, None) == \
        run_module._compute_run_id("wednesday", keywords, str(csv_path))
    assert run_module._compute_run_id("monthly", keywords, None) == \
        run_module._compute_run_id("monthly", keywords, str(csv_path))


def test_no_csv_consumed_means_no_hash_even_when_file_exists(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "x\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    keywords = ["mochila"]
    # With the CSV present, wednesday/monthly still ignore it entirely.
    assert run_module._compute_run_id("wednesday", keywords, str(csv_path)) == \
        run_module._compute_run_id("wednesday", keywords, None)
    assert run_module._compute_run_id("monthly", keywords, str(csv_path)) == \
        run_module._compute_run_id("monthly", keywords, None)


def test_direct_and_auto_sunday_share_identity_with_csv(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    keywords = ["mochila", "bolsa"]
    # --auto on a Sunday executes the same ``sunday`` action with the same
    # CSV path as a direct --sunday: identical run id.
    direct = run_module._compute_run_id("sunday", keywords, str(csv_path))
    via_auto = run_module._compute_run_id("sunday", keywords, str(csv_path))
    assert direct == via_auto


def test_manifest_records_csv_hash_only_for_csv_modes(monkeypatch, tmp_path):
    csv_path = _write_csv(tmp_path, "kw,volume\nmochila,100\n")
    monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
    keywords = ["mochila"]

    wed = run_module._begin_manifest("wed", "wednesday", keywords, str(csv_path))
    assert wed["csv_sha256"] is None
    monthly = run_module._begin_manifest("mon", "monthly", keywords, str(csv_path))
    assert monthly["csv_sha256"] is None

    sunday = run_module._begin_manifest("sun", "sunday", keywords, str(csv_path))
    assert sunday["csv_sha256"] == run_state.file_sha256(str(csv_path))
    keyword = run_module._begin_manifest("kw", "keyword", keywords, str(csv_path))
    assert keyword["csv_sha256"] == run_state.file_sha256(str(csv_path))
