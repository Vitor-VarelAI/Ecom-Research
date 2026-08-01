"""
Adapter: Landing Price.

Extrai preços praticados nas landings dos concorrentes (dos top ads do Apify).
Estratégia HYBRID em 2 níveis:
  Nível 1 (grátis): GET com requests, timeout 10s, User-Agent browser, sem JS.
  Nível 2 (Firecrawl, só se nível 1 não encontrar preço): POST /v2/scrape, 1 credit/página.
Parser comum: JSON-LD schema.org Product/Offer PRIMEIRO, depois regex €XX/XX€/XX,XX EUR.

Interface:
    fetch_price(url) -> dict[str, Any] | None
    fetch_prices_from_ads(ads_data) -> dict[str, Any]

Regras:
- Máximo 3 landings por keyword, sem retries
- Timeout 10s por fetch (nível 1)
- Firecrawl: fetches sequenciais com pausa ≥6s entre calls
- Falha em qualquer landing = ignora e continua
- Falha em todas = "sem dados: landings não legíveis"
- NUNCA bloqueia o run
"""

import logging
import os
import re
import time
import json as json_lib
from typing import Any
from urllib.parse import urlparse

import requests

from pipeline import quota

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10  # segundos
MAX_FETCHES = 3
FIRECRAWL_PAUSE = 6  # segundos entre calls Firecrawl
FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _is_ad_library_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    hostname = (parsed.hostname or "").casefold()
    path = (parsed.path or "").casefold()
    return hostname in {"facebook.com", "www.facebook.com", "m.facebook.com"} and "/ads/library" in path


def _extract_jsonld_price(html: str) -> list[float] | None:
    """
    Procura <script type="application/ld+json"> com schema.org Product/Offer.
    Devolve lista de preços encontrados ou None.
    """
    # Padrão: <script type="application/ld+json"> ... </script>
    ld_pattern = re.compile(
        r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    prices: list[float] = []

    for match in ld_pattern.finditer(html):
        try:
            data = json_lib.loads(match.group(1))
        except (json_lib.JSONDecodeError, ValueError):
            continue

        # Pode ser um dict único ou uma lista de objetos
        items = data if isinstance(data, list) else [data]

        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("@type", "")).lower()

            # Product com offers
            if item_type == "product" and "offers" in item:
                offers = item["offers"]
                offers_list = offers if isinstance(offers, list) else [offers]
                for offer in offers_list:
                    if isinstance(offer, dict):
                        offer_type = str(offer.get("@type", "")).lower()
                        if offer_type == "aggregateoffer":
                            low = _safe_float(offer.get("lowPrice"))
                            high = _safe_float(offer.get("highPrice"))
                            if low is not None:
                                prices.append(low)
                            if high is not None:
                                prices.append(high)
                        else:
                            price = _safe_float(offer.get("price"))
                            if price is not None:
                                prices.append(price)

            # Offer direto
            elif item_type == "offer":
                price = _safe_float(item.get("price"))
                if price is not None:
                    prices.append(price)

            # AggregateOffer
            elif item_type == "aggregateoffer":
                low = _safe_float(item.get("lowPrice"))
                high = _safe_float(item.get("highPrice"))
                if low is not None:
                    prices.append(low)
                if high is not None:
                    prices.append(high)

    return prices if prices else None


def _extract_regex_price(html: str) -> list[float] | None:
    """
    Fallback: regex para padrões de preço em euros.
    Formatos: €29,99 | 29,99€ | 29,99 EUR | € 29.99 | 29.99 €
    """
    patterns = [
        r'(?:€|EUR)\s*(\d+(?:[.,]\d{1,2})?)',     # €29,99 ou EUR 29.99
        r'(\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR)',      # 29,99€
    ]

    combined = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)
    prices: list[float] = []

    for match in combined.finditer(html):
        for group in match.groups():
            if group is None:
                continue
            try:
                # Normalizar: substituir , por . para float
                normalized = group.replace(",", ".")
                price = float(normalized)
                # Filtrar valores absurdos (preços realistas: 0.01 a 99999)
                if 0.01 <= price <= 99999.0:
                    prices.append(price)
            except (ValueError, TypeError):
                continue

    return prices if prices else None


def _safe_float(val: Any) -> float | None:
    """Converte valor para float de forma segura."""
    if val is None:
        return None
    try:
        f = float(val)
        if f <= 0:
            return None
        return f
    except (ValueError, TypeError):
        return None


