"""
Bounded, sanitized extraction of provider error messages from HTTP error bodies.

The adapters never log or return raw response bodies, tokens, token-bearing
URLs, HTML dumps, or arbitrary nested JSON.  For a non-2xx response we read
only the documented provider message fields — ``error`` as a string, or a
nested ``error.message`` — sanitize the fragment with the project's secret/
error conventions (strip URLs and credential pairs, redact the configured
API keys/tokens verbatim, drop control characters), truncate it to a bounded
length, and hand the caller a ready-to-return ``source/status/message``
string.  Anything unparsable or undocumented degrades to a safe fallback.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_PROVIDER_MESSAGE = 160  # returned fragment is bounded, never a body dump
MAX_BODY_SNIFF = 64 * 1024  # never json.loads an unbounded body

REDACTED = "[removido]"

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:^|(?<![a-z0-9]))"
    r"(api[_-]?key|apify[_-]?token|auth[_-]?token|token|bearer|secret)"
    r"\s*[=:]\s*\S+"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")


def _configured_secrets() -> list[str]:
    """The exact credential values the adapters hold, redacted verbatim when a
    provider message echoes them back (survives formatting the regexes miss)."""
    values = (os.getenv("APIFY_API_TOKEN"), os.getenv("SERPAPI_KEY"))
    return [value for value in values if value]


def sanitize_message(message: str) -> str:
    """Strip URLs, credential pairs and control chars; bound the length."""
    if not isinstance(message, str) or not message:
        return ""
    cleaned = _CONTROL_RE.sub(" ", message)
    cleaned = _URL_RE.sub(REDACTED, cleaned)
    for secret in _configured_secrets():
        cleaned = cleaned.replace(secret, REDACTED)
    cleaned = _CREDENTIAL_RE.sub(REDACTED, cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > MAX_PROVIDER_MESSAGE:
        cleaned = cleaned[: MAX_PROVIDER_MESSAGE - 1].rstrip() + "…"
    return cleaned


def provider_error_message(body: Any) -> str:
    """Extract the documented provider message from a parsed body.

    Reads only ``error`` (a string) or nested ``error.message`` (a string);
    anything else is a safe fallback — never arbitrary nested JSON.
    """
    if not isinstance(body, dict):
        return ""
    error_field = body.get("error")
    if isinstance(error_field, str):
        return sanitize_message(error_field)
    if isinstance(error_field, dict):
        nested = error_field.get("message")
        if isinstance(nested, str):
            return sanitize_message(nested)
    return ""


def message_from_response(response: Any) -> str:
    """Boundedly extract a sanitized provider message from a non-2xx body.

    The body is read up to a cap, parsed as JSON and passed through
    ``provider_error_message``.  Non-JSON bodies (HTML dumps, empty bodies,
    etc.) degrade to the safe empty fallback — the body itself is never
    logged or returned.
    """
    text = getattr(response, "text", "") or ""
    if not text:
        return ""
    try:
        body = json.loads(text[:MAX_BODY_SNIFF])
    except (ValueError, TypeError):
        return ""
    return provider_error_message(body)


def compose(source: str, status_code: int, response: Any, fallback: str | None = None) -> str:
    """One bounded ``source/status/message`` error string for a non-2xx response.

    ``fallback`` is used only when the body carries no documented message, so
    a known status (e.g. SerpAPI 403) keeps its precise existing wording.
    """
    message = message_from_response(response)
    if message:
        return f"{source}: HTTP {status_code} — {message}"
    if fallback:
        return fallback
    return f"{source}: HTTP {status_code}"
