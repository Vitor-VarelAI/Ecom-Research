"""
Stdlib-only run identity, atomic writes and run manifests.

This module is deliberately tiny and dependency-free.  It provides:

* ``compute_run_id`` — a deterministic identity for one logical action.
  Inputs are the logical date, the resolved action mode, the ordered
  keywords, the CSV content hash (when the mode consumes CSV) and the
  scoring version.  Credentials, wall-clock timestamps and ``--digest``
  are intentionally excluded so that a rerun of the same action reuses
  the persisted artifacts instead of spending paid sources again.
* ``atomic_write_json`` / ``atomic_write_text`` — temp-file + fsync +
  ``os.replace`` so readers never observe a half-written file.
* ``RunStore`` — persistence of run manifests under ``data/runs/`` with a
  per-run Linux file lock (fail-closed: an unusable lock raises instead of
  proceeding unlocked).  A manifest with ``state == "completed"`` is the
  idempotency guard: repeated runs of the same logical action reuse its
  digest and results and make no adapter calls.

Source-level exactly-once is NOT guaranteed across crashes: if the process
dies after a paid call but before the manifest is written, the next run
executes the research again.  That is a documented, accepted residual risk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

logger = logging.getLogger(__name__)

try:  # Linux/macOS; guarded so the module imports on any platform.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (second precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(data: str | bytes) -> str:
    """Hex SHA-256 of a string or bytes payload."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str) -> str | None:
    """Hex SHA-256 of a file's bytes, or None when unreadable."""
    try:
        with open(path, "rb") as source:
            return hashlib.sha256(source.read()).hexdigest()
    except OSError:
        return None


def _atomic_write(path: str, payload: str, *, as_text: bool) -> None:
    """Atomically replace ``path`` and durably journal its directory entry.

    Fsyncing only the temporary file does not guarantee that the subsequent
    rename survives a power loss.  The parent directory is therefore fsynced
    after ``os.replace``; a failure propagates so delivery fails closed rather
    than treating a write-ahead marker as durable when it may not be.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp_path, path)
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data: dict[str, Any]) -> None:
    """Atomically write a JSON object to path."""
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", as_text=True)


def atomic_write_text(path: str, text: str) -> None:
    """Atomically write plain text to path."""
    _atomic_write(path, text, as_text=True)


def compute_run_id(
    *,
    logical_date: str,
    mode: str,
    keywords: list[str],
    csv_sha256: str | None,
    scoring_version: str,
) -> str:
    """
    Deterministic identity for one logical action.

    The same inputs always produce the same run id; ``--digest``, clock
    timestamps and credentials never influence it.  A repeated completed
    run therefore resolves to the same manifest and is short-circuited.
    """
    canonical = json.dumps(
        {
            "date": logical_date,
            "mode": mode,
            "keywords": list(keywords),
            "csv_sha256": csv_sha256,
            "scoring_version": scoring_version,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RunLock:
    """Fail-closed advisory Linux file lock.

    The lock is a blocking exclusive ``flock`` on a dedicated lock file; the
    kernel releases it automatically when the process exits (including on a
    crash), so a second process executing the same run blocks until the
    first finishes.  The lock NEVER proceeds unlocked: if fcntl is
    unavailable, the lock file cannot be created/opened, or the lock cannot
    be acquired, ``__enter__`` raises a clear RuntimeError.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: Any = None

    def __enter__(self) -> "RunLock":
        if fcntl is None:
            raise RuntimeError(
                "run_state: fcntl indisponível neste sistema; recusar prosseguir sem lock (fail closed)"
            )
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fd = open(self.path, "a+", encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"run_state: não foi possível abrir o lock {self.path}: {exc}"
            ) from exc
        try:
            # Blocking exclusive lock; released on process exit by the kernel.
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except OSError as exc:
            try:
                self._fd.close()
            except OSError:
                pass
            self._fd = None
            raise RuntimeError(
                f"run_state: não foi possível adquirir o lock {self.path}: {exc}"
            ) from exc
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fd.close()
        except OSError:
            pass
        self._fd = None


class RunStore:
    """Persistent run manifests under ``<data_dir>/runs/``."""

    def __init__(self, data_dir: str) -> None:
        self.runs_dir = os.path.join(data_dir, "runs")

    def _manifest_path(self, run_id: str) -> str:
        return os.path.join(self.runs_dir, f"{run_id}.json")

    @staticmethod
    def _valid_basic_manifest(data: dict[str, Any]) -> bool:
        """Every manifest — even a partial running/failed/checkpoint one — must
        carry a sane identity and shape so selectors can never trip on garbage."""
        if not isinstance(data.get("state"), str) or not data["state"]:
            return False
        if not isinstance(data.get("mode"), str) or not data["mode"]:
            return False
        if not isinstance(data.get("logical_date"), str) or not data["logical_date"]:
            return False
        if not isinstance(data.get("keywords"), list) or not all(
            isinstance(keyword, str) and keyword for keyword in data["keywords"]
        ):
            return False
        if not isinstance(data.get("results"), list):
            return False
        return True

    @staticmethod
    def _valid_completed_manifest(data: dict[str, Any]) -> bool:
        """A completed manifest must be deeply usable by ``_reuse_run``:
        results as dicts, a digest with string text and dict delivery, and
        artifacts as dicts of lists.  Invalid nested state is corrupt/absent."""
        if not all(isinstance(result, dict) for result in data.get("results", [])):
            return False
        digest = data.get("digest")
        if not isinstance(digest, dict):
            return False
        if not isinstance(digest.get("text"), str):
            return False
        if not isinstance(digest.get("delivery"), dict):
            return False
        artifacts = data.get("artifacts")
        if not isinstance(artifacts, dict):
            return False
        return all(isinstance(artifacts.get(key), list) for key in ("md", "json"))

    def load(self, run_id: str) -> dict[str, Any] | None:
        """Load a manifest, or None when absent/corrupt/not a matching dict.

        Only dict manifests whose ``run_id`` equals the requested id are
        returned: lists, scalars and manifests for a different run are
        treated as corrupt/absent so they can never reach ``.get()``.  A
        ``completed`` manifest is validated deeply (results/digest/artifacts)
        because ``_reuse_run`` relies on those nested fields; running/failed
        checkpoint manifests only need the basic identity fields.
        """
        path = self._manifest_path(run_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as source:
                data = json.load(source)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("run_state: manifesto %s corrompido; tratado como ausente", run_id)
            return None
        if not isinstance(data, dict) or data.get("run_id") != run_id:
            logger.warning(
                "run_state: manifesto %s inválido (tipo ou run_id divergente); tratado como ausente",
                run_id,
            )
            return None
        if not self._valid_basic_manifest(data):
            logger.warning("run_state: manifesto %s com campos básicos inválidos; tratado como ausente", run_id)
            return None
        if data.get("state") == "completed" and not self._valid_completed_manifest(data):
            logger.warning(
                "run_state: manifesto %s 'completed' com estado aninhado inválido; tratado como ausente",
                run_id,
            )
            return None
        return data

    def save(self, run_id: str, manifest: dict[str, Any]) -> None:
        atomic_write_json(self._manifest_path(run_id), manifest)

    @contextmanager
    def lock(self, run_id: str) -> Iterator[RunLock]:
        """Context manager acquiring the per-run lock."""
        lock = RunLock(os.path.join(self.runs_dir, f"{run_id}.lock"))
        with lock:
            yield lock
