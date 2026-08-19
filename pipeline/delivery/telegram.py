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

from pipeline.notifications import NotificationBatch, render_html, render_rich
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
# v1 chunk states and v2 batch states intentionally share this directory.
# ``schema_version`` distinguishes them so completed v1 runs remain historical
# no-ops while non-completed v1 states fail closed during the explicit cutover.
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


# ── v2 batch delivery (slice 2) ────────────────────────────────────────────
#
# ``deliver_batch`` sends one NotificationBatch with durable per-item state
# (schema_version=2) under ``<state_dir>/<state_id>.json``.  The write-ahead
# protocol of the legacy chunk delivery generalizes to items:
#
# 1. a fresh pending v2 state is durable BEFORE any network call;
# 2. before each item call an ``in_flight`` marker is durable; a failed write
#    means Telegram is never called (state_persist_failed);
# 3. success is only HTTP 200 + dict JSON ``ok=true`` + an integer
#    ``result.message_id``: only then is the item marked SENT with
#    ``sent_at``/``message_id`` and the marker cleared.  A failed post-accept
#    write leaves the durable file in-flight and the run fails closed
#    (state_ambiguous);
# 4. ambiguous outcomes (timeout, transport, unexpected exception, HTTP >=500,
#    unreadable/non-dict JSON, success-like malformed body) keep the durable
#    in_flight marker and block every rerun;
# 5. definite rejections (HTTP 4xx, or valid dict JSON ``ok=false``) clear the
#    marker and persist BEFORE any fallback/retry is allowed.  Only a narrow,
#    evidence-backed rich_unsupported classifier (4xx + a description carrying
#    an exact Rich error token from the allowlist, or a description exactly
#    equal to an evidence-backed full Bot API description) triggers one HTML
#    attempt for that item; HTML never falls back again.  The raw description
#    is never logged, persisted or reported;
# 6. a legacy state without ``schema_version`` is never rewritten or
#    auto-migrated: v1 ``status=success`` is a historical completed no-op,
#    every other v1 status fails closed as state_legacy_migration.
#
# The state never contains the bot token, chat id, any URL or a network
# payload — only renderer fragments hashes (canonical SHA-256 over method +
# fragment).  Secrets stay absent from logs and reports.

#: Per-item status values of a valid v2 state.
_ITEM_STATUSES = frozenset({"pending", "sent", "rejected", "ambiguous"})
_VALID_RENDERERS = frozenset({"rich", "html"})
_DEFAULT_RENDERER = "html"  # safe rollout until live Rich acceptance exists
_SCHEMA_VERSION = 2

#: Exact symbolic error tokens (Telegram Bot API ``description``) that
#: identify a Rich-message rejection the server cannot parse or does not
#: support: entity tokens (live-verified/open-source integrations) and
#: structural rejections (live-verified).  Matched case-insensitively as
#: WHOLE tokens only, so a near-match such as ``RICH_MESSAGE_EMAIL_INVALIDATED``
#: never matches ``RICH_MESSAGE_EMAIL_INVALID``.  There is no official
#: structured Telegram error field; ``error_code`` (integer) plus human
#: ``description`` is all the Bot API exposes.
_RICH_UNSUPPORTED_TOKENS = frozenset(
    {
        # entity tokens (live-verified / open-source integrations)
        "rich_message_email_invalid",
        "rich_message_url_invalid",
        "rich_message_mention_invalid",
        "rich_message_hashtag_invalid",
        "rich_message_cashtag_invalid",
        "rich_message_bot_command_invalid",
        "rich_message_phone_invalid",
        "rich_message_bank_card_invalid",
        # structural rejections (live-verified)
        "rich_message_blocks_too_many",
        "rich_message_depth_invalid",
        "rich_message_text_too_long",
        "rich_message_media_too_many",
        "rich_message_table_cols_too_many",
    }
)

