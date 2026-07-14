"""
Adapter: Meta Ad Library API.

Fonte: https://www.facebook.com/ads/library/api
Endpoint: GET https://graph.facebook.com/v19.0/ads_archive

Interface comum: fetch(keyword) -> dict
Em caso de erro, devolve {"error": "razão"} — nunca levanta excepção.
"""

import os
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

META_API_URL = "https://graph.facebook.com/v19.0/ads_archive"
REQUEST_TIMEOUT = 15  # segundos


def _get_token() -> str | None:
    """Obtém o token da Meta a partir da env var. Nunca hardcoded."""
    return os.getenv("META_ACCESS_TOKEN")


def fetch(keyword: str) -> dict[str, Any]:
    """
    Dado uma keyword, consulta a Meta Ad Library API e devolve:
    {
        "total_ads": int,
        "top_ads": [ {advertiser, days_active, reach_eu, link}, ... ] (top 3 por longevidade),
        "copy_angles": [str, ...],       # ângulos de copy dominantes (max 5)
        "saturation": "baixa" | "média" | "alta",
    }
    Em caso de erro:
    {
        "error": "razão do erro"
    }
    """
    token = _get_token()
    if not token:
        return {"error": "META_ACCESS_TOKEN não definido em .env"}

    params: dict[str, Any] = {
        "ad_reached_countries": ["PT", "ES", "BR"],
        "search_terms": keyword,
        "ad_active_status": "ACTIVE",
        "access_token": token,
        "limit": 50,  # buscar até 50 ads para análise
        "fields": "id,ad_creation_time,ad_creative_bodies,ad_delivery_stop_time,"
        "ad_snapshot_url,page_name,delivery_by_region",
    }

    try:
        resp = requests.get(META_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("Meta API: erro de rede — %s", e)
        return {"error": f"Erro de rede na Meta API: {e}"}

    if resp.status_code == 401:
        return {"error": "Meta API: token inválido ou expirado (HTTP 401)"}
    if resp.status_code == 403:
        return {"error": "Meta API: acesso negado — verifica permissões da app FB (HTTP 403)"}
    if resp.status_code != 200:
        logger.warning("Meta API: HTTP %s — %s", resp.status_code, resp.text[:300])
        return {"error": f"Meta API: HTTP {resp.status_code}"}

    try:
        data = resp.json()
    except ValueError:
        return {"error": "Meta API: resposta não é JSON válido"}

    ads = data.get("data", [])
    if not ads:
        return {
            "total_ads": 0,
            "top_ads": [],
            "copy_angles": [],
            "saturation": "baixa",
            "error": "Meta API: zero ads encontrados para esta keyword",
        }

    # Calcular longevidade (dias desde criação até hoje, ou até stop_time)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ads_with_longevity: list[dict[str, Any]] = []
    copies: list[str] = []

    for ad in ads:
        created = ad.get("ad_creation_time")
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                days_active = (now - created_dt).days
            except (ValueError, TypeError):
                days_active = 0
        else:
            days_active = 0

        # Extrair copy bodies
        bodies = ad.get("ad_creative_bodies", [])
        if isinstance(bodies, list):
            copies.extend(bodies[:2])  # primeiras 2 variantes por ad

        # Extrair alcance por região (UE)
        delivery_regions = ad.get("delivery_by_region", [])
        reach_eu = 0
        if isinstance(delivery_regions, list):
            for region in delivery_regions:
                if isinstance(region, dict) and region.get("region", "").upper() in ("EU", "PT", "ES", "BR"):
                    reach_eu += region.get("percentage", 0)

        ads_with_longevity.append({
            "advertiser": ad.get("page_name", "Desconhecido"),
            "days_active": days_active,
            "reach_eu": reach_eu,
            "link": ad.get("ad_snapshot_url", ""),
        })

    # Top 3 por longevidade
    ads_with_longevity.sort(key=lambda x: x["days_active"], reverse=True)
    top_ads = ads_with_longevity[:3]

    # Ângulos de copy dominantes (limpar, deduplicar, max 5)
    unique_copies: list[str] = []
    seen: set[str] = set()
    for c in copies:
        clean = c.strip()
        if clean and clean not in seen and len(clean) > 10:
            seen.add(clean)
            unique_copies.append(clean)
    copy_angles = unique_copies[:5]

    # Saturação
    total = len(ads)
    if total < 10:
        saturation = "baixa"
    elif total <= 50:
        saturation = "média"
    else:
        saturation = "alta"

    return {
        "total_ads": total,
        "top_ads": top_ads,
        "copy_angles": copy_angles,
        "saturation": saturation,
    }
