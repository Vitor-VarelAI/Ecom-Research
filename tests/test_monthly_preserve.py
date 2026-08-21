"""Monthly Trends failure preserves the previous pack and the OBSERVAR watchlist."""

from __future__ import annotations

import json
from datetime import date as real_date

import run
from tests.fixtures.ads_helpers import classified_ads_data
from tests.fixtures.products_helpers import stable_id, write_registry

TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SEO = {"organic_results": [], "ads_count": 0, "paa_questions": [], "seo_opportunity": "alta"}


def _write_previous(tmp_path, label="OBSERVAR"):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    previous = {
        "keyword": "mochila",
        "date": real_date.today().isoformat(),
        "score": {"score": 50, "label": label, "breakdown": {}},
        "trends": TRENDS,
        "keyword_planner": CSV,
        "facebook_ads": classified_ads_data(),
        "seo": SEO,
        "landing_price": {"price_range": {"min": 10, "max": 20}},
        "suggested_keywords": [{"keyword": "mochila escolar", "score": 10, "status": "proposed"}],
    }
    (data_dir / f"{real_date.today().isoformat()}--mochila.json").write_text(json.dumps(previous), encoding="utf-8")
    return write_registry(tmp_path, ["mochila"])


def _product(tmp_path, keyword="mochila"):
    """The registry Product for a keyword under the tmp root."""
    registry = write_registry(tmp_path, [keyword])
    return registry.products[0]


def test_monthly_trends_failure_preserves_previous_pack(monkeypatch, tmp_path, capsys):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: {"error": "Trends offline"})
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run._run_monthly(send=False)

    assert len(results) == 1
    assert results[0]["preserved"] is True
    assert "error" in results[0]
    assert generated == []  # no replacement pack written
    # No new artifact: the data dir still holds exactly the previous pack.
    data_files = [p.name for p in (tmp_path / "data").glob("*.json")]
    assert data_files == [f"{real_date.today().isoformat()}--mochila.json"]

    out = capsys.readouterr().out
    assert "fonte falhou; pack anterior preservado" in out

    # The OBSERVAR product survives in the watchlist for the next monthly run.
    watchlist = run._find_packs_by_label(["OBSERVAR"])
    assert [p.product_id for p in watchlist] == [stable_id("mochila")]

    # The failure is recorded in the run state (digest + preserved result).
    runs_dir = tmp_path / "data" / "runs"
    manifests = [json.loads(p.read_text(encoding="utf-8")) for p in runs_dir.glob("*.json")]
    assert len(manifests) == 1
    assert manifests[0]["state"] == "completed"
    assert manifests[0]["mode"] == "monthly"
    assert manifests[0]["results"][0]["preserved"] is True
    assert "fonte falhou; pack anterior preservado" in manifests[0]["digest"]["text"]


def test_monthly_success_writes_mode_suffixed_pack(monkeypatch, tmp_path):
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: {**TRENDS, "slope_pct": 12})
    generated = []
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: generated.append(kwargs) or "pack.md")

    results = run._run_monthly(send=False)

    assert generated and generated[0]["mode"] == "monthly"
    assert results[0]["score_result"]["label"] in {"AVANÇAR", "OBSERVAR", "DESCARTAR"}
    assert "error" not in results[0]
    # (7) watchlist products resolve through the registry and carry identity.
    assert results[0]["product_id"] == stable_id("mochila")
    assert results[0]["research_keyword"] == "mochila"
    assert generated[0]["product_id"] == stable_id("mochila")
    assert generated[0]["research_keyword"] == "mochila"


def test_monthly_preserved_result_carries_product_identity(monkeypatch, tmp_path):
    """Preserved outcomes also carry canonical product identity/name/query."""
    _write_previous(tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(run.trends, "fetch", lambda *a, **k: {"error": "Trends offline"})
    monkeypatch.setattr(run.pack, "generate", lambda **kwargs: "pack.md")

    results = run._run_monthly(send=False)

    assert results[0]["preserved"] is True
    assert results[0]["product_id"] == stable_id("mochila")
    assert results[0]["product_name"] == "mochila"
    assert results[0]["research_keyword"] == "mochila"
    assert results[0]["keyword"] == "mochila"
