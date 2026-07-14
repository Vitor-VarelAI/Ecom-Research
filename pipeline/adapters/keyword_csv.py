"""
Adapter: Google Keyword Planner CSV Parser.

Faz parse do export CSV do Google Keyword Planner (formato standard PT).
Resiliente a variações de header — usa matching case-insensitive e múltiplos
nomes possíveis para cada coluna.

Interface comum: parse(filepath) -> dict
Em caso de erro, devolve {"error": "razão"} — nunca levanta excepção.
"""

import csv
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Mapeamento de nomes de coluna possíveis (lowercase) -> chave canónica
COLUMN_MAP: dict[str, str] = {
    # Keyword
    "keyword": "keyword",
    "palavra-chave": "keyword",
    "search keyword": "keyword",
    "palavra": "keyword",
    # Volume mensal
    "avg. monthly searches": "volume",
    "avg monthly searches": "volume",
    "average monthly searches": "volume",
    "monthly volume": "volume",
    "pesquisas mensais": "volume",
    "volume mensal": "volume",
    "volume de pesquisas mensais": "volume",
    "volume": "volume",
    # CPC
    "top of page bid (low range)": "cpc",
    "top of page bid (high range)": "cpc_high",
    "top of page bid": "cpc",
    "cpc": "cpc",
    "cpc médio": "cpc",
    "custo por clique": "cpc",
    # Competition
    "competition": "competition",
    "concorrência": "competition",
    "competition (indexed value)": "competition",
}


def _normalize_header(header: str) -> str:
    """Normaliza um header para matching."""
    return header.strip().lower()


def _looks_like_keyword_planner(rows: list[dict[str, str]]) -> bool:
    """Heurística: verifica se o CSV parece ser um export do Keyword Planner."""
    if not rows:
        return False
    # Pelo menos 80% das linhas devem ter uma keyword não vazia
    keywords_found = sum(1 for r in rows if r.get("keyword", "").strip())
    return keywords_found >= max(1, len(rows) * 0.8)


def parse(filepath: str) -> dict[str, Any]:
    """
    Faz parse de um CSV exportado do Google Keyword Planner.

    Devolve:
    {
        "volume": int | None,             # volume mensal da keyword principal
        "cpc": float | None,              # CPC médio em EUR
        "competition": str | None,        # nível de competição
        "related_keywords": [             # top 5 keywords relacionadas com volume
            {"keyword": str, "volume": int},
            ...
        ],
        "total_keywords": int,            # total de linhas no CSV
    }
    Em caso de erro:
    {
        "error": "razão do erro"
    }
    """
    if not os.path.exists(filepath):
        return {"error": f"Ficheiro CSV não encontrado: {filepath}"}

    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            raw_rows = list(reader)
    except (csv.Error, UnicodeDecodeError) as e:
        logger.warning("CSV: erro de parsing — %s", e)
        return {"error": f"Erro ao ler CSV: {e}"}
    except OSError as e:
        return {"error": f"Erro de ficheiro: {e}"}

    if not raw_rows:
        return {"error": "CSV vazio — zero linhas de dados"}

    # Mapear headers
    fieldnames = raw_rows[0].keys() if raw_rows else []
    col_index: dict[str, str] = {}  # header real -> chave canónica
    for fn in fieldnames:
        norm = _normalize_header(fn)
        if norm in COLUMN_MAP:
            col_index[fn] = COLUMN_MAP[norm]

    if not col_index:
        return {"error": f"CSV: headers não reconhecidos — esperado colunas Keyword/Volume/CPC. Headers encontrados: {list(fieldnames)}"}

    # Converter linhas
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        if raw is None:
            continue  # linha corrompida, ignorar
        row: dict[str, Any] = {}
        for src_key, canon_key in col_index.items():
            val = (raw.get(src_key) or "").strip()
            if canon_key in ("volume",):
                try:
                    row[canon_key] = int(val.replace(",", "").replace(".", ""))
                except (ValueError, TypeError):
                    row[canon_key] = 0
            elif canon_key in ("cpc", "cpc_high"):
                try:
                    row[canon_key] = float(val.replace(",", ".").replace("€", "").replace("R$", "").strip())
                except (ValueError, TypeError):
                    row[canon_key] = None
            else:
                row[canon_key] = val
        rows.append(row)

    # Filtrar linhas com keyword
    rows_with_keyword = [r for r in rows if r.get("keyword", "").strip()]
    if not rows_with_keyword:
        return {"error": "CSV: zero keywords com nome"}

    # Ordenar por volume decrescente
    rows_with_keyword.sort(key=lambda r: r.get("volume", 0) or 0, reverse=True)

    # Primeira linha = keyword principal (maior volume)
    main = rows_with_keyword[0]
    volume = main.get("volume", 0) if main.get("volume", 0) > 0 else None
    cpc = main.get("cpc")
    competition = main.get("competition")

    # Top 5 related (excluindo a principal)
    related = []
    for r in rows_with_keyword[1:6]:
        kw = r.get("keyword", "").strip()
        vol = r.get("volume", 0) or 0
        if kw and vol > 0:
            related.append({"keyword": kw, "volume": vol})

    return {
        "volume": volume,
        "cpc": cpc,
        "competition": competition,
        "related_keywords": related,
        "total_keywords": len(rows_with_keyword),
    }
