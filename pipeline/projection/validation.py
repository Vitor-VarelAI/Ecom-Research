"""Pack classification and canonical fingerprint for the SQLite projection.

A pack file is classified into exactly one of:

* ``current`` — a complete current classified pack: non-empty top-level
  ``run_id`` and ``product_id``, ``artifact_complete == true``, a strict
  ``YYYY-MM-DD`` date and advisory score label, ``facebook_ads.
  matching_schema_version`` equal to the live matcher schema version (an
  actual int — ``true`` and ``1.0`` do not count) and a ``candidates`` list
  whose entries are classified and carry the canonical match/evidence shape
  the matcher produces.  Only these are importable.
* ``legacy`` — a run-scoped research pack that predates classification (no
  matching-schema marker, or an older schema version).  Skipped read-only
  and reported clearly; never rewritten.
* ``not_a_pack`` — valid JSON that is not a research pack at all (e.g.
  ``data/quota.json``).  Skipped read-only and reported.

Fail-closed contract: malformed JSON (or JSON that is not an object, or
JSON carrying the non-standard ``NaN``/``Infinity``/``-Infinity``
constants) raises ``MalformedPackError``; a pack that *claims* the current
matching schema but violates the required current-pack shape raises
``CurrentPackShapeError`` with an actionable message and field/position
context — it is never mislabeled legacy.

The canonical fingerprint is a SHA-256 over the full parsed pack JSON
serialized with ``sort_keys``, compact separators and ``ensure_ascii=False``
(strict standard JSON: non-finite numbers raise instead of being emitted as
``NaN``/``Infinity``) — the same canonical encoding used for JSON-valued
columns.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pipeline.matching.product_ads import MATCHING_SCHEMA_VERSION

# Advisory decision labels the pipeline may persist in ``score.label``.
VALID_SCORE_LABELS = frozenset({"AVANÇAR", "OBSERVAR", "DESCARTAR", "DADOS INSUFICIENTES"})
# Verdict labels a classified candidate may carry in ``match.label``.
MATCH_LABELS = frozenset({"matched", "needs_review", "rejected"})
# Strict canonical pack date format — zero-padded, nothing else.
_STRICT_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PackReadError(Exception):
    """Base for pack classification failures (fail closed)."""


class MalformedPackError(PackReadError):
    """The file is not valid JSON, or the JSON is not a research-pack object."""


class CurrentPackShapeError(PackReadError):
    """A pack claims the current matching schema but violates its shape."""


def canonical_json(value: Any) -> str:
    """Deterministic canonical JSON text (sorted keys, compact, utf-8).

    Strict standard JSON: ``allow_nan=False``, so a non-finite float raises
    ``ValueError`` instead of being emitted as ``NaN``/``Infinity``.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def pack_fingerprint(pack: dict[str, Any]) -> str:
    """SHA-256 over the canonical full parsed pack JSON."""
    return hashlib.sha256(canonical_json(pack).encode("utf-8")).hexdigest()


def _reject_non_standard_constant(value: str) -> Any:
    """``json.load`` hook: the non-standard ``NaN``/``Infinity``/``-Infinity``
    literals are not valid JSON and fail closed at parse time."""
    raise ValueError(f"constante JSON não padrão: {value}")


