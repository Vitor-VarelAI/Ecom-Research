"""
Adapter: Google Keyword Planner CSV Parser.

Faz parse do export CSV do Google Keyword Planner (formato standard PT).
Resiliente a variações de header — usa matching case-insensitive e múltiplos
nomes possíveis para cada coluna.

Interface comum: parse(filepath) -> dict
Em caso de erro, devolve {"error": "razão"} — nunca levanta excepção.
"""

import codecs
import csv
import io
import logging
import os
import unicodedata
from typing import Any

from pipeline.suggestions import is_product_related

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
    "competition (indexed value)": "competition_indexed",
}


def _normalize_header(header: str) -> str:
    """Normaliza um header para matching."""
    return header.strip().lower()


def _normalize_keyword(value: Any) -> str:
    """Normaliza uma keyword para matching exacto: case-insensitive e
    accent-insensitive (NFKD + strip de combining chars)."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold().strip()


def _decode_utf16(payload: bytes, encoding: str) -> str:
    """UTF-16 decode que nunca levanta: dados truncados (comprimento ímpar)
    ou surrogates órfãos caem para latin-1 (que nunca falha), preservando o
    contrato gracioso do adapter."""
    try:
        return payload.decode(encoding)
    except (UnicodeDecodeError, ValueError):
        return payload.decode("latin-1")


def _read_text(filepath: str) -> str:
    """Lê o ficheiro detectando o encoding: BOM UTF-16 LE/BE e BOM UTF-8 são
    reconhecidos; sem BOM, UTF-8 estrito, depois UTF-16 LE (bytes NUL) e por
    fim latin-1.  Decodes UTF-16 malformados (comprimento ímpar, surrogates
    órfãos) caem para latin-1.  Nunca falha."""
    with open(filepath, "rb") as f:
        raw = f.read()
    if raw.startswith(codecs.BOM_UTF16_LE):
        return _decode_utf16(raw[len(codecs.BOM_UTF16_LE):], "utf-16-le")
    if raw.startswith(codecs.BOM_UTF16_BE):
        return _decode_utf16(raw[len(codecs.BOM_UTF16_BE):], "utf-16-be")
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8")
    if b"\x00" in raw:
        return _decode_utf16(raw, "utf-16-le")  # NULs a cada 2 bytes → UTF-16 sem BOM
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_candidates(text: str) -> tuple[list[list[str]], tuple[list[str], list[list[str]]]] | None:
    """Parse ``text`` independently with tab then comma and return the first
    candidate whose header is recognized (``_find_header``).

    Returns ``(all_rows, header)`` for the winning delimiter or None when
    neither delimiter yields a recognized header.  This replaces the old
    whole-file character-count heuristic (``_detect_delimiter``): a
    tab-export whose data contains many commas must still be parsed as tab,
    and a comma CSV with tabs inside quoted fields must still be parsed as
    comma — the header, not the raw counts, decides.
    """
    for delimiter in ("\t", ","):
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            all_rows = [fields for fields in reader if fields and any(field.strip() for field in fields)]
        except (csv.Error, UnicodeDecodeError):
            continue
        header = _find_header(all_rows)
        if header is not None:
            return all_rows, header
    return None


def _first_nonempty_line(text: str) -> str:
    """Primeira linha não vazia do texto (para a mensagem de erro)."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _find_header(rows: list[list[str]]) -> tuple[list[str], list[list[str]]] | None:
    """Primeira linha que pareça um header — pelo menos 2 campos reconhecidos
    no COLUMN_MAP.  Devolve (headers, linhas seguintes) ou None.  Tolerante a
    preâmbulos (título, intervalo de datas) antes do header."""
    for index, fields in enumerate(rows):
        if len(fields) < 2:
            continue
        recognized = sum(1 for field in fields if _normalize_header(field) in COLUMN_MAP)
        if recognized >= 2:
            return fields, rows[index + 1:]
    return None


def _looks_like_keyword_planner(rows: list[dict[str, str]]) -> bool:
    """Heurística: verifica se o CSV parece ser um export do Keyword Planner."""
    if not rows:
        return False
    # Pelo menos 80% das linhas devem ter uma keyword não vazia
    keywords_found = sum(1 for r in rows if r.get("keyword", "").strip())
    return keywords_found >= max(1, len(rows) * 0.8)


