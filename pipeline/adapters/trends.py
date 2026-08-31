"""
Adapter: Google Trends via SerpAPI.

Endpoint: GET https://serpapi.com/search
Parâmetros: engine=google_trends, q=keyword, date=today 3-m (90 dias)

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

from pipeline.adapters import http_errors
from pipeline import quota

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache")
CACHE_TTL = 30 * 24 * 3600  # 30 dias em segundos
# Tolerância a skew de relógio: timestamps de cache mais de 5 minutos no
# futuro são inválidos (miss) — um relógio adiantado do escrevente nunca
# serve como cache fresca nem suprime refresh.
CLOCK_SKEW_TOLERANCE = 5 * 60
# A live timeseries call with ``date=today 3-m`` routinely exceeds 15s on the
# provider side; 60s is required for the Trends lookup to complete and charge
# quota instead of timing out without charging.
REQUEST_TIMEOUT = 60


def _get_api_key() -> str | None:
    return os.getenv("SERPAPI_KEY")


def _slugify(keyword: str) -> str:
    """Gera um nome de ficheiro seguro a partir da keyword."""
    slug = keyword.lower().strip()
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
    slug = "-".join(slug.split("-")).strip("-")
    return hashlib.md5(keyword.encode()).hexdigest()[:12]


def _cache_keyword(keyword: str, market: Any | None) -> str:
    return keyword if market is None else f"{market.market_id}:{keyword}"


def _cache_path(keyword: str, market: Any | None = None) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{_slugify(_cache_keyword(keyword, market))}.json")


def _related_cache_path(keyword: str, market: Any | None = None) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{_slugify(_cache_keyword(keyword, market))}_related.json")


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


def _read_cache(keyword: str, market: Any | None = None) -> dict[str, Any] | None:
    """Lê cache se existir e for válida (< 30 dias).

    Entradas com payload de erro são tratadas como miss e removidas: um erro
    de parse nunca deve ficar em cache por 30 dias (correção de hardening).
    JSON válido que não seja um dict (lista, string, null), com
    ``_cached_at`` não numérico/não finito, ou com forma inválida
    (``direction``/``slope_pct``/``related_queries`` ausentes ou errados) é
    tratado como miss — nunca crash, nunca refresh suprimido.
    """
    path = _cache_path(keyword, market)
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
    if (
        cached_ts is None
        or cached_ts > time.time() + CLOCK_SKEW_TOLERANCE
        or time.time() - cached_ts > CACHE_TTL
    ):
        logger.info("Trends cache: expirada/inválida para keyword=%s", keyword)
        return None
    if not _valid_full_payload(data):
        logger.warning("Trends cache: payload com forma inválida para keyword=%s; miss", keyword)
        return None
    if market is not None and (
        data.get("market_id") != market.market_id
        or data.get("query_geo") != market.trends_geo
    ):
        logger.warning("Trends cache: scope de mercado inválido para keyword=%s; miss", keyword)
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


def _write_cache(keyword: str, data: dict[str, Any], market: Any | None = None) -> None:
    """Escreve dados na cache com timestamp.

    Payloads com erro nunca são cacheados: uma falha de parse é transitória e
    não deve bloquear novos fetches durante 30 dias.
    """
    if "error" in data:
        logger.warning("Trends cache: payload de erro NÃO escrito para keyword=%s", keyword)
        return
    data_to_write = dict(data)
    data_to_write["_cached_at"] = time.time()
    path = _cache_path(keyword, market)
    try:
        with open(path, "w") as f:
            json.dump(data_to_write, f, ensure_ascii=False, indent=2)
        logger.info("Trends cache: escrita para keyword=%s", keyword)
    except OSError as e:
        logger.warning("Trends cache: erro ao escrever — %s", e)


def _read_related_cache(keyword: str, market: Any | None = None) -> dict[str, Any] | None:
    path = _related_cache_path(keyword, market)
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
    if (
        cached_ts is None
        or cached_ts > time.time() + CLOCK_SKEW_TOLERANCE
        or time.time() - cached_ts > CACHE_TTL
    ):
        logger.info("Trends related cache: expirada/inválida para keyword=%s", keyword)
        return None
    if not isinstance(data.get("related_queries"), list):
        logger.warning("Trends related cache: payload sem lista de related queries para keyword=%s; miss", keyword)
        return None
    if market is not None and (
        data.get("market_id") != market.market_id
        or data.get("query_geo") != market.trends_geo
    ):
        logger.warning("Trends related cache: scope de mercado inválido para keyword=%s; miss", keyword)
        return None
    return {key: value for key, value in data.items() if key != "_cached_at"}


def _write_related_cache(keyword: str, data: dict[str, Any], market: Any | None = None) -> None:
    data_to_write = dict(data)
    data_to_write["_cached_at"] = time.time()
    try:
        with open(_related_cache_path(keyword, market), "w") as f:
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


def _related_score_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    """Deterministic sort key for mixed numeric/"Breakout" related scores.

    Non-numeric scores ("Breakout") always sort first; numeric scores then
    sort descending.  Sorting with ``reverse=True`` on a bare ``score`` would
    raise TypeError on mixed types — this key never does.
    """
    score = item.get("score")
    if isinstance(score, str):
        return (0, 0.0)
    try:
        return (1, -float(score))
    except (TypeError, ValueError):
        return (0, 0.0)


def _finite_number(value: Any) -> float | None:
    """Finite numeric value (int/float excluding bool, or a numeric string),
    or None for NaN/Infinity and any other shape.

    Trends timeline/region values are small non-negative interest indices;
    accepting NaN/Infinity would fabricate slope/seasonality and poison cache
    validation, so only finite numbers ever enter the parse.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
    elif isinstance(value, str):
        try:
            raw = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(raw):
        return None
    return raw


