"""Shared URL sanitization for persisted evidence (privacy).

The adapters and run.py must never persist or log a landing URL with
userinfo, query strings or fragments — those can carry tokens/UTMs that are
competitive intelligence or even credentials.  The raw URL is allowed *only*
for the actual HTTP fetch; everything that leaves the process (provenance,
packs, manifests, logs) goes through ``sanitize_url``.

This module is the single sanitizer home: ``landing_price`` uses it for
``source_url``/provenance, and ``run.py`` uses ``sanitize_ads_data`` before
building results, packs and checkpoints, so the raw landing URL can never
reach serialized artifacts.  ``sanitize_ads_data`` deep-copies and
sanitizes ``landing_url`` in both ``top_ads`` and ``candidates`` without
mutating its input, canonicalizes the public Ad Library ``link`` to its
ad-ID-only form (dropping every other query parameter and any fragment)
and — defense in depth — recursively reduces embedded http(s) URLs to
their safe scheme/host/path in every other string inside candidate/top_ad
objects (``copy``, ``advertiser``, nested ``match_evidence``) and in the
top-level ``advertisers``/``copy_examples`` projections.  No circular
imports: it depends only on the stdlib.
"""

from __future__ import annotations

import copy
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

_SAFE_SCHEMES = frozenset({"http", "https"})

# Hosts aceites para o campo público ``link`` da Ad Library.  Apenas estes
# origins são reconhecidos como referência pública; qualquer outro host cai
# na sanitização ordinária (scheme/host/path) ou é removido.
_ALLOWED_AD_LIBRARY_HOSTS = frozenset(
    {"facebook.com", "www.facebook.com", "m.facebook.com"}
)

# URL embutida em texto livre: começa em http(s):// e estende-se até ao
# primeiro espaço (inclui ``https://`` sem conteúdo — URL inválida que é
# removida por completo).  Pontuação de fecho comum (ponto, vírgula,
# parêntesis, aspas…) é separada antes de sanitizar para não contaminar o
# path.
_EMBEDDED_URL_RE = re.compile(r"https?://\S*", flags=re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;:!?…)]}»\"'"

# Identificador público opaco da Ad Library: ASCII alfanumérico + ``_`` +
# ``-``, 1..128 caracteres.  Nada de URLs, credenciais, paths, queries ou
# percent-encoding — um ``id`` que não seja um token opaco limitado não é
# uma referência pública e é rejeitado por completo (fail-closed).
_AD_LIBRARY_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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


def sanitize_url(url: Any) -> str | None:
    """Reduz uma URL à forma pública mínima que pode ser devolvida/persistida.

    Mantém apenas scheme http(s), hostname, porta segura (1-65535) e path;
    remove userinfo, query e fragment.  Devolve None para valores não-string
    (nunca levanta exceção), URLs vazias, inválidas, com porta inválida, sem
    scheme http(s) ou da Ad Library — essas nunca chegam a
    provenance/source_url.  Os fetches usam sempre a URL original; este
    sanitizador aplica-se só ao que sai do processo.
    """
    if not isinstance(url, str) or not url:
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


def sanitize_ad_library_link(link: Any) -> str:
    """Política dedicada da fronteira final de persistência para o campo
    público ``link`` da Ad Library.

    Devolve a ligação canónica — scheme://host + o path ``/ads/library``
    original + ``?id=<id-público>`` apenas — quando ``link`` é uma URL
    http(s) válida num host Facebook permitido, sem userinfo, com porta
    válida (por omissão do scheme ou 1-65535) e com uma query ``id``
    pública utilizável.  O id tem de ser um token opaco limitado
    (ASCII alfanumérico + ``_`` + ``-``, 1..128 caracteres): ids tipo
    URL/credencial, com percent-encoding ou fora do limite são rejeitados
    por completo.  Todos os outros parâmetros de query e qualquer fragment
    são removidos: o id é a referência pública da biblioteca, nunca uma
    landing.

    Devolve ``""`` para tudo o resto (ausente, não-string, scheme/host/path
    errados, credenciais, porta inválida, sem id ou id não-opaco) — o
    caller cai então na sanitização ordinária ``sanitize_url`` (que também
    remove links da Ad Library) ou mapeia para ``""``.
    """
    if not isinstance(link, str) or not link:
        return ""
    try:
        parsed = urlparse(link)
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in _SAFE_SCHEMES:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not (1 <= port <= 65535):
        return ""
    hostname = parsed.hostname
    if not hostname or hostname.casefold() not in _ALLOWED_AD_LIBRARY_HOSTS:
        return ""
    if "/ads/library" not in (parsed.path or "").casefold():
        return ""
    # ``parse_qsl`` descodifica percent-encoding, por isso um id codificado
    # malicioso (``id=https%3A%2F%2F…``) é comparado já descodificado contra
    # o padrão opaco limitado e é rejeitado.
    ad_id = next(
        (value for key, value in parse_qsl(parsed.query) if key.casefold() == "id" and value),
        None,
    )
    if ad_id is None or not _AD_LIBRARY_PUBLIC_ID_RE.fullmatch(ad_id):
        return ""
    netloc = hostname
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    if port is not None and port != default_port:
        netloc = f"{hostname}:{port}"
    return f"{parsed.scheme.casefold()}://{netloc}{parsed.path}?id={quote(ad_id, safe='')}"


