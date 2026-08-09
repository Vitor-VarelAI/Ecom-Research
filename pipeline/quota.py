"""
Contador de quota SerpAPI.

Armazena em data/quota.json o número de calls feitas no mês corrente.
Reset automático no dia 1 de cada mês.

Hard stop: 230 calls/mês (free tier são 250, margem de segurança de 20).

Concorrência: todas as operações (leitura, incremento, reset mensal) são
serializadas por um flock exclusivo num ficheiro de lock dedicado
(data/quota.lock) — nunca no próprio quota.json, porque as escritas atómicas
substituem o inode.  ``transaction()`` segura esse lock durante uma chamada
de provider inteira: a verificação de disponibilidade à entrada e o
``charge()`` à saída são atómicos entre processos, pelo que um segundo
processo bloqueia em vez de intercalar um gasto entre a nossa verificação e
o nosso registo.  Nada é cobrado a menos que ``charge()`` seja chamado —
chamadas que a contabilidade atual não cobra continuam sem custo, e um crash
durante a chamada deixa o contador intacto.

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
    transaction(n=1, firecrawl=False) -> QuotaReservation (context manager)
"""

import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from pipeline.run_state import atomic_write_json

logger = logging.getLogger(__name__)

try:  # Linux/macOS; guarded so the module imports on any platform.
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

QUOTA_LIMIT = 230  # hard stop (250 free - 20 margem)
FIRECRAWL_LIMIT = 1000  # free tier
FIRECRAWL_WARNING = 800  # soft warning
QUOTA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "quota.json")

# Lock state: a single module-level fd (so a process holds one lock) plus a
# re-entrant thread lock (so threads in the same process serialize too, and
# nested quota calls from the same thread cannot deadlock).
_lock_fd: Any = None
_lock_depth = 0
_thread_lock = threading.RLock()


def _lock_path() -> str:
    """Lock file lives next to the state file (never replaced by atomic writes)."""
    return f"{QUOTA_FILE}.lock"


def _current_month() -> str:
    """Devolve o mês corrente no formato YYYY-MM (UTC)."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


@contextmanager
def _quota_file_lock() -> Iterator[None]:
    """Context manager holding the exclusive quota lock (thread + process).

    Fail-closed: raises RuntimeError when fcntl is unavailable or the lock
    file cannot be created/acquired.  Re-entrant within the same thread.
    """
    global _lock_fd, _lock_depth
    with _thread_lock:
        if fcntl is None:
            raise RuntimeError(
                "quota: fcntl indisponível neste sistema; recusar tocar quota sem lock (fail closed)"
            )
        if _lock_depth == 0:
            lock_file = _lock_path()
            os.makedirs(os.path.dirname(lock_file) or ".", exist_ok=True)
            try:
                fd = open(lock_file, "a+", encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(f"quota: não foi possível abrir o lock {lock_file}: {exc}") from exc
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except OSError as exc:
                try:
                    fd.close()
                except OSError:
                    pass
                raise RuntimeError(f"quota: não foi possível adquirir o lock {lock_file}: {exc}") from exc
            _lock_fd = fd
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
            if _lock_depth == 0 and _lock_fd is not None:
                fd = _lock_fd
                _lock_fd = None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    fd.close()
                except OSError:
                    pass


def _fresh_state() -> dict[str, Any]:
    return {"month": _current_month(), "calls": 0, "limit": QUOTA_LIMIT,
            "firecrawl_credits": 0, "firecrawl_limit": FIRECRAWL_LIMIT}


def _read_quota_unlocked() -> dict[str, Any]:
    """Lê o ficheiro quota.json. O caller já segura o lock de quota."""
    if not os.path.exists(QUOTA_FILE):
        return _fresh_state()

    try:
        with open(QUOTA_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("quota.json corrompido — a reiniciar contador")
        return _fresh_state()

    # Reset se mudou de mês
    if data.get("month") != _current_month():
        logger.info(
            "quota: novo mês (%s → %s) — reset do contador",
            data.get("month"), _current_month(),
        )
        data = _fresh_state()
        _write_quota_unlocked(data)

    # Garantir que campos existem (compatibilidade retroativa)
    if "limit" not in data:
        data["limit"] = QUOTA_LIMIT
    if "firecrawl_credits" not in data:
        data["firecrawl_credits"] = 0
    if "firecrawl_limit" not in data:
        data["firecrawl_limit"] = FIRECRAWL_LIMIT

    return data


def _read_quota() -> dict[str, Any]:
    """Lê o ficheiro quota.json sob o lock de quota."""
    with _quota_file_lock():
        return _read_quota_unlocked()


def _write_quota_unlocked(data: dict[str, Any]) -> None:
    """Escreve o estado da quota para disco (escrita atómica). O caller já
    segura o lock de quota."""
    try:
        atomic_write_json(QUOTA_FILE, data)
    except OSError as e:
        logger.warning("quota: erro ao escrever quota.json — %s", e)


def _write_quota(data: dict[str, Any]) -> None:
    """Escreve o estado da quota para disco (escrita atómica, sob lock)."""
    with _quota_file_lock():
        _write_quota_unlocked(data)


def can_spend(n: int = 1) -> bool:
    """Verifica se há quota disponível para n calls."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        return (data.get("calls", 0) + n) <= data.get("limit", QUOTA_LIMIT)


