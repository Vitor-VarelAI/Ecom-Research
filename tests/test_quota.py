"""
Testes do contador de quota SerpAPI.

Testa: bloqueio aos 230, reset dia 1, can_spend/spend/remaining/get_usage.
Sem chamadas reais à rede.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from pipeline import quota


# ── Helpers ────────────────────────────────────────────────────────────────


def _reset_quota_file(tmp_dir: str, calls: int = 0, month: str | None = None) -> str:
    """Cria um quota.json de teste e devolve o path do dir."""
    if month is None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
    quota_path = os.path.join(tmp_dir, "quota.json")
    data = {"month": month, "calls": calls, "limit": 230}
    with open(quota_path, "w") as f:
        json.dump(data, f)
    return quota_path


# ── Tests ──────────────────────────────────────────────────────────────────


class TestQuotaBasics:
    """Testa operações básicas do contador."""

    def test_initial_state(self, monkeypatch, tmp_path):
        """Estado inicial: zero calls."""
        quota_file = _reset_quota_file(str(tmp_path), calls=0)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        assert quota.can_spend(1) is True
        assert quota.remaining() == 230
        usage = quota.get_usage()
        assert usage["calls"] == 0
        assert usage["limit"] == 230
        assert usage["remaining"] == 230

    def test_spend_increments(self, monkeypatch, tmp_path):
        """spend() incrementa o contador."""
        quota_file = _reset_quota_file(str(tmp_path), calls=0)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        quota.spend(1)
        assert quota.remaining() == 229

        quota.spend(3)
        assert quota.remaining() == 226

        usage = quota.get_usage()
        assert usage["calls"] == 4

    def test_can_spend_blocks_at_limit(self, monkeypatch, tmp_path):
        """can_spend() bloqueia quando atinge o limite."""
        quota_file = _reset_quota_file(str(tmp_path), calls=229)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        # 229 + 1 = 230 → OK (at limit)
        assert quota.can_spend(1) is True

        # 229 + 2 = 231 > 230 → BLOCK
        assert quota.can_spend(2) is False

    def test_can_spend_at_exact_limit(self, monkeypatch, tmp_path):
        """No limite exacto (230), não pode gastar mais."""
        quota_file = _reset_quota_file(str(tmp_path), calls=230)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        assert quota.can_spend(1) is False
        assert quota.remaining() == 0

    def test_spend_beyond_limit_still_writes(self, monkeypatch, tmp_path):
        """spend() escreve mesmo acima do limite (can_spend é o guarda)."""
        quota_file = _reset_quota_file(str(tmp_path), calls=230)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        quota.spend(5)
        usage = quota.get_usage()
        assert usage["calls"] == 235
        assert usage["remaining"] == 0  # clamped


class TestQuotaMonthReset:
    """Testa o reset automático no dia 1."""

    def test_new_month_resets(self, monkeypatch, tmp_path):
        """Se o mês no ficheiro é diferente do mês corrente, reset."""
        # Criar quota do mês passado
        quota_file = _reset_quota_file(str(tmp_path), calls=200, month="2020-01")
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        # Deve resetar para o mês corrente com 0 calls
        assert quota.can_spend(1) is True
        usage = quota.get_usage()
        assert usage["calls"] == 0
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        assert usage["month"] == current_month

    def test_same_month_preserves(self, monkeypatch, tmp_path):
        """Mês corrente igual → preserva calls."""
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        quota_file = _reset_quota_file(str(tmp_path), calls=50, month=current_month)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        usage = quota.get_usage()
        assert usage["calls"] == 50
        assert usage["month"] == current_month


class TestQuotaCorrupted:
    """Testa resiliência a ficheiro corrompido."""

    def test_corrupted_file_resets(self, monkeypatch, tmp_path):
        """Ficheiro JSON inválido → reset automático."""
        quota_path = os.path.join(str(tmp_path), "quota.json")
        with open(quota_path, "w") as f:
            f.write("isto não é JSON {{{")

        monkeypatch.setattr(quota, "QUOTA_FILE", quota_path)

        usage = quota.get_usage()
        assert usage["calls"] == 0
        assert usage["limit"] == 230

    def test_missing_limit_defaults(self, monkeypatch, tmp_path):
        """Ficheiro sem campo 'limit' → usa default 230."""
        quota_path = os.path.join(str(tmp_path), "quota.json")
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        with open(quota_path, "w") as f:
            json.dump({"month": current_month, "calls": 10}, f)

        monkeypatch.setattr(quota, "QUOTA_FILE", quota_path)

        usage = quota.get_usage()
        assert usage["limit"] == 230
        assert usage["calls"] == 10


class TestQuotaPersists:
    """Testa que os valores persistem entre leituras."""

    def test_multiple_reads_consistent(self, monkeypatch, tmp_path):
        """Leituras consecutivas são consistentes."""
        quota_file = _reset_quota_file(str(tmp_path), calls=0)
        monkeypatch.setattr(quota, "QUOTA_FILE", quota_file)

        quota.spend(10)
        assert quota.remaining() == 220

        # Ler de novo (simula outro import)
        assert quota.remaining() == 220
        assert quota.get_usage()["calls"] == 10

        quota.spend(15)
        assert quota.get_usage()["calls"] == 25
