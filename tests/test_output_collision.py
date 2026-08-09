"""Output naming: scheduled modes get suffixes; legacy readers see both names."""

from __future__ import annotations

import json
from datetime import date

import run
from pipeline.output import pack

TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
       "saturation": "baixa"}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SCORE = {"score": 50, "label": "OBSERVAR", "breakdown": {}}


def test_scheduled_modes_get_distinct_suffix_paths():
    run_date = date(2026, 8, 1)
    monthly = pack._pack_paths("mochila", run_date, "monthly")
    sunday = pack._pack_paths("mochila", run_date, "sunday")
    wednesday = pack._pack_paths("mochila", run_date, "wednesday")
    legacy = pack._pack_paths("mochila", run_date)

    assert len({monthly, sunday, wednesday, legacy}) == 4
    assert monthly[0].endswith("--mochila--monthly.md")
    assert sunday[0].endswith("--mochila--sunday.md")
    assert wednesday[1].endswith("--mochila--wednesday.json")
    # Manual/full output names are unchanged for compatibility.
    assert legacy[0].endswith("--mochila.md")
    assert legacy[1].endswith("--mochila.json")


def _temp_paths(tmp_path):
    packs_dir = tmp_path / "packs"
    data_dir = tmp_path / "data"
    packs_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)

    def paths(keyword, run_date=None, mode=None, run_id=None):
        run_date = run_date or date.today()
        slug = pack._slugify(keyword)
        base = f"{run_date.isoformat()}--{slug}"
        if mode in ("sunday", "wednesday", "monthly"):
            base = f"{base}--{mode}"
        return (str(packs_dir / f"{base}.md"), str(data_dir / f"{base}.json"))

    return paths


def test_same_day_monthly_and_sunday_packs_coexist(tmp_path, monkeypatch):
    monkeypatch.setattr(pack, "_pack_paths", _temp_paths(tmp_path))
    run_date = date(2026, 8, 1)
    pack.generate("mochila", TRENDS, CSV, ADS, SCORE, run_date=run_date, mode="monthly", run_id="m1")
    pack.generate("mochila", TRENDS, CSV, ADS, SCORE, run_date=run_date, mode="sunday", run_id="s1")

    data_files = sorted(p.name for p in (tmp_path / "data").glob("*.json"))
    assert data_files == ["2026-08-01--mochila--monthly.json", "2026-08-01--mochila--sunday.json"]
    monthly = json.load(open(tmp_path / "data" / "2026-08-01--mochila--monthly.json", encoding="utf-8"))
    sunday = json.load(open(tmp_path / "data" / "2026-08-01--mochila--sunday.json", encoding="utf-8"))
    assert monthly["run_id"] == "m1" and sunday["run_id"] == "s1"


