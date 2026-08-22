"""Idempotent SQLite projection of current classified research packs.

The store is a disposable read-only projection over ``data/*.json`` packs —
the packs stay canonical evidence and are never rewritten.  Import is
transactional per pack: the pack row and all candidate rows commit or roll
back together, foreign keys are enabled and the schema version is pinned
via ``PRAGMA user_version``.  A schema mismatch (a foreign or stale DB)
fails closed with an instruction to delete/rebuild the disposable file.

Identity is ``(run_id, product_id)``.  Reimporting identical content is a
no-op; the same identity with different content raises ``PackConflictError``
and leaves existing rows untouched.  Candidate rows are keyed by
``(run_id, product_id, position)`` because ``ad_archive_id`` may be absent.

Projection copies already-sanitized persisted values verbatim: no URL
sanitizer is called and no score or match decision is derived.  JSON-valued
fields are stored as canonical JSON text (sorted keys, compact separators,
``ensure_ascii=False`` — the same encoding as the pack fingerprint).
"""

from __future__ import annotations

import math
import os
import sqlite3
from typing import Any

from pipeline.projection.validation import Classification, canonical_json
from pipeline.run_state import utc_now_iso

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pack (
    run_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source_path TEXT NOT NULL,
    keyword TEXT,
    product_name TEXT,
    research_keyword TEXT,
    mode TEXT,
    pack_date TEXT NOT NULL,
    score_label TEXT NOT NULL,
    score_value REAL,
    scoring_version TEXT,
    matcher_version TEXT,
    matching_schema_version INTEGER NOT NULL,
    generated_at TEXT,
    completed_at TEXT,
    candidate_count INTEGER,
    matched_count INTEGER,
    needs_review_count INTEGER,
    rejected_count INTEGER,
    total_ads INTEGER,
    source_states_json TEXT,
    scoring_config_json TEXT,
    pack_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (run_id, product_id)
);

