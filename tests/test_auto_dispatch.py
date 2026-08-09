"""Automatic dispatch tests, including day-1 overlap ordering."""

from datetime import date as real_date
from unittest.mock import patch

import run as run_module


def _today(monkeypatch, value):
    class FakeDate:
        @classmethod
        def today(cls):
            return value

    monkeypatch.setattr(run_module, "date", FakeDate)


def test_sunday_dispatches(monkeypatch):
    _today(monkeypatch, real_date(2026, 7, 19))
    with patch.object(run_module, "_run_sunday", return_value=[{"keyword": "sun"}]) as sunday, \
         patch.object(run_module, "_run_wednesday") as wednesday, \
         patch.object(run_module, "_run_monthly") as monthly:
        assert run_module._auto_dispatch(send=False) == [{"keyword": "sun"}]
    sunday.assert_called_once_with(send=False, csv_path=None)
    wednesday.assert_not_called()
    monthly.assert_not_called()


def test_wednesday_dispatches(monkeypatch):
    _today(monkeypatch, real_date(2026, 7, 15))
    with patch.object(run_module, "_run_sunday") as sunday, \
         patch.object(run_module, "_run_wednesday", return_value=[{"keyword": "wed"}]) as wednesday, \
         patch.object(run_module, "_run_monthly") as monthly:
        assert run_module._auto_dispatch(send=False) == [{"keyword": "wed"}]
    wednesday.assert_called_once_with(send=False)
    sunday.assert_not_called()
    monthly.assert_not_called()


def test_day_one_sunday_runs_monthly_then_sunday(monkeypatch):
    _today(monkeypatch, real_date(2026, 3, 1))
    calls = []

    def monthly(send):
        calls.append("monthly")
        return [{"keyword": "monthly"}]

    def sunday(send, csv_path=None):
        calls.append("sunday")
        return [{"keyword": "sunday"}]

    with patch.object(run_module, "_run_monthly", side_effect=monthly), patch.object(run_module, "_run_sunday", side_effect=sunday):
        result = run_module._auto_dispatch(send=False)
    assert calls == ["monthly", "sunday"]
    assert [item["keyword"] for item in result] == ["monthly", "sunday"]


def test_day_one_wednesday_runs_monthly_then_wednesday(monkeypatch):
    _today(monkeypatch, real_date(2026, 7, 1))
    calls = []

    def monthly(send):
        calls.append("monthly")
        return [{"keyword": "monthly"}]

    def wednesday(send):
        calls.append("wednesday")
        return [{"keyword": "wednesday"}]

    with patch.object(run_module, "_run_monthly", side_effect=monthly), patch.object(run_module, "_run_wednesday", side_effect=wednesday):
        result = run_module._auto_dispatch(send=False)
    assert calls == ["monthly", "wednesday"]
    assert [item["keyword"] for item in result] == ["monthly", "wednesday"]


def test_random_day_has_no_action(monkeypatch):
    _today(monkeypatch, real_date(2026, 7, 16))
    with patch.object(run_module, "_run_monthly") as monthly, patch.object(run_module, "_run_sunday") as sunday, patch.object(run_module, "_run_wednesday") as wednesday:
        assert run_module._auto_dispatch(send=False) == []
    monthly.assert_not_called()
    sunday.assert_not_called()
    wednesday.assert_not_called()
