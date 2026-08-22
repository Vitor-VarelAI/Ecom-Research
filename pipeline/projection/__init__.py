"""SQLite projection of current classified research packs + Datasette explorer.

Packs (``data/*.json``) remain canonical evidence; this package builds a
disposable, idempotent read-only projection keyed by ``(run_id,
product_id)`` with a SHA-256 fingerprint over the canonical pack JSON.
"""

from pipeline.projection.validation import (
    Classification,
    CurrentPackShapeError,
    MalformedPackError,
    classify_pack,
    load_pack_file,
    pack_fingerprint,
)
from pipeline.projection.store import (
    PackConflictError,
    ProjectionError,
    ProjectionStore,
    SchemaMismatchError,
    SCHEMA_VERSION,
)

__all__ = [
    "Classification",
    "CurrentPackShapeError",
    "MalformedPackError",
    "PackConflictError",
    "ProjectionError",
    "ProjectionStore",
    "SchemaMismatchError",
    "SCHEMA_VERSION",
    "classify_pack",
    "load_pack_file",
    "pack_fingerprint",
]