CREATE TABLE IF NOT EXISTS candidate (
    run_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    ad_archive_id TEXT,
    advertiser TEXT,
    page_id TEXT,
    days_active REAL,
    reported_active_time TEXT,
    link TEXT,
    landing_url TEXT,
    copy TEXT,
    publisher_platforms_json TEXT,
    countries_json TEXT,
    match_evidence_json TEXT,
    match_label TEXT,
    match_confidence TEXT,
    match_reasons_json TEXT,
    match_head_token TEXT,
    match_json TEXT,
    candidate_json TEXT NOT NULL,
    PRIMARY KEY (run_id, product_id, position),
    FOREIGN KEY (run_id, product_id) REFERENCES pack(run_id, product_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_candidate_match_label ON candidate(match_label);
CREATE INDEX IF NOT EXISTS idx_candidate_advertiser ON candidate(advertiser);
CREATE INDEX IF NOT EXISTS idx_pack_date ON pack(pack_date);
"""


class ProjectionError(Exception):
    """Base for projection failures."""


class SchemaMismatchError(ProjectionError):
    """The DB file exists but is not this projection schema version."""


class PackConflictError(ProjectionError):
    """Same (run_id, product_id) already imported with different content."""


# Expected structural contract for ``validate_projection_schema`` (mirrors
# ``_SCHEMA`` above — the projection's one source of truth).
_PACK_PRIMARY_KEY = ("run_id", "product_id")
_CANDIDATE_PRIMARY_KEY = ("run_id", "product_id", "position")
_CANDIDATE_FOREIGN_KEY = (("run_id", "run_id"), ("product_id", "product_id"))


def validate_projection_schema(conn: sqlite3.Connection, db_path: str | None = None) -> None:
    """Fail closed unless the open connection is exactly this projection schema.

    The single shared schema-validation path: ``ProjectionStore`` runs it on
    every open (existing DBs and freshly created ones alike, so a foreign or
    corrupt DB that happens to carry ``user_version == SCHEMA_VERSION`` is
    rejected before any query) and ``explore.py serve`` runs it before
    launching Datasette.  Verifies SQLite integrity, the expected
    ``pack``/``candidate`` tables and columns, the composite primary keys and
    the candidate→pack foreign key.  Raises ``SchemaMismatchError`` with an
    actionable delete/re-import message.  No migrations are ever applied.
    """
    reasons: list[str] = []
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        reasons.append(f"user_version={version!r} (esperado {SCHEMA_VERSION})")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        reasons.append(f"integridade SQLite falhou: {integrity}")
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        reasons.append(f"foreign key violada em {len(violations)} linha(s)")
    _validate_table_shape(conn, "pack", _PACK_COLUMNS, _PACK_PRIMARY_KEY, reasons)
    _validate_table_shape(conn, "candidate", _CANDIDATE_COLUMNS, _CANDIDATE_PRIMARY_KEY, reasons)
    _validate_foreign_key(conn, "candidate", _CANDIDATE_FOREIGN_KEY, "pack", reasons)
    if reasons:
        raise SchemaMismatchError(
            f"{db_path or 'base de dados'}: não é a projeção esperada ({'; '.join(reasons)}). "
            "Esta base é descartável — apaga-a e corre 'explore.py import' para a reconstruir."
        )


def _validate_table_shape(
    conn: sqlite3.Connection,
    name: str,
    expected_columns: tuple[str, ...],
    primary_key: tuple[str, ...],
    reasons: list[str],
) -> None:
    info = conn.execute(f"PRAGMA table_info({name})").fetchall()
    if not info:
        reasons.append(f"tabela {name} ausente")
        return
    columns = {row[1]: row[5] for row in info}
    if set(columns) != set(expected_columns):
        missing = sorted(set(expected_columns) - set(columns))
        extra = sorted(set(columns) - set(expected_columns))
        details: list[str] = []
        if missing:
            details.append(f"faltam colunas: {', '.join(missing)}")
        if extra:
            details.append(f"colunas inesperadas: {', '.join(extra)}")
        reasons.append(f"tabela {name} com estrutura inesperada ({'; '.join(details)})")
        return
    actual_pk = {column: order for column, order in columns.items() if order}
    expected_pk = {column: index for index, column in enumerate(primary_key, start=1)}
    if actual_pk != expected_pk:
        reasons.append(
            f"chave primária de {name} inesperada: {actual_pk or 'sem PK'} (esperado {list(primary_key)})"
        )


def _validate_foreign_key(
    conn: sqlite3.Connection,
    table: str,
    pairs: tuple[tuple[str, str], ...],
    parent: str,
    reasons: list[str],
) -> None:
    rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    found = {(row[3], row[4]) for row in rows if row[2] == parent}
    if set(pairs) != found:
        reasons.append(
            f"foreign key de {table} para {parent} inesperada: {sorted(found) or 'sem FK'} "
            f"(esperado {list(pairs)})"
        )


class ProjectionStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = self._open(db_path)

    # ── connection / schema ──────────────────────────────────────────────

    def _open(self, db_path: str) -> sqlite3.Connection:
        fresh = not os.path.exists(db_path) or os.path.getsize(db_path) == 0
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.Error as exc:
            raise SchemaMismatchError(
                f"{db_path}: base de dados ilegível ou corrompida ({exc}). "
                "Esta base é descartável — apaga-a e corre 'explore.py import' para a reconstruir."
            ) from exc
        try:
            conn.isolation_level = None  # explicit transactions only
            conn.execute("PRAGMA foreign_keys = ON")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if fresh and version == 0:
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
            # Shared validation path: an existing DB must be exactly this
            # projection (integrity, tables, composite PKs, FK) — a foreign
            # or corrupt DB with the same user_version is rejected here,
            # before any query.  Freshly created DBs are validated too, so
            # the invariant holds for every connection this store returns.
            validate_projection_schema(conn, db_path)
        except sqlite3.Error as exc:
            conn.close()
            raise SchemaMismatchError(
                f"{db_path}: base de dados ilegível ou corrompida ({exc}). "
                "Esta base é descartável — apaga-a e corre 'explore.py import' para a reconstruir."
            ) from exc
        except BaseException:
            conn.close()
            raise
        return conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ProjectionStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── queries ──────────────────────────────────────────────────────────

    def fingerprint_for(self, run_id: str, product_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT fingerprint FROM pack WHERE run_id=? AND product_id=?",
            (run_id, product_id),
        ).fetchone()
        return row[0] if row else None

    def count_packs(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM pack").fetchone()[0]

    def count_candidates(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]

    # ── import ───────────────────────────────────────────────────────────

    def import_pack(self, classification: Classification) -> str:
        """Import one current classified pack in a single transaction.

        Returns ``"imported"`` or ``"unchanged"`` (identical reimport).
        Raises ``PackConflictError`` on same identity with different
        content — existing rows are left untouched.
        """
        if classification.kind != "current":
            raise ProjectionError(f"import_pack só aceita packs atuais, recebeu {classification.kind}")
        assert classification.pack is not None and classification.fingerprint is not None
        run_id = classification.run_id
        product_id = classification.product_id
        existing = self.fingerprint_for(run_id, product_id)
        if existing is not None:
            if existing == classification.fingerprint:
                return "unchanged"
            raise PackConflictError(
                f"conflito: {classification.source_path}: (run_id={run_id}, product_id={product_id}) já importado "
                f"com conteúdo diferente (fingerprint {existing[:12]}... ≠ {classification.fingerprint[:12]}...). "
                "Nenhuma linha foi alterada; apaga a base descartável se quiseres reimportar o conteúdo novo."
            )
        pack_row = _project_pack_row(classification.pack, classification.fingerprint, classification.source_path)
        candidate_rows = [
            _project_candidate_row(classification.pack, candidate, position)
            for position, candidate in enumerate(classification.pack["facebook_ads"].get("candidates", []))
        ]
        conn = self._conn
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO pack (run_id, product_id, fingerprint, source_path, keyword, product_name, "
                "research_keyword, mode, pack_date, score_label, score_value, scoring_version, matcher_version, "
                "matching_schema_version, generated_at, completed_at, candidate_count, matched_count, "
                "needs_review_count, rejected_count, total_ads, source_states_json, scoring_config_json, "
                "pack_json, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?)",
                tuple(pack_row[column] for column in _PACK_COLUMNS),
            )
            for row in candidate_rows:
                _insert_candidate_row(conn, row)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        return "imported"


_PACK_COLUMNS = (
    "run_id", "product_id", "fingerprint", "source_path", "keyword", "product_name", "research_keyword",
    "mode", "pack_date", "score_label", "score_value", "scoring_version", "matcher_version",
    "matching_schema_version", "generated_at", "completed_at", "candidate_count", "matched_count",
    "needs_review_count", "rejected_count", "total_ads", "source_states_json", "scoring_config_json",
    "pack_json", "imported_at",
)


def _project_pack_row(pack: dict[str, Any], fingerprint: str, source_path: str) -> dict[str, Any]:
    """Verbatim pack/scoring/provenance/count projection (no derivation)."""
    ads = pack.get("facebook_ads") or {}
    score = pack.get("score") or {}
    score_value = score.get("score")
    if score_value is not None and (
        isinstance(score_value, bool)
        or not isinstance(score_value, (int, float))
        or not math.isfinite(score_value)
    ):
        # Never silently convert an invalid persisted score to NULL: shape
        # validation rejects these packs at classify time, and the store
        # refuses a forged classification that bypasses it.
        raise ProjectionError(
            f"score.score não pode ser persistido como NULL: {score_value!r} "
            "(validação de shape devia ter rejeitado este pack)"
        )
    return {
        "run_id": pack["run_id"],
        "product_id": pack["product_id"],
        "fingerprint": fingerprint,
        "source_path": source_path,
        "keyword": pack.get("keyword"),
        "product_name": pack.get("product_name"),
        "research_keyword": pack.get("research_keyword"),
        "mode": pack.get("mode"),
        "pack_date": pack.get("date"),
        "score_label": score.get("label"),
        "score_value": score_value,
        "scoring_version": pack.get("scoring_version"),
        "matcher_version": pack.get("matcher_version"),
        "matching_schema_version": ads.get("matching_schema_version"),
        "generated_at": pack.get("generated_at"),
        "completed_at": pack.get("completed_at"),
        "candidate_count": ads.get("candidate_count"),
        "matched_count": ads.get("matched_count"),
        "needs_review_count": ads.get("needs_review_count"),
        "rejected_count": ads.get("rejected_count"),
        "total_ads": ads.get("total_ads"),
        "source_states_json": _canonical_or_none(pack.get("source_states")),
        "scoring_config_json": _canonical_or_none(pack.get("scoring_config")),
        "pack_json": canonical_json(pack),
        "imported_at": utc_now_iso(),
    }


_CANDIDATE_COLUMNS = (
    "run_id", "product_id", "position", "ad_archive_id", "advertiser", "page_id", "days_active",
    "reported_active_time", "link", "landing_url", "copy", "publisher_platforms_json", "countries_json",
    "match_evidence_json", "match_label", "match_confidence", "match_reasons_json", "match_head_token",
    "match_json", "candidate_json",
)


def _project_candidate_row(pack: dict[str, Any], candidate: dict[str, Any], position: int) -> dict[str, Any]:
    """Verbatim candidate/match/evidence projection (no sanitizer, no
    derived verdicts — match fields are copied from the persisted pack)."""
    match = candidate.get("match") or {}
    return {
        "run_id": pack["run_id"],
        "product_id": pack["product_id"],
        "position": position,
        "ad_archive_id": candidate.get("ad_archive_id"),
        "advertiser": candidate.get("advertiser"),
        "page_id": candidate.get("page_id"),
        "days_active": candidate.get("days_active"),
        "reported_active_time": candidate.get("reported_active_time"),
        "link": candidate.get("link"),
        "landing_url": candidate.get("landing_url"),
        "copy": candidate.get("copy"),
        "publisher_platforms_json": _canonical_or_none(candidate.get("publisher_platforms")),
        "countries_json": _canonical_or_none(candidate.get("countries")),
        "match_evidence_json": _canonical_or_none(candidate.get("match_evidence")),
        "match_label": match.get("label"),
        "match_confidence": match.get("confidence"),
        "match_reasons_json": _canonical_or_none(match.get("reasons")),
        "match_head_token": match.get("head_token"),
        "match_json": _canonical_or_none(match) if match else None,
        "candidate_json": canonical_json(candidate),
    }


def _insert_candidate_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO candidate (run_id, product_id, position, ad_archive_id, advertiser, page_id, days_active, "
        "reported_active_time, link, landing_url, copy, publisher_platforms_json, countries_json, "
        "match_evidence_json, match_label, match_confidence, match_reasons_json, match_head_token, match_json, "
        "candidate_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(row[column] for column in _CANDIDATE_COLUMNS),
    )


def _canonical_or_none(value: Any) -> str | None:
    return canonical_json(value) if value is not None else None
