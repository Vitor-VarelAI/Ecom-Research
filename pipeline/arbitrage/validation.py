"""Fail-closed post-decision validation with an idempotent local ledger."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from pipeline.arbitrage.config import ArbitrageConfigError, load_config
from pipeline.arbitrage.decision import TESTABLE_48H, decide_offer
from pipeline.arbitrage.evidence import build_offer_evidence
from pipeline.arbitrage.models import Offer
from pipeline.arbitrage.openseo import (
    DATAFORSEO_MARKETS,
    DEFAULT_MCP_URL,
    OpenSeoClient,
    OpenSeoError,
)

VALIDATION_SCHEMA = "offer-validation"
VALIDATION_VERSION = 1
KEYWORD_TOOL = "get_keyword_metrics"
SERP_TOOL = "get_serp_results"
TOOL_ORDER = (KEYWORD_TOOL, SERP_TOOL)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "endpoint",
        "password",
        "secret",
        "token",
    }
)


class ValidationError(RuntimeError):
    """Sanitized validation failure that is safe to show in the CLI."""


def resolve_project_id(project_id: str | None = None) -> str | None:
    value = project_id or os.getenv("OPENSEO_PROJECT_ID")
    value = value.strip() if isinstance(value, str) else value
    return value or None


def resolve_mcp_url(endpoint: str | None = None) -> str:
    value = endpoint or os.getenv("OPENSEO_MCP_URL") or DEFAULT_MCP_URL
    return value.strip()


def _timestamp(now: Callable[[], datetime] | None) -> str:
    value = now() if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                continue
            result[str(key)] = _json_safe(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_artifact(path: Path, artifact: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(_json_safe(artifact), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_artifact(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _offer_snapshot(offer: Offer) -> dict[str, Any]:
    return {
        "offer_id": offer.offer_id,
        "name": offer.name,
        "brand": offer.brand,
        "market_id": offer.market_id,
        "research_keyword": offer.research_keyword,
        "enabled": offer.enabled,
        "monetization_model": offer.monetization_model,
        "product_type": offer.product_type,
    }


def _decision_snapshot(decision: Any) -> dict[str, Any]:
    return {
        "authorized": (
            decision.readiness_status == TESTABLE_48H
            and decision.bucket == "TEST_NOW"
        ),
        "readiness_status": decision.readiness_status,
        "bucket": decision.bucket,
        "commercial_score": decision.commercial_score,
        "score_48h": decision.score_48h,
        "contribution_eur": decision.contribution_eur,
        "reasons": tuple(decision.reasons),
    }


def _new_artifact(
    offer: Offer,
    decision: Any,
    market: Mapping[str, Any],
    project_id: str,
    as_of: date,
    now: Callable[[], datetime] | None,
) -> dict[str, Any]:
    timestamp = _timestamp(now)
    return {
        "schema": VALIDATION_SCHEMA,
        "version": VALIDATION_VERSION,
        "as_of": as_of.isoformat(),
        "created_at": timestamp,
        "updated_at": timestamp,
        "project_id": project_id,
        "offer_snapshot": _offer_snapshot(offer),
        "decision": _decision_snapshot(decision),
        "dataforseo_market": dict(market),
        "tools": {
            tool: {"status": "pending"}
            for tool in TOOL_ORDER
        },
        "ledger": [],
    }


def _artifact_matches(
    artifact: Mapping[str, Any],
    offer: Offer,
    decision: Any,
    market: Mapping[str, Any],
    project_id: str,
    as_of: date,
) -> bool:
    return (
        artifact.get("schema") == VALIDATION_SCHEMA
        and artifact.get("version") == VALIDATION_VERSION
        and artifact.get("project_id") == project_id
        and artifact.get("as_of") == as_of.isoformat()
        and artifact.get("offer_snapshot") == _offer_snapshot(offer)
        and artifact.get("decision") == _json_safe(_decision_snapshot(decision))
        and artifact.get("dataforseo_market") == dict(market)
    )


def _is_complete(artifact: Mapping[str, Any]) -> bool:
    tools = artifact.get("tools")
    return isinstance(tools, Mapping) and all(
        isinstance(tools.get(tool), Mapping)
        and tools[tool].get("status") == "completed"
        and "structured_content" in tools[tool]
        for tool in TOOL_ORDER
    )


def _tool_arguments(
    tool: str,
    offer: Offer,
    project_id: str,
    market: Mapping[str, Any],
) -> dict[str, Any]:
    common = {
        "projectId": project_id,
        "locationCode": market["location_code"],
        "languageCode": market["language_code"],
    }
    if tool == KEYWORD_TOOL:
        return {
            **common,
            "keywords": [offer.research_keyword],
            "includeMonthlyTrends": True,
            "includeClickstreamData": False,
            "sortBy": "search_volume",
        }
    if tool == SERP_TOOL:
        return {
            "projectId": project_id,
            "queries": [
                {
                    "keyword": offer.research_keyword,
                    "locationCode": market["location_code"],
                    "languageCode": market["language_code"],
                }
            ],
        }
    raise ValidationError(f"unsupported validation tool: {tool}")


def _artifact_path(
    artifact_dir: str | Path,
    offer_id: str,
    project_id: str,
    as_of: date,
) -> Path:
    project_hash = sha256(project_id.encode("utf-8")).hexdigest()[:12]
    return (
        Path(artifact_dir)
        / as_of.isoformat()
        / f"{offer_id}-{project_hash}.json"
    )


def _append_ledger(
    artifact: dict[str, Any],
    tool: str,
    state: str,
    now: Callable[[], datetime] | None,
) -> None:
    artifact.setdefault("ledger", []).append(
        {
            "tool": tool,
            "state": state,
            "at": _timestamp(now),
        }
    )


def validate_offer(
    offer_id: str,
    *,
    config_path: str | Path,
    data_dir: str | Path,
    artifact_dir: str | Path,
    project_id: str | None = None,
    endpoint: str | None = None,
    as_of: date | None = None,
    client: Any | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Validate one approved offer using exactly two scoped OpenSEO tools."""

    config = load_config(config_path)
    offer = next(
        (candidate for candidate in config.offers if candidate.offer_id == offer_id),
        None,
    )
    if offer is None:
        raise ValidationError(f"offer not found: {offer_id}")

    effective_as_of = as_of or date.today()
    evidence = build_offer_evidence(
        offer,
        config,
        effective_as_of,
        data_dir,
    )
    decision = decide_offer(offer, evidence)
    if not (
        decision.readiness_status == TESTABLE_48H
        and decision.bucket == "TEST_NOW"
    ):
        raise ValidationError(
            "offer is not authorized for validation: "
            f"readiness_status={decision.readiness_status}, "
            f"bucket={decision.bucket}"
        )

    market = DATAFORSEO_MARKETS.get(str(offer.market_id).upper())
    if market is None:
        raise ValidationError(
            f"unsupported DataForSEO market: {offer.market_id}"
        )
    effective_project_id = resolve_project_id(project_id)
    if effective_project_id is None:
        raise ValidationError("OPENSEO_PROJECT_ID is required before HTTP")
    effective_endpoint = resolve_mcp_url(endpoint)

    path = _artifact_path(
        artifact_dir,
        offer.offer_id,
        effective_project_id,
        effective_as_of,
    )
    existing = _read_artifact(path)
    if existing is not None and _artifact_matches(
        existing,
        offer,
        decision,
        market,
        effective_project_id,
        effective_as_of,
    ) and _is_complete(existing):
        return existing

    artifact = (
        existing
        if existing is not None
        and _artifact_matches(
            existing,
            offer,
            decision,
            market,
            effective_project_id,
            effective_as_of,
        )
        else _new_artifact(
            offer,
            decision,
            market,
            effective_project_id,
            effective_as_of,
            now,
        )
    )
    artifact["updated_at"] = _timestamp(now)
    tool_client = client if client is not None else OpenSeoClient(effective_endpoint)

    for tool in TOOL_ORDER:
        tool_state = artifact["tools"].setdefault(tool, {"status": "pending"})
        if (
            tool_state.get("status") == "completed"
            and "structured_content" in tool_state
        ):
            continue

        started_at = _timestamp(now)
        tool_state.clear()
        tool_state.update({"status": "running", "started_at": started_at})
        _append_ledger(artifact, tool, "running", now)
        artifact["updated_at"] = started_at
        _write_artifact(path, artifact)

        try:
            structured_content = tool_client.call_tool(
                tool,
                _tool_arguments(tool, offer, effective_project_id, market),
            )
        except Exception as exc:
            tool_state.clear()
            tool_state.update(
                {
                    "status": "failed",
                    "completed_at": _timestamp(now),
                    "error": (
                        str(exc)
                        if isinstance(exc, (ValidationError, OpenSeoError))
                        else f"OpenSEO tool failed for {tool}"
                    ),
                }
            )
            _append_ledger(artifact, tool, "failed", now)
            artifact["updated_at"] = _timestamp(now)
            _write_artifact(path, artifact)
            if isinstance(exc, ValidationError):
                raise
            raise ValidationError(f"OpenSEO tool failed for {tool}") from None

        completed_at = _timestamp(now)
        tool_state.clear()
        tool_state.update(
            {
                "status": "completed",
                "started_at": started_at,
                "completed_at": completed_at,
                "structured_content": _json_safe(structured_content),
            }
        )
        _append_ledger(artifact, tool, "completed", now)
        artifact["updated_at"] = completed_at
        _write_artifact(path, artifact)

    return artifact


def validation_to_json(artifact: Mapping[str, Any]) -> str:
    return json.dumps(_json_safe(artifact), ensure_ascii=False, indent=2)


__all__ = [
    "KEYWORD_TOOL",
    "SERP_TOOL",
    "TOOL_ORDER",
    "VALIDATION_SCHEMA",
    "VALIDATION_VERSION",
    "ValidationError",
    "resolve_mcp_url",
    "resolve_project_id",
    "validate_offer",
    "validation_to_json",
]
