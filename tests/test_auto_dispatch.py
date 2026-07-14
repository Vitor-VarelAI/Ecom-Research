"""
Testes do dispatch automático de dias (--auto).

Mocka datetime para simular diferentes dias da semana e verificar
que o dispatch chama a função correta.

Sem chamadas reais à rede.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

# Garantir que o repo root está no path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import run as run_module


@pytest.fixture
def mock_adapters():
    """Stub de todos os adapters para evitar chamadas reais."""
    with patch.object(run_module.meta_ads, "fetch") as mock_meta, \
         patch.object(run_module.trends, "fetch") as mock_trends, \
         patch.object(run_module.serp_seo, "fetch") as mock_seo, \
         patch.object(run_module.quota, "can_spend", return_value=True), \
         patch.object(run_module.quota, "spend"):
        # Meta: 3 ads ativos (para não trigger pre-filter)
        mock_meta.return_value = {
            "total_ads": 3,
            "top_ads": [{"advertiser": "Test", "days_active": 30, "reach_eu": 100, "link": ""}],
            "copy_angles": ["Test copy"],
            "saturation": "baixa",
        }
        # Trends
        mock_trends.return_value = {
            "direction": "estável",
            "slope_pct": 0.0,
            "seasonal": False,
            "peak": None,
            "top_geos": [{"geo": "PT", "value": 100}],
            "related_queries": [],
        }
        # SEO
        mock_seo.return_value = {
            "organic_results": [{"position": 1, "domain": "test.pt", "title": "Test"}],
            "ads_count": 2,
            "paa_questions": ["Pergunta?"],
            "seo_opportunity": "alta",
        }
        yield


class TestAutoDispatchDays:
    """Testa que --auto despacha para a função correta conforme o dia."""

    def test_sunday_dispatches_to_sunday(self, mock_adapters, monkeypatch, tmp_path):
        """Domingo → _run_sunday."""
        # Mockar _load_keywords para devolver keywords de teste
        monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))

        # Criar keywords.txt de teste
        config_dir = os.path.join(str(tmp_path), "config")
        os.makedirs(config_dir, exist_ok=True)
        kw_path = os.path.join(config_dir, "keywords.txt")
        with open(kw_path, "w") as f:
            f.write("keyword-teste-1\nkeyword-teste-2\n")

        # Criar diretórios necessários
        os.makedirs(os.path.join(str(tmp_path), "packs"), exist_ok=True)
        os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)
        os.makedirs(os.path.join(str(tmp_path), "data", "cache"), exist_ok=True)

        # Mock CSV para evitar erro
        monkeypatch.setattr(run_module.keyword_csv, "parse", lambda path: {
            "volume": 1000, "cpc": 0.50, "competition": "MÉDIA",
            "related_keywords": [], "total_keywords": 1,
        })

        with patch("run.date") as mock_date:
            # Domingo = 2026-07-19 (domingo, weekday=6)
            mock_date.today.return_value = __import__("datetime").date(2026, 7, 19)
            mock_date.side_effect = None

            # Mock _run_sunday
            with patch.object(run_module, "_run_sunday", return_value=[]) as mock_sunday, \
                 patch.object(run_module, "_run_wednesday", return_value=[]) as mock_wed, \
                 patch.object(run_module, "_run_monthly", return_value=[]) as mock_monthly:
                run_module._auto_dispatch(send=False)

                mock_sunday.assert_called_once()
                mock_wed.assert_not_called()
                mock_monthly.assert_not_called()

    def test_wednesday_dispatches_to_wednesday(self, mock_adapters, monkeypatch, tmp_path):
        """Quarta → _run_wednesday."""
        monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
        os.makedirs(os.path.join(str(tmp_path), "packs"), exist_ok=True)
        os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)

        with patch("run.date") as mock_date:
            mock_date.today.return_value = __import__("datetime").date(2026, 7, 15)  # quarta
            mock_date.side_effect = None

            with patch.object(run_module, "_run_sunday", return_value=[]) as mock_sunday, \
                 patch.object(run_module, "_run_wednesday", return_value=[]) as mock_wed, \
                 patch.object(run_module, "_run_monthly", return_value=[]) as mock_monthly:
                run_module._auto_dispatch(send=False)

                mock_sunday.assert_not_called()
                mock_wed.assert_called_once()
                mock_monthly.assert_not_called()

    def test_first_day_dispatches_to_monthly(self, mock_adapters, monkeypatch, tmp_path):
        """Dia 1 → _run_monthly."""
        monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
        os.makedirs(os.path.join(str(tmp_path), "packs"), exist_ok=True)
        os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)

        with patch("run.date") as mock_date:
            # 2026-09-01 é uma terça-feira (weekday=1), mas dia 1
            mock_date.today.return_value = __import__("datetime").date(2026, 9, 1)
            mock_date.side_effect = None

            with patch.object(run_module, "_run_sunday", return_value=[]) as mock_sunday, \
                 patch.object(run_module, "_run_wednesday", return_value=[]) as mock_wed, \
                 patch.object(run_module, "_run_monthly", return_value=[]) as mock_monthly:
                run_module._auto_dispatch(send=False)

                mock_sunday.assert_not_called()
                mock_wed.assert_not_called()
                mock_monthly.assert_called_once()

    def test_random_day_returns_empty(self, mock_adapters, monkeypatch, tmp_path):
        """Terça-feira qualquer (não dia 1) → lista vazia."""
        monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
        os.makedirs(os.path.join(str(tmp_path), "packs"), exist_ok=True)
        os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)

        with patch("run.date") as mock_date:
            mock_date.today.return_value = __import__("datetime").date(2026, 7, 16)  # quinta
            mock_date.side_effect = None

            with patch.object(run_module, "_run_sunday", return_value=[]) as mock_sunday, \
                 patch.object(run_module, "_run_wednesday", return_value=[]) as mock_wed, \
                 patch.object(run_module, "_run_monthly", return_value=[]) as mock_monthly:
                result = run_module._auto_dispatch(send=False)

                mock_sunday.assert_not_called()
                mock_wed.assert_not_called()
                mock_monthly.assert_not_called()
                assert result == []

    def test_sunday_overrides_first(self, mock_adapters, monkeypatch, tmp_path):
        """Se domingo for dia 1, --sunday tem precedência."""
        monkeypatch.setattr(run_module, "REPO_ROOT", str(tmp_path))
        os.makedirs(os.path.join(str(tmp_path), "packs"), exist_ok=True)
        os.makedirs(os.path.join(str(tmp_path), "data"), exist_ok=True)

        # Criar keywords.txt vazio para não processar nada
        config_dir = os.path.join(str(tmp_path), "config")
        os.makedirs(config_dir, exist_ok=True)
        kw_path = os.path.join(config_dir, "keywords.txt")
        with open(kw_path, "w") as f:
            f.write("# vazio\n")

        with patch("run.date") as mock_date:
            # 2026-03-01 é domingo E dia 1
            mock_date.today.return_value = __import__("datetime").date(2026, 3, 1)
            mock_date.side_effect = None

            with patch.object(run_module, "_run_sunday", return_value=[]) as mock_sunday, \
                 patch.object(run_module, "_run_wednesday", return_value=[]) as mock_wed, \
                 patch.object(run_module, "_run_monthly", return_value=[]) as mock_monthly:
                run_module._auto_dispatch(send=False)

                mock_sunday.assert_called_once()  # domingo priority
                mock_wed.assert_not_called()
                # monthly também não é chamado porque sunday foi chamado primeiro
