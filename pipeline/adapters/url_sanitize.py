"""Shared URL sanitization for persisted evidence (privacy).

The adapters and run.py must never persist or log a landing URL with
userinfo, query strings or fragments — those can carry tokens/UTMs that are
competitive intelligence or even credentials.  The raw URL is allowed *only*
for the actual HTTP fetch; everything that leaves the process (provenance,
packs, manifests, logs) goes through ``sanitize_url``.

This module is the single sanitizer home: ``landing_price`` uses it for
``source_url``/provenance, and ``run.py`` uses ``sanitize_ads_data`` before
building results, packs and checkpoints, so the raw landing URL can never
reach serialized artifacts.  No circular imports: it depends only on the
stdlib.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

_SAFE_SCHEMES = frozenset({"http", "https"})


def is_ad_library_url(url: str) -> bool:
    """True when the URL is a Facebook Ad Library link (an origin identifier,
    never a landing).  The public ad-ID query on those links is allowed to
    persist — it is the library's public reference, not a secret."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    hostname = (parsed.hostname or "").casefold()
    path = (parsed.path or "").casefold()
    return hostname in {"facebook.com", "www.facebook.com", "m.facebook.com"} and "/ads/library" in path


def sanitize_url(url: str) -> str | None:
    """Reduz uma URL à forma pública mínima que pode ser devolvida/persistida.

    Mantém apenas scheme http(s), hostname, porta segura (1-65535) e path;
    remove userinfo, query e fragment.  Devolve None para URLs vazias,
    inválidas, com porta inválida, sem scheme http(s) ou da Ad Library —
    essas nunca chegam a provenance/source_url.  Os fetches usam sempre a URL
    original; este sanitizador aplica-se só ao que sai do processo.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in _SAFE_SCHEMES:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    if is_ad_library_url(url):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    netloc = hostname
    if port is not None:
        if not (1 <= port <= 65535):
            return None
        # Portas por defeito do scheme são redundantes — a URL continua "ordinary".
        default_port = 443 if parsed.scheme.casefold() == "https" else 80
        if port != default_port:
            netloc = f"{hostname}:{port}"
    return f"{parsed.scheme.casefold()}://{netloc}{parsed.path}"


def domain_of(url: str) -> str:
    """Hostname limpo de uma landing URL pública (sem esquema/porta/query)."""
    try:
        hostname = urlparse(url).hostname
    except ValueError:
        return ""
    return hostname or ""


def sanitize_ads_data(ads_data: dict[str, Any]) -> dict[str, Any]:
    """Devolve uma *cópia* de ``ads_data`` com ``top_ads[].landing_url``
    sanitizado (scheme + host + porta segura + path).

    O objeto original nunca é mutado: ``landing_price.fetch_prices_from_ads``
    consome as URLs cruas antes de esta função correr.  Ad library links,
    URLs inválidas e valores ausentes tornam-se ``""`` — nunca a URL crua.
    O campo ``link`` (referência pública da Ad Library, com ad-ID) não é
    alterado.  Ads não-dict passam intactos.
    """
    if not isinstance(ads_data, dict):
        return ads_data
    top_ads = ads_data.get("top_ads", [])
    if not isinstance(top_ads, list):
        return dict(ads_data)
    sanitized_top: list[dict[str, Any]] = []
    for ad in top_ads:
        if not isinstance(ad, dict):
            sanitized_top.append(ad)
            continue
        clean_ad = dict(ad)
        raw = clean_ad.get("landing_url", "")
        safe = sanitize_url(raw) if raw else ""
        clean_ad["landing_url"] = safe or ""
        sanitized_top.append(clean_ad)
    clean_ads = dict(ads_data)
    clean_ads["top_ads"] = sanitized_top
    return clean_ads