def load_pack_file(path: str) -> dict[str, Any]:
    """Read and parse one data/*.json file, fail closed on malformed JSON.

    ``NaN``/``Infinity``/``-Infinity`` literals are rejected at parse time
    (strict standard JSON) and surface as ``MalformedPackError``.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle, parse_constant=_reject_non_standard_constant)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise MalformedPackError(f"{path}: JSON inválido ({exc})") from exc
    if not isinstance(data, dict):
        raise MalformedPackError(f"{path}: não é um objeto JSON de research pack")
    return data


def _facebook_ads(pack: dict[str, Any]) -> dict[str, Any]:
    return pack["facebook_ads"] if isinstance(pack.get("facebook_ads"), dict) else {}


def _claims_current_schema(pack: dict[str, Any]) -> bool:
    """The pack claims the current matching schema iff the classified
    ``facebook_ads`` payload carries the live schema version as an actual
    int (``true`` and ``1.0`` do not count).  The additive top-level
    ``matching_schema_version`` marker alone is not the claim — legacy
    packs may carry it too."""
    version = _facebook_ads(pack).get("matching_schema_version")
    return isinstance(version, int) and not isinstance(version, bool) and version == MATCHING_SCHEMA_VERSION


def _valid_iso_date(value: Any) -> bool:
    """Strict canonical ``YYYY-MM-DD``: zero-padded, calendar-valid and
    round-trip stable (``2026-1-5``, ``16-08-2026`` and ``2026-08-16T...``
    are all rejected)."""
    if not isinstance(value, str) or not _STRICT_ISO_DATE.match(value):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return parsed.strftime("%Y-%m-%d") == value


def _validate_candidate_shape(candidate: dict[str, Any], position: int, problems: list[str]) -> None:
    """Projected-field checks for one classified candidate, with position
    context.  Mirrors the canonical shapes the matcher persists: the
    verdict/evidence fields are required, optional scalars and lists are
    type-checked when present.  Nothing is reclassified or sanitized here —
    an unsupported type must fail closed before it can reach sqlite as NULL
    or a binding error."""
    def problem(field: str, detail: str) -> None:
        problems.append(f"candidato {position}: {field} {detail}")

    for key in ("ad_archive_id", "advertiser", "page_id", "link", "landing_url", "copy"):
        value = candidate.get(key)
        if not isinstance(value, str):
            problem(key, f"esperado texto, recebido {type(value).__name__}: {value!r}")
    days_active = candidate.get("days_active")
    if isinstance(days_active, bool) or not isinstance(days_active, (int, float)) or not math.isfinite(days_active):
        problem("days_active", f"esperado número finito não booleano, recebido {days_active!r}")
    reported_active_time = candidate.get("reported_active_time")
    if reported_active_time is not None and not isinstance(reported_active_time, str):
        problem(
            "reported_active_time",
            f"esperado texto, recebido {type(reported_active_time).__name__}: {reported_active_time!r}",
        )
    for key in ("publisher_platforms", "countries"):
        value = candidate.get(key)
        if value is not None and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
            problem(key, f"esperado lista de textos, recebido {value!r}")
    evidence = candidate.get("match_evidence")
    if isinstance(evidence, dict):
        for key in ("commercial", "geo", "weak"):
            value = evidence.get(key)
            if value is not None and not isinstance(value, str):
                problem(f"match_evidence.{key}", f"esperado texto, recebido {type(value).__name__}: {value!r}")
    elif not isinstance(evidence, str):
        problem("match_evidence", f"esperado objeto ou texto, recebido {type(evidence).__name__}: {evidence!r}")

    match = candidate.get("match")
    if not isinstance(match, dict):
        problem("match", f"esperado objeto de classificação, recebido {type(match).__name__}: {match!r}")
        return
    if "label" not in match or match["label"] not in MATCH_LABELS:
        problem("match.label", f"label de classificação inválido: {match.get('label')!r}")
    if "confidence" not in match or not isinstance(match["confidence"], str):
        problem("match.confidence", f"esperado texto, recebido {match.get('confidence')!r}")
    if (
        "reasons" not in match
        or not isinstance(match["reasons"], list)
        or not all(isinstance(item, str) for item in match["reasons"])
    ):
        problem("match.reasons", f"esperado lista de textos, recebido {match.get('reasons')!r}")
    if (
        "matcher_version" not in match
        or not isinstance(match["matcher_version"], str)
        or not match["matcher_version"]
    ):
        problem("match.matcher_version", f"esperado texto não vazio, recebido {match.get('matcher_version')!r}")
    head_token = match.get("head_token")
    if "head_token" not in match or (head_token is not None and not isinstance(head_token, str)):
        problem("match.head_token", f"esperado texto ou null, recebido {head_token!r}")
    for key in ("geo_tokens", "missing_tokens", "weak_only_tokens"):
        value = match.get(key)
        if key not in match or not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            problem(f"match.{key}", f"esperado lista de textos, recebido {value!r}")


def _validate_current_shape(pack: dict[str, Any]) -> list[str]:
    """Every required current-pack shape check, returning a list of
    human-readable problems (empty list = shape OK).  Checks cover every
    field the projection persists, with field/position context, so an
    unsupported type can never reach sqlite as NULL or a binding error."""
    problems: list[str] = []
    run_id = pack.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        problems.append("run_id ausente ou vazio")
    product_id = pack.get("product_id")
    if not isinstance(product_id, str) or not product_id.strip():
        problems.append("product_id ausente ou vazio")
    if pack.get("artifact_complete") is not True:
        problems.append("artifact_complete != true")
    if not _valid_iso_date(pack.get("date")):
        problems.append(f"date inválida: {pack.get('date')!r} (esperado YYYY-MM-DD estrito)")
    score = pack.get("score")
    label = score.get("label") if isinstance(score, dict) else None
    if label not in VALID_SCORE_LABELS:
        problems.append(f"score.label inválido: {label!r}")
    if isinstance(score, dict) and "score" in score:
        score_value = score["score"]
        if (
            isinstance(score_value, bool)
            or not isinstance(score_value, (int, float))
            or not math.isfinite(score_value)
        ):
            problems.append(f"score.score inválido: {score_value!r} (esperado número finito não booleano)")
    for key in (
        "keyword", "product_name", "research_keyword", "mode", "scoring_version",
        "matcher_version", "generated_at", "completed_at",
    ):
        value = pack.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{key} esperado texto, recebido {type(value).__name__}: {value!r}")
    source_states = pack.get("source_states")
    if source_states is not None and not isinstance(source_states, dict):
        problems.append(f"source_states esperado objeto, recebido {type(source_states).__name__}: {source_states!r}")
    scoring_config = pack.get("scoring_config")
    if scoring_config is not None and not isinstance(scoring_config, dict):
        problems.append(f"scoring_config esperado objeto, recebido {type(scoring_config).__name__}: {scoring_config!r}")
    ads = _facebook_ads(pack)
    ads_version = ads.get("matching_schema_version")
    if not isinstance(ads_version, int) or isinstance(ads_version, bool) or ads_version != MATCHING_SCHEMA_VERSION:
        problems.append("facebook_ads.matching_schema_version != atual (int exato)")
    for key in ("candidate_count", "matched_count", "needs_review_count", "rejected_count", "total_ads"):
        value = ads.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            problems.append(f"facebook_ads.{key} esperado inteiro, recebido {value!r}")
    candidates = ads.get("candidates")
    if not isinstance(candidates, list):
        problems.append("facebook_ads.candidates ausente ou não é lista")
    else:
        for position, candidate in enumerate(candidates):
            if not isinstance(candidate, dict):
                problems.append(f"candidato {position} não é objeto")
                continue
            _validate_candidate_shape(candidate, position, problems)
    return problems


@dataclass(frozen=True)
class Classification:
    """Result of classifying one pack file."""

    kind: str  # "current" | "legacy" | "not_a_pack"
    source_path: str
    reason: str | None = None
    pack: dict[str, Any] | None = None
    fingerprint: str | None = None
    run_id: str | None = None
    product_id: str | None = None


def classify_pack(pack: dict[str, Any], source_path: str) -> Classification:
    """Classify one parsed pack dict (fail closed for malformed/claims-
    current-but-invalid)."""
    if _claims_current_schema(pack):
        problems = _validate_current_shape(pack)
        if problems:
            raise CurrentPackShapeError(
                f"{source_path}: pack afirma matching_schema_version={MATCHING_SCHEMA_VERSION} "
                f"mas viola a shape atual: {'; '.join(problems)}"
            )
        try:
            fingerprint = pack_fingerprint(pack)
        except ValueError as exc:
            raise CurrentPackShapeError(
                f"{source_path}: pack contém números não finitos (NaN/Infinity) que o JSON "
                f"canónico estrito rejeita: {exc}"
            ) from exc
        return Classification(
            kind="current",
            source_path=source_path,
            pack=pack,
            fingerprint=fingerprint,
            run_id=pack["run_id"],
            product_id=pack["product_id"],
        )
    if _looks_like_pack(pack):
        version = _facebook_ads(pack).get("matching_schema_version")
        reason = (
            f"matching_schema_version={version!r} (esperado {MATCHING_SCHEMA_VERSION})"
            if version is not None
            else "sem matching_schema_version (pack pré-classificação)"
        )
        return Classification(kind="legacy", source_path=source_path, reason=reason)
    return Classification(
        kind="not_a_pack",
        source_path=source_path,
        reason="não é um research pack",
    )


def _looks_like_pack(pack: dict[str, Any]) -> bool:
    """A research pack carries at least one run-scoped pack marker; a
    non-pack JSON file (quota, etc.) carries none of them."""
    return any(key in pack for key in ("run_id", "artifact_complete", "facebook_ads", "score", "date", "keyword"))