#: Evidence-backed FULL Bot API ``description`` strings for Rich-method
#: capability rejections and the Rich format rejection.  These are matched
#: exactly: the whole normalized description (case-insensitive, surrounding
#: whitespace stripped) must equal one of these — arbitrary text that merely
#: contains a phrase never matches.  The speculative natural-language phrases
#: previously used here are gone: they had no live evidence and are not part
#: of the evidence-backed classifier.
_RICH_UNSUPPORTED_DESCRIPTIONS = frozenset(
    {
        "not found: method not found",
        "bad request: unknown method",
        "bad request: rich message must be non-empty",
    }
)

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _payload_hash(fragment: dict[str, Any]) -> str:
    """Canonical SHA-256 over the method + complete renderer payload fragment
    (never the token, chat id or a full network payload)."""
    canonical = json.dumps(
        fragment, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha256_hex(canonical)


def _resolve_renderer(renderer: str | None) -> str | None:
    """Resolve the operational renderer: explicit argument, then
    ``TELEGRAM_RENDERER``, then the safe ``html`` default.  Any other value
    fails closed (the caller reports renderer_invalid)."""
    if renderer is not None:
        value = renderer
    else:
        value = os.getenv("TELEGRAM_RENDERER", _DEFAULT_RENDERER)
    if value not in _VALID_RENDERERS:
        return None
    return value


def _render_fragment(item: Any, renderer: str) -> dict[str, Any]:
    """Render one item with the chosen renderer; raises on oversized content."""
    if renderer == "rich":
        return render_rich(item)
    return render_html(item)


def _fresh_batch_state(batch: NotificationBatch, renderer: str) -> dict[str, Any]:
    """A fresh v2 pending state: full audit text plus per-item identity,
    durable renderer, payload hash and empty outcome fields.  Rendering every
    item here means any oversized content fails before the first network call."""
    now = utc_now_iso()
    items: list[dict[str, Any]] = []
    for item in batch.items:
        fragment = _render_fragment(item, renderer)
        items.append(
            {
                "item_id": item.item_id,
                "kind": item.kind,
                "product": item.product,
                "order": item.order,
                "audit_text": item.audit_text,
                "renderer": renderer,
                "payload_hash": _payload_hash(fragment),
                "status": "pending",
                "in_flight": False,
                "sent_at": None,
                "message_id": None,
                "fallback_attempted": False,
                "last_error_kind": None,
            }
        )
    return {
        "schema_version": _SCHEMA_VERSION,
        "run_id": batch.run_id,
        "batch_id": batch.batch_id,
        "audit_text": batch.audit_text,
        "renderer": renderer,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "items": items,
    }


def _has_unsent_in_flight_v2(data: dict[str, Any]) -> bool:
    """True when an unsent item is durably in-flight: the previous attempt may
    have sent it, so any rerun must refuse to resend."""
    return any(
        isinstance(item, dict)
        and item.get("in_flight") is True
        and item.get("status") != "sent"
        for item in data.get("items", [])
    )


def _valid_state_v2(data: dict[str, Any], batch: NotificationBatch) -> bool:
    """Deep fail-closed validation of a v2 state against the recomputed batch:
    identity, order, content, audit text, per-item renderer, recomputed payload
    hashes, field types and status/in_flight invariants must all match."""
    schema_version = data.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != _SCHEMA_VERSION
    ):
        return False
    if data.get("run_id") != batch.run_id or data.get("batch_id") != batch.batch_id:
        return False
    if data.get("audit_text") != batch.audit_text:
        return False
    if data.get("renderer") not in _VALID_RENDERERS:
        return False
    if not isinstance(data.get("status"), str) or not data["status"]:
        return False
    if not isinstance(data.get("created_at"), str) or not isinstance(data.get("updated_at"), str):
        return False
    items = data.get("items")
    if not isinstance(items, list) or len(items) != len(batch.items):
        return False
    for index, (item_state, model_item) in enumerate(zip(items, batch.items)):
        if not isinstance(item_state, dict):
            return False
        if item_state.get("item_id") != model_item.item_id:
            return False
        if item_state.get("kind") != model_item.kind:
            return False
        if item_state.get("product") != model_item.product:
            return False
        if item_state.get("order") != index:
            return False
        if item_state.get("audit_text") != model_item.audit_text:
            return False
        item_renderer = item_state.get("renderer")
        if item_renderer not in _VALID_RENDERERS:
            return False
        try:
            recomputed = _payload_hash(_render_fragment(model_item, item_renderer))
        except Exception:
            return False
        if item_state.get("payload_hash") != recomputed:
            return False
        if item_state.get("status") not in _ITEM_STATUSES:
            return False
        if not isinstance(item_state.get("in_flight"), bool):
            return False
        sent_at = item_state.get("sent_at")
        if sent_at is not None and not isinstance(sent_at, str):
            return False
        message_id = item_state.get("message_id")
        if message_id is not None and (
            not isinstance(message_id, int) or isinstance(message_id, bool)
        ):
            return False
        if not isinstance(item_state.get("fallback_attempted"), bool):
            return False
        last_error = item_state.get("last_error_kind")
        if last_error is not None and not isinstance(last_error, str):
            return False
        status = item_state.get("status")
        if status == "sent":
            if item_state.get("in_flight") is not False:
                return False
            if not sent_at or not isinstance(sent_at, str):
                return False
            if not isinstance(message_id, int) or isinstance(message_id, bool):
                return False
        else:
            if sent_at is not None or message_id is not None:
                return False
        if status == "ambiguous" and item_state.get("in_flight") is not True:
            return False
    return True


