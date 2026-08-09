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
import math
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


def _related_cache_path(keyword: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{_slugify(keyword)}_related.json")


def _valid_full_payload(data: dict[str, Any]) -> bool:
    """A Trends cache hit must look like a real timeseries result:
    ``direction`` a non-empty string, ``slope_pct`` a finite number and
    ``related_queries`` a list.  Anything else is a miss — malformed valid
    JSON must never be served as evidence nor suppress a refresh."""
    direction = data.get("direction")
    if not isinstance(direction, str) or not direction:
        return False
    slope = data.get("slope_pct")
    if isinstance(slope, bool) or not isinstance(slope, (int, float)):
        return False
    if not math.isfinite(float(slope)):
        return False
    if not isinstance(data.get("related_queries"), list):
        return False
    return True


def _read_cache(keyword: str) -> dict[str, Any] | None:
    """Lê cache se existir e for válida (< 30 dias).

    Entradas com payload de erro são tratadas como miss e removidas: um erro
    de parse nunca deve ficar em cache por 30 dias (correção de hardening).
    JSON válido que não seja um dict (lista, string, null), com
    ``_cached_at`` não numérico/não finito, ou com forma inválida
    (``direction``/``slope_pct``/``related_queries`` ausentes ou errados) é
    tratado como miss — nunca crash, nunca refresh suprimido.
    """
    path = _cache_path(keyword)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Trends cache: ficheiro corrompido para keyword=%s", keyword)
        return None

    if not isinstance(data, dict):
        logger.warning("Trends cache: payload não-dict para keyword=%s", keyword)
        return None
    if "error" in data:
        logger.warning("Trends cache: payload de erro antigo removido para keyword=%s", keyword)
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    cached_ts = _cached_at_value(data)
    if cached_ts is None or time.time() - cached_ts > CACHE_TTL:
        logger.info("Trends cache: expirada/inválida para keyword=%s", keyword)
        return None
    if not _valid_full_payload(data):
        logger.warning("Trends cache: payload com forma inválida para keyword=%s; miss", keyword)
        return None

    logger.info("Trends cache: HIT para keyword=%s (idade=%ds)", keyword, int(time.time() - cached_ts))
    return data


def _cached_at_value(data: dict[str, Any]) -> float | None:
    """Finite numeric ``_cached_at``, or None when absent/invalid.

    Rejects bools and non-finite floats (NaN/Infinity are valid JSON but
    useless timestamps), so a poisoned cache entry is a miss, never a
    fresh hit that suppresses a refresh.
    """
    value = data.get("_cached_at")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if not math.isfinite(timestamp):
        return None
    return timestamp


def _write_cache(keyword: str, data: dict[str, Any]) -> None:
    """Escreve dados na cache com timestamp.

    Payloads com erro nunca são cacheados: uma falha de parse é transitória e
    não deve bloquear novos fetches durante 30 dias.
    """
    if "error" in data:
        logger.warning("Trends cache: payload de erro NÃO escrito para keyword=%s", keyword)
        return
    data["_cached_at"] = time.time()
    path = _cache_path(keyword)
    try:
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Trends cache: escrita para keyword=%s", keyword)
    except OSError as e:
        logger.warning("Trends cache: erro ao escrever — %s", e)


def _read_related_cache(keyword: str) -> dict[str, Any] | None:
    path = _related_cache_path(keyword)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("Trends related cache: ficheiro corrompido para keyword=%s", keyword)
        return None
    if not isinstance(data, dict):
        logger.warning("Trends related cache: payload não-dict para keyword=%s", keyword)
        return None
    if "error" in data:
        logger.warning("Trends related cache: payload de erro antigo removido para keyword=%s", keyword)
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    cached_ts = _cached_at_value(data)
    if cached_ts is None or time.time() - cached_ts > CACHE_TTL:
        logger.info("Trends related cache: expirada/inválida para keyword=%s", keyword)
        return None
    if not isinstance(data.get("related_queries"), list):
        logger.warning("Trends related cache: payload sem lista de related queries para keyword=%s; miss", keyword)
        return None
    return {key: value for key, value in data.items() if key != "_cached_at"}


def _write_related_cache(keyword: str, data: dict[str, Any]) -> None:
    data_to_write = dict(data)
    data_to_write["_cached_at"] = time.time()
    try:
        with open(_related_cache_path(keyword), "w") as f:
            json.dump(data_to_write, f, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("Trends related cache: erro ao escrever — %s", exc)


def _parse_related_queries(data: dict[str, Any]) -> list[dict[str, Any]]:
    related = data.get("related_queries", {})
    rising = related.get("rising", []) if isinstance(related, dict) else []
    if not isinstance(rising, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in rising:
        if not isinstance(item, dict):
            continue
        raw_score = item.get("extracted_value", item.get("value", item.get("score", 0)))
        score: int | float | str
        if isinstance(raw_score, (int, float)):
            score = raw_score
        else:
            score = str(raw_score or "")
        parsed.append({
            "keyword": item.get("query", item.get("keyword", "")),
            "score": score,
            "type": item.get("type", ""),
        })
    return parsed


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


def fetch(keyword: str, timeseries_only: bool = False, force_refresh: bool = False) -> dict[str, Any]:
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

    Args:
        timeseries_only: se True, faz só a call TIMESERIES (1 quota), salta RELATED_QUERIES.
                         Usado no --monthly re-score. Default False.
        force_refresh: se True, ignora a cache e força nova chamada à API.
                       Usado no --monthly para garantir dados frescos. Default False.
    """
    # 1. Tentar cache (a menos que force_refresh)
    if not force_refresh:
        cached = _read_cache(keyword)
        if cached is not None:
            # Remove metadados internos antes de devolver
            result = {k: v for k, v in cached.items() if k != "_cached_at"}
            return result

    # 2. Cache miss — verificar quota (reserva/commit atómico sob lock)
    calls_needed = 1 if timeseries_only else 2
    with quota.transaction(calls_needed) as reservation:
        if not reservation.available:
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
        except Exception as exc:
            # Never propagate or log raw exception text/URL: it can echo the
            # request, including the api_key query parameter.  Log only the
            # exception type; return a stable sanitized message.
            logger.warning("SerpAPI Trends: erro de rede (%s)", type(exc).__name__)
            return {"error": f"Erro de rede na SerpAPI ({type(exc).__name__})"}

        if resp_ts.status_code == 403:
            return {"error": "SerpAPI Trends: chave inválida ou quota esgotada (HTTP 403)"}
        if resp_ts.status_code != 200:
            logger.warning("SerpAPI Trends: HTTP %s", resp_ts.status_code)
            return {"error": f"SerpAPI Trends: HTTP {resp_ts.status_code}"}

        try:
            raw_ts = resp_ts.json()
        except ValueError:
            return {"error": "SerpAPI Trends: resposta não é JSON válido"}

        if "error" in raw_ts:
            return {"error": f"SerpAPI Trends: {raw_ts['error']}"}

        # 1 call gasta (só após o sucesso da timeseries)
        reservation.charge(1)

        # --- Call 2: RELATED_QUERIES (saltar se timeseries_only) ---
        related_queries: list[dict[str, Any]] = []

        if not timeseries_only:
            params_rq = {
                "engine": "google_trends",
                "q": keyword,
                "data_type": "RELATED_QUERIES",
                "api_key": api_key,
            }

            try:
                resp_rq = requests.get(SERPAPI_URL, params=params_rq, timeout=REQUEST_TIMEOUT)
            except Exception as exc:
                logger.warning("SerpAPI RELATED_QUERIES: erro de rede (%s)", type(exc).__name__)
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
            reservation.charge(1)

        # Parse e cache (sempre reescrever, mesmo em force_refresh)
        result = _parse_serpapi_response(raw_ts)
        result["related_queries"] = related_queries
        _write_cache(keyword, result)
        return result


def fetch_related(keyword: str, force_refresh: bool = False) -> dict[str, Any]:
    """Fetch only related queries, with the same 30-day disk cache.

    This is used after a successful zero-ad result: it costs one SerpAPI
    search and gives the user alternatives without running the expensive
    timeseries or SEO calls.
    """
    if not force_refresh:
        cached = _read_related_cache(keyword)
        if cached is not None:
            return cached

    with quota.transaction(1) as reservation:
        if not reservation.available:
            return {"error": "Quota SerpAPI esgotada para este mês (230/250). Tente no próximo mês."}

        api_key = _get_api_key()
        if not api_key:
            return {"error": "SERPAPI_KEY não definida em .env"}

        params = {
            "engine": "google_trends",
            "q": keyword,
            "data_type": "RELATED_QUERIES",
            "api_key": api_key,
        }
        try:
            response = requests.get(SERPAPI_URL, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("SerpAPI RELATED_QUERIES: erro de rede")
            return {"error": f"Erro de rede na SerpAPI RELATED_QUERIES ({type(exc).__name__})"}

        # SerpAPI counts the request even when it returns an HTTP or JSON error.
        reservation.charge(1)
        if response.status_code != 200:
            return {"error": f"SerpAPI RELATED_QUERIES: HTTP {response.status_code}"}

        try:
            raw = response.json()
        except (ValueError, TypeError):
            return {"error": "SerpAPI RELATED_QUERIES: resposta não é JSON válido"}
        if not isinstance(raw, dict):
            return {"error": "SerpAPI RELATED_QUERIES: resposta inválida"}
        if "error" in raw:
            return {"error": f"SerpAPI RELATED_QUERIES: {raw['error']}"}

        result = {"related_queries": _parse_related_queries(raw)}
        _write_related_cache(keyword, result)
        return result
