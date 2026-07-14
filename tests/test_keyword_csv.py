"""
Testes do parser CSV Keyword Planner.

Usa ficheiros CSV temporários como fixture.
Sem chamadas reais à rede.
"""

import csv
import os
import tempfile
import textwrap

import pytest

from pipeline.adapters import keyword_csv


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_csv(content: str) -> str:
    """Escreve um CSV temporário e devolve o path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8-sig", newline=""
    )
    tmp.write(textwrap.dedent(content).strip())
    tmp.close()
    return tmp.name


# ── Tests ──────────────────────────────────────────────────────────────────


class TestKeywordCsvParse:
    """Testa o parsing de CSVs válidos."""

    def test_english_headers(self):
        """Headers em inglês (formato standard internacional)."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        mochila personalizada,5400,0.65,0.42
        mochila escolar,3200,0.80,0.35
        mochila viagem,2100,0.45,0.28
        mochila bebé,1800,0.30,0.22
        mochila desporto,950,0.55,0.18
        mochila gaming,800,0.70,0.15
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 5400
            assert result["cpc"] == 0.42
            assert result["total_keywords"] == 6
            assert len(result["related_keywords"]) == 5
            # Primeira relacionada deve ser a de maior volume
            assert result["related_keywords"][0]["keyword"] == "mochila escolar"
            assert result["related_keywords"][0]["volume"] == 3200
        finally:
            os.unlink(path)

    def test_portuguese_headers(self):
        """Headers em português (Google Ads PT)."""
        csv_content = """
        Palavra-chave,Pesquisas mensais,Concorrência,CPC médio
        caneca personalizada,3200,0.55,0.38
        caneca foto,1800,0.40,0.25
        caneca namorado,1200,0.30,0.20
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 3200
            assert result["cpc"] == 0.38
            assert result["total_keywords"] == 3
            assert len(result["related_keywords"]) == 2
        finally:
            os.unlink(path)

    def test_utf8_bom(self):
        """CSV com BOM (UTF-8-SIG) deve ser lido correctamente."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        vela aromática,8900,0.72,0.55
        vela soja,4500,0.60,0.42
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 8900
        finally:
            os.unlink(path)

    def test_single_keyword(self):
        """CSV com apenas 1 keyword."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        tapete yoga premium,1500,0.35,0.90
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 1500
            assert result["related_keywords"] == []
        finally:
            os.unlink(path)


class TestKeywordCsvErrors:
    """Testa tratamento de erros."""

    def test_file_not_found(self):
        result = keyword_csv.parse("/tmp/nao-existe-mesmo.csv")
        assert "error" in result
        assert "não encontrado" in result["error"].lower()

    def test_empty_csv(self):
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert "error" in result
            assert "vazio" in result["error"].lower()
        finally:
            os.unlink(path)

    def test_unknown_headers(self):
        """Headers completamente irreconhecíveis."""
        csv_content = """
        ColunaX,ColunaY,ColunaZ
        valor1,valor2,valor3
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert "error" in result
            assert "headers não reconhecidos" in result["error"].lower()
        finally:
            os.unlink(path)

    def test_corrupted_csv(self):
        """CSV com formatação inválida."""
        path = "/tmp/corrupt.csv"
        with open(path, "w") as f:
            f.write("Keyword,Volume\n")
            f.write('"unclosed quote,100\n')
        try:
            result = keyword_csv.parse(path)
            # Pode dar erro ou parse parcial — o importante é não crashar
            assert isinstance(result, dict)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_numbers_with_commas(self):
        """Volumes com vírgulas (ex: "5,400")."""
        csv_content = """
        Keyword,Avg. monthly searches,Competition,Top of page bid (low range)
        almofada personalizada,"5,400",0.45,0.33
        """
        path = _write_csv(csv_content)
        try:
            result = keyword_csv.parse(path)
            assert result["volume"] == 5400
        finally:
            os.unlink(path)