def test_previous_pack_reader_selects_deterministically_by_timestamp(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    base = {"keyword": "mochila", "date": "2026-08-01", "score": {"label": "OBSERVAR", "score": 50, "breakdown": {}},
            "trends": {}, "keyword_planner": {}, "facebook_ads": {}}
    (data_dir / "2026-08-01--mochila.json").write_text(
        json.dumps({**base, "generated_at": "2026-08-01T08:00:00+00:00"}), encoding="utf-8")
    (data_dir / "2026-08-01--mochila--monthly.json").write_text(
        json.dumps({**base, "generated_at": "2026-08-01T09:00:00+00:00"}), encoding="utf-8")

    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    previous = run._load_previous_pack("mochila")
    assert previous["generated_at"] == "2026-08-01T09:00:00+00:00"
    # Both names are visible to the watchlist search.
    assert run._find_packs_by_label(["OBSERVAR"]) == ["mochila"]


def test_legacy_failed_pack_does_not_evict_observar_evidence(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    base = {"keyword": "mochila"}
    (data_dir / "2026-06-15--mochila.json").write_text(
        json.dumps({**base, "date": "2026-06-15", "score": {"label": "OBSERVAR", "score": 50, "breakdown": {}},
                    "generated_at": "2026-06-15T08:00:00+00:00"}), encoding="utf-8")
    (data_dir / "2026-07-01--mochila.json").write_text(
        json.dumps({**base, "date": "2026-07-01", "score": {"label": "DADOS INSUFICIENTES", "score": 0, "breakdown": {}},
                    "generated_at": "2026-07-01T08:00:00+00:00"}), encoding="utf-8")

    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    assert run._find_packs_by_label(["OBSERVAR"]) == ["mochila"]
    previous = run._load_previous_pack("mochila")
    assert previous["score"]["label"] == "OBSERVAR"


def test_pack_is_complete_strict_shape():
    valid = {"keyword": "k", "date": "2026-08-01", "score": {"label": "OBSERVAR", "score": 50}}
    assert run._pack_is_complete(valid)
    assert not run._pack_is_complete({"keyword": "k", "date": "2026-08-01", "score": []})
    assert not run._pack_is_complete({"keyword": "k", "date": "2026-08-01"})
    assert not run._pack_is_complete({"keyword": "k", "date": "2026-08-01", "score": {"label": ""}})
    assert not run._pack_is_complete({"keyword": "k", "date": "2026-08-01", "score": {"label": 5}})
    assert not run._pack_is_complete({"keyword": "", "date": "2026-08-01", "score": {"label": "OBSERVAR"}})
    assert not run._pack_is_complete({"keyword": "k", "date": "bad", "score": {"label": "OBSERVAR"}})
    assert not run._pack_is_complete({"keyword": "k", "date": "2026-08-01", "score": {"label": "OBSERVAR"}, "run_id": "r"})
    assert run._pack_is_complete({"keyword": "k", "date": "2026-08-01", "score": {"label": "OBSERVAR"}, "run_id": "r", "artifact_complete": True})
    assert not run._pack_is_complete("not-a-dict")
    assert not run._pack_is_complete(None)


def test_malformed_legacy_packs_are_ignored_by_selectors(monkeypatch, tmp_path):
    """Malformed legacy packs (score: [], blank keyword, bad date, missing
    score) are ignored — they never crash the selectors nor evict evidence."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    today = date.today().isoformat()
    stamp = "2026-08-01T08:00:00+00:00"
    valid = {"keyword": "mochila", "date": today,
             "score": {"label": "OBSERVAR", "score": 50, "breakdown": {}},
             "generated_at": stamp}
    (data_dir / f"{today}--mochila.json").write_text(json.dumps(valid), encoding="utf-8")
    malformed = {
        "score-list": {"keyword": "x1", "date": today, "score": [], "generated_at": stamp},
        "no-score": {"keyword": "x2", "date": today, "generated_at": stamp},
        "blank-kw": {"keyword": "", "date": today, "score": {"label": "OBSERVAR"}, "generated_at": stamp},
        "bad-date": {"keyword": "x3", "date": "ontem", "score": {"label": "OBSERVAR"}, "generated_at": stamp},
        "blank-label": {"keyword": "x4", "date": today, "score": {"label": ""}, "generated_at": stamp},
    }
    for name, payload in malformed.items():
        (data_dir / f"{today}--{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    # Run-scoped JSON without artifact_complete is incomplete/orphan.
    (data_dir / f"{today}--incomplete.json").write_text(json.dumps(
        {"keyword": "x5", "date": today, "score": {"label": "OBSERVAR"},
         "run_id": "r1", "generated_at": stamp}), encoding="utf-8")

    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    # The malformed packs are ignored: only the valid one is selected.
    assert run._find_packs_by_label(["OBSERVAR"]) == ["mochila"]
    previous = run._load_previous_pack("mochila")
    assert previous["score"]["label"] == "OBSERVAR"


def test_newest_real_label_follows_decision(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    base = {"keyword": "mochila"}
    (data_dir / "2026-06-15--mochila.json").write_text(
        json.dumps({**base, "date": "2026-06-15", "score": {"label": "OBSERVAR", "score": 50, "breakdown": {}},
                    "generated_at": "2026-06-15T08:00:00+00:00"}), encoding="utf-8")
    (data_dir / "2026-09-01--mochila--monthly.json").write_text(
        json.dumps({**base, "date": "2026-09-01", "score": {"label": "AVANÇAR", "score": 80, "breakdown": {}},
                    "generated_at": "2026-09-01T08:00:00+00:00"}), encoding="utf-8")

    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    assert run._find_packs_by_label(["OBSERVAR"]) == []
    assert run._find_packs_by_label(["AVANÇAR"]) == ["mochila"]