def spend(n: int = 1) -> None:
    """Regista n calls SerpAPI no contador do mês corrente."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        data["calls"] = data.get("calls", 0) + n
        _write_quota_unlocked(data)
        remaining_val = data["limit"] - data["calls"]
        logger.info("quota: +%d call(s) → %d/%d usadas (%d restantes)", n, data["calls"], data["limit"], remaining_val)


def remaining() -> int:
    """Devolve o número de calls restantes este mês."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        return max(0, data.get("limit", QUOTA_LIMIT) - data.get("calls", 0))


def get_usage() -> dict[str, Any]:
    """Devolve estado completo da quota."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        return {
            "month": data["month"],
            "calls": data.get("calls", 0),
            "limit": data["limit"],
            "remaining": max(0, data["limit"] - data.get("calls", 0)),
            "firecrawl_credits": data.get("firecrawl_credits", 0),
            "firecrawl_limit": data.get("firecrawl_limit", FIRECRAWL_LIMIT),
            "firecrawl_remaining": max(0, data.get("firecrawl_limit", FIRECRAWL_LIMIT) - data.get("firecrawl_credits", 0)),
        }


# ── Firecrawl credits ──


def can_spend_firecrawl(n: int = 1) -> bool:
    """Verifica se há credits Firecrawl disponíveis."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        return (data.get("firecrawl_credits", 0) + n) <= data.get("firecrawl_limit", FIRECRAWL_LIMIT)


def spend_firecrawl(n: int = 1) -> None:
    """Regista n credits Firecrawl no contador do mês corrente."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        data["firecrawl_credits"] = data.get("firecrawl_credits", 0) + n
        _write_quota_unlocked(data)
        credits = data["firecrawl_credits"]
        logger.info("firecrawl: +%d credit(s) → %d/%d usados (%d restantes)",
                    n, credits, data.get("firecrawl_limit", FIRECRAWL_LIMIT),
                    max(0, data.get("firecrawl_limit", FIRECRAWL_LIMIT) - credits))


def firecrawl_near_warning() -> bool:
    """Devolve True se os credits Firecrawl ultrapassaram o soft warning (800)."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        return data.get("firecrawl_credits", 0) >= FIRECRAWL_WARNING


def get_firecrawl_usage() -> dict[str, Any]:
    """Devolve estado dos credits Firecrawl."""
    with _quota_file_lock():
        data = _read_quota_unlocked()
        limit = data.get("firecrawl_limit", FIRECRAWL_LIMIT)
        credits = data.get("firecrawl_credits", 0)
        return {
            "credits": credits,
            "limit": limit,
            "remaining": max(0, limit - credits),
            "near_warning": credits >= FIRECRAWL_WARNING,
        }


# ── Reservation / commit transactions ──


class QuotaReservation:
    """One quota slot held by an active provider transaction.

    ``available`` is decided atomically at transaction entry (under the
    quota lock).  ``charge()`` persists the call; releasing the transaction
    without ``charge()`` persists nothing, matching the legacy accounting
    where a provider call that fails before the charge point is not counted.
    """

    __slots__ = ("firecrawl", "available", "_charged")

    def __init__(self, *, firecrawl: bool, available: bool) -> None:
        self.firecrawl = firecrawl
        self.available = available
        self._charged = 0

    def charge(self, n: int = 1) -> None:
        """Persist n charged calls/credits (the transaction holds the lock)."""
        if not self.available:
            raise RuntimeError("quota: sem reserva disponível — charge() inválido")
        data = _read_quota_unlocked()
        key = "firecrawl_credits" if self.firecrawl else "calls"
        data[key] = data.get(key, 0) + n
        _write_quota_unlocked(data)
        self._charged += n


@contextmanager
def transaction(n: int = 1, *, firecrawl: bool = False) -> Iterator[QuotaReservation]:
    """Hold the quota lock across one provider transaction (reservation).

    The lock is acquired before the availability check and held until the
    provider call finishes, so the check at entry and any ``charge()`` at
    exit are atomic against other processes: a concurrent process blocks on
    the file lock instead of interleaving a spend between our check and our
    charge.  This serializes provider calls across processes (accepted).
    """
    with _quota_file_lock():
        data = _read_quota_unlocked()
        limit = data.get("firecrawl_limit", FIRECRAWL_LIMIT) if firecrawl else data.get("limit", QUOTA_LIMIT)
        used = data.get("firecrawl_credits", 0) if firecrawl else data.get("calls", 0)
        reservation = QuotaReservation(firecrawl=firecrawl, available=(used + n) <= limit)
        yield reservation