def _timeline_value(point: Any) -> float | None:
    """Finite numeric value of one timeline point, or None when malformed.

    The point and its ``values[0]`` entry must both be dicts (a non-dict
    ``values[0]`` used to crash the parse with AttributeError) and the
    extracted value must be finite; a malformed point never fabricates a
    trend value nor crashes.
    """
    if not isinstance(point, dict):
        return None
    values = point.get("values")
    if not isinstance(values, list) or not values:
        return None
    item = values[0]
    if not isinstance(item, dict):
        return None
    return _finite_number(item.get("extracted_value", 0))


def _slope_percent(values: list[float]) -> float:
    mid = len(values) // 2
    first_avg = sum(values[:mid]) / mid if mid > 0 else values[0]
    second_count = len(values) - mid
    second_avg = sum(values[mid:]) / second_count if second_count > 0 else values[-1]
    if first_avg > 0:
        return ((second_avg - first_avg) / first_avg) * 100
    return 0.0 if second_avg == 0 else 100.0


def _parse_serpapi_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Extrai informação relevante da resposta raw da SerpAPI Google Trends.
    Devolve dict com direction, slope_pct, seasonal, peak, top_geos.
    Malformed nested shapes (non-dict points, non-dict ``values[0]``,
    NaN/Infinity values) degrade to the same sanitized error as missing
    timeline data — never crash, never invent a trend value.  Only finite
    numeric values are accepted; malformed points are skipped and the peak
    stays anchored to the real point (not a shifted timeline index).
    """
    interest_over_time = data.get("interest_over_time", {})
    if not isinstance(interest_over_time, dict):
        interest_over_time = {}
    timeline = interest_over_time.get("timeline_data", [])
    if not isinstance(timeline, list):
        timeline = []

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

    values: list[float] = []
    points: list[tuple[dict[str, Any], float]] = []
    for point in timeline:
        if not isinstance(point, dict):
            continue
        value = _timeline_value(point)
        if value is None:
            continue
        points.append((point, value))
        values.append(value)

    if not values or len(values) < 2:
        return {
            "direction": "estável",
            "slope_pct": 0.0,
            "seasonal": False,
            "peak": None,
            "top_geos": [],
            "error": "Trends: não foi possível extrair valores do timeline",
        }

    # 90d preserves the existing full-series score input.  The final third
    # approximates the last 30 days of the requested 3-month series.
    slope_pct = _slope_percent(values)
    window_30d = values[-max(2, round(len(values) / 3)):]
    slope_30d_pct = _slope_percent(window_30d)

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
    if peak_idx is not None and peak_idx < len(points):
        # Ancorado ao ponto real (values/points caminham juntos): um ponto
        # malformado descartado nunca desloca o pico para a data errada.
        peak_label = points[peak_idx][0].get("date", None)

    # Top geos — só regiões com geo string e valor finito entram; itens
    # não-dict, geo ausente e valores NaN/Inf são descartados.
    interest_by_region = data.get("interest_by_region", [])
    top_geos: list[dict[str, Any]] = []
    if isinstance(interest_by_region, list):
        for region in interest_by_region[:3]:
            if not isinstance(region, dict):
                continue
            geo = region.get("geo")
            if not isinstance(geo, str) or not geo.strip():
                continue
            value = _finite_number(region.get("value", 0))
            if value is None:
                continue
            top_geos.append({"geo": geo.strip(), "value": value})

    return {
        "direction": direction,
        "slope_pct": round(slope_pct, 1),
        "slope_30d_pct": round(slope_30d_pct, 1),
        "slope_90d_pct": round(slope_pct, 1),
        "seasonal": seasonal,
        "peak": peak_label,
        "top_geos": top_geos,
    }


def fetch(
    keyword: str,
    timeseries_only: bool = False,
    force_refresh: bool = False,
    market: Any | None = None,
) -> dict[str, Any]:
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
        cached = _read_cache(keyword, market)
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
            "date": "today 3-m",  # últimos 90 dias (forma documentada da SerpAPI)
            "api_key": api_key,
        }
        if market is not None:
            params_ts["geo"] = market.trends_geo

        try:
            resp_ts = requests.get(SERPAPI_URL, params=params_ts, timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            # Never propagate or log raw exception text/URL: it can echo the
            # request, including the api_key query parameter.  Log only the
            # exception type; return a stable sanitized message.
            logger.warning("SerpAPI Trends: erro de rede (%s)", type(exc).__name__)
            return {"error": f"Erro de rede na SerpAPI ({type(exc).__name__})"}

        # 1 call gasta: a call foi realmente feita (a SerpAPI conta-a mesmo
        # quando o HTTP/JSON falha; uma exceção de rede não chega à SerpAPI).
        reservation.charge(1)

        if resp_ts.status_code != 200:
            logger.warning("SerpAPI Trends: HTTP %s", resp_ts.status_code)
            return {"error": http_errors.compose(
                "SerpAPI Trends", resp_ts.status_code, resp_ts,
                fallback="SerpAPI Trends: chave inválida ou quota esgotada (HTTP 403)"
                if resp_ts.status_code == 403 else None,
            )}

        try:
            raw_ts = resp_ts.json()
        except ValueError:
            return {"error": "SerpAPI Trends: resposta não é JSON válido"}
        if not isinstance(raw_ts, dict):
            # HTTP 200 mas raiz não-objeto (lista/string/null): nunca fazer
            # ``.get``/``in`` sobre uma raiz não-dict — erro estruturado.
            return {"error": "SerpAPI Trends: resposta inválida (raiz não é objeto JSON)"}

        if "error" in raw_ts:
            message = http_errors.provider_error_message(raw_ts)
            return {"error": f"SerpAPI Trends: {message or 'erro reportado pelo fornecedor'}"}

        # --- Call 2: RELATED_QUERIES (saltar se timeseries_only) ---
        related_queries: list[dict[str, Any]] = []

        if not timeseries_only:
            params_rq = {
                "engine": "google_trends",
                "q": keyword,
                "data_type": "RELATED_QUERIES",
                "api_key": api_key,
            }
            if market is not None:
                params_rq["geo"] = market.trends_geo

            try:
                resp_rq = requests.get(SERPAPI_URL, params=params_rq, timeout=REQUEST_TIMEOUT)
                related_call_made = True
            except Exception as exc:
                logger.warning("SerpAPI RELATED_QUERIES: erro de rede (%s)", type(exc).__name__)
                # Degradação graciosa: prosseguir sem related_queries
                related_call_made = False
            else:
                if resp_rq.status_code == 200:
                    try:
                        raw_rq = resp_rq.json()
                    except ValueError:
                        logger.warning("SerpAPI RELATED_QUERIES: resposta não é JSON válido")
                    else:
                        if isinstance(raw_rq, dict) and "error" not in raw_rq:
                            rq_data = raw_rq.get("related_queries", {})
                            rising_list = rq_data.get("rising", []) if isinstance(rq_data, dict) else []
                            if isinstance(rising_list, list):
                                for item in rising_list:
                                    if isinstance(item, dict):
                                        related_queries.append({
                                            "keyword": item.get("query", item.get("keyword", "")),
                                            "score": item.get("extracted_value", item.get("value", 0)),
                                        })
                                # Mixed numeric/"Breakout" scores sort without TypeError:
                                # non-numeric (Breakout) first, then numeric descending.
                                related_queries.sort(key=_related_score_sort_key)
                else:
                    logger.warning("SerpAPI RELATED_QUERIES: HTTP %s", resp_rq.status_code)

            # 2ª call gasta só quando a call foi realmente feita (a exceção de
            # rede significa que a SerpAPI nunca recebeu o pedido).
            if related_call_made:
                reservation.charge(1)

        # Parse e cache (sempre reescrever, mesmo em force_refresh)
        result = _parse_serpapi_response(raw_ts)
        result["related_queries"] = related_queries
        if market is not None:
            result["market_id"] = market.market_id
            result["query_geo"] = market.trends_geo
        _write_cache(keyword, result, market)
        return result


def fetch_related(
    keyword: str,
    force_refresh: bool = False,
    market: Any | None = None,
) -> dict[str, Any]:
    """Fetch only related queries, with the same 30-day disk cache.

    This is used after a successful zero-ad result: it costs one SerpAPI
    search and gives the user alternatives without running the expensive
    timeseries or SEO calls.
    """
    if not force_refresh:
        cached = _read_related_cache(keyword, market)
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
        if market is not None:
            params["geo"] = market.trends_geo
        try:
            response = requests.get(SERPAPI_URL, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("SerpAPI RELATED_QUERIES: erro de rede")
            return {"error": f"Erro de rede na SerpAPI RELATED_QUERIES ({type(exc).__name__})"}

        # SerpAPI counts the request even when it returns an HTTP or JSON error.
        reservation.charge(1)
        if response.status_code != 200:
            return {"error": http_errors.compose("SerpAPI RELATED_QUERIES", response.status_code, response)}

        try:
            raw = response.json()
        except (ValueError, TypeError):
            return {"error": "SerpAPI RELATED_QUERIES: resposta não é JSON válido"}
        if not isinstance(raw, dict):
            return {"error": "SerpAPI RELATED_QUERIES: resposta inválida"}
        if "error" in raw:
            message = http_errors.provider_error_message(raw)
            return {"error": f"SerpAPI RELATED_QUERIES: {message or 'erro reportado pelo fornecedor'}"}

        result = {"related_queries": _parse_related_queries(raw)}
        if market is not None:
            result["market_id"] = market.market_id
            result["query_geo"] = market.trends_geo
        _write_related_cache(keyword, result, market)
        return result
