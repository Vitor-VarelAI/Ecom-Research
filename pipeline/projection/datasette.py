"""Datasette metadata and read-only serve command construction.

The metadata JSON carries read-only canned queries over the projection DB
(the latest packs, needs-review candidates and matched advertisers).  The
serve argv is built by a pure function so tests can assert immutable mode,
localhost binding and metadata wiring without launching a server.
"""

from __future__ import annotations

import os
import sys

# Canned read-only queries for the projection tables.  Queries never mutate:
# they only SELECT over pack/candidate.
CANNED_QUERIES = {
    "latest_packs": {
        "title": "Últimos packs classificados",
        "sql": (
            "SELECT run_id, product_id, keyword, pack_date, score_label, score_value, "
            "candidate_count, matched_count, total_ads, imported_at "
            "FROM pack ORDER BY pack_date DESC, imported_at DESC LIMIT 50"
        ),
    },
    "needs_review_candidates": {
        "title": "Candidatos needs_review",
        "sql": (
            "SELECT c.run_id, c.product_id, c.position, c.advertiser, c.page_id, "
            "c.copy, c.match_head_token, c.match_confidence, p.pack_date "
            "FROM candidate c JOIN pack p ON p.run_id = c.run_id AND p.product_id = c.product_id "
            "WHERE c.match_label = 'needs_review' ORDER BY p.pack_date DESC, c.position LIMIT 200"
        ),
    },
    "matched_advertisers": {
        "title": "Anunciantes matched",
        "sql": (
            "SELECT advertiser, page_id, COUNT(*) AS matched_ads, COUNT(DISTINCT product_id) AS products "
            "FROM candidate WHERE match_label = 'matched' "
            "GROUP BY advertiser, page_id ORDER BY matched_ads DESC, advertiser LIMIT 100"
        ),
    },
}


def database_name(db_path: str) -> str:
    """Datasette names a database by its filename without the .db suffix."""
    return os.path.splitext(os.path.basename(db_path))[0]


def build_metadata(db_path: str) -> dict:
    """Metadata JSON keyed by the Datasette database name."""
    return {"databases": {database_name(db_path): {"queries": CANNED_QUERIES}}}


def build_serve_argv(
    db_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
    metadata_path: str,
) -> list[str]:
    """Subprocess argv for a read-only Datasette server over the projection DB.

    Immutable mode guarantees the projection DB is never modified by the
    explorer.  Localhost binding is the default.
    """
    return [
        sys.executable,
        "-m",
        "datasette",
        "serve",
        "--immutable",
        db_path,
        "--host",
        host,
        "--port",
        str(port),
        "--metadata",
        metadata_path,
    ]
