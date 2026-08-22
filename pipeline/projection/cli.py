"""explore.py CLI — idempotent SQLite projection + read-only Datasette.

Import scans ``--data-dir/*.json`` in deterministic sorted order and
imports every *current classified* pack into the disposable SQLite
projection; legacy and non-pack files are skipped read-only and reported.
Serve validates the DB and launches Datasette in immutable mode over the
projection with canned read-only queries.

Exit codes: 0 success (including the documented zero-current-packs
expectation), 2 fail-closed (malformed JSON / current-schema shape
violation / schema mismatch or sqlite/projection failure / unusable DB for
serve), 3 pack content conflict.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

from pipeline.projection.datasette import build_metadata, build_serve_argv
from pipeline.projection.store import (
    PackConflictError,
    ProjectionError,
    ProjectionStore,
    SchemaMismatchError,
    validate_projection_schema,
)
from pipeline.projection.validation import (
    CurrentPackShapeError,
    MalformedPackError,
    classify_pack,
    load_pack_file,
)

EXIT_OK = 0
EXIT_FAIL_CLOSED = 2
EXIT_CONFLICT = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="explore.py",
        description="Projeção SQLite idempotente dos packs classificados + explorer Datasette read-only",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Importar packs atuais classificados para SQLite")
    import_parser.add_argument("--db", default="data/ecom_research.db", help="Base SQLite descartável")
    import_parser.add_argument("--data-dir", default="data", help="Diretório com data/*.json")

    serve_parser = subparsers.add_parser("serve", help="Servir a base via Datasette em modo imutável/read-only")
    serve_parser.add_argument("--db", default="data/ecom_research.db", help="Base SQLite descartável")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind local por omissão")
    serve_parser.add_argument("--port", type=int, default=8001, help="Porta (default 8001)")
    return parser


def cmd_import(args: argparse.Namespace) -> int:
    """Import current classified packs; fail closed with exit code 2 on any
    expected schema/sqlite/projection failure (no traceback), 3 on content
    conflict."""
    try:
        return _run_import(args)
    except PackConflictError as exc:
        print(f"ERRO (conflito): {exc}", file=sys.stderr)
        return EXIT_CONFLICT
    except (SchemaMismatchError, ProjectionError, sqlite3.Error) as exc:
        print(f"ERRO (fail closed): {exc}", file=sys.stderr)
        return EXIT_FAIL_CLOSED


def _run_import(args: argparse.Namespace) -> int:
    files = sorted(glob.glob(os.path.join(args.data_dir, "*.json")))
    imported = 0
    unchanged = 0
    skipped: list[tuple[str, str]] = []
    with ProjectionStore(args.db) as store:
        for path in files:
            try:
                pack = load_pack_file(path)
                classification = classify_pack(pack, path)
            except (MalformedPackError, CurrentPackShapeError) as exc:
                print(f"ERRO (fail closed): {exc}", file=sys.stderr)
                return EXIT_FAIL_CLOSED
            if classification.kind != "current":
                skipped.append((path, classification.reason or classification.kind))
                continue
            outcome = store.import_pack(classification)
            if outcome == "imported":
                imported += 1
            else:
                unchanged += 1
    _print_report(args, files, imported, unchanged, skipped)
    return EXIT_OK


def _print_report(args, files, imported, unchanged, skipped) -> None:
    print(f"Base: {args.db}")
    print(f"Ficheiros: {len(files)} | importados: {imported} | inalterados: {unchanged} | skip read-only: {len(skipped)}")
    for path, reason in skipped:
        print(f"  - skip {os.path.basename(path)}: {reason}")
    if imported == 0 and unchanged == 0:
        print(
            "0 packs atuais classificados — expectativa documentada enquanto o pipeline "
            "não produzir packs com matching_schema_version atual e candidatos classificados."
        )


def cmd_serve(args: argparse.Namespace) -> int:
    _validate_db_for_serve(args.db)
    metadata_path = _write_metadata(args.db)
    argv = build_serve_argv(args.db, host=args.host, port=args.port, metadata_path=metadata_path)
    print("Iniciando Datasette read-only (immutable): " + " ".join(argv))
    return subprocess.run(argv).returncode


def _validate_db_for_serve(db_path: str) -> None:
    """Refuse serve unless the projection DB exists and passes the shared
    schema validation (integrity, tables, composite keys, candidate→pack FK)
    — the same path the store uses on open.  Opens read-only (``mode=ro``)
    so validation never writes to the file.  Raises SystemExit(2) with an
    actionable message on any refusal."""
    if not os.path.exists(db_path) or os.path.getsize(db_path) == 0:
        print(
            f"ERRO: base de dados ausente ou vazia: {db_path}. "
            "Corre primeiro: .venv/bin/python explore.py import",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_FAIL_CLOSED)
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
        try:
            validate_projection_schema(conn, db_path)
        finally:
            conn.close()
    except SchemaMismatchError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_FAIL_CLOSED) from exc
    except sqlite3.Error as exc:
        print(f"ERRO: base de dados ilegível: {db_path} ({exc})", file=sys.stderr)
        raise SystemExit(EXIT_FAIL_CLOSED) from exc


def _write_metadata(db_path: str) -> str:
    metadata = build_metadata(db_path)
    handle, path = tempfile.mkstemp(prefix="ecom-datasette-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump(metadata, out, ensure_ascii=False, indent=2)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "import":
        return cmd_import(args)
    if args.command == "serve":
        return cmd_serve(args)
    parser.print_help()
    return EXIT_FAIL_CLOSED


if __name__ == "__main__":
    raise SystemExit(main())