def parse(filepath: str, keyword: str | None = None) -> dict[str, Any]:
    """
    Faz parse de um CSV exportado do Google Keyword Planner.

    Detecta o encoding (UTF-16 LE/BE com BOM, UTF-8 com BOM, UTF-8, latin-1),
    o delimiter (tab ou vírgula) e a linha real do header (tolerante a
    preâmbulos como o título e o intervalo de datas do export actual) — nada
    é hardcoded a um nome/linha/coluna específicos.

    Quando ``keyword`` é fornecida, a linha seleccionada é a que corresponde
    exactamente (matching normalizado case/accent-insensitive) a essa keyword;
    se a keyword não existir no export, devolve erro explícito — nunca as
    métricas de outra linha.  Em caso de sucesso keyword-aware, ``matched_keyword``
    repete o texto exacto da linha do CSV que serviu a keyword (prova de
    ligação linha-resultado nos artefactos).  As related são filtradas pela
    política de product head de ``pipeline.suggestions`` (``is_product_related``):
    o token de produto da keyword pedida tem de aparecer no candidato sob
    equivalência exacta ou variante de um único ``s`` final, acento/case-folded
    (``camisola``~``camisolas``; nunca prefixos arbitrários — ``pasta``~``pastelaria``
    não casa) — nunca basta overlap por stopwords como ``de`` (``caneca de
    natal`` não é related de ``camisola de portugal``) nem por geografia
    sozinha — antes da selecção top-5 por volume.  Sem ``keyword``, mantém o
    comportamento histórico: a linha de maior volume e top 5 sem filtro.

    Devolve:
    {
        "volume": int | None,             # volume mensal da keyword principal
        "cpc": float | None,              # CPC médio em EUR
        "competition": str | None,        # nível textual de competição (ex.: High)
        "matched_keyword": str,           # só em modo keyword-aware: texto exacto no CSV
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
        text = _read_text(filepath)
        parsed = _parse_candidates(text)
    except OSError as e:
        return {"error": f"Erro de ficheiro: {e}"}

    if parsed is None:
        first = _first_nonempty_line(text)
        return {"error": f"CSV: headers não reconhecidos (tab nem vírgula) — esperado colunas Keyword/Volume/CPC. Primeira linha: {first}"}
    all_rows, header = parsed
    fieldnames, data_rows = header

    if not data_rows:
        return {"error": "CSV vazio — zero linhas de dados"}

    # Mapear headers
    col_index: dict[str, str] = {}  # header real -> chave canónica
    for fn in fieldnames:
        norm = _normalize_header(fn)
        if norm in COLUMN_MAP:
            col_index[fn] = COLUMN_MAP[norm]

    # Converter linhas
    rows: list[dict[str, Any]] = []
    for fields in data_rows:
        raw = dict(zip(fieldnames, fields))
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
            elif canon_key == "competition_indexed":
                row[canon_key] = val
            else:
                row[canon_key] = val
        # Competition textual (ex.: "High") vence; o indexed só é usado como
        # fallback quando não existe valor textual.
        if not (row.get("competition") or "").strip() and row.get("competition_indexed"):
            row["competition"] = row["competition_indexed"]
        rows.append(row)

    # Filtrar linhas com keyword
    rows_with_keyword = [r for r in rows if r.get("keyword", "").strip()]
    if not rows_with_keyword:
        return {"error": "CSV: zero keywords com nome"}

    # Ordenar por volume decrescente
    rows_with_keyword.sort(key=lambda r: r.get("volume", 0) or 0, reverse=True)

    # Selecção por keyword (matching exacto normalizado); sem keyword, a linha
    # de maior volume (comportamento histórico).
    wanted = _normalize_keyword(keyword) if keyword is not None else None
    if wanted is not None:
        matches = [r for r in rows_with_keyword if _normalize_keyword(r.get("keyword", "")) == wanted]
        if not matches:
            return {
                "error": f"Keyword '{keyword}' não encontrada no CSV (matching exacto normalizado)"
            }
        main = matches[0]  # maior volume entre os matches exactos
    else:
        main = rows_with_keyword[0]
    volume = main.get("volume", 0) if main.get("volume", 0) > 0 else None
    cpc = main.get("cpc")
    competition = main.get("competition")

    # Em modo keyword-aware, filtrar as related ANTES da selecção top-5 por
    # volume: cada candidata tem de conter o product head da keyword pedida
    # (``is_product_related`` em pipeline/suggestions — acento/case-folded,
    # equivalência exacta ou variante de um único 's' final, nunca prefixos
    # arbitrários), rejeitando
    # linhas de outros produtos e overlap só por stopwords (``de``) ou por
    # geografia.  O pool mantém a ordenação por volume já aplicada; sem
    # ``keyword``, nenhum filtro corre (comportamento histórico intacto).
    related_pool = rows_with_keyword
    if wanted is not None:
        related_pool = [
            r for r in rows_with_keyword
            if is_product_related(keyword, r.get("keyword", ""))
        ]

    # Top 5 related (excluindo a keyword principal — e, em modo keyword-aware,
    # todas as linhas que correspondem à keyword pedida)
    related = []
    for r in related_pool:
        if len(related) >= 5:
            break
        if r is main:
            continue
        if wanted is not None and _normalize_keyword(r.get("keyword", "")) == wanted:
            continue
        kw = r.get("keyword", "").strip()
        vol = r.get("volume", 0) or 0
        if kw and vol > 0:
            related.append({"keyword": kw, "volume": vol})

    result = {
        "volume": volume,
        "cpc": cpc,
        "competition": competition,
        "related_keywords": related,
        "total_keywords": len(rows_with_keyword),
    }
    if wanted is not None:
        # Prova de ligação linha-resultado: o texto exacto da linha do CSV.
        result["matched_keyword"] = (main.get("keyword") or "").strip()
    return result
