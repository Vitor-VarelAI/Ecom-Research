"""
Contador de quota SerpAPI.

Armazena em data/quota.json o número de calls feitas no mês corrente.
Reset automático no dia 1 de cada mês.

Hard stop: 230 calls/mês (free tier são 250, margem de segurança de 20).

Uso:
    from pipeline import quota
    if quota.can_spend():
        # fazer call SerpAPI
        quota.spend()

Interface:
    can_spend(n=1) -> bool
    spend(n=1) -> None
    remaining() -> int
    get_usage() -> dict
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

QUOTA_LIMIT = 230  # hard stop (250 free - 20 margem)
QUOTA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "quota.json")


def _current_month() -> str:
    """Devolve o mês corrente no formato YYYY-MM (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _read_quota() -> dict[str, Any]:
    """Lê o ficheiro quota.json. Se não existir ou mês diferente, devolve resetado."""
    if not os.path.exists(QUOTA_FILE):
        return {"month": _current_month(), "calls": 0, "limit": QUOTA_LIMIT}

    try:
        with open(QUOTA_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("quota.json corrompido — a reiniciar contador")
        return {"month": _current_month(), "calls": 0, "limit": QUOTA_LIMIT}

    # Reset se mudou de mês
    if data.get("month") != _current_month():
        logger.info(
            "quota: novo mês (%s → %s) — reset do contador",
            data.get("month"), _current_month(),
        )
        data = {"month": _current_month(), "calls": 0, "limit": QUOTA_LIMIT}
        _write_quota(data)

    # Garantir que limit está definido
    if "limit" not in data:
        data["limit"] = QUOTA_LIMIT

    return data


def _write_quota(data: dict[str, Any]) -> None:
    """Escreve o estado da quota para disco."""
    os.makedirs(os.path.dirname(QUOTA_FILE), exist_ok=True)
    try:
        with open(QUOTA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning("quota: erro ao escrever quota.json — %s", e)


def can_spend(n: int = 1) -> bool:
    """Verifica se há quota disponível para n calls."""
    data = _read_quota()
    return (data["calls"] + n) <= data["limit"]


def spend(n: int = 1) -> None:
    """Regista n calls SerpAPI no contador do mês corrente."""
    data = _read_quota()
    data["calls"] = data.get("calls", 0) + n
    _write_quota(data)
    remaining_val = data["limit"] - data["calls"]
    logger.info("quota: +%d call(s) → %d/%d usadas (%d restantes)", n, data["calls"], data["limit"], remaining_val)


def remaining() -> int:
    """Devolve o número de calls restantes este mês."""
    data = _read_quota()
    return max(0, data["limit"] - data.get("calls", 0))


def get_usage() -> dict[str, Any]:
    """Devolve estado completo da quota."""
    data = _read_quota()
    return {
        "month": data["month"],
        "calls": data.get("calls", 0),
        "limit": data["limit"],
        "remaining": max(0, data["limit"] - data.get("calls", 0)),
    }
