"""Pack metadata: run id/mode/timestamps/scoring/source states in JSON, provenance in MD."""

from __future__ import annotations

import json
from datetime import date

from pipeline.output import pack

TRENDS = {"direction": "estável", "slope_pct": 0, "seasonal": False, "top_geos": [], "related_queries": []}
ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 1, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 30}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 30}],
       "saturation": "baixa"}
CSV = {"volume": 1000, "cpc": 1, "related_keywords": []}
SCORE = {"score": 50, "label": "OBSERVAR", "breakdown": {}}
STATES = {"apify_ads": {"status": "valid", "origin": "fresh", "reason": "leitura válida"}}


def _temp_paths(tmp_path):
    packs_dir = tmp_path / "packs"
    data_dir = tmp_path / "data"
    packs_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)

    def paths(keyword, run_date=None, mode=None, run_id=None, product_id=None, market_id=None):
        run_date = run_date or date.today()
        slug = pack._slugify(keyword)
        base = f"{run_date.isoformat()}--{slug}"
        if mode in ("sunday", "wednesday", "monthly"):
            base = f"{base}--{mode}"
        return (str(packs_dir / f"{base}.md"), str(data_dir / f"{base}.json"))

    return paths


def test_json_metadata_and_legacy_aliases_coexist(tmp_path, monkeypatch):
    monkeypatch.setattr(pack, "_pack_paths", _temp_paths(tmp_path))
    run_date = date(2026, 8, 2)
    md_path = pack.generate(
        "mochila", TRENDS, CSV, ADS, SCORE,
        run_date=run_date,
        mode="sunday",
        run_id="run123",
        source_states=STATES,
        source_policies={"apify_ads": {"retries": 0}},
        scoring_version="1.0.0",
        scoring_config={"weights": {"trend": 0.3}, "thresholds": {"AVANÇAR": 70}},
        matcher_version="1.0.0",
        matching_schema_version=1,
        market={
            "market_id": "PT", "name": "Portugal", "currency": "EUR",
            "ads_country": "PT", "trends_geo": "PT", "serp_gl": "pt",
            "serp_hl": "pt", "serp_location": "Portugal",
        },
    )
    json_path = md_path.replace("/packs/", "/data/").replace(".md", ".json")
    raw = json.load(open(json_path, encoding="utf-8"))

    # New additive metadata.
    assert raw["run_id"] == "run123"
    assert raw["mode"] == "sunday"
    assert raw["scoring_version"] == "1.0.0"
    assert raw["scoring_config"]["weights"]["trend"] == 0.3
    assert raw["matcher_version"] == "1.0.0"
    assert raw["matching_schema_version"] == 1
    assert raw["source_states"] == STATES
    assert raw["source_policies"]["apify_ads"]["retries"] == 0
    assert raw["generated_at"] and raw["completed_at"]
    assert raw["market_id"] == "PT"
    assert raw["market"]["currency"] == "EUR"

    # Legacy top-level keys are preserved as compatibility aliases.
    assert raw["keyword"] == "mochila"
    assert raw["date"] == "2026-08-02"
    assert raw["score"] == SCORE
    assert raw["trends"] == TRENDS
    assert raw["keyword_planner"] == CSV
    assert raw["facebook_ads"] == ADS
    assert raw["suggested_keywords"] == []

    content = open(md_path, encoding="utf-8").read()
    assert "## Proveniência" in content
    assert "modo sunday" in content
    assert "run run123" in content
    assert "matcher v1.0.0" in content
    assert "Fontes: apify_ads=valid" in content
    assert "## DECISÃO" in content
    assert "Sinal advisory: OBSERVAR PT" in content
    assert "Mercado:   PT" in content
    assert "Prova PT:" in content
    assert "## Sinais PT" in content


def test_generate_without_metadata_omits_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(pack, "_pack_paths", _temp_paths(tmp_path))
    md_path = pack.generate("mochila", TRENDS, CSV, ADS, SCORE, run_date=date(2026, 8, 2))
    content = open(md_path, encoding="utf-8").read()
    assert "## Proveniência" not in content
    json_path = md_path.replace("/packs/", "/data/").replace(".md", ".json")
    raw = json.load(open(json_path, encoding="utf-8"))
    assert "run_id" not in raw
    assert "source_states" not in raw
