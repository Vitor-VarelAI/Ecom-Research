"""Send-only Telegram delivery with a complete stdout fallback."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)
TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LENGTH = 4000
REQUEST_TIMEOUT = (5, 15)


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


def send(text: str) -> bool:
    """Send a plain-text digest; return false and print the full digest on failure."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.warning("Telegram não configurado; digest impresso no stdout")
        _stdout_fallback(text)
        return False

    url = f"{TELEGRAM_API}/bot{bot_token}/sendMessage"
    payload_base: dict[str, Any] = {
        "chat_id": chat_id,
        "disable_web_page_preview": True,
    }
    try:
        for chunk in _chunks(text):
            payload = {**payload_base, "text": chunk}
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            try:
                body = response.json()
            except (ValueError, TypeError) as exc:
                raise RuntimeError("resposta JSON inválida") from exc
            if not isinstance(body, dict) or body.get("ok") is not True:
                raise RuntimeError("resposta Telegram sem ok=true")
    except Exception:
        # Delivery must never make the research pipeline fail, and exception
        # text may contain request details, so log only the generic outcome.
        logger.warning("Telegram falhou; digest impresso no stdout")
        _stdout_fallback(text)
        return False
    return True
