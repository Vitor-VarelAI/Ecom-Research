"""Send-only Telegram delivery with a complete stdout fallback.

Compatibility: ``send(text) -> bool`` keeps the original contract — plain
text, chunks of at most 4000 chars, payload limited to ``chat_id``/``text``/
``disable_web_page_preview``, stdout fallback with the full digest on any
failure, and no token ever written to logs.

Hardening API: ``deliver(text, state_id, state_dir, on_chunk_sent)`` adds
durable delivery state under ``<data>/digests/<state_id>.json``.  The state
records the digest text and hash, per-chunk sent progress and a sanitized
status (missing credentials, timeout, HTTP/JSON/API failure, success,
partial, state_persist_failed, state_ambiguous).  On a rerun, chunks already
recorded as sent are never re-sent; only unsent chunks are retried.

Write-ahead in-flight protocol (stateful delivery):

1. The fresh pending state is persisted before any network send.
2. Before each chunk is sent, an ``in_flight`` marker is set and durably
   persisted; if that write fails, Telegram is never called and the attempt
   reports ``state_persist_failed``.
3. After remote acceptance the chunk is marked sent, the marker cleared and
   the state persisted.  If that write fails, the last durable file still
   shows the chunk in-flight and the attempt fails closed as
   ``state_ambiguous``: a rerun that finds an unsent in-flight chunk refuses
   to resend until an operator resolves the marker.
4. Definite rejections (HTTP 4xx, or a valid JSON ``ok != true``) clear the
   marker and persist before a retry is allowed; if the clear cannot be
   persisted the attempt also fails closed as ``state_ambiguous``.
5. Ambiguous outcomes (timeout, transport errors, unexpected exceptions,
   HTTP >=500, or a 200 whose body is not valid JSON) keep the durable
   in_flight marker and fail closed as ``state_ambiguous``: the remote may
   have accepted, so a rerun refuses to resend until an operator decides.

The state file never contains the bot token, chat id, or any token-bearing
URL.  Exactly-once across a crash is still impossible: a crash between
Telegram accepting a chunk and the post-accept persistence write leaves the
outcome indeterminate, and the protocol's answer is to refuse the resend
(``state_ambiguous``) rather than risk a duplicate — an operator decides.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable

import requests

from pipeline.run_state import atomic_write_json, sha256_hex, utc_now_iso

logger = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4000
REQUEST_TIMEOUT = (5, 15)

# state_id is used as a filename; only run ids (SHA-256 hex digests) and the
# documented activation ids (letters/digits/hyphens/underscores) are accepted.
# Dots, path separators, backslashes and spaces are rejected fail-closed so a
# state_id can never traverse or escape the digests directory.
_STATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

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


def _valid_state_id(state_id: str) -> bool:
    """Fail-closed state_id validation: SHA-256 hex digests and the documented
    activation ids (``activation-final-20260810``) pass; anything containing a
    dot, path separator, backslash or space is rejected before any file or
    network access."""
    return bool(_STATE_ID_RE.fullmatch(state_id))


def _state_path(state_id: str, state_dir: str) -> str:
    return os.path.join(state_dir, f"{state_id}.json")


def _ensure_private_dir(path: str) -> None:
    """Create the delivery-state dir with owner-only perms (0700).

    The digest text carries no token, but it is competitive intelligence
    (keywords, advertisers, saturation); keep it off any other local
    account.  ``chmod`` runs even when the dir already exists so a
    pre-existing group/world-readable dir is tightened.  Any failure is
    logged and swallowed — hardening never blocks delivery.
    """
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError as exc:
        logger.warning("Telegram: não foi possível apertar permissões de %s — %s", path, exc)


def _valid_state(data: dict[str, Any], text: str) -> bool:
    """Deep validation of one delivery state against the freshly computed
    chunks for ``text``: the hash and text must match, the chunk list must be
    exactly the recomputed chunks (same count, indices and texts), every chunk
    must carry a bool ``sent`` and an ``sent_at`` that is None or a string,
    a chunk recorded as sent must say when it was sent, and the optional
    ``in_flight`` marker (absent in legacy files, bool otherwise) must be a
    bool when present."""
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
        in_flight = chunk.get("in_flight")
        if in_flight is not None and not isinstance(in_flight, bool):
            return False
    return True


def _has_unsent_in_flight(data: dict[str, Any]) -> bool:
    """True quando um chunk não-sent está marcado in_flight no ficheiro durável.

    Isso significa que o envio foi iniciado mas o resultado nunca foi
    persistido: o Telegram pode ou não ter aceite o chunk. Qualquer rerun
    deve recusar reenviar até resolução operacional explícita."""
    return any(
        isinstance(chunk, dict)
        and chunk.get("sent") is False
        and chunk.get("in_flight") is True
        for chunk in data.get("chunks", [])
    )


def _load_state(state_id: str, state_dir: str, text: str) -> tuple[str, dict[str, Any] | None]:
    """Classify the persisted delivery state for ``text``.

    Returns ``(status, data)``:

    * ``("absent", None)`` — no state file yet; a fresh state may be created.
    * ``("valid", data)`` — the file exists, hashes to ``text`` and its chunk
      list matches the freshly computed chunks, indices, texts, ``sent``
      flags, timestamps and (optional) ``in_flight`` markers, and no unsent
      chunk is in-flight.
    * ``("ambiguous", data)`` — structurally valid for ``text`` but an unsent
      chunk carries a durable ``in_flight`` marker: the previous attempt may
      have sent it.  Delivery must fail closed with ``state_ambiguous`` and
      never resend until an operator resolves the marker.
    * ``("corrupt", data)`` — the file exists but is unreadable, is not a
      dict, belongs to different content, or has an inconsistent chunk list
      (``data`` is the parsed dict when available).  Delivery must fail
      closed: send nothing and never overwrite the suspect state.
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
    if _has_unsent_in_flight(data):
        logger.error(
            "Telegram: estado de entrega com chunk em voo não resolvido em %s (fail closed: ambíguo)",
            path,
        )
        return "ambiguous", data
    return "valid", data


