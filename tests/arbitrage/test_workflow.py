from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import pipeline.arbitrage.workflow as workflow
from pipeline.arbitrage.models import ArbitrageConfig
from pipeline.arbitrage.workflow import (
    SHORTLIST_BUCKETS,
    build_offer_shortlist,
)

from tests.arbitrage.test_gates import _evidence
from tests.arbitrage.test_scoring import _complete_evidence


def _config(*offers) -> ArbitrageConfig:
    return ArbitrageConfig(
        schema_version=1,
        base_currency="EUR",
        fx_rates=(),
        offers=tuple(offers),
    )


def _identified(offer, evidence, offer_id: str):
    return replace(offer, offer_id=offer_id), replace(evidence, offer_id=offer_id)


def test_workflow_resolves_decides_and_orders_all_shortlist_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    test_offer, test_evidence = _identified(*_complete_evidence(), "offer-test")
    research_offer, research_evidence = _complete_evidence()
    research_offer = replace(research_offer, offer_id="offer-research", enabled=False)
    research_evidence = replace(research_evidence, offer_id="offer-research")
    watch_offer, watch_evidence = _identified(
        *_complete_evidence(
            selected_creative_values={
                field: 0
                for field in (
                    "visual_transformation",
                    "hookability",
                    "problem_awareness",
                    "demo_potential",
                    "ugc_potential",
                    "storytelling_potential",
                    "ai_creative_potential",
                    "before_after_potential",
                    "angle_breadth",
                    "creative_gap",
                )
            }
        ),
        "offer-watch",
    )
    reject_offer, reject_evidence = _identified(
        _complete_evidence()[0],
        _evidence(
            us_fulfilment=False,
            program_url=None,
            landing_url=None,
            ad_links=(),
        ),
        "offer-reject",
    )
    evidence_by_id = {
        evidence.offer_id: evidence
        for evidence in (test_evidence, research_evidence, watch_evidence, reject_evidence)
    }

    monkeypatch.setattr(
        workflow,
        "build_offer_evidence",
        lambda offer, config, as_of, data_dir: evidence_by_id[offer.offer_id],
    )

    shortlist = build_offer_shortlist(
        _config(watch_offer, reject_offer, research_offer, test_offer),
        date(2026, 9, 7),
        tmp_path,
    )

    assert tuple(shortlist.sections) == SHORTLIST_BUCKETS
    assert [item.offer_id for item in shortlist.sections["TEST_NOW"]] == ["offer-test"]
    assert [item.offer_id for item in shortlist.sections["RESEARCH_FURTHER"]] == ["offer-research"]
    assert [item.offer_id for item in shortlist.sections["WATCHLIST"]] == ["offer-watch"]
    assert [item.offer_id for item in shortlist.sections["REJECT"]] == ["offer-reject"]

    item = shortlist.sections["TEST_NOW"][0]
    assert item.program_url == "https://partner.example/program"
    assert item.landing_url == "https://shop.example/landing"
    assert item.ad_links == ("https://meta.example/ad-1",)
    assert item.contribution_eur is not None
    assert item.reasons
    assert shortlist.as_dict()["TEST_NOW"][0]["contribution_eur"] == "12.88"
    reject_item = shortlist.sections["REJECT"][0]
    assert reject_item.program_url is None
    assert reject_item.landing_url is None
    assert reject_item.ad_links == ()


def test_empty_offer_config_cli_returns_four_empty_sections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "offers.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_currency": "EUR",
                "fx_rates": [],
                "offers": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("run.REPO_ROOT", str(tmp_path))

    import run

    run.main(["--offer-shortlist"])

    payload = json.loads(capsys.readouterr().out)
    assert tuple(payload) == SHORTLIST_BUCKETS
    assert payload == {bucket: [] for bucket in SHORTLIST_BUCKETS}


def test_invalid_offer_config_exits_before_evidence_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "offers.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("run.REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(
        workflow,
        "build_offer_evidence",
        lambda *args, **kwargs: pytest.fail("evidence resolution must not run"),
    )

    import run

    with pytest.raises(SystemExit) as exc_info:
        run.main(["--offer-shortlist"])

    assert exc_info.value.code == 2