def fetch_price(url: str) -> dict[str, Any] | None:
    """
    Extrai preço de uma landing page usando estratégia híbrida.

    Nível 1: GET com requests, timeout 10s, sem JS.
    Nível 2: Se nível 1 não encontra preço e FIRECRAWL_KEY disponível,
             usa Firecrawl /v2/scrape (1 credit, renderiza JS).

    Args:
        url: URL da landing page

    Returns:
        {"source_url": str, "prices": [float, ...], "via": "http"|"firecrawl"} ou None se falhar
    """
    if not url or _is_ad_library_url(url):
        return None

    # ── Nível 1: HTTP simples ──
    headers = {"User-Agent": BROWSER_UA}

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        logger.debug("Landing price (HTTP): erro de rede para %s — %s", url[:80], e)
        resp = None

    if resp and resp.status_code == 200:
        html = resp.text
        prices = _extract_jsonld_price(html)
        if not prices:
            prices = _extract_regex_price(html)

        if prices:
            return {"source_url": url, "prices": prices, "via": "http"}

    # ── Nível 2: Firecrawl (só se nível 1 não encontrou preço) ──
    firecrawl_result = _fetch_via_firecrawl(url)
    if firecrawl_result and firecrawl_result.get("prices"):
        return firecrawl_result

    logger.debug("Landing price: zero preços encontrados em %s (HTTP + Firecrawl)", url[:80])
    return None


def _fetch_via_firecrawl(url: str) -> dict[str, Any] | None:
    """
    Usa Firecrawl /v2/scrape para renderizar JS e extrair preço.
    Custa 1 credit. Só usado quando o fetch HTTP normal não encontra preço.

    Returns:
        {"source_url": str, "prices": [float, ...], "via": "firecrawl"} ou None
    """
    api_key = os.getenv("FIRECRAWL_KEY")
    if not api_key:
        return None

    if not quota.can_spend_firecrawl(1):
        logger.warning("Firecrawl: credits esgotados — a saltar")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "formats": ["html", "markdown"],
    }

    try:
        resp = requests.post(FIRECRAWL_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.debug("Firecrawl: erro de rede para %s — %s", url[:80], e)
        return None

    if resp.status_code != 200:
        logger.debug("Firecrawl: HTTP %s para %s", resp.status_code, url[:80])
        return None

    # Registar o credit
    quota.spend_firecrawl(1)

    try:
        data = resp.json()
    except ValueError:
        logger.debug("Firecrawl: resposta não é JSON válido")
        return None

    # Firecrawl devolve {data: {html: "...", markdown: "..."}}
    content = data.get("data", {})
    html = content.get("html", "") or ""
    markdown = content.get("markdown", "") or ""

    # Parser comum: JSON-LD primeiro, depois regex
    prices = _extract_jsonld_price(html)
    if not prices:
        # Tentar regex no markdown também (landings JS podem ter preço no markdown)
        prices = _extract_regex_price(html)
    if not prices and markdown:
        prices = _extract_regex_price(markdown)

    if not prices:
        return None

    return {"source_url": url, "prices": prices, "via": "firecrawl"}


def fetch_prices_from_ads(ads_data: dict[str, Any]) -> dict[str, Any]:
    """
    Extrai preços das landings dos top 3 ads por longevidade.

    Dos top_ads, usa exclusivamente landing_url; links da Ad Library são
    identificadores de origem e nunca são landings.
    Máximo 3 fetches, sem retries.

    Args:
        ads_data: dict devolvido por apify_ads.fetch()

    Returns:
        {
            "price_range": {"min": float, "max": float, "currency": "EUR", "sources": int}
            ou erro se nenhum fetch funcionou
        }
    """
    if "error" in ads_data or not ads_data:
        return {"error": "sem dados: Facebook Ad Library via Apify indisponível (não foi possível extrair landings)"}

    top_ads = ads_data.get("top_ads", [])
    if not top_ads:
        return {"error": "sem dados: zero ads para extrair landings"}

    all_prices: list[float] = []
    sources = 0
    via_methods: list[str] = []
    errors: list[str] = []
    firecrawl_used = False

    for ad in top_ads[:MAX_FETCHES]:
        landing_url = ad.get("landing_url", "")
        if not landing_url or _is_ad_library_url(landing_url):
            errors.append("sem dados: landing_url indisponível para um dos top ads")
            continue

        result = fetch_price(landing_url)
        if result and result.get("prices"):
            all_prices.extend(result["prices"])
            sources += 1
            via_methods.append(result.get("via", "http"))
            if result.get("via") == "firecrawl":
                firecrawl_used = True
                # Pausa ≥6s entre calls Firecrawl (rate limit: 10 req/min)
                time.sleep(FIRECRAWL_PAUSE)

    if not all_prices:
        if errors:
            return {"error": "; ".join(errors[:3])}
        return {"error": "sem dados: landings não legíveis (nenhum fetch de preço bem-sucedido)"}

    # Determinar via predominante
    via = "firecrawl" if firecrawl_used else "http"

    return {
        "price_range": {
            "min": round(min(all_prices), 2),
            "max": round(max(all_prices), 2),
            "currency": "EUR",
            "sources": sources,
            "via": via,
        },
    }
