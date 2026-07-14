"""
Adapter: Google Trends via SerpAPI.

Endpoint: GET https://serpapi.com/search
Parâmetros: engine=google_trends, q=keyword, date=today+3-m (90 dias)

Cache: /data/cache/{keyword_slug}.json com TTL de 30 dias.
Se cache válida, ZERO chamadas à API.

Toda chamada à SerpAPI passa pelo quota.py: can_spend() antes, spend() depois.

Interface comum: fetch(keyword) -> dict
Em caso de erro, devolve {"error": "razão"} — nunca levanta excepção.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from pipeline import quota

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache")
CACHE_TTL = 30 * 24 * 3600  # 30 dias em segundos
REQUEST_TIMEOUT = 15


def _get_api_key() -> str | None:
    return os.getenv("SERPAPI_KEY")


def _slugify(keyword: str) -> str:
    """Gera um nome de ficheiro seguro a partir da keyword."""
    slug = keyword.lower().strip()
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
    slug = "-".join(slug.split("-")).strip("-")
    return hashlib.md5(keyword.encode()).hexdigest()[:12]


def _cache_path(keyword: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{_slugify(keyword)}.json")


def _read_cache(keyword: str) -> dict[str, Any] | None:
    """Lê cache se existir e for válida (< 7 dias)."""
    path = _cache_path(keyword)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Trends cache: ficheiro corrompido para keyword=%s", keyword)
        return None

    cached_ts = data.get("_cached_at", 0)
    if time.time() - cached_ts > CACHE_TTL:
        logger.info("Trends cache: expirada para keyword=%s", keyword)
        return None

    logger.info("Trends cache: HIT para keyword=%s (idade=%ds)", keyword, int(time.time() - cached_ts))
    return data


def _write_cache(keyword: str, data: dict[str, Any]) -> None:
    """Escreve dados na cache com timestamp."""
    data["_cached_at"] = time.time()
    path = _cache_path(keyword)
    try:
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Trends cache: escrita para keyword=%s", keyword)
    except OSError as e:
        logger.warning("Trends cache: erro ao escrever — %s", e)


def _parse_serpapi_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Extrai informação relevante da resposta raw da SerpAPI Google Trends.
    Devolve dict com direction, slope_pct, seasonal, peak, top_geos.
    """
    interest_over_time = data.get("interest_over_time", {})
    timeline = interest_over_time.get("timeline_data", [])

    # Calcular slope % (diferença entre primeira e última metade)
    if not timeline or len(timeline) < 2:
        return {
            "direction": "estável",
            "slope_pct": 0.0,
            "seasonal": False,
            "peak": None,
            "top_geos": [],
            "error": "Trends: dados insuficientes (< 2 pontos no timeline)",
        }

    values = []
    for point in timeline:
        vals = point.get("values", [])
        if vals and isinstance(vals, list) and len(vals) > 0:
            try:
                values.append(float(vals[0].get("extracted_value", 0)))
            except (ValueError, TypeError, KeyError):
                values.append(0.0)

    if not values or len(values) < 2:
        return {
            "direction": "estável",
            "slope_pct": 0.0,
            "seasonal": False,
            "peak": None,
            "top_geos": [],
            "error": "Trends: não foi possível extrair valores do timeline",
        }

    # Slope: compara média da primeira metade com média da segunda metade
    mid = len(values) // 2
    first_half_avg = sum(values[:mid]) / mid if mid > 0 else values[0]
    second_half_avg = sum(values[mid:]) / (len(values) - mid) if (len(values) - mid) > 0 else values[-1]

    if first_half_avg > 0:
        slope_pct = ((second_half_avg - first_half_avg) / first_half_avg) * 100
    else:
        slope_pct = 0.0 if second_half_avg == 0 else 100.0

    # Direção
    if slope_pct > 5:
        direction = "subida"
    elif slope_pct < -5:
        direction = "queda"
    else:
        direction = "estável"

    # Sazonalidade: amplitude relativa > 1.0 (máx-mín > média)
    max_val = max(values)
    min_val = min(values)
    avg_val = sum(values) / len(values)
    # Ex: se o pico é 3x maior que a média, é sazonal
    seasonal = (max_val - min_val) > avg_val if avg_val > 0 else False
    peak_idx = values.index(max_val) if seasonal else None
    peak_label = None
    if peak_idx is not None and peak_idx < len(timeline):
        peak_label = timeline[peak_idx].get("date", None)

    # Top geos
    interest_by_region = data.get("interest_by_region", [])
    top_geos: list[dict[str, Any]] = []
    if isinstance(interest_by_region, list):
        top_geos = [
            {"geo": r.get("geo", "?"), "value": r.get("value", 0)}
            for r in interest_by_region[:3]
        ]

    return {
        "direction": direction,
        "slope_pct": round(slope_pct, 1),
        "seasonal": seasonal,
        "peak": peak_label,
        "top_geos": top_geos,
    }


