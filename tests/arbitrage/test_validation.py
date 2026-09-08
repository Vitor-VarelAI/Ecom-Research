from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import pipeline.arbitrage.validation as validation
from pipeline.arbitrage.models import ArbitrageConfig
from pipeline.arbitrage.validation import (
    KEYWORD_TOOL,
    SERP_TOOL,
    ValidationError,
    validate_offer,
)

from tests.arbitrage.test_scoring import _complete_evidence


class _FakeClient:
    def __init__(
        self,
        *,
        fail_tool: str | None = None,
        responses: dict[str, Any] | None = None,
    ) -> None:
        self.fail_tool = fail_tool
        self.responses = responses or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, dict(arguments)))
        if tool_name == self.fail_tool:
            raise RuntimeError("remote detail must stay sanitized")
        return self.responses.get(tool_name, {"tool": tool_name})


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
) -> tuple[object, object]:
    offer, evidence = _complete_evidence()
    offer = replace(offer, offer_id="offer-approved", enabled=enabled)
    evidence = replace(evidence, offer_id=offer.offer_id)
    config = ArbitrageConfig(
        schema_version=1,
        base_currency="EUR",
        fx_rates=(),
        offers=(offer,),
    )
    monkeypatch.setattr(validation, "load_config", lambda path: config)
    monkeypatch.setattr(
        validation,
        "build_offer_evidence",
        lambda offer, config, as_of, data_dir: evidence,
    )
    return offer, evidence


def _now() -> datetime:
    return datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def test_unauthorized_offer_makes_zero_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture(monkeypatch, enabled=False)
    client = _FakeClient()

    with pytest.raises(ValidationError, match="RESEARCH_ONLY"):
        validate_offer(
            "offer-approved",
            config_path=tmp_path / "offers.json",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "validations",
            project_id="project-123",
            client=client,
            as_of=date(2026, 9, 7),
            now=_now,
        )

    assert client.calls == []


def test_approved_offer_calls_only_scoped_tools_and_persists_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture(monkeypatch)
    client = _FakeClient(
        responses={
            KEYWORD_TOOL: {
                "keyword": "demo",
                "api_key": "must-not-persist",
            },
            SERP_TOOL: {"query": "demo", "results": []},
        }
    )

    artifact = validate_offer(
        "offer-approved",
        config_path=tmp_path / "offers.json",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "validations",
        project_id="project-123",
        endpoint="http://127.0.0.1:3001/mcp",
        client=client,
        as_of=date(2026, 9, 7),
        now=_now,
    )

    assert client.calls == [
        (
            KEYWORD_TOOL,
            {
                "projectId": "project-123",
                "keywords": ["demo"],
                "locationCode": 2840,
                "languageCode": "en",
                "includeMonthlyTrends": True,
                "includeClickstreamData": False,
                "sortBy": "search_volume",
            },
        ),
        (
            SERP_TOOL,
            {
                "projectId": "project-123",
                "queries": [
                    {
                        "keyword": "demo",
                        "locationCode": 2840,
                        "languageCode": "en",
                    }
                ],
            },
        ),
    ]
    assert artifact["schema"] == "offer-validation"
    assert artifact["version"] == 1
    assert artifact["as_of"] == "2026-09-07"
    assert artifact["project_id"] == "project-123"
    assert (
        tmp_path / "validations" / "2026-09-07"
    ).is_dir()
    assert artifact["decision"]["authorized"] is True
    assert artifact["dataforseo_market"] == {
        "market_id": "US",
        "location_code": 2840,
        "language_code": "en",
    }
    assert artifact["tools"][KEYWORD_TOOL]["status"] == "completed"
    assert artifact["tools"][SERP_TOOL]["status"] == "completed"
    assert artifact["tools"][KEYWORD_TOOL]["structured_content"] == {
        "keyword": "demo",
    }
    assert len(artifact["ledger"]) == 4
    assert all("endpoint" not in entry for entry in artifact["ledger"])
    assert "api_key" not in str(artifact)


def test_resume_reuses_completed_tool_and_complete_artifact_needs_zero_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture(monkeypatch)
    first_client = _FakeClient(fail_tool=SERP_TOOL)

    with pytest.raises(ValidationError, match="OpenSEO tool failed"):
        validate_offer(
            "offer-approved",
            config_path=tmp_path / "offers.json",
            data_dir=tmp_path / "data",
            artifact_dir=tmp_path / "validations",
            project_id="project-123",
            client=first_client,
            as_of=date(2026, 9, 7),
            now=_now,
        )

    second_client = _FakeClient()
    completed = validate_offer(
        "offer-approved",
        config_path=tmp_path / "offers.json",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "validations",
        project_id="project-123",
        client=second_client,
        as_of=date(2026, 9, 7),
        now=_now,
    )

    assert [tool for tool, _ in first_client.calls] == [KEYWORD_TOOL, SERP_TOOL]
    assert [tool for tool, _ in second_client.calls] == [SERP_TOOL]
    assert completed["tools"][KEYWORD_TOOL]["status"] == "completed"
    assert completed["tools"][SERP_TOOL]["status"] == "completed"

    third_client = _FakeClient()
    reused = validate_offer(
        "offer-approved",
        config_path=tmp_path / "offers.json",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "validations",
        project_id="project-123",
        client=third_client,
        as_of=date(2026, 9, 7),
        now=_now,
    )

    assert third_client.calls == []
    assert reused == completed

    other_project_client = _FakeClient()
    other_project = validate_offer(
        "offer-approved",
        config_path=tmp_path / "offers.json",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "validations",
        project_id="project-456",
        client=other_project_client,
        as_of=date(2026, 9, 7),
        now=_now,
    )
    assert [tool for tool, _ in other_project_client.calls] == [
        KEYWORD_TOOL,
        SERP_TOOL,
    ]
    assert other_project["project_id"] == "project-456"

    other_date_client = _FakeClient()
    other_date = validate_offer(
        "offer-approved",
        config_path=tmp_path / "offers.json",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "validations",
        project_id="project-123",
        client=other_date_client,
        as_of=date(2026, 9, 8),
        now=_now,
    )
    assert [tool for tool, _ in other_date_client.calls] == [
        KEYWORD_TOOL,
        SERP_TOOL,
    ]
    assert other_date["as_of"] == "2026-09-08"
