"""explore.py CLI: import report/conflict, immutable serve argv, DB refusal.

The serve command construction is tested without launching a server: the
argv builder is pure, and the missing-DB/schema-mismatch refusal paths
raise before any subprocess call.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from pipeline.projection.cli import cmd_import, cmd_serve, main
from pipeline.projection.datasette import build_metadata, build_serve_argv
from pipeline.projection.store import ProjectionStore, SCHEMA_VERSION
from pipeline.projection.validation import classify_pack, load_pack_file
from tests.fixtures.projection_helpers import current_pack, legacy_pack, not_a_pack


def _write(data_dir, name, data):
    path = data_dir / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _make_args(command, **kwargs):
    defaults = {"db": None, "data_dir": None, "host": "127.0.0.1", "port": 8001}
    defaults.update(kwargs)
    return type("Args", (), defaults)()


class TestImportCli:
    def test_imports_current_packs_and_reports(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "a.json", current_pack())
        _write(data_dir, "b.json", current_pack(run_id="run2", product_id="p2", date="2026-08-17"))
        db = tmp_path / "proj.db"
        code = cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir)))
        assert code == 0
        out = capsys.readouterr().out
        assert "importados: 2" in out
        with ProjectionStore(str(db)) as store:
            assert store.count_packs() == 2
            assert store.count_candidates() == 2

    def test_legacy_and_non_pack_skipped_read_only(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "legacy.json", legacy_pack())
        _write(data_dir, "quota.json", not_a_pack())
        db = tmp_path / "proj.db"
        code = cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir)))
        assert code == 0
        out = capsys.readouterr().out
        assert "importados: 0" in out
        assert "skip read-only: 2" in out
        assert "matching_schema_version" in out  # legacy reason reported
        with ProjectionStore(str(db)) as store:
            assert store.count_packs() == 0

    def test_zero_current_packs_is_success(self, tmp_path, capsys):
        """The documented expectation: importing before any classified pack
        exists yields zero imports and exits 0, not an error."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "legacy.json", legacy_pack())
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 0
        assert "importados: 0" in capsys.readouterr().out

    def test_empty_data_dir_imports_nothing(self, tmp_path, capsys):
        data_dir = tmp_path / "empty"
        data_dir.mkdir()
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 0
        assert "importados: 0" in capsys.readouterr().out

    def test_default_import_creates_missing_data_directory(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert not (tmp_path / "data").exists()

        assert main(["import"]) == 0

        assert (tmp_path / "data" / "ecom_research.db").is_file()
        assert "importados: 0" in capsys.readouterr().out

    def test_conflicting_reimport_exits_nonzero_and_keeps_rows(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        path = _write(data_dir, "a.json", current_pack())
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 0
        # Same identity, different content.
        path.write_text(json.dumps(current_pack(score={"score": 99, "label": "AVANÇAR", "breakdown": {}}), ensure_ascii=False), encoding="utf-8")
        code = cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir)))
        assert code != 0
        err = capsys.readouterr().err
        assert "conflito" in err.lower()
        with ProjectionStore(str(db)) as store:
            assert store.count_packs() == 1  # unchanged
            row = store._conn.execute("SELECT score_label FROM pack").fetchone()
            assert row[0] == "OBSERVAR"  # original content preserved

    def test_malformed_json_fails_closed(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "broken.json").write_text("{not json", encoding="utf-8")
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "JSON inválido" in capsys.readouterr().err

    def test_claims_current_but_invalid_shape_fails_closed(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        bad = current_pack(artifact_complete=False)
        _write(data_dir, "bad.json", bad)
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "shape" in capsys.readouterr().err

    def test_same_version_foreign_db_rejected(self, tmp_path, capsys):
        """A foreign DB carrying user_version == SCHEMA_VERSION is refused at
        import with exit 2 — structure validation, not just the pragma."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "a.json", current_pack())
        db = tmp_path / "foreign.db"
        conn = sqlite3.connect(str(db))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("CREATE TABLE pack (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE candidate (id INTEGER PRIMARY KEY, pack_id INTEGER)")
        conn.commit()
        conn.close()
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "apaga" in capsys.readouterr().err.lower()

    def test_stale_schema_import_exits_2_without_traceback(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "a.json", current_pack())
        db = tmp_path / "stale.db"
        conn = sqlite3.connect(str(db))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 7}")
        conn.commit()
        conn.close()
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "user_version" in capsys.readouterr().err

    def test_unreadable_db_fails_closed(self, tmp_path, capsys):
        """A file that is not a SQLite database exits 2 with a message, no
        traceback."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "a.json", current_pack())
        db = tmp_path / "notadb.db"
        db.write_text("this is not a sqlite database at all", encoding="utf-8")
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "fail closed" in capsys.readouterr().err

    def test_invalid_score_fails_closed_through_cli(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "a.json", current_pack(score={"score": "alto", "label": "OBSERVAR", "breakdown": {}}))
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "score" in capsys.readouterr().err.lower()

    def test_malformed_candidate_evidence_fails_closed_through_cli(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pack = current_pack()
        pack["facebook_ads"]["candidates"][0]["match_evidence"] = {"commercial": ["x"]}
        _write(data_dir, "a.json", pack)
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "candidato 0" in capsys.readouterr().err

    def test_missing_candidate_evidence_fails_closed_through_cli(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pack = current_pack()
        pack["facebook_ads"]["candidates"][0] = {"ad_archive_id": "x", "match": {"label": "matched"}}
        _write(data_dir, "a.json", pack)
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "candidato 0" in capsys.readouterr().err

    def test_unsupported_projected_scalar_fails_closed_through_cli(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pack = current_pack()
        pack["facebook_ads"]["candidates"][0]["landing_url"] = {"url": "https://shop.example"}
        _write(data_dir, "a.json", pack)
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "candidato 0" in capsys.readouterr().err

    def test_non_canonical_date_fails_closed_through_cli(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "a.json", current_pack(date="2026-1-5"))
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "date" in capsys.readouterr().err.lower()

    def test_non_finite_json_fails_closed_through_cli(self, tmp_path, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "nan.json").write_text('{"run_id": NaN}', encoding="utf-8")
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 2
        assert "JSON inválido" in capsys.readouterr().err

    def test_optional_candidate_fields_compatible_with_product_ads_output(self, tmp_path, capsys):
        """Valid optional candidate fields keep working end to end through
        the CLI: empty platforms/countries lists and the evidence dict."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pack = current_pack()
        pack["facebook_ads"]["candidates"][0]["publisher_platforms"] = []
        pack["facebook_ads"]["candidates"][0]["countries"] = []
        _write(data_dir, "a.json", pack)
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 0
        assert "importados: 1" in capsys.readouterr().out

    def test_deterministic_discovery_order(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        _write(data_dir, "zz.json", current_pack(run_id="zz", product_id="pz", date="2026-08-18"))
        _write(data_dir, "aa.json", current_pack(run_id="aa", product_id="pa", date="2026-08-18"))
        db = tmp_path / "proj.db"
        assert cmd_import(_make_args("import", db=str(db), data_dir=str(data_dir))) == 0
        with ProjectionStore(str(db)) as store:
            source_paths = [row[0] for row in store._conn.execute("SELECT source_path FROM pack ORDER BY run_id")]
        assert source_paths == [str(data_dir / "aa.json"), str(data_dir / "zz.json")]


class TestServeCli:
    def test_missing_db_refused_before_launch(self, tmp_path, monkeypatch):
        db = tmp_path / "missing.db"

        def no_launch(argv):
            raise AssertionError("serve must not launch subprocess for a missing DB")

        monkeypatch.setattr("pipeline.projection.cli.subprocess.run", no_launch)
        with pytest.raises(SystemExit) as exc:
            main(["serve", "--db", str(db)])
        assert exc.value.code == 2

    def test_schema_mismatch_refused_before_launch(self, tmp_path, monkeypatch):
        db = tmp_path / "wrong.db"
        conn = sqlite3.connect(str(db))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 3}")
        conn.commit()
        conn.close()

        def no_launch(argv):
            raise AssertionError("serve must not launch subprocess for a schema-mismatched DB")

        monkeypatch.setattr("pipeline.projection.cli.subprocess.run", no_launch)
        with pytest.raises(SystemExit) as exc:
            main(["serve", "--db", str(db)])
        assert exc.value.code == 2

    def test_same_version_foreign_db_refused_for_serve(self, tmp_path, monkeypatch):
        """Serve refuses a foreign DB carrying user_version == SCHEMA_VERSION:
        the shared validator checks structure/keys, not just the pragma."""
        db = tmp_path / "foreign.db"
        conn = sqlite3.connect(str(db))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("CREATE TABLE pack (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE candidate (id INTEGER PRIMARY KEY, pack_id INTEGER)")
        conn.commit()
        conn.close()

        def no_launch(argv):
            raise AssertionError("serve must not launch subprocess for a foreign same-version DB")

        monkeypatch.setattr("pipeline.projection.cli.subprocess.run", no_launch)
        with pytest.raises(SystemExit) as exc:
            main(["serve", "--db", str(db)])
        assert exc.value.code == 2

    def test_corrupt_db_refused_for_serve(self, tmp_path, monkeypatch):
        db = tmp_path / "corrupt.db"
        ProjectionStore(str(db)).close()
        size = os.path.getsize(db)
        with open(db, "r+b") as handle:
            handle.seek(size // 2)
            handle.write(b"\x00" * (size - size // 2))

        def no_launch(argv):
            raise AssertionError("serve must not launch subprocess for a corrupt DB")

        monkeypatch.setattr("pipeline.projection.cli.subprocess.run", no_launch)
        with pytest.raises(SystemExit) as exc:
            main(["serve", "--db", str(db)])
        assert exc.value.code == 2

    def test_immutable_serve_argv(self, tmp_path):
        db = tmp_path / "proj.db"
        ProjectionStore(str(db)).close()
        meta = tmp_path / "meta.json"
        argv = build_serve_argv(str(db), host="127.0.0.1", port=8001, metadata_path=str(meta))
        assert argv[0].endswith("python")
        assert "-m" in argv and "datasette" in argv
        assert "serve" in argv
        assert "--immutable" in argv
        assert argv[argv.index("--immutable") + 1] == str(db)
        assert argv[argv.index("--host") + 1] == "127.0.0.1"
        assert argv[argv.index("--port") + 1] == "8001"
        assert argv[argv.index("--metadata") + 1] == str(meta)
        # localhost by default
        argv_default = build_serve_argv(str(db), metadata_path=str(meta))
        assert argv_default[argv_default.index("--host") + 1] == "127.0.0.1"

    def test_metadata_has_canned_queries_for_the_db_name(self, tmp_path):
        db = tmp_path / "ecom_research.db"
        ProjectionStore(str(db)).close()
        metadata = build_metadata(str(db))
        db_meta = metadata["databases"]["ecom_research"]
        assert set(db_meta["queries"]) == {"latest_packs", "needs_review_candidates", "matched_advertisers"}
        assert "FROM pack" in db_meta["queries"]["latest_packs"]["sql"]
        assert "needs_review" in db_meta["queries"]["needs_review_candidates"]["sql"]
        assert "matched" in db_meta["queries"]["matched_advertisers"]["sql"]

    def test_cmd_serve_invokes_immutable_subprocess(self, tmp_path, monkeypatch):
        db = tmp_path / "proj.db"
        ProjectionStore(str(db)).close()
        launched = {}

        def fake_run(argv):
            launched["argv"] = argv
            return type("Proc", (), {"returncode": 0})()

        monkeypatch.setattr("pipeline.projection.cli.subprocess.run", fake_run)
        assert cmd_serve(_make_args("serve", db=str(db))) == 0
        assert "--immutable" in launched["argv"]
        assert str(db) in launched["argv"]

    def test_serve_validation_is_read_only(self, tmp_path, monkeypatch):
        """The shared schema validation for serve opens mode=ro — the DB
        file bytes are never modified by validating it."""
        db = tmp_path / "proj.db"
        ProjectionStore(str(db)).close()
        before = db.read_bytes()
        launched = {}

        def fake_run(argv):
            launched["argv"] = argv
            return type("Proc", (), {"returncode": 0})()

        monkeypatch.setattr("pipeline.projection.cli.subprocess.run", fake_run)
        assert cmd_serve(_make_args("serve", db=str(db))) == 0
        assert db.read_bytes() == before