def fetch(keyword: str) -> dict[str, Any]:
    """
    Dado uma keyword, consulta Google Trends via SerpAPI (com cache 30 dias) e devolve:
    {
        "direction": "subida" | "estável" | "queda",
        "slope_pct": float,
        "seasonal": bool,
        "peak": str | None,
        "top_geos": [ {geo, value}, ... ],
        "related_queries": [ {keyword, score}, ... ],  # queries em subida
    }
    Em caso de erro:
    {
        "error": "razão do erro"
    }
    """
    # 1. Tentar cache
    cached = _read_cache(keyword)
    if cached is not None:
        # Remove metadados internos antes de devolver
        result = {k: v for k, v in cached.items() if k != "_cached_at"}
        return result

    # 2. Cache miss — verificar quota (2 calls: timeseries + related_queries)
    if not quota.can_spend(2):
        return {"error": "Quota SerpAPI esgotada para este mês (230/250). Tente no próximo mês."}

    api_key = _get_api_key()
    if not api_key:
        return {"error": "SERPAPI_KEY não definida em .env"}

    # --- Call 1: TIMESERIES ---
    params_ts = {
        "engine": "google_trends",
        "q": keyword,
        "date": "today+3-m",  # últimos 90 dias
        "api_key": api_key,
    }

    try:
        resp_ts = requests.get(SERPAPI_URL, params=params_ts, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("SerpAPI Trends: erro de rede — %s", e)
        return {"error": f"Erro de rede na SerpAPI: {e}"}

    if resp_ts.status_code == 403:
        return {"error": "SerpAPI Trends: chave inválida ou quota esgotada (HTTP 403)"}
    if resp_ts.status_code != 200:
        logger.warning("SerpAPI Trends: HTTP %s — %s", resp_ts.status_code, resp_ts.text[:300])
        return {"error": f"SerpAPI Trends: HTTP {resp_ts.status_code}"}

    try:
        raw_ts = resp_ts.json()
    except ValueError:
        return {"error": "SerpAPI Trends: resposta não é JSON válido"}

    if "error" in raw_ts:
        return {"error": f"SerpAPI Trends: {raw_ts['error']}"}

    # 1 call gasta
    quota.spend(1)

    # --- Call 2: RELATED_QUERIES ---
    params_rq = {
        "engine": "google_trends",
        "q": keyword,
        "data_type": "RELATED_QUERIES",
        "api_key": api_key,
    }

    related_queries: list[dict[str, Any]] = []
    try:
        resp_rq = requests.get(SERPAPI_URL, params=params_rq, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("SerpAPI RELATED_QUERIES: erro de rede — %s", e)
        # Degradação graciosa: prosseguir sem related_queries
    else:
        if resp_rq.status_code == 200:
            try:
                raw_rq = resp_rq.json()
            except ValueError:
                logger.warning("SerpAPI RELATED_QUERIES: resposta não é JSON válido")
            else:
                if "error" not in raw_rq:
                    rq_data = raw_rq.get("related_queries", {})
                    rising_list = rq_data.get("rising", [])
                    if isinstance(rising_list, list):
                        for item in rising_list:
                            if isinstance(item, dict):
                                related_queries.append({
                                    "keyword": item.get("query", item.get("keyword", "")),
                                    "score": item.get("extracted_value", item.get("value", 0)),
                                })
                        related_queries.sort(key=lambda x: x["score"], reverse=True)
        else:
            logger.warning("SerpAPI RELATED_QUERIES: HTTP %s", resp_rq.status_code)

    # 2ª call gasta (mesmo se falhou — a SerpAPI conta sempre)
    quota.spend(1)

    # Parse e cache
    result = _parse_serpapi_response(raw_ts)
    result["related_queries"] = related_queries
    _write_cache(keyword, result)
    return result
