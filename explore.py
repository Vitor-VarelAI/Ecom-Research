#!/usr/bin/env python3
"""ECOM-RESEARCH explorer — idempotent SQLite projection + read-only Datasette.

Packs (``data/*.json``) remain canonical evidence and are never rewritten.
``import`` builds a disposable SQLite projection of current classified
packs; ``serve`` opens it with Datasette in immutable/read-only mode.

Uso:
    .venv/bin/python explore.py import [--db data/ecom_research.db] [--data-dir data]
    .venv/bin/python explore.py serve [--db data/ecom_research.db] [--host 127.0.0.1] [--port 8001]
"""

from pipeline.projection.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
