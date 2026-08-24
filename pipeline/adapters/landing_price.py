"""
Adapter: Landing Price.

Extrai preços praticados nas landings dos concorrentes (dos top ads do Apify).
Estratégia HYBRID em 2 níveis:
  Nível 1 (grátis): GET com requests, timeout 10s, User-Agent browser, sem JS.
  Nível 2 (Firecrawl, só se nível 1 não encontrar preço): POST /v2/scrape, 1 credit/página.
Parser comum: JSON-LD schema.org Product/Offer PRIMEIRO (com priceCurrency),
depois regex €XX/XX€/XX,XX EUR (moeda explícita — EUR).

Segurança SSRF: cada alvo (inicial e cada destino de redirect) é validado por
``ssrf_guard.validate_target`` antes de qualquer request — apenas http(s)
com endereços públicos unicast (`is_global`, excluindo explicitamente redes privadas, loopback, link-local, reservadas, multicast e unspecified), sem userinfo. Redirects são
seguidos manualmente (301/302/303/307/308, máx. MAX_REDIRECTS=5); uma
cadeia bloqueada ou inválida suprime o fallback Firecrawl (que poderia
seguir o mesmo redirect inseguro). Falha de rede ou resposta final segura
sem preço continuam a cair no Firecrawl.

Moeda: preços JSON-LD só são aceites com priceCurrency explícito e
normalizável (ISO-4217-like, ex.: ARS/EUR/USD). Preços JSON-LD sem moeda
clara nunca são assumidos como EUR — falham com segurança e caem no regex de
moeda explícita. Aggregation nunca combina moedas diferentes num range.

Interface:
    fetch_price(url) -> dict[str, Any] | None   # inclui currency normalizada
    fetch_prices_from_ads(ads_data) -> dict[str, Any]

Regras:
- Máximo 3 landings por keyword, sem retries
- Timeout 10s por fetch (nível 1)
- Firecrawl: fetches sequenciais com pausa ≥6s entre calls
- Falha em qualquer landing = ignora e continua
- Falha em todas = "sem dados: landings não legíveis"
- Moedas diferentes entre landings = erro explícito, nunca um price_range
- Proveniência por fonte limitada (URL pública + domain, via, currency, min/max)
- NUNCA bloqueia o run
"""

import logging
import math
import os
import re
import time
import json as json_lib
from typing import Any

import requests
from urllib.parse import urljoin

from pipeline import quota
from pipeline.adapters import ssrf_guard
from pipeline.adapters.ssrf_guard import validate_target as _validate_target
from pipeline.adapters.url_sanitize import (
    domain_of as _domain_of,
    is_ad_library_url as _is_ad_library_url,
    sanitize_url as _sanitize_url,
)

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

MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

# Código ISO-4217-like: exatamente 3 letras ASCII (ARS, BRL, EUR, USD, ...).
_ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _normalize_currency(value: Any) -> str | None:
    """Normaliza priceCurrency JSON-LD para um código ISO-like maiúsculo.

    Valores sem um código claro de 3 letras ("$", "R$", "", "EUR ") devolvem
    None — nunca são assumidos como EUR. Minúsculas ("usd") e lixo não-letra
    à volta do código são normalizados.
    """
    if value is None:
        return None
    code = re.sub(r"[^A-Za-z]", "", str(value)).upper()
    if _ISO_CURRENCY_RE.fullmatch(code):
        return code
    return None


def _item_types(value: Any) -> list[str]:
    """@type pode ser string ou lista — normaliza para tokens lowercase."""
    if isinstance(value, list):
        return [str(token).lower() for token in value]
    return [str(value).lower()]


