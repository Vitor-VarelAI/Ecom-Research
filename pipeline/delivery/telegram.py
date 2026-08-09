"""Send-only Telegram delivery with a complete stdout fallback.

Compatibility: ``send(text) -> bool`` keeps the original contract — plain
text, chunks of at most 4000 chars, payload limited to ``chat_id``/``text``/
``disable_web_page_preview``, stdout fallback with the full digest on any
failure, and no token ever written to logs.

Hardening API: ``deliver(text, state_id, state_dir, on_chunk_sent)`` adds
durable delivery state under ``<data>/digests/<state_id>.json``.  The state
records the digest text and hash, per-chunk sent progress and a sanitized
status (missing credentials, timeout, HTTP/JSON/API failure, success,
partial).  On a rerun, chunks already recorded as sent are never re-sent;
only unsent chunks are retried.  Each successful chunk is persisted
immediately (atomic write) before the ``on_chunk_sent`` callback runs.

The state file never contains the bot token or any token-bearing URL.
Acknowledged residual risk: a crash between Telegram accepting a chunk and
the local persistence write can cause that single chunk to be sent twice on
the next attempt.  That window is unavoidable without server-side dedupe and
is accepted.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

import requests

from pipeline.run_state import atomic_write_json, sha256_hex, utc_now_iso

logger = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4000
REQUEST_TIMEOUT = (5, 15)

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DEFAULT_DIGEST_DIR = os.path.join(_REPO_ROOT, "data", "digests")


def _chunks(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
        if remaining.startswith("\n"):
            remaining = remaining[1:]
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def _stdout_fallback(text: str) -> None:
    print(text)


def _state_path(state_id: str, state_dir: str) -> str:
    return os.path.join(state_dir, f"{state_id}.json")


def _valid_state(data: dict[str, Any], text: str) -> bool:
    """Deep validation of one delivery state against the freshly computed
    chunks for ``text``: the hash and text must match, the chunk list must be
    exactly the recomputed chunks (same count, indices and texts), every chunk
    must carry a bool ``sent`` and an ``sent_at`` that is None or a string,
    and a chunk recorded as sent must say when it was sent."""
    if data.get("hash") != sha256_hex(text):
        return False
    if data.get("text") != text:
        return False
    chunks = data.get("chunks")
    if not isinstance(chunks, list):
        return False
    expected = _chunks(text)
    if len(chunks) != len(expected):
        return False
    for index, (chunk, chunk_text) in enumerate(zip(chunks, expected)):
        if not isinstance(chunk, dict):
            return False
        if chunk.get("index") != index:
            return False
        if chunk.get("text") != chunk_text:
            return False
        if not isinstance(chunk.get("sent"), bool):
            return False
        sent_at = chunk.get("sent_at")
        if sent_at is not None and not isinstance(sent_at, str):
            return False
        if chunk.get("sent") and not sent_at:
            return False
    return True


def _load_state(state_id: str, state_dir: str, text: str) -> tuple[str, dict[str, Any] | None]:
    """Classify the persisted delivery state for ``text``.

    Returns ``(status, data)``:

    * ``("absent", None)`` — no state file yet; a fresh state may be created.
    * ``("valid", data)`` — the file exists, hashes to ``text`` and its chunk
      list matches the freshly computed chunks, indices, texts, ``sent``
      flags and timestamps.
    * ``("corrupt", data)`` — the file exists but is unreadable, is not a
      dict, belongs to different content, or has an inconsistent chunk list
      (``data`` is the parsed dict when available).  Delivery must fail
      closed: send nothing and never overwrite the suspect state, otherwise a
      chunk accepted by Telegram under the old content could be accepted
      again under new content.
    """
    path = _state_path(state_id, state_dir)
    if not os.path.exists(path):
        return "absent", None
    try:
        with open(path, "r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError):
        logger.error("Telegram: estado de entrega ilegível em %s (fail closed)", path)
        return "corrupt", None
    if not isinstance(data, dict) or not _valid_state(data, text):
        logger.error("Telegram: estado de entrega inválido ou de outro conteúdo em %s (fail closed)", path)
        return "corrupt", data
    return "valid", data


def _fresh_state(text: str) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "text": text,
        "hash": sha256_hex(text),
        "chunks": [
            {"index": index, "text": chunk, "sent": False, "sent_at": None}
            for index, chunk in enumerate(_chunks(text))
        ],
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }


def _persist_state(state: dict[str, Any], state_id: str, state_dir: str) -> None:
    if not state_id:
        return
    state["updated_at"] = utc_now_iso()
    try:
        atomic_write_json(_state_path(state_id, state_dir), state)
    except OSError as exc:
        logger.warning("Telegram: estado de entrega não persistido — %s", exc)


def _send_chunk(chunk: str, payload_base: dict[str, Any], url: str) -> str | None:
    """Send one chunk; return a sanitized failure kind or None on success.

    Arbitrary unexpected exceptions (not just requests errors) are reduced
    to the sanitized ``unexpected_error`` kind: exception text and secrets
    are never propagated, logged, or persisted.  Callers fall back to stdout
    and persist the sanitized kind, so a delivery bug can never block the
    digest or leak internals into ``data/digests/``.
    """
    payload = {**payload_base, "text": chunk}
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return "http_error"
        try:
            body = response.json()
        except (ValueError, TypeError):
            return "json_invalid"
        if not isinstance(body, dict) or body.get("ok") is not True:
            return "api_not_ok"
        return None
    except requests.Timeout:
        return "timeout"
    except requests.RequestException:
        return "network_error"
    except Exception as exc:
        logger.error("Telegram: erro inesperado ao enviar chunk (tipo=%s; sanitizado)", type(exc).__name__)
        return "unexpected_error"


def deliver(
    text: str,
    state_id: str | None = None,
    state_dir: str | None = None,
    on_chunk_sent: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """
    Send a plain-text digest with durable, resumable delivery state.

    Returns a report dict: ``{"delivered": bool, "status": str,
    "chunks_total": int, "chunks_sent": int, "errors": [sanitized kinds]}``.

    When ``state_id`` is given, progress is persisted under ``state_dir``
    (default ``<repo>/data/digests``) and resumed on later calls: already-sent
    chunks are never re-sent.  When ``state_id`` is None the call is
    stateless, matching the legacy behaviour.
    """
    if state_dir is None:
        state_dir = DEFAULT_DIGEST_DIR
    state = None
    if state_id:
        status, state = _load_state(state_id, state_dir, text)
        if status == "corrupt":
            # Fail closed: an existing state that is corrupt or belongs to
            # different content is never trusted and never overwritten.
            logger.error("Telegram: estado de entrega corrompido ou de outro conteúdo; entrega recusada")
            return {
                "delivered": False,
                "status": "state_corrupt",
                "chunks_total": 0,
                "chunks_sent": 0,
                "errors": ["state_corrupt"],
            }
        if status == "absent":
            state = _fresh_state(text)
    else:
        state = _fresh_state(text)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.warning("Telegram não configurado; digest impresso no stdout")
        state["status"] = "missing_config"
        _persist_state(state, state_id, state_dir)
        return _report(state, delivered=False)

    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"  # token-bearing, never persisted
    payload_base: dict[str, Any] = {
        "chat_id": chat_id,
        "disable_web_page_preview": True,
    }

    errors: list[str] = []
    for chunk in state["chunks"]:
        if chunk.get("sent"):
            continue  # never re-send a chunk already recorded as sent
        failure = _send_chunk(chunk["text"], payload_base, url)
        if failure is not None:
            errors.append(failure)
            break  # one failure aborts this invocation; state resumes next time
        chunk["sent"] = True
        chunk["sent_at"] = utc_now_iso()
        _persist_state(state, state_id, state_dir)
        if on_chunk_sent is not None:
            on_chunk_sent(chunk, _report(state, delivered=False))

    sent = sum(1 for chunk in state["chunks"] if chunk.get("sent"))
    total = len(state["chunks"])
    if errors:
        state["status"] = "partial" if sent else "error"
        state["errors"] = errors
    else:
        state["status"] = "success"
        state.pop("errors", None)
    _persist_state(state, state_id, state_dir)
    return _report(state, delivered=not errors)


def _report(state: dict[str, Any], *, delivered: bool) -> dict[str, Any]:
    chunks = state.get("chunks", [])
    return {
        "delivered": delivered,
        "status": state.get("status", "pending"),
        "chunks_total": len(chunks),
        "chunks_sent": sum(1 for chunk in chunks if chunk.get("sent")),
        "errors": state.get("errors", []),
    }


def send(text: str) -> bool:
    """Send a plain-text digest; return false and print the full digest on failure."""
    report = deliver(text, state_id=None)
    if not report["delivered"]:
        _stdout_fallback(text)
    return report["delivered"]