def _fresh_state(text: str) -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "text": text,
        "hash": sha256_hex(text),
        "chunks": [
            {"index": index, "text": chunk, "sent": False, "sent_at": None, "in_flight": False}
            for index, chunk in enumerate(_chunks(text))
        ],
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }


def _persist_state(state: dict[str, Any], state_id: str, state_dir: str) -> bool:
    """Durably persist the state; return True on success, False on failure.

    Stateless calls (``state_id`` is None) have nothing to persist and always
    report success so the legacy ``send`` path is unchanged.  Failures are
    logged (sanitized) and reported to the caller, which decides how to fail
    closed."""
    if not state_id:
        return True
    state["updated_at"] = utc_now_iso()
    try:
        atomic_write_json(_state_path(state_id, state_dir), state)
        return True
    except OSError as exc:
        logger.warning("Telegram: estado de entrega não persistido — %s", exc)
        return False


def _send_chunk(chunk: str, payload_base: dict[str, Any], url: str) -> tuple[str, bool] | None:
    """Send one chunk; return ``(kind, ambiguous)`` or None on success.

    The outcome is classified so stateful delivery can decide whether a rerun
    is safe:

    * ``ambiguous=True`` — the remote may have accepted: timeout, transport
      errors, unexpected exceptions, HTTP >= 500 and a 200 response whose body
      is not valid JSON.  A rerun must refuse to resend.
    * ``ambiguous=False`` — definite rejection: HTTP 4xx, or a valid JSON
      response with ``ok != true``.  The in-flight marker can be cleared and a
      later retry is safe.

    Exception text and secrets are never propagated, logged, or persisted;
    arbitrary unexpected exceptions are reduced to the sanitized
    ``unexpected_error`` kind, so a delivery bug can never block the digest or
    leak internals into ``data/digests/``.
    """
    payload = {**payload_base, "text": chunk}
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            if 400 <= response.status_code < 500:
                return "http_error", False
            return "http_error", True  # >=500 or other: outcome unknown
        try:
            body = response.json()
        except (ValueError, TypeError):
            return "json_invalid", True  # 200 sem corpo utilizável: pode ter aceite
        if not isinstance(body, dict) or body.get("ok") is not True:
            return "api_not_ok", False  # resposta válida de rejeição
        return None
    except requests.Timeout:
        return "timeout", True
    except requests.RequestException:
        return "network_error", True
    except Exception as exc:
        logger.error("Telegram: erro inesperado ao enviar chunk (tipo=%s; sanitizado)", type(exc).__name__)
        return "unexpected_error", True


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
    if state_id is not None and not _valid_state_id(state_id):
        # Fail closed before any file or network access: a state_id that could
        # traverse or escape the state dir is never used as a filename.
        logger.error("Telegram: state_id inválido (fail closed) — entrega recusada")
        return {
            "delivered": False,
            "status": "state_invalid",
            "chunks_total": 0,
            "chunks_sent": 0,
            "errors": ["state_invalid"],
        }
    state = None
    if state_id:
        _ensure_private_dir(state_dir)
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
        if status == "ambiguous":
            # Fail closed: an unsent chunk is durably in-flight, so the previous
            # attempt may have accepted it.  Never resend until an operator
            # resolves the marker (no resolver exists in this project).
            logger.error("Telegram: chunk em voo não resolvido; reenvio recusado até resolução operacional")
            chunks = state.get("chunks", []) if state else []
            return {
                "delivered": False,
                "status": "state_ambiguous",
                "chunks_total": len(chunks),
                "chunks_sent": sum(1 for chunk in chunks if chunk.get("sent")),
                "errors": ["state_ambiguous"],
            }
        if status == "absent":
            state = _fresh_state(text)
            # Write-ahead: a fresh pending state must be durable BEFORE any
            # network send, otherwise a rerun would have no record of the
            # attempt.  (A valid resume is already durable and needs no
            # preliminary write.)
            if not _persist_state(state, state_id, state_dir):
                logger.error("Telegram: estado inicial não persistido em %s (fail closed)", _state_path(state_id, state_dir))
                return {
                    "delivered": False,
                    "status": "state_persist_failed",
                    "chunks_total": len(state["chunks"]),
                    "chunks_sent": 0,
                    "errors": ["state_persist_failed"],
                }
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
        # Write-ahead: mark the chunk in-flight and persist it durably BEFORE
        # any network call.  If this write fails, Telegram is never called and
        # the attempt reports a sanitized state_persist_failed.
        chunk["in_flight"] = True
        if not _persist_state(state, state_id, state_dir):
            chunk["in_flight"] = False  # revert in-memory so the report is truthful
            state["status"] = "state_persist_failed"
            state["errors"] = ["state_persist_failed"]
            return _report(state, delivered=False)
        outcome = _send_chunk(chunk["text"], payload_base, url)
        if outcome is not None:
            failure, ambiguous = outcome
            if ambiguous:
                # The remote may have accepted the chunk (timeout, transport
                # error, HTTP >=500, or a 200 with a malformed body): leave the
                # durable in_flight marker untouched and fail closed.  A rerun
                # that finds an unsent in-flight chunk refuses to resend until
                # an operator resolves the marker.
                state["status"] = "state_ambiguous"
                state["errors"] = ["state_ambiguous"]
                _persist_state(state, state_id, state_dir)
                return _report(state, delivered=False)
            errors.append(failure)
            # Definite rejection (HTTP 4xx / API ok != true): clear the
            # in-flight marker and persist BEFORE a retry is allowed.  If the
            # clear cannot be persisted, the durable file still says in-flight
            # — a retry could double-send, so fail closed as ambiguous.
            chunk["in_flight"] = False
            if not _persist_state(state, state_id, state_dir):
                state["status"] = "state_ambiguous"
                state["errors"] = ["state_ambiguous"]
                return _report(state, delivered=False)
            break  # one failure aborts this invocation; state resumes next time
        chunk["sent"] = True
        chunk["sent_at"] = utc_now_iso()
        chunk["in_flight"] = False
        # After remote acceptance, record sent and clear in-flight durably.  A
        # failure here leaves the last durable file in-flight: the outcome is
        # indeterminate (Telegram may have accepted), so fail closed ambiguous.
        if not _persist_state(state, state_id, state_dir):
            state["status"] = "state_ambiguous"
            state["errors"] = ["state_ambiguous"]
            return _report(state, delivered=False)
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
    # Final status persist: every chunk is already durably sent or durably
    # cleared, so a failure here loses only the status string — the outcome is
    # still known and a rerun stays idempotent.
    if not _persist_state(state, state_id, state_dir):
        logger.warning("Telegram: estado final não persistido em %s (chunks já duráveis)", _state_path(state_id, state_dir))
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