def _extract_jsonld_entries(html: str) -> list[dict[str, Any]] | None:
    """
    Procura <script type="application/ld+json"> com schema.org Product/Offer.

    Devolve entradas ``{"price": float, "currency": str | None}`` ou None.
    A moeda vem de priceCurrency (Offer/AggregateOffer/Product) e é
    normalizada; entradas sem priceCurrency ficam com ``currency=None`` e são
    descartadas mais tarde (nunca assumidas como EUR).
    """
    # Padrão: <script type="application/ld+json"> ... </script>
    ld_pattern = re.compile(
        r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL | re.IGNORECASE,
    )
    entries: list[dict[str, Any]] = []

    def add_price(value: Any, currency: Any) -> None:
        price = _safe_float(value)
        if price is not None:
            entries.append({"price": price, "currency": _normalize_currency(currency)})

    def add_offer(offer: dict[str, Any], parent_currency: Any = None) -> None:
        offer_types = _item_types(offer.get("@type", ""))
        currency = offer.get("priceCurrency", parent_currency)
        if "aggregateoffer" in offer_types:
            add_price(offer.get("lowPrice"), currency)
            add_price(offer.get("highPrice"), currency)
        elif "offer" in offer_types:
            add_price(offer.get("price"), currency)
        else:
            # Offers sem @type: desambiguar pelas chaves presentes.
            if "lowPrice" in offer or "highPrice" in offer:
                add_price(offer.get("lowPrice"), currency)
                add_price(offer.get("highPrice"), currency)
            else:
                add_price(offer.get("price"), currency)

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
            item_types = _item_types(item.get("@type", ""))

            # Product com offers
            if "product" in item_types and "offers" in item:
                offers = item["offers"]
                offers_list = offers if isinstance(offers, list) else [offers]
                for offer in offers_list:
                    if isinstance(offer, dict):
                        add_offer(offer, item.get("priceCurrency"))

            # Offer direto
            elif "offer" in item_types:
                add_offer(item, item.get("priceCurrency"))

            # AggregateOffer
            elif "aggregateoffer" in item_types:
                add_offer(item, item.get("priceCurrency"))

    return entries if entries else None


def _resolve_currency(entries: list[dict[str, Any]] | None) -> tuple[list[float], str] | None:
    """Reduz entradas JSON-LD a ``(prices, currency)`` quando há moeda única.

    Entradas sem moeda explícita são descartadas (nunca EUR). Mais do que uma
    moeda distinta na mesma página devolve None — ambíguo, o fetch cai para o
    regex de moeda explícita em vez de inventar um range.
    """
    if not entries:
        return None
    labeled = [entry for entry in entries if entry.get("currency")]
    if not labeled:
        return None
    currencies = {entry["currency"] for entry in labeled}
    if len(currencies) > 1:
        return None
    currency = currencies.pop()
    return [entry["price"] for entry in labeled], currency


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
    """Converte valor para float de forma segura.

    Rejeita NaN e infinidades (``math.isfinite``) além de não-positivos: um
    preço NaN/inf na página nunca entra no range nem na provenance.
    """
    if val is None:
        return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def _http_get_with_redirects(
    url: str, headers: dict[str, str]
) -> tuple[requests.Response | None, bool]:
    """GET com redirecionamentos manuais, cada hop validado contra SSRF.

    Devolve ``(resp, blocked)``:
    - ``resp`` é a resposta do hop final (ou None em rede/segurança/limite);
    - ``blocked`` é True quando a cadeia foi interrompida por segurança
      (alvo inicial ou redirect inseguro, Location ausente/inválida, loop,
      limite de MAX_REDIRECTS) — nesse caso o caller NÃO deve usar o fallback
      Firecrawl, porque o Firecrawl poderia seguir o mesmo redirect inseguro.

    O alvo inicial e cada destino de redirect são validados ANTES do request
    (o hop bloqueado nunca é chamado). Apenas 301/302/303/307/308 são
    seguidos; Location relativa resolve com urljoin contra o URL do hop
    atual. Falhas de rede (RequestException) NÃO marcam ``blocked`` — o
    fallback Firecrawl continua permitido para essas.
    """
    current = url
    seen: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        ok, reason = _validate_target(current)
        if not ok:
            logger.debug(
                "Landing price: alvo bloqueado (%s) — sem fallback Firecrawl",
                reason,
            )
            return None, True
        if current in seen:
            logger.debug(
                "Landing price: loop de redirecionamento em %s — sem fallback Firecrawl",
                _sanitize_url(current) or "",
            )
            return None, True
        seen.add(current)
        try:
            resp = requests.get(
                current, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=False
            )
        except requests.RequestException as e:
            # Nunca logar o texto da exceção: pode ecoar a URL crua
            # (userinfo/query/fragment) ou credenciais. Só o tipo e a URL
            # sanitizada saem do processo.
            logger.debug(
                "Landing price (HTTP): erro de rede para %s (%s)",
                _sanitize_url(current) or "", type(e).__name__,
            )
            return None, False
        if resp.status_code not in _REDIRECT_STATUS_CODES:
            return resp, False
        location = resp.headers.get("Location")
        if not location:
            logger.debug(
                "Landing price: redirect sem Location em %s — sem fallback Firecrawl",
                _sanitize_url(current) or "",
            )
            return None, True
        if ssrf_guard.has_forbidden_url_characters(location):
            logger.debug(
                "Landing price: Location com caracteres proibidos em %s — sem fallback Firecrawl",
                _sanitize_url(current) or "",
            )
            return None, True
        try:
            next_url = urljoin(current, location)
        except ValueError:
            logger.debug(
                "Landing price: Location inválida em %s — sem fallback Firecrawl",
                _sanitize_url(current) or "",
            )
            return None, True
        current = next_url
    logger.debug(
        "Landing price: limite de %d redirecionamentos excedido em %s — sem fallback Firecrawl",
        MAX_REDIRECTS, _sanitize_url(url) or "",
    )
    return None, True