def sanitize_embedded_urls(text: str) -> str:
    """Devolve ``text`` com cada URL http(s) embutida reduzida à forma
    pública mínima (via ``sanitize_url``): userinfo, query e fragment são
    removidos, o scheme/host/path seguro fica e o texto envolvente
    (não-URL) é preservado.  URLs inválidas ou da Ad Library não deixam o
    conteúdo cru para trás — a ocorrência é removida por completo.
    Texto sem URLs e esquemas não-http(s) (mailto:, ftp://…) passam
    intactos.
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        stripped = url.rstrip(_URL_TRAILING_PUNCTUATION)
        trailing = url[len(stripped):]
        safe = sanitize_url(stripped)
        if safe is None:
            # URL inválida/Ad Library: removida por completo; a pontuação de
            # frase que a seguia é preservada como texto envolvente.
            return trailing
        return safe + trailing

    return _EMBEDDED_URL_RE.sub(_replace, text)


def _sanitize_ad_strings(value: Any) -> Any:
    """Recursively reduce every embedded http(s) URL inside a string to its
    safe scheme/host/path form (``sanitize_embedded_urls``); dicts and lists
    are walked recursively and non-strings pass through unchanged."""
    if isinstance(value, str):
        return sanitize_embedded_urls(value)
    if isinstance(value, dict):
        return {key: _sanitize_ad_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_ad_strings(item) for item in value]
    return value


def _sanitize_link(link: Any) -> str:
    """Um campo ``link`` na fronteira final de persistência: uma referência
    segura da Ad Library é canonizada para a forma só-com-id; qualquer outro
    valor cai na sanitização ordinária (scheme/host/path) ou torna-se
    ``""`` — nunca a URL crua nem exceção."""
    return sanitize_ad_library_link(link) or sanitize_url(link) or ""


def _sanitize_ad(ad: dict[str, Any]) -> dict[str, Any]:
    """One candidate/top_ad at the final persistence boundary: ``landing_url``
    is reduced as a whole URL (never userinfo/query/fragment) and maps
    missing/invalid/non-string values to ``""``, the public Ad Library
    ``link`` is preserved only in canonical ad-ID-only form (every other
    link is sanitized or dropped), and every other string — including nested
    ``match_evidence`` — has embedded http(s) URLs reduced to their safe
    scheme/host/path.  Defense in depth for alternate or malformed adapter
    payloads."""
    clean = copy.deepcopy(ad)
    raw = clean.get("landing_url", "")
    clean["landing_url"] = sanitize_url(raw) or ""
    if "link" in clean:
        clean["link"] = _sanitize_link(clean["link"])
    for key, value in clean.items():
        if key in ("landing_url", "link"):
            continue
        clean[key] = _sanitize_ad_strings(value)
    return clean


def sanitize_ads_data(ads_data: dict[str, Any]) -> dict[str, Any]:
    """Devolve uma *cópia profunda* de ``ads_data`` com ``landing_url``
    sanitizado (scheme + host + porta segura + path) em ``top_ads`` E em
    ``candidates``.

    O objeto original nunca é mutado: ``landing_price.fetch_prices_from_ads``
    consome as URLs cruas antes de esta função correr.  Ad library links,
    URLs inválidas, valores ausentes e valores não-string tornam-se ``""``
    — nunca a URL crua.  O campo ``link`` é preservado apenas na forma
    canónica da Ad Library (só com o id público; quaisquer outros parâmetros
    de query e fragments são removidos); qualquer outro ``link`` é
    sanitizado (scheme/host/path) ou removido.  Como defesa em profundidade,
    todas as restantes strings dos objetos de anúncio — incluindo ``copy``,
    ``advertiser`` e o ``match_evidence`` aninhado — e as projeções
    top-level ``advertisers``/``copy_examples`` têm as URLs http(s)
    embutidas reduzidas à forma pública mínima, para que payloads de
    adapters alternativos ou malformados nunca persistam
    userinfo/query/fragment.  Ads não-dict passam intactos.
    """
    if not isinstance(ads_data, dict):
        return ads_data
    clean = copy.deepcopy(ads_data)
    for key in ("top_ads", "candidates"):
        items = clean.get(key)
        if not isinstance(items, list):
            continue
        clean[key] = [_sanitize_ad(ad) if isinstance(ad, dict) else ad for ad in items]
    for key in ("advertisers", "copy_examples"):
        if key in clean:
            clean[key] = _sanitize_ad_strings(clean[key])
    return clean
