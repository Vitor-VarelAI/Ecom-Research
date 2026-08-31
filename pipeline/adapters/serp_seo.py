"""
Adapter: Google SERP snapshot via SerpAPI.

Endpoint: GET https://serpapi.com/search
Parâmetros: engine=google, q=keyword, gl=pt, hl=pt, location=Portugal

Cache: /data/cache/{keyword_slug}_serp.json com TTL de 30 dias.
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
from typing import Any
from urllib.parse import urlparse

import requests

from pipeline import quota

from pipeline.adapters import http_errors

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search"
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")
CACHE_TTL = 30 * 24 * 3600  # 30 dias em segundos
# Tolerância a skew de relógio: timestamps de cache mais de 5 minutos no
# futuro são inválidos (miss) — um relógio adiantado nunca serve cache fresca.
CLOCK_SKEW_TOLERANCE = 5 * 60
REQUEST_TIMEOUT = 15

# Domínios de marketplaces conhecidos (PT/GB/ES/BR)
MARKETPLACE_DOMAINS: set[str] = {
    "amazon.co.uk", "amazon.es", "amazon.com.br", "amazon.com",
    "elcorteingles.pt", "elcorteingles.es",
    "worten.pt", "fnac.pt", "fnac.es",
    "continente.pt", "pcdiga.pt", "radio-popular.pt",
    "mercadolivre.com.br", "shopee.com.br",
    "aliexpress.com", "ebay.co.uk", "ebay.com", "temu.com",
    "leroymerlin.pt", "ikea.com",
}


def _get_api_key() -> str | None:
    return os.getenv("SERPAPI_KEY")


def _slugify(keyword: str) -> str:
    """Gera um nome de ficheiro seguro a partir da keyword."""
    return hashlib.md5(keyword.encode()).hexdigest()[:12]


def _cache_path(keyword: str, market: Any | None = None) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    scoped_keyword = keyword if market is None else f"{market.market_id}:{keyword}"
    return os.path.join(CACHE_DIR, f"{_slugify(scoped_keyword)}_serp.json")


def _valid_organic_item(item: Any) -> bool:
    """One cached organic entry must be a dict whose ``position`` is a finite
    number (not bool) and whose ``domain``/``title`` are strings — exactly the
    shape ``_parse_serp_response`` produces and pack rendering consumes.  A
    malformed entry (non-dict, NaN position, non-str title) would crash or
    poison pack rendering, so it makes the whole payload a miss."""
    if not isinstance(item, dict):
        return False
    position = item.get("position")
    if isinstance(position, bool) or not isinstance(position, (int, float)):
        return False
    if not math.isfinite(float(position)):
        return False
    if not isinstance(item.get("domain"), str):
        return False
    if not isinstance(item.get("title"), str):
        return False
    return True


def _valid_payload(data: dict[str, Any]) -> bool:
    """A SEO cache hit must carry the full parsed shape, deeply validated:
    ``organic_results`` a list of well-formed entries (dict, finite numeric
    position, string domain/title), ``ads_count`` a finite number,
    ``paa_questions`` a list of strings and ``seo_opportunity`` a non-empty
    string.  Anything else is a miss — malformed valid JSON must never be
    served (it could crash pack rendering later) nor suppress a refresh."""
    organic = data.get("organic_results")
    if not isinstance(organic, list) or not all(_valid_organic_item(item) for item in organic):
        return False
    ads_count = data.get("ads_count")
    if isinstance(ads_count, bool) or not isinstance(ads_count, (int, float)):
        return False
    if not math.isfinite(float(ads_count)):
        return False
    paa = data.get("paa_questions")
    if not isinstance(paa, list) or not all(isinstance(item, str) for item in paa):
        return False
    opportunity = data.get("seo_opportunity")
    if not isinstance(opportunity, str) or not opportunity:
        return False
    return True


def _read_cache(keyword: str, market: Any | None = None) -> dict[str, Any] | None:
    """Lê cache se existir e for válida (< 30 dias).

    JSON válido que não seja um dict, com ``_cached_at`` não numérico/não
    finito, com payload de erro, ou com forma inválida é tratado como miss
    — nunca crash, nunca refresh suprimido.  Payloads de erro são removidos.
    """
    path = _cache_path(keyword, market)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("SERP cache: ficheiro corrompido para keyword=%s", keyword)
        return None

    if not isinstance(data, dict):
        logger.warning("SERP cache: payload não-dict para keyword=%s", keyword)
        return None
    if "error" in data:
        logger.warning("SERP cache: payload de erro antigo removido para keyword=%s", keyword)
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    cached_ts = data.get("_cached_at", 0)
    if isinstance(cached_ts, bool) or not isinstance(cached_ts, (int, float)):
        logger.warning("SERP cache: timestamp inválido para keyword=%s", keyword)
        return None
    if (
        not math.isfinite(float(cached_ts))
        or cached_ts > time.time() + CLOCK_SKEW_TOLERANCE
        or time.time() - cached_ts > CACHE_TTL
    ):
        logger.info("SERP cache: expirada/inválida para keyword=%s", keyword)
        return None
    if not _valid_payload(data):
        logger.warning("SERP cache: payload com forma inválida para keyword=%s; miss", keyword)
        return None
    if market is not None and (
        data.get("market_id") != market.market_id
        or data.get("query_gl") != market.serp_gl
        or data.get("query_hl") != market.serp_hl
        or data.get("query_location") != market.serp_location
    ):
        logger.warning("SERP cache: scope de mercado inválido para keyword=%s; miss", keyword)
        return None

    logger.info("SERP cache: HIT para keyword=%s (idade=%ds)", keyword, int(time.time() - cached_ts))
    return data


def _write_cache(keyword: str, data: dict[str, Any], market: Any | None = None) -> None:
    """Escreve dados na cache com timestamp sem mutar o resultado fresco."""
    data_to_write = dict(data)
    data_to_write["_cached_at"] = time.time()
    path = _cache_path(keyword, market)
    try:
        with open(path, "w") as f:
            json.dump(data_to_write, f, ensure_ascii=False, indent=2)
        logger.info("SERP cache: escrita para keyword=%s", keyword)
    except OSError as e:
        logger.warning("SERP cache: erro ao escrever — %s", e)


def _extract_domain(url_str: str) -> str:
    """Extrai o domínio limpo de um URL (ex: www.amazon.es → amazon.es)."""
    try:
        parsed = urlparse(url_str)
        domain = parsed.netloc or parsed.path.split("/")[0]
        # Remover www. se presente
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return "?"


def _is_marketplace(domain: str) -> bool:
    """Verifica se o domínio está na lista de marketplaces conhecidos."""
    # Match exato ou subdomínio de marketplace
    if domain in MARKETPLACE_DOMAINS:
        return True
    # Verifica se o domínio termina com um marketplace conhecido (ex: sub.amazon.es)
    for mp in MARKETPLACE_DOMAINS:
        if domain.endswith("." + mp):
            return True
    return False


def _assess_seo_opportunity(organic_results: list[dict[str, Any]]) -> str:
    """
    Avalia oportunidade SEO:
    - "alta": top 10 tem poucos (0-1) marketplaces → espaço para entrar
    - "média": top 10 tem 2-3 marketplaces
    - "baixa": top 10 dominado por 4+ marketplaces
    """
    if not organic_results:
        return "alta"  # sem resultados = oportunidade

    mp_count = 0
    for r in organic_results[:10]:
        domain = r.get("domain", "")
        if _is_marketplace(domain):
            mp_count += 1

    if mp_count <= 1:
        return "alta"
    elif mp_count <= 3:
        return "média"
    else:
        return "baixa"


def _parse_serp_response(data: dict[str, Any]) -> dict[str, Any]:
    """
    Extrai informação relevante da resposta raw da SerpAPI Google SERP.
    Devolve dict com organic_results, ads_count, paa_questions, seo_opportunity.
    """
    # Top 10 orgânicos — tolerante a shapes malformados (organic_results
    # não-lista, itens não-dict, ``link`` ausente) nunca crasham.
    organic_raw = data.get("organic_results", [])
    organic_results: list[dict[str, Any]] = []
    if not isinstance(organic_raw, list):
        organic_raw = []
    for r in organic_raw[:10]:
        if not isinstance(r, dict):
            continue
        link = r.get("link", "")
        organic_results.append({
            "position": r.get("position", 0),
            "domain": _extract_domain(link),
            "title": r.get("title", ""),
        })

    # Ads count — ``ads``/``shopping_ads``/``inline_ads`` podem faltar ou ser
    # não-lista; qualquer shape inválido degrada para 0, nunca crasha.
    ads_raw = data.get("ads") or data.get("shopping_ads") or data.get("inline_ads")
    ads_count = len(ads_raw) if isinstance(ads_raw, list) else 0

    # PAA (People Also Ask) — top 3
    related_questions = data.get("related_questions", [])
    paa_questions: list[str] = []
    if isinstance(related_questions, list):
        for q in related_questions[:3]:
            if isinstance(q, dict):
                question = q.get("question", "") or q.get("title", "")
                if question:
                    paa_questions.append(question)

    # SEO opportunity assessment
    seo_opportunity = _assess_seo_opportunity(organic_results)

    return {
        "organic_results": organic_results,
        "ads_count": ads_count,
        "paa_questions": paa_questions,
        "seo_opportunity": seo_opportunity,
    }


def fetch(keyword: str, market: Any | None = None) -> dict[str, Any]:
    """
    Dado uma keyword, consulta Google SERP via SerpAPI (com cache 30 dias) e devolve:
    {
        "organic_results": [ {position, domain, title}, ... ],  # top 10
        "ads_count": int,          # nº de ads no SERP
        "paa_questions": [str, ...],  # top 3 People Also Ask
        "seo_opportunity": "alta" | "média" | "baixa",
    }
    Em caso de erro:
    {
        "error": "razão do erro"
    }
    """
    # 1. Tentar cache
    cached = _read_cache(keyword, market)
    if cached is not None:
        result = {k: v for k, v in cached.items() if k != "_cached_at"}
        return result

    # 2. Cache miss — verificar quota (reserva/commit atómico sob lock)
    with quota.transaction(1) as reservation:
        if not reservation.available:
            return {"error": "Quota SerpAPI esgotada para este mês (230/250). Tente no próximo mês."}

        # 3. Chamar API
        api_key = _get_api_key()
        if not api_key:
            return {"error": "SERPAPI_KEY não definida em .env"}

        params = {
            "engine": "google",
            "q": keyword,
            "gl": market.serp_gl if market is not None else "pt",
            "hl": market.serp_hl if market is not None else "pt",
            "location": market.serp_location if market is not None else "Portugal",
            "api_key": api_key,
        }

        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=REQUEST_TIMEOUT)
        except Exception as exc:
            # Never propagate or log raw exception text/URL: it can echo the
            # request, including the api_key query parameter.  Log only the
            # exception type; return a stable sanitized message.  A transport
            # exception means SerpAPI never received the request — nothing is
            # charged.
            logger.warning("SerpAPI SERP: erro de rede (%s)", type(exc).__name__)
            return {"error": f"Erro de rede na SerpAPI (SERP) ({type(exc).__name__})"}

        # A call foi realmente feita: a SerpAPI conta-a mesmo quando o HTTP ou
        # o JSON falham (como nas Trends).  Cobra exactamente uma vez, logo
        # após a resposta — nunca em dobro, nunca antes de haver resposta.
        reservation.charge(1)

        if resp.status_code != 200:
            logger.warning("SerpAPI SERP: HTTP %s", resp.status_code)
            return {"error": http_errors.compose(
                "SerpAPI SERP", resp.status_code, resp,
                fallback="SerpAPI SERP: chave inválida ou quota esgotada (HTTP 403)"
                if resp.status_code == 403 else None,
            )}

        try:
            raw = resp.json()
        except ValueError:
            return {"error": "SerpAPI SERP: resposta não é JSON válido"}
        if not isinstance(raw, dict):
            # HTTP 200 mas raiz não-objeto: nunca ``.get``/``in`` sobre uma
            # raiz não-dict — erro estruturado sanitizado, nunca crash.
            return {"error": "SerpAPI SERP: resposta inválida (raiz não é objeto JSON)"}

        # Verificar erro da própria SerpAPI (mensagem sanitizada e limitada)
        if "error" in raw:
            message = http_errors.provider_error_message(raw)
            return {"error": f"SerpAPI SERP: {message or 'erro reportado pelo fornecedor'}"}

        result = _parse_serp_response(raw)
        if market is not None:
            result["market_id"] = market.market_id
            result["query_gl"] = market.serp_gl
            result["query_hl"] = market.serp_hl
            result["query_location"] = market.serp_location
        _write_cache(keyword, result, market)
        return result