def fetch_price(url: str) -> dict[str, Any] | None:
    """
    Extrai preço de uma landing page usando estratégia híbrida.

    Nível 1: GET com requests, timeout 10s, sem JS.
    Nível 2: Se nível 1 não encontra preço e FIRECRAWL_KEY disponível,
             usa Firecrawl /v2/scrape (1 credit, renderiza JS).

    Args:
        url: URL da landing page

    Returns:
        {"source_url": str, "prices": [float, ...], "currency": str,
         "via": "http"|"firecrawl"} ou None se falhar.

    A moeda vem do JSON-LD (priceCurrency normalizado) ou é ``EUR`` quando o
    preço vem do regex, que só aceita €/EUR explícito. JSON-LD sem moeda clara
    nunca é assumido como EUR: cai no regex ou falha.
    """
    if _sanitize_url(url) is None:
        return None
    # A URL original é usada nos fetches; só o que é devolvido é sanitizado.
    # Logs de falha usam a forma sanitizada (scheme+host+porta+path) — nunca a
    # URL crua com userinfo/query/fragment.
    safe_url = _sanitize_url(url) or ""

    # ── Nível 1: HTTP simples (redirects manuais, cada hop validado) ──
    headers = {"User-Agent": BROWSER_UA}
    resp, chain_blocked = _http_get_with_redirects(url, headers)
    if chain_blocked:
        # Cadeia bloqueada/inválida: o fallback Firecrawl é suprimido — o
        # Firecrawl poderia seguir o mesmo redirect inseguro.
        return None

    if resp and resp.status_code == 200:
        html = resp.text
        resolved = _resolve_currency(_extract_jsonld_entries(html))
        if resolved:
            prices, currency = resolved
            return {"source_url": safe_url, "prices": prices, "currency": currency, "via": "http"}
        # JSON-LD sem moeda explícita (ou ambíguo) → regex com moeda explícita (EUR)
        prices = _extract_regex_price(html)
        if prices:
            return {"source_url": safe_url, "prices": prices, "currency": "EUR", "via": "http"}

    # ── Nível 2: Firecrawl (só se nível 1 não encontrou preço) ──
    firecrawl_result = _fetch_via_firecrawl(url)
    if firecrawl_result and firecrawl_result.get("prices"):
        return firecrawl_result

    logger.debug("Landing price: zero preços encontrados em %s (HTTP + Firecrawl)", safe_url)
    return None


