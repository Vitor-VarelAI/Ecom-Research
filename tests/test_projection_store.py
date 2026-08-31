"""SQLite projection store: schema, idempotency, conflict and rollback.

Covers the transactional contract: one transaction per pack (pack row +
all candidate rows), foreign keys enabled, schema version pinned via
``PRAGMA user_version``, identical reimport as a no-op, same identity with
different content raising a specific conflict error, candidate insertion
rollback, verbatim field projection (no sanitizers, no derived decisions)
and unchanged source bytes.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from pipeline.projection.store import (
    PackConflictError,
    ProjectionError,
    ProjectionStore,
    SchemaMismatchError,
    SCHEMA_VERSION,
    validate_projection_schema,
)
from pipeline.projection.validation import (
    Classification,
    canonical_json,
    classify_pack,
    load_pack_file,
    pack_fingerprint,
)
from tests.fixtures.projection_helpers import candidate, current_pack, legacy_pack

import pipeline.projection.store as store_module


def _import(tmp_path, db_name="projection.db", data_name="pack.json", pack=None, data_dir=None):
    db_path = tmp_path / db_name
    data_dir = data_dir or tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    path = data_dir / data_name
    path.write_text(json.dumps(pack if pack is not None else current_pack(), ensure_ascii=False), encoding="utf-8")
    classification = classify_pack(load_pack_file(str(path)), str(path))
    store = ProjectionStore(str(db_path))
    outcome = store.import_pack(classification)
    return store, str(db_path), classification


def _query(store, sql, params=()):
    return store._conn.execute(sql, params).fetchall()


class TestSchema:
    def test_schema_version_pinned_on_fresh_db(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        store = ProjectionStore(str(db_path))
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {row[0] for row in _query(store, "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"pack", "candidate"} <= tables
        store.close()

    def test_existing_db_with_wrong_user_version_instructs_rebuild(self, tmp_path):
        db_path = tmp_path / "wrong.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 7}")
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatchError) as exc:
            ProjectionStore(str(db_path))
        assert "user_version" in str(exc.value)
        assert "apaga" in str(exc.value).lower() or "delete" in str(exc.value).lower()

    def test_existing_foreign_db_with_user_version_zero_instructs_rebuild(self, tmp_path):
        """A non-empty DB with user_version 0 and tables is not ours."""
        db_path = tmp_path / "foreign.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.execute("INSERT INTO other VALUES (1)")
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatchError):
            ProjectionStore(str(db_path))

    def test_same_version_foreign_db_rejected(self, tmp_path):
        """A foreign DB carrying user_version == SCHEMA_VERSION is still
        rejected: structure, composite keys and the FK are validated, not
        just the version pragma."""
        db_path = tmp_path / "foreign_same_version.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("CREATE TABLE pack (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("CREATE TABLE candidate (id INTEGER PRIMARY KEY, pack_id INTEGER)")
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatchError) as exc:
            ProjectionStore(str(db_path))
        assert "apaga" in str(exc.value).lower()
        assert "estrutura" in str(exc.value).lower() or "chave" in str(exc.value).lower()

    def test_same_version_corrupt_db_rejected(self, tmp_path):
        """A corrupted DB file that still carries the right user_version is
        rejected by integrity validation before any query."""
        db_path = tmp_path / "corrupt.db"
        ProjectionStore(str(db_path)).close()
        size = os.path.getsize(db_path)
        with open(db_path, "r+b") as handle:
            handle.seek(size // 2)
            handle.write(b"\x00" * (size - size // 2))
        with pytest.raises(SchemaMismatchError) as exc:
            ProjectionStore(str(db_path))
        assert "apaga" in str(exc.value).lower()

    @pytest.mark.parametrize(
        ("old", "new", "reason"),
        [
            ("run_id TEXT NOT NULL", "run_id INTEGER NOT NULL", "tipo"),
            ("pack_date TEXT NOT NULL", "pack_date TEXT", "nulabilidade"),
            ("ON DELETE CASCADE", "ON DELETE SET NULL", "foreign key"),
        ],
    )
    def test_same_version_schema_contract_rejects_incompatible_columns_and_fk(
        self, tmp_path, old, new, reason
    ):
        db_path = tmp_path / "incompatible.db"
        schema = store_module._SCHEMA.replace(old, new, 1)
        assert schema != store_module._SCHEMA
        conn = sqlite3.connect(str(db_path))
        conn.executescript(schema)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        conn.close()
        with pytest.raises(SchemaMismatchError) as exc:
            ProjectionStore(str(db_path))
        assert reason in str(exc.value).lower()

    def test_shared_validator_passes_fresh_db(self, tmp_path):
        """The shared schema validator accepts exactly what the store
        creates — guards against drift between _SCHEMA and the validator."""
        db_path = tmp_path / "ok.db"
        store = ProjectionStore(str(db_path))
        validate_projection_schema(store._conn, str(db_path))  # no raise
        store.close()


class TestImport:
    def test_pack_and_candidate_rows_persisted(self, tmp_path):
        store, db_path, classification = _import(tmp_path)
        assert store.count_packs() == 1
        assert store.count_candidates() == 1
        pack_row = _query(store, "SELECT run_id, product_id, keyword, pack_date, score_label, score_value, "
                                 "candidate_count, matched_count, needs_review_count, rejected_count, total_ads, "
                                 "matching_schema_version FROM pack")[0]
        assert pack_row[0] == classification.run_id
        assert pack_row[1] == classification.product_id
        assert pack_row[2] == "mochila personalizada"
        assert pack_row[3] == "2026-08-16"
        assert pack_row[4] == "OBSERVAR"
        assert pack_row[5] == 55.0
        assert pack_row[6:] == (1, 1, 0, 0, 1, 1)
        store.close()

    def test_verbatim_field_projection_no_sanitizer_no_derivation(self, tmp_path):
        """Persisted values are copied verbatim from the already-sanitized
        pack: URLs with query strings stay untouched, counts and verdicts
        come from the pack, never recomputed."""
        ad = candidate(landing_url="https://shop.example/produto?utm_source=x&ref=token")
        pack = current_pack(facebook_ads=current_pack()["facebook_ads"] | {"candidates": [ad]})
        store, db_path, _ = _import(tmp_path, pack=pack)
        cand = _query(store, "SELECT landing_url, link, copy, advertiser, days_active, match_label, "
                             "match_confidence, position FROM candidate")[0]
        assert cand[0] == "https://shop.example/produto?utm_source=x&ref=token"
        assert cand[1] == ad["link"]
        assert cand[2] == ad["copy"]
        assert cand[3] == "Loja"
        assert cand[4] == 30
        assert cand[5] == "matched"
        assert cand[6] == "high"
        assert cand[7] == 0
        store.close()

    def test_json_valued_fields_stored_as_canonical_json(self, tmp_path):
        store, db_path, _ = _import(tmp_path)
        cand = _query(store, "SELECT publisher_platforms_json, countries_json, match_evidence_json, "
                             "match_reasons_json, candidate_json FROM candidate")[0]
        assert cand[0] == canonical_json(["facebook"])
        assert cand[1] == canonical_json(["PT"])
        assert "mochilas personalizadas premium" in cand[2]
        assert "head_proven" in cand[3]
        assert "match" in json.loads(cand[4])
        pack_json = _query(store, "SELECT pack_json, fingerprint, source_states_json, scoring_config_json "
                                  "FROM pack")[0]
        assert pack_json[1] == pack_fingerprint(load_pack_file(next(tmp_path.glob("data/*.json"))))
        assert "apify_ads" in pack_json[2]
        assert "weights" in pack_json[3]
        store.close()

    def test_candidate_key_uses_list_position_when_ad_archive_id_absent(self, tmp_path):
        """Two candidates without ad_archive_id are keyed by position."""
        no_id = candidate(ad_id="")
        pack = current_pack(facebook_ads=current_pack()["facebook_ads"] | {"candidates": [no_id, no_id]})
        store, db_path, _ = _import(tmp_path, pack=pack)
        rows = _query(store, "SELECT position, ad_archive_id FROM candidate ORDER BY position")
        assert [(row[0], row[1]) for row in rows] == [(0, ""), (1, "")]
        assert store.count_candidates() == 2
        store.close()

    def test_multiple_candidates_all_imported_in_one_transaction(self, tmp_path):
        ads = [candidate(f"a{i}", advertiser=f"Loja {i}", match_label=("matched" if i % 2 == 0 else "needs_review")) for i in range(5)]
        pack = current_pack(facebook_ads=current_pack()["facebook_ads"] | {"candidates": ads})
        store, db_path, _ = _import(tmp_path, pack=pack)
        assert store.count_packs() == 1
        assert store.count_candidates() == 5
        store.close()


class TestIdempotencyAndConflict:
    def test_same_run_and_product_imports_once_per_market(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pt = current_pack(
            market_id="PT",
            market={"market_id": "PT", "currency": "EUR"},
        )
        gb = current_pack(
            keyword="scented candles",
            research_keyword="scented candles",
            market_id="GB",
            market={"market_id": "GB", "currency": "GBP"},
        )
        pt_path = data_dir / "pt.json"
        gb_path = data_dir / "gb.json"
        pt_path.write_text(json.dumps(pt), encoding="utf-8")
        gb_path.write_text(json.dumps(gb), encoding="utf-8")
        pt_classification = classify_pack(pt, str(pt_path))
        gb_classification = classify_pack(gb, str(gb_path))

        store = ProjectionStore(str(tmp_path / "projection.db"))
        assert store.import_pack(pt_classification) == "imported"
        assert store.import_pack(gb_classification) == "imported"

        assert _query(
            store,
            "SELECT market_id FROM pack ORDER BY market_id",
        ) == [("GB",), ("PT",)]
        assert store.count_packs() == 2
        assert store.count_candidates() == 2
        store.close()

    def test_identical_reimport_is_noop(self, tmp_path):
        store, db_path, classification = _import(tmp_path)
        imported_at = _query(store, "SELECT imported_at FROM pack")[0][0]
        outcome = store.import_pack(classification)
        assert outcome == "unchanged"
        assert store.count_packs() == 1
        assert store.count_candidates() == 1
        assert _query(store, "SELECT imported_at FROM pack")[0][0] == imported_at
        store.close()

    def test_reimport_from_disk_is_noop(self, tmp_path):
        store, db_path, classification = _import(tmp_path)
        reclassified = classify_pack(load_pack_file(next(tmp_path.glob("data/*.json"))), "data/x.json")
        assert reclassified.fingerprint == classification.fingerprint
        assert store.import_pack(reclassified) == "unchanged"
        store.close()

    def test_same_identity_different_content_raises_conflict_and_leaves_rows(self, tmp_path):
        store, db_path, classification = _import(tmp_path)
        changed = current_pack(score={"score": 90, "label": "AVANÇAR", "breakdown": {}})
        changed_path = tmp_path / "data" / "changed.json"
        changed_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
        reclassified = classify_pack(load_pack_file(str(changed_path)), str(changed_path))
        assert reclassified.run_id == classification.run_id
        assert reclassified.fingerprint != classification.fingerprint
        with pytest.raises(PackConflictError) as exc:
            store.import_pack(reclassified)
        assert "conflito" in str(exc.value).lower() or "conflict" in str(exc.value).lower()
        # Existing rows unchanged.
        assert store.count_packs() == 1
        assert store.count_candidates() == 1
        assert _query(store, "SELECT fingerprint FROM pack")[0][0] == classification.fingerprint
        store.close()


class TestScoreProjection:
    @pytest.mark.parametrize(
        "bad_score", [True, "SCORE_VALUE_SENTINEL", float("nan"), float("inf"), -float("inf"), 10**100]
    )
    def test_invalid_score_never_silently_nulled(self, tmp_path, bad_score):
        """An invalid persisted score raises ProjectionError instead of being
        converted to NULL — even when classification is forged to bypass the
        shape gate."""
        db_path = tmp_path / "score.db"
        store = ProjectionStore(str(db_path))
        pack = current_pack(score={"score": bad_score, "label": "OBSERVAR", "breakdown": {}})
        classification = Classification(
            kind="current",
            source_path="forged.json",
            pack=pack,
            fingerprint="f" * 64,
            run_id=pack["run_id"],
            product_id=pack["product_id"],
        )
        with pytest.raises(ProjectionError) as exc:
            store.import_pack(classification)
        message = str(exc.value)
        assert "NULL" in message
        assert "SCORE_VALUE_SENTINEL" not in message
        assert store.count_packs() == 0  # nothing persisted
        store.close()


class TestRollback:
    def test_candidate_insertion_failure_rolls_back_pack_row(self, tmp_path, monkeypatch):
        """If any candidate insert fails mid-transaction, the whole pack
        import rolls back — no orphan pack row."""
        store, db_path, _ = _import(tmp_path)  # build a valid DB
        store.close()

        def boom(conn, row):
            raise RuntimeError("forced candidate insert failure")

        monkeypatch.setattr(store_module, "_insert_candidate_row", boom)
        store = ProjectionStore(db_path)
        fresh = current_pack(run_id="otherrun", product_id="otherproduct")
        fresh_path = tmp_path / "data" / "fresh.json"
        fresh_path.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
        classification = classify_pack(load_pack_file(str(fresh_path)), str(fresh_path))
        with pytest.raises(RuntimeError, match="forced"):
            store.import_pack(classification)
        assert store.count_packs() == 1  # only the original setup pack
        assert store.count_candidates() == 1
        store.close()

    def test_legacy_pack_never_touches_the_db(self, tmp_path):
        store, db_path, _ = _import(tmp_path)
        legacy_path = tmp_path / "data" / "legacy.json"
        legacy_path.write_text(json.dumps(legacy_pack(), ensure_ascii=False), encoding="utf-8")
        classification = classify_pack(load_pack_file(str(legacy_path)), str(legacy_path))
        assert classification.kind == "legacy"
        assert store.count_packs() == 1  # only the current pack from setup
        store.close()


class TestSourceBytes:
    def test_source_pack_bytes_unchanged_by_import(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir(exist_ok=True)
        path = data_dir / "pack.json"
        path.write_text(json.dumps(current_pack(), ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        store, db_path, _ = _import(tmp_path, data_dir=data_dir)
        assert path.read_bytes() == before
        store.close()
