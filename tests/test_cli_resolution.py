"""Mode resolution and CLI validation: precedence, warnings, CSV, digest."""

from __future__ import annotations

import os
from datetime import date as real_date

import pytest

import run
from tests.fixtures.products_helpers import write_registry


def _resolve(argv, today=None):
    parser = run._build_parser()
    args = parser.parse_args(argv)
    return run.resolve_mode(args, today=today)


def _sunday():
    return real_date(2026, 7, 19)  # a Sunday


def _wednesday():
    return real_date(2026, 7, 15)  # a Wednesday


def test_precedence_auto_wins_over_all_selectors():
    resolved = _resolve(["--auto", "--sunday", "--wednesday", "--monthly", "--all", "mochila"])
    assert resolved["mode"] == "auto"
    assert any("sobrepõe-se" in w and "sunday" in w for w in resolved["warnings"])
    assert any("keyword" in w for w in resolved["warnings"])


def test_precedence_sunday_over_wednesday_monthly_all_keyword():
    resolved = _resolve(["--sunday", "--wednesday", "--monthly", "--all", "mochila"])
    assert resolved["mode"] == "sunday"
    assert any("--wednesday --monthly --all --keyword" in w for w in resolved["warnings"])


def test_precedence_wednesday_over_monthly_all_keyword():
    resolved = _resolve(["--wednesday", "--monthly", "--all", "mochila"])
    assert resolved["mode"] == "wednesday"
    assert any("--monthly --all --keyword" in w for w in resolved["warnings"])


def test_precedence_monthly_over_all_keyword():
    resolved = _resolve(["--monthly", "--all", "mochila"])
    assert resolved["mode"] == "monthly"
    assert any("--all --keyword" in w for w in resolved["warnings"])


def test_precedence_all_over_positional_keyword():
    resolved = _resolve(["--all", "mochila"])
    assert resolved["mode"] == "all"
    assert any("keyword" in w for w in resolved["warnings"])


def test_positional_keyword_is_last_resort():
    assert _resolve(["mochila"])["mode"] == "keyword"
    assert _resolve([])["mode"] == "none"


def test_digest_is_orthogonal_and_never_warns():
    resolved = _resolve(["--sunday", "--digest"])
    assert resolved["mode"] == "sunday"
    assert resolved["warnings"] == []


def test_single_flag_modes_are_silent():
    for argv, expected in ((["--sunday"], "sunday"), (["--wednesday"], "wednesday"),
                           (["--monthly"], "monthly"), (["--all"], "all")):
        resolved = _resolve(argv)
        assert resolved["mode"] == expected
        assert resolved["warnings"] == []


def test_missing_explicit_csv_is_fatal_when_mode_consumes_csv(tmp_path):
    missing = str(tmp_path / "nope.csv")
    for argv in (["--sunday", "--csv", missing], ["--all", "--csv", missing], ["kw", "--csv", missing]):
        resolved = _resolve(argv)
        assert resolved["csv_fatal"] == f"CSV não encontrado: {missing}"


def test_auto_rejects_missing_csv_only_on_sundays(tmp_path):
    missing = str(tmp_path / "nope.csv")
    assert _resolve(["--auto", "--csv", missing], today=_sunday())["csv_fatal"]
    resolved_wed = _resolve(["--auto", "--csv", missing], today=_wednesday())
    assert resolved_wed["csv_fatal"] is None
    assert any("ignorado" in w for w in resolved_wed["warnings"])


def test_csv_ignored_with_warning_on_modes_that_do_not_consume_it(tmp_path):
    missing = str(tmp_path / "nope.csv")
    for argv in (["--wednesday", "--csv", missing], ["--monthly", "--csv", missing]):
        resolved = _resolve(argv)
        assert resolved["csv_fatal"] is None
        assert any("--csv ignorado" in w for w in resolved["warnings"])


def test_existing_csv_passes_validation(tmp_path):
    csv_file = tmp_path / "planner.csv"
    csv_file.write_text("keyword,volume,cpc\nmochila,100,1\n", encoding="utf-8")
    resolved = _resolve(["--sunday", "--csv", str(csv_file)])
    assert resolved["csv_fatal"] is None
    assert resolved["warnings"] == []


def test_main_prints_warnings_to_stderr_and_errors_on_missing_csv(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run, "_run_sunday", lambda *a, **k: [])
    missing = str(tmp_path / "nope.csv")
    with pytest.raises(SystemExit) as exc:
        run.main(["--sunday", "--monthly", "--csv", missing])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--monthly" in err and "ignorados" in err
    assert "CSV não encontrado" in err


# ── Vertical slice 2: positional resolution through the product registry ──


def _install_registry(tmp_path):
    write_registry(tmp_path, [
        {"keyword": "mochila personalizada"},
        {"keyword": "velas aromáticas", "aliases": ["velas", "candles"]},
    ])


def test_main_resolves_positional_by_id_name_query_and_alias(monkeypatch, tmp_path):
    """(b) vertical slice: the positional token resolves via the registry —
    exact product_id, name, research_keyword (case/accent folded) or alias —
    and the resolved Product reaches the action."""
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    _install_registry(tmp_path)
    captured = []
    monkeypatch.setattr(
        run, "_run_keyword_action",
        lambda product, **kwargs: captured.append((product.product_id, product.name, product.research_keyword)),
    )

    run.main(["mochila personalizada"])  # research_keyword/name
    assert captured == [("e942e8bf7f30", "mochila personalizada", "mochila personalizada")]

    captured.clear()
    run.main(["e942e8bf7f30"])  # exact product_id
    assert captured == [("e942e8bf7f30", "mochila personalizada", "mochila personalizada")]

    captured.clear()
    run.main(["VELAS AROMÁTICAS"])  # name/query, case+accents folded
    assert captured == [("258053734570", "velas aromáticas", "velas aromáticas")]

    for alias in ("velas", "candles"):
        captured.clear()
        run.main([alias])
        assert captured == [("258053734570", "velas aromáticas", "velas aromáticas")]


def test_main_unknown_positional_exits_2_before_any_source(monkeypatch, tmp_path, capsys):
    """(b) vertical slice: an unknown positional token is a hard exit-2 error
    raised before any source/network call."""
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    _install_registry(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("adapter/source called before resolution error")

    monkeypatch.setattr(run.apify_ads, "fetch", boom)
    monkeypatch.setattr(run.trends, "fetch", boom)
    monkeypatch.setattr(run, "_run_keyword_action", boom)

    with pytest.raises(SystemExit) as exc:
        run.main(["nao existe"])
    assert exc.value.code == 2
    assert "unknown product token" in capsys.readouterr().err


def test_main_missing_registry_fails_closed_exit_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        run.main(["--all"])
    assert exc.value.code == 2
    assert "cannot read product registry" in capsys.readouterr().err


def test_main_invalid_registry_fails_closed_exit_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "products.json").write_text("{ not json SECRET_LEAK_XYZ }", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        run.main(["mochila"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "not valid JSON" in err
    assert "SECRET_LEAK_XYZ" not in err  # registry errors never leak file contents
