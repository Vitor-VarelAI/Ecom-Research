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
import os
import time
from typing import Any
from urllib.parse import urlparse

import requests

from pipeline import quota

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search"
REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CACHE_DIR = os.path.join(REPO_ROOT, "data", "cache")
CACHE_TTL = 30 * 24 * 3600  # 30 dias em segundos
REQUEST_TIMEOUT = 15

# Domínios de marketplaces conhecidos (PT/ES/BR)
MARKETPLACE_DOMAINS: set[str] = {
    "amazon.es", "amazon.com.br", "amazon.com",
    "elcorteingles.pt", "elcorteingles.es",
    "worten.pt", "fnac.pt", "fnac.es",
    "continente.pt", "pcdiga.pt", "radio-popular.pt",
    "mercadolivre.com.br", "shopee.com.br",
    "aliexpress.com", "ebay.com", "temu.com",
    "leroymerlin.pt", "ikea.com",
}


def _get_api_key() -> str | None:
    return os.getenv("SERPAPI_KEY")


def _slugify(keyword: str) -> str:
    """Gera um nome de ficheiro seguro a partir da keyword."""
    return hashlib.md5(keyword.encode()).hexdigest()[:12]


def _cache_path(keyword: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{_slugify(keyword)}_serp.json")


def _read_cache(keyword: str) -> dict[str, Any] | None:
    """Lê cache se existir e for válida (< 30 dias)."""
    path = _cache_path(keyword)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("SERP cache: ficheiro corrompido para keyword=%s", keyword)
        return None

    cached_ts = data.get("_cached_at", 0)
    if time.time() - cached_ts > CACHE_TTL:
        logger.info("SERP cache: expirada para keyword=%s", keyword)
        return None

    logger.info("SERP cache: HIT para keyword=%s (idade=%ds)", keyword, int(time.time() - cached_ts))
    return data


def _write_cache(keyword: str, data: dict[str, Any]) -> None:
    """Escreve dados na cache com timestamp."""
    data["_cached_at"] = time.time()
    path = _cache_path(keyword)
    try:
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    # Top 10 orgânicos
    organic_raw = data.get("organic_results", [])
    organic_results: list[dict[str, Any]] = []
    for r in organic_raw[:10]:
        link = r.get("link", "")
        organic_results.append({
            "position": r.get("position", 0),
            "domain": _extract_domain(link),
            "title": r.get("title", ""),
        })

    # Ads count
    ads_results = data.get("ads", []) or data.get("shopping_ads", []) or data.get("inline_ads", [])
    ads_count = len(ads_results) if isinstance(ads_results, list) else 0

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


def fetch(keyword: str) -> dict[str, Any]:
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
    cached = _read_cache(keyword)
    if cached is not None:
        result = {k: v for k, v in cached.items() if k != "_cached_at"}
        return result

    # 2. Cache miss — verificar quota
    if not quota.can_spend(1):
        return {"error": "Quota SerpAPI esgotada para este mês (230/250). Tente no próximo mês."}

    # 3. Chamar API
    api_key = _get_api_key()
    if not api_key:
        return {"error": "SERPAPI_KEY não definida em .env"}

    params = {
        "engine": "google",
        "q": keyword,
        "gl": "pt",
        "hl": "pt",
        "location": "Portugal",
        "api_key": api_key,
    }

    try:
        resp = requests.get(SERPAPI_URL, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("SerpAPI SERP: erro de rede — %s", e)
        return {"error": f"Erro de rede na SerpAPI (SERP): {e}"}

    if resp.status_code == 403:
        return {"error": "SerpAPI SERP: chave inválida ou quota esgotada (HTTP 403)"}
    if resp.status_code != 200:
        logger.warning("SerpAPI SERP: HTTP %s — %s", resp.status_code, resp.text[:300])
        return {"error": f"SerpAPI SERP: HTTP {resp.status_code}"}

    try:
        raw = resp.json()
    except ValueError:
        return {"error": "SerpAPI SERP: resposta não é JSON válido"}

    # Verificar erro da própria SerpAPI
    if "error" in raw:
        return {"error": f"SerpAPI SERP: {raw['error']}"}

    # Registar a call na quota
    quota.spend(1)

    result = _parse_serp_response(raw)
    _write_cache(keyword, result)
    return result