def _fetch_via_firecrawl(url: str) -> dict[str, Any] | None:
    """
    Usa Firecrawl /v2/scrape para renderizar JS e extrair preço.
    Custa 1 credit. Só usado quando o fetch HTTP normal não encontra preço.

    Returns:
        {"source_url": str, "prices": [float, ...], "currency": str,
         "via": "firecrawl"} ou None
    """
    api_key = os.getenv("FIRECRAWL_KEY")
    if not api_key:
        return None
    safe_url = _sanitize_url(url)
    if safe_url is None:
        return None  # sem URL pública válida não há source_url persistível

    # Revalidação imediata antes da reserva de quota/POST: um alvo bloqueado
    # (privado/userinfo/DNS falhado) nunca gasta credit nem sai do processo —
    # e a cadeia bloqueada em nível 1 já suprimiu este fallback, por isso esta
    # revalidação é a última fronteira antes de Firecrawl ver a URL.
    ok, reason = _validate_target(url)
    if not ok:
        logger.debug("Firecrawl: alvo bloqueado (%s) — sem POST nem credit", reason)
        return None

    with quota.transaction(1, firecrawl=True) as reservation:
        if not reservation.available:
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
            # Só o tipo da exceção e a URL sanitizada — nunca o texto da
            # exceção (pode ecoar a URL crua/credenciais).
            logger.debug("Firecrawl: erro de rede para %s (%s)", safe_url, type(e).__name__)
            return None

        if resp.status_code != 200:
            logger.debug("Firecrawl: HTTP %s para %s", resp.status_code, safe_url)
            return None

        # Registar o credit (só após HTTP 200, como antes)
        reservation.charge(1)

        try:
            data = resp.json()
        except (ValueError, TypeError):
            logger.debug("Firecrawl: resposta não é JSON válido")
            return None

        # Firecrawl devolve {data: {html: "...", markdown: "..."}}. Raiz ou
        # `data` que não sejam dict (list/null/string/falta) nunca crasham —
        # best-effort: devolve None e o credit já foi registado (HTTP 200).
        if not isinstance(data, dict):
            logger.debug("Firecrawl: raiz da resposta não é objeto — ignorada")
            return None
        content = data.get("data")
        if not isinstance(content, dict):
            logger.debug("Firecrawl: campo data não é objeto — ignorada")
            return None
        # ``data.html`` / ``data.markdown`` têm de ser strings: valores
        # scalar/list/dict tornam-se strings vazias e nunca chegam ao parse
        # JSON-LD/regex (finditer exige str — um dict/list crasharia aqui).
        raw_html = content.get("html", "")
        raw_markdown = content.get("markdown", "")
        html = raw_html if isinstance(raw_html, str) else ""
        markdown = raw_markdown if isinstance(raw_markdown, str) else ""

        # Parser comum: JSON-LD primeiro (com moeda), depois regex EUR explícito
        resolved = _resolve_currency(_extract_jsonld_entries(html))
        if resolved:
            prices, currency = resolved
            return {"source_url": safe_url, "prices": prices, "currency": currency, "via": "firecrawl"}
        # JSON-LD sem moeda clara → regex EUR explícito no HTML ou markdown
        prices = _extract_regex_price(html)
        if not prices and markdown:
            prices = _extract_regex_price(markdown)
        if prices:
            return {"source_url": safe_url, "prices": prices, "currency": "EUR", "via": "firecrawl"}

        return None


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
            "price_range": {"min": float, "max": float, "currency": str,
                             "sources": int, "via": str},
            "source_provenance": [{"source_url": str, "domain": str,
                                    "via": str, "currency": str,
                                    "min": float, "max": float}, ...],
        }
        ou erro estruturado (inclui erro de moedas mistas, que nunca é
        confundível com price_range).
    """
    if "error" in ads_data or not ads_data:
        return {"error": "sem dados: Facebook Ad Library via Apify indisponível (não foi possível extrair landings)"}

    top_ads = ads_data.get("top_ads", [])
    if not top_ads:
        return {"error": "sem dados: zero ads para extrair landings"}

    all_prices: list[float] = []
    per_source: list[dict[str, Any]] = []
    errors: list[str] = []
    firecrawl_used = False

    for ad in top_ads[:MAX_FETCHES]:
        landing_url = ad.get("landing_url", "")
        if not landing_url or _is_ad_library_url(landing_url):
            errors.append("sem dados: landing_url indisponível para um dos top ads")
            continue

        result = fetch_price(landing_url)
        if result and result.get("prices") and result.get("currency"):
            prices: list[float] = result["prices"]
            all_prices.extend(prices)
            per_source.append({
                "source_url": result["source_url"],
                "domain": _domain_of(result["source_url"]),
                "via": result.get("via", "http"),
                "currency": result["currency"],
                "min": round(min(prices), 2),
                "max": round(max(prices), 2),
            })
            if result.get("via") == "firecrawl":
                firecrawl_used = True
                # Pausa ≥6s entre calls Firecrawl (rate limit: 10 req/min)
                time.sleep(FIRECRAWL_PAUSE)

    if not per_source:
        if errors:
            return {"error": "; ".join(errors[:3])}
        return {"error": "sem dados: landings não legíveis (nenhum fetch de preço bem-sucedido)"}

    currencies = sorted({entry["currency"] for entry in per_source})
    if len(currencies) > 1:
        return {
            "error": "sem dados: landings reportam moedas diferentes ("
                     + ", ".join(currencies)
                     + ") — ranges não combináveis",
            "mixed_currencies": currencies,
            "source_provenance": per_source,
        }

    currency = currencies[0]
    # Determinar via predominante
    via = "firecrawl" if firecrawl_used else "http"

    return {
        "price_range": {
            "min": round(min(all_prices), 2),
            "max": round(max(all_prices), 2),
            "currency": currency,
            "sources": len(per_source),
            "via": via,
        },
        "source_provenance": per_source,
    }
