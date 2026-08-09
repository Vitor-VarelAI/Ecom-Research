"""Quota writes are atomic: no partial/temp files, content always consistent."""

from __future__ import annotations

import json
import os

from pipeline import quota


def test_quota_spend_writes_atomically(monkeypatch, tmp_path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({"month": quota._current_month(), "calls": 0, "limit": 230}), encoding="utf-8")
    monkeypatch.setattr(quota, "QUOTA_FILE", str(quota_file))

    for _ in range(5):
        quota.spend(1)

    assert quota.remaining() == 225
    leftovers = [name for name in os.listdir(str(tmp_path)) if ".part" in name or name.startswith(".tmp")]
    assert leftovers == []
    data = json.load(open(quota_file, encoding="utf-8"))
    assert data["calls"] == 5


def test_quota_firecrawl_spend_atomic(monkeypatch, tmp_path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({"month": quota._current_month(), "calls": 0, "limit": 230,
                                      "firecrawl_credits": 0, "firecrawl_limit": 1000}), encoding="utf-8")
    monkeypatch.setattr(quota, "QUOTA_FILE", str(quota_file))

    quota.spend_firecrawl(3)
    assert quota.get_firecrawl_usage()["credits"] == 3
    leftovers = [name for name in os.listdir(str(tmp_path)) if ".part" in name or name.startswith(".tmp")]
    assert leftovers == []


def test_quota_file_is_never_left_partial_on_month_reset(monkeypatch, tmp_path):
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({"month": "2020-01", "calls": 200, "limit": 230}), encoding="utf-8")
    monkeypatch.setattr(quota, "QUOTA_FILE", str(quota_file))

    usage = quota.get_usage()  # triggers month reset + write
    assert usage["calls"] == 0
    leftovers = [name for name in os.listdir(str(tmp_path)) if ".part" in name or name.startswith(".tmp")]
    assert leftovers == []
