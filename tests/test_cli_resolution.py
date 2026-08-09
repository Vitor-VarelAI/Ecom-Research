"""Mode resolution and CLI validation: precedence, warnings, CSV, digest."""

from __future__ import annotations

import os
from datetime import date as real_date

import pytest

import run


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