def _load_batch_state(
    state_id: str, state_dir: str, batch: NotificationBatch
) -> tuple[str, dict[str, Any] | None]:
    """Classify the persisted v2 batch state for ``batch``.

    Returns ``(status, data)``:

    * ``absent`` — no file yet; a fresh state may be created.
    * ``valid`` — deep validation passed and no unsent item is in-flight.
    * ``ambiguous`` — an unsent item carries a durable in_flight marker: the
      previous attempt may have sent it; delivery fails closed and never
      resends until an operator resolves the marker.
    * ``corrupt`` — unreadable, not a dict, not schema v2, or failing deep
      validation against the recomputed batch; sends nothing and never
      overwrites the suspect state.
    * ``legacy_success`` — legacy v1 file with ``status == "success"``:
      historical completed, no network, explicit no-op.
    * ``legacy_other`` — legacy v1 file with any other status (partial/error/
      missing config/in-flight): fails closed as state_legacy_migration and is
      never rewritten or auto-migrated.
    """
    path = _state_path(state_id, state_dir)
    if not os.path.exists(path):
        return "absent", None
    try:
        with open(path, "r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError):
        logger.error("Telegram: estado de entrega v2 ilegível em %s (fail closed)", path)
        return "corrupt", None
    if not isinstance(data, dict):
        return "corrupt", data
    if "schema_version" not in data:
        # Explicit legacy cutover: never rewrite, never auto-migrate.
        if data.get("status") == "success":
            logger.info("Telegram: estado v1 concluído em %s tratado como no-op histórico", path)
            return "legacy_success", data
        logger.error(
            "Telegram: estado v1 não concluído em %s exige migração explícita (fail closed)",
            path,
        )
        return "legacy_other", data
    if not _valid_state_v2(data, batch):
        logger.error(
            "Telegram: estado v2 inválido ou de outro conteúdo em %s (fail closed)", path
        )
        return "corrupt", data
    if _has_unsent_in_flight_v2(data):
        logger.error(
            "Telegram: estado v2 com item em voo não resolvido em %s (fail closed: ambíguo)",
            path,
        )
        return "ambiguous", data
    return "valid", data


def _try_body(response: Any) -> Any:
    """Parse a JSON response body, or None when unreadable."""
    try:
        return response.json()
    except (ValueError, TypeError):
        return None


def _send_item_once(
    url: str, payload: dict[str, Any]
) -> tuple[str, bool, int | None, Any]:
    """POST one item; classify the outcome as ``(kind, ambiguous, status, body)``.

    ``ambiguous=True`` means the remote may have accepted (timeout, transport,
    unexpected exception, HTTP >=500, 200 with unreadable/non-dict JSON, or a
    success-like body without an integer ``result.message_id``): the rerun must
    refuse to resend.  ``ambiguous=False`` is a definite rejection (HTTP 4xx or
    valid dict JSON ``ok != true``): the in-flight marker can be cleared.
    """
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    except requests.Timeout:
        return "timeout", True, None, None
    except requests.RequestException:
        return "network_error", True, None, None
    except Exception as exc:
        logger.error(
            "Telegram: erro inesperado ao enviar item (tipo=%s; sanitizado)",
            type(exc).__name__,
        )
        return "unexpected_error", True, None, None
    status = response.status_code
    if status != 200:
        if 400 <= status < 500:
            return "http_error", False, status, _try_body(response)
        return "http_error", True, status, _try_body(response)  # >=500: unknown
    body = _try_body(response)
    if body is None or not isinstance(body, dict):
        return "json_invalid", True, status, body  # 200 sem corpo utilizável
    if body.get("ok") is not True:
        return "api_not_ok", False, status, body  # rejeição definitiva
    result = body.get("result")
    if isinstance(result, dict):
        message_id = result.get("message_id")
        if isinstance(message_id, int) and not isinstance(message_id, bool):
            return "sent", False, status, body
    # ok=true sem message_id inteiro: o Telegram pode ter aceite.
    return "message_id_missing", True, status, body


def _is_rich_unsupported(status_code: int | None, body: Any) -> bool:
    """Narrow evidence-backed fallback classifier.

    A definite rejection triggers the single HTML fallback only when the
    outcome is consistently 4xx (HTTP status or integer body ``error_code``)
    AND the ``description`` either:

    * contains one of the exact symbolic Rich error tokens
      (``RICH_MESSAGE_*_INVALID`` entity/structural tokens) as a whole token,
      case-insensitively — a near-match such as
      ``RICH_MESSAGE_EMAIL_INVALIDATED`` never matches; or
    * is exactly equal to an evidence-backed full Bot API description
      (case-insensitive, surrounding whitespace stripped) — a description
      that merely contains the phrase never matches.

    The classifier fails closed on inconsistent responses: an HTTP status
    >=500 never matches even when the body ``error_code`` is 4xx, and an
    integer body ``error_code`` that exists and is not 4xx never matches even
    when the HTTP status is 4xx.  HTTP 200 + body ``error_code`` 4xx and HTTP
    4xx without a body ``error_code`` both remain valid 4xx gates.
    Chat/auth/rate/config errors and unknown text never fall back."""
    if status_code is not None and status_code >= 500:
        return False
    error_code = None
    description = ""
    if isinstance(body, dict):
        raw_code = body.get("error_code")
        if isinstance(raw_code, int) and not isinstance(raw_code, bool):
            error_code = raw_code
        raw = body.get("description")
        if isinstance(raw, str):
            description = raw
    # Fail closed on inconsistent responses: an integer body error_code that
    # exists and is not 4xx never matches, even when the HTTP status is 4xx.
    if error_code is not None and not (400 <= error_code < 500):
        return False
    is_4xx = status_code is not None and 400 <= status_code < 500
    if not (is_4xx or (error_code is not None and 400 <= error_code < 500)):
        return False
    if not description:
        return False
    lowered = description.strip().lower()
    tokens = {match.group(0) for match in _WORD_RE.finditer(lowered)}
    if tokens & _RICH_UNSUPPORTED_TOKENS:
        return True
    return lowered in _RICH_UNSUPPORTED_DESCRIPTIONS


def _sent_count_v2(state: dict[str, Any]) -> int:
    return sum(
        1
        for item in state.get("items", [])
        if isinstance(item, dict) and item.get("status") == "sent"
    )


def _fallback_used_v2(state: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and item.get("fallback_attempted") is True
        for item in state.get("items", [])
    )


def _report_v2(
    *,
    delivered: bool,
    status: str,
    items_total: int,
    items_sent: int,
    errors: list[str],
    renderer: str | None,
    fallback_used: bool,
) -> dict[str, Any]:
    """Sanitized report: no item payloads, token, chat id, URL or raw Telegram
    descriptions ever reach it."""
    return {
        "delivered": delivered,
        "status": status,
        "schema_version": _SCHEMA_VERSION,
        "items_total": items_total,
        "items_sent": items_sent,
        "errors": list(errors),
        "renderer": renderer,
        "fallback_used": fallback_used,
    }


def _report_from_state(state: dict[str, Any], *, delivered: bool) -> dict[str, Any]:
    return _report_v2(
        delivered=delivered,
        status=state.get("status", "pending"),
        items_total=len(state.get("items", [])),
        items_sent=_sent_count_v2(state),
        errors=state.get("errors", []),
        renderer=state.get("renderer"),
        fallback_used=_fallback_used_v2(state),
    )


def _item_snapshot(item_state: dict[str, Any]) -> dict[str, Any]:
    """Sanitized per-item snapshot for the post-persist callback: identity and
    outcome only, never a payload fragment."""
    return {
        "item_id": item_state["item_id"],
        "order": item_state["order"],
        "kind": item_state["kind"],
        "status": item_state["status"],
        "sent_at": item_state["sent_at"],
        "message_id": item_state["message_id"],
        "renderer": item_state["renderer"],
        "fallback_attempted": item_state["fallback_attempted"],
    }


def deliver_batch(
    batch: NotificationBatch,
    state_id: str,
    state_dir: str | None = None,
    renderer: str | None = None,
    on_item_sent: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Deliver a NotificationBatch with durable v2 per-item state.

    Every item is sent separately in deterministic batch order using the
    slice-1 renderers.  A fresh v2 state is durable before any network call;
    each item is marked in_flight and durably persisted before its call;
    items already SENT are never sent again and a resume starts at the first
    unconfirmed item.  The operational renderer is the explicit ``renderer``
    argument, then ``TELEGRAM_RENDERER``, then the safe ``html`` default; a
    resume follows the durable per-item renderer, never the current env.

    Returns a sanitized report (delivered/status/schema_version/items_total/
    items_sent/errors/renderer/fallback_used)."""
    if state_dir is None:
        state_dir = DEFAULT_DIGEST_DIR
    if not _valid_state_id(state_id):
        logger.error("Telegram: state_id inválido (fail closed) — entrega de batch recusada")
        return _report_v2(
            delivered=False,
            status="state_invalid",
            items_total=0,
            items_sent=0,
            errors=["state_invalid"],
            renderer=None,
            fallback_used=False,
        )
    _ensure_private_dir(state_dir)
    status, state = _load_batch_state(state_id, state_dir, batch)
    if status == "corrupt":
        return _report_v2(
            delivered=False,
            status="state_corrupt",
            items_total=0,
            items_sent=0,
            errors=["state_corrupt"],
            renderer=None,
            fallback_used=False,
        )
    if status == "legacy_success":
        # Historical completed v1 digest: explicit no-op, no network, no rewrite.
        return _report_v2(
            delivered=True,
            status="success",
            items_total=0,
            items_sent=0,
            errors=[],
            renderer=None,
            fallback_used=False,
        )
    if status == "legacy_other":
        return _report_v2(
            delivered=False,
            status="state_legacy_migration",
            items_total=0,
            items_sent=0,
            errors=["state_legacy_migration"],
            renderer=None,
            fallback_used=False,
        )
    if status == "ambiguous":
        return _report_v2(
            delivered=False,
            status="state_ambiguous",
            items_total=len(batch.items),
            items_sent=_sent_count_v2(state or {}),
            errors=["state_ambiguous"],
            renderer=(state or {}).get("renderer"),
            fallback_used=_fallback_used_v2(state or {}),
        )
    if status == "absent":
        resolved = _resolve_renderer(renderer)
        if resolved is None:
            return _report_v2(
                delivered=False,
                status="renderer_invalid",
                items_total=0,
                items_sent=0,
                errors=["renderer_invalid"],
                renderer=None,
                fallback_used=False,
            )
        try:
            state = _fresh_batch_state(batch, resolved)
        except Exception as exc:
            logger.error(
                "Telegram: renderização do batch falhou (tipo=%s; sanitizado)",
                type(exc).__name__,
            )
            return _report_v2(
                delivered=False,
                status="render_failed",
                items_total=0,
                items_sent=0,
                errors=["render_failed"],
                renderer=resolved,
                fallback_used=False,
            )
        # Write-ahead: the fresh pending state must be durable BEFORE any
        # network send.
        if not _persist_state(state, state_id, state_dir):
            return _report_v2(
                delivered=False,
                status="state_persist_failed",
                items_total=len(state["items"]),
                items_sent=0,
                errors=["state_persist_failed"],
                renderer=resolved,
                fallback_used=False,
            )
    # status == "valid": resume using the durable per-item renderers.
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.warning("Telegram não configurado; batch não enviado (missing_config)")
        state["status"] = "missing_config"
        state["errors"] = ["missing_config"]
        _persist_state(state, state_id, state_dir)
        return _report_from_state(state, delivered=False)

    errors: list[str] = []
    for item_state in state["items"]:
        if item_state["status"] == "sent":
            continue  # never re-send an item already recorded as sent
        while True:
            # Write-ahead: durable in_flight BEFORE any network call.  If this
            # write fails, Telegram is never called.
            item_state["in_flight"] = True
            if not _persist_state(state, state_id, state_dir):
                item_state["in_flight"] = False  # revert so the report is truthful
                state["status"] = "state_persist_failed"
                state["errors"] = ["state_persist_failed"]
                return _report_from_state(state, delivered=False)
            model_item = batch.items[item_state["order"]]
            item_renderer = item_state["renderer"]
            try:
                fragment = _render_fragment(model_item, item_renderer)
            except Exception:
                item_state["in_flight"] = False
                state["status"] = "render_failed"
                state["errors"] = ["render_failed"]
                _persist_state(state, state_id, state_dir)
                return _report_from_state(state, delivered=False)
            url = f"{TELEGRAM_API}/bot{bot_token}/{fragment['method']}"  # token-bearing, never persisted
            payload = {"chat_id": chat_id, **fragment["payload"]}
            kind, ambiguous, http_status, body = _send_item_once(url, payload)
            if kind == "sent":
                item_state["status"] = "sent"
                item_state["sent_at"] = utc_now_iso()
                item_state["message_id"] = body["result"]["message_id"]
                item_state["in_flight"] = False
                # After remote acceptance, record sent and clear in-flight
                # durably.  A failure here leaves the durable file in-flight:
                # the outcome is indeterminate, so fail closed ambiguous.
                if not _persist_state(state, state_id, state_dir):
                    state["status"] = "state_ambiguous"
                    state["errors"] = ["state_ambiguous"]
                    return _report_from_state(state, delivered=False)
                if on_item_sent is not None:
                    on_item_sent(_item_snapshot(item_state))
                break
            if ambiguous:
                # The remote may have accepted: keep the durable in_flight
                # marker, never fall back, and block every rerun.
                item_state["status"] = "ambiguous"
                state["status"] = "state_ambiguous"
                state["errors"] = ["state_ambiguous"]
                _persist_state(state, state_id, state_dir)
                return _report_from_state(state, delivered=False)
            # Definite rejection: clear the in-flight marker and persist BEFORE
            # any fallback/retry becomes safe.  An unpersisted clear is fail
            # closed ambiguous: the durable file would still say in-flight.
            item_state["in_flight"] = False
            item_state["status"] = "rejected"
            item_state["last_error_kind"] = kind
            if not _persist_state(state, state_id, state_dir):
                state["status"] = "state_ambiguous"
                state["errors"] = ["state_ambiguous"]
                return _report_from_state(state, delivered=False)
            # Narrow rich_unsupported classifier: exactly one HTML attempt.
            if (
                item_renderer == "rich"
                and not item_state["fallback_attempted"]
                and _is_rich_unsupported(http_status, body)
            ):
                # Render the html fragment BEFORE mutating anything: a render
                # failure leaves the durable rejected record untouched.
                try:
                    html_fragment = _render_fragment(model_item, "html")
                except Exception:
                    item_state["last_error_kind"] = "render_failed"
                    errors.append("render_failed")
                    break
                # Fallback preflight: durably record the renderer switch and the
                # recomputed payload hash BEFORE the html network call.  Any
                # persistence failure here blocks the html send.
                item_state["fallback_attempted"] = True
                item_state["renderer"] = "html"
                item_state["status"] = "pending"
                item_state["last_error_kind"] = "rich_unsupported"
                item_state["payload_hash"] = _payload_hash(html_fragment)
                if not _persist_state(state, state_id, state_dir):
                    state["status"] = "state_persist_failed"
                    state["errors"] = ["state_persist_failed"]
                    return _report_from_state(state, delivered=False)
                continue  # same item, html attempt (html never falls back again)
            errors.append(kind)
            break
        if item_state["status"] != "sent":
            break  # one unrecovered rejection stops this invocation

    sent = _sent_count_v2(state)
    if errors:
        state["status"] = "partial" if sent else "error"
        state["errors"] = errors
    else:
        state["status"] = "success"
        state.pop("errors", None)
    # Final status persist: every item is already durably sent or durably
    # cleared, so a failure here loses only the status string.
    if not _persist_state(state, state_id, state_dir):
        logger.warning(
            "Telegram: estado final de batch não persistido em %s (itens já duráveis)",
            _state_path(state_id, state_dir),
        )
    return _report_from_state(state, delivered=not errors)
