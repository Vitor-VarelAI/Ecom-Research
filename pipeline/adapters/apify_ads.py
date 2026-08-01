"""
Facebook Ad Library adapter backed by the Apify actor.

The adapter deliberately makes one bounded actor invocation per keyword.  The
actor receives one global Ad Library URL and returns at most 100 samples; all
filtering, relevance checks, and grouping happen locally and deterministically.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

APIFY_RUN_URL = (
    "https://api.apify.com/v2/acts/"
    "curious_coder~facebook-ads-library-scraper/run-sync-get-dataset-items"
)
FACEBOOK_AD_LIBRARY_URL = "https://www.facebook.com/ads/library/"
SAMPLE_LIMIT = 100
REQUEST_TIMEOUT = (10, 135)


def _get_token() -> str | None:
    return os.getenv("APIFY_API_TOKEN")


def _fold(value: Any) -> str:
    """Accent-fold a value and reduce it to deterministic searchable text."""
    text = str(value or "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.casefold()


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[\w]+", _fold(value), flags=re.UNICODE)


def _string_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _string_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)


def _first_value(ad: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = ad.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_as_text(item) for item in value if item not in (None, ""))
    if isinstance(value, dict):
        return " ".join(_as_text(item) for item in value.values())
    return str(value or "").strip()


def _parse_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        # A common Actor format is "2026-06-01T00:00:00+0000".
        try:
            parsed = datetime.strptime(candidate, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _days_active(ad: dict[str, Any], now: datetime) -> int:
    reported = _first_value(ad, "days_active", "active_days")
    if isinstance(reported, (int, float)):
        return max(0, int(reported))

    reported_text = _as_text(_first_value(ad, "reported_active_time"))
    if reported_text:
        numbers = re.findall(r"\d+(?:[.,]\d+)?", reported_text.casefold())
        if numbers:
            amount = float(numbers[0].replace(",", "."))
            if any(word in reported_text.casefold() for word in ("year", "ano", "year")):
                return max(0, int(amount * 365))
            if any(word in reported_text.casefold() for word in ("month", "mês", "mes")):
                return max(0, int(amount * 30))
            if any(word in reported_text.casefold() for word in ("week", "semana")):
                return max(0, int(amount * 7))
            return max(0, int(amount))

    created = _first_value(
        ad,
        "ad_delivery_start_time",
        "ad_creation_time",
        "created_at",
        "start_date",
    )
    parsed = _parse_datetime(created)
    return max(0, (now - parsed).days) if parsed else 0


def _is_inactive(ad: dict[str, Any], now: datetime) -> bool:
    for key in ("is_active", "active"):
        if key in ad and ad[key] is False:
            return True

    for key in ("active_status", "ad_active_status", "status", "delivery_status"):
        if key not in ad or ad[key] in (None, ""):
            continue
        value = _fold(ad[key])
        if any(word in value for word in ("inactive", "paused", "closed", "expired", "stopped", "deleted", "ended")):
            return True
        if value in {"false", "0", "off"}:
            return True

    stop_value = _first_value(ad, "ad_delivery_stop_time", "stop_date", "end_date")
    stop_date = _parse_datetime(stop_value)
    return stop_date is not None and stop_date <= now


def _copy_text(ad: dict[str, Any]) -> str:
    value = _first_value(
        ad,
        "copy",
        "ad_creative_bodies",
        "body",
        "ad_text",
        "text",
        "description",
        "message",
    )
    return _as_text(value)


def _is_relevant(keyword: str, ad: dict[str, Any]) -> bool:
    query_tokens = list(dict.fromkeys(_tokens(keyword)))
    if not query_tokens:
        return False

    text_parts = [
        _copy_text(ad),
        _as_text(_first_value(
            ad,
            "page_name",
            "advertiser",
            "title",
            "headline",
            "ad_creative_link_titles",
            "ad_creative_link_descriptions",
            "ad_creative_link_captions",
            "caption",
            "primary_text",
        )),
    ]
    text = _fold(" ".join(text_parts))
    text_token_string = " ".join(_tokens(text))
    phrase = " ".join(query_tokens)
    if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text_token_string):
        return True

    text_tokens = set(_tokens(text))
    if len(query_tokens) == 1:
        return query_tokens[0] in text_tokens

    coverage = sum(token in text_tokens for token in query_tokens) / len(query_tokens)
    return coverage >= 0.60


def _is_ad_library_url(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold()
    return hostname.endswith("facebook.com") and "/ads/library" in (parsed.path or "").casefold()


def _is_facebook_library_link(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold()
    return hostname in {"facebook.com", "www.facebook.com", "m.facebook.com"} and "/ads/library" in (
        parsed.path or ""
    ).casefold()


def _iter_urls(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, list):
        for item in value:
            yield from _iter_urls(item)
    elif isinstance(value, dict):
        for key in ("url", "link", "href", "destination_url"):
            if value.get(key):
                yield from _iter_urls(value[key])


def _landing_url(ad: dict[str, Any]) -> str:
    fields = (
        "landing_url",
        "destination_url",
        "link_url",
        "website_url",
        "call_to_action_link",
        "ad_creative_link_urls",
        "snapshot_links",
        "links",
        "link",
    )
    for field in fields:
        for candidate in _iter_urls(ad.get(field)):
            if candidate.startswith(("http://", "https://")) and not _is_facebook_library_link(candidate):
                return candidate
    return ""


def _ad_library_url(ad: dict[str, Any], ad_id: str) -> str:
    for field in ("ad_library_url", "ad_snapshot_url", "snapshot_url"):
        candidate = _first_value(ad, field)
        if isinstance(candidate, str) and candidate and _is_facebook_library_link(candidate):
            return candidate
    if ad_id:
        return f"{FACEBOOK_AD_LIBRARY_URL}?id={quote(ad_id, safe='')}"
    return ""


def _countries(ad: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("countries", "country", "ad_reached_countries", "country_codes"):
        raw_value = ad.get(field)
        if isinstance(raw_value, list):
            for item in raw_value:
                if isinstance(item, dict):
                    item = item.get("country", item.get("code", item.get("region", "")))
                if item:
                    values.append(str(item).upper())
        elif raw_value:
            values.append(str(raw_value).upper())
    regions = ad.get("delivery_by_region", [])
    if isinstance(regions, list):
        for region in regions:
            if isinstance(region, dict) and region.get("region"):
                values.append(str(region["region"]).upper())
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _extract_ads(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "results", "ads"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    # A single ad object is accepted as one raw item; a metadata-only object is not.
    if any(key in payload for key in ("ad_archive_id", "id", "page_name", "ad_creative_bodies")):
        return [payload]
    return []


def _stable_key(ad: dict[str, Any], ad_id: str, ad_library_url: str, landing_url: str) -> str:
    if ad_id:
        return f"id:{ad_id}"
    if ad_library_url:
        return f"url:{ad_library_url}"
    if landing_url:
        return f"url:{landing_url}"
    page = _fold(_first_value(ad, "page_id", "page_name", "advertiser"))
    copy = _fold(_copy_text(ad))
    date_value = _fold(_first_value(ad, "ad_creation_time", "ad_delivery_start_time", "created_at", "start_date"))
    return f"fallback:{page}|{copy}|{date_value}"


def _normalise_ad(ad: dict[str, Any], now: datetime) -> dict[str, Any]:
    ad_id = _as_text(_first_value(ad, "ad_archive_id", "id", "ad_id"))
    advertiser = _as_text(_first_value(ad, "page_name", "advertiser", "page", "brand")) or "Desconhecido"
    page_id = _as_text(_first_value(ad, "page_id", "pageId"))
    copy = _copy_text(ad)
    reported_active_time = _as_text(_first_value(ad, "reported_active_time"))
    countries = _countries(ad)
    platforms = _first_value(ad, "publisher_platforms", "platforms") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    return {
        "ad_archive_id": ad_id,
        "advertiser": advertiser,
        "page_id": page_id,
        "days_active": _days_active(ad, now),
        "reported_active_time": reported_active_time,
        "link": _ad_library_url(ad, ad_id),
        "landing_url": _landing_url(ad),
        "copy": copy,
        "publisher_platforms": platforms,
        "countries": countries,
    }


def _empty_success(raw_count: int, filtered_out: int, duplicates_removed: int, capped: bool) -> dict[str, Any]:
    return {
        "source": "Facebook Ad Library via Apify",
        "query": "",
        "sample_limit": SAMPLE_LIMIT,
        "sample_capped": capped,
        "raw_ads_count": raw_count,
        "filtered_out_count": filtered_out,
        "duplicates_removed": duplicates_removed,
        "total_ads": 0,
        "ad_ids": [],
        "top_ads": [],
        "advertisers": [],
        "market_proven": False,
        "copy_examples": [],
        "saturation": "baixa",
    }


def _normalise(keyword: str, raw_ads: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    filtered: list[tuple[str, dict[str, Any]]] = []
    filtered_out = 0
    for raw in raw_ads:
        if _is_inactive(raw, now) or not _is_relevant(keyword, raw):
            filtered_out += 1
            continue
        normalised = _normalise_ad(raw, now)
        filtered.append((
            _stable_key(raw, normalised["ad_archive_id"], normalised["link"], normalised["landing_url"]),
            normalised,
        ))

    unique: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for key, ad in filtered:
        if key in unique:
            continue
        unique[key] = ad

    ads = list(unique.values())
    duplicate_count = len(filtered) - len(ads)
    top_ads = sorted(ads, key=lambda item: (-item["days_active"], item["ad_archive_id"], item["advertiser"]))[:3]

    advertiser_groups: dict[str, dict[str, Any]] = {}
    for ad in ads:
        group_key = ad["page_id"] or _fold(ad["advertiser"])
        group = advertiser_groups.setdefault(
            group_key,
            {
                "page_id": ad["page_id"],
                "page_name": ad["advertiser"],
                "active_ads": 0,
                "oldest_ad_days": 0,
            },
        )
        group["active_ads"] += 1
        group["oldest_ad_days"] = max(group["oldest_ad_days"], ad["days_active"])

    advertisers = sorted(
        advertiser_groups.values(),
        key=lambda item: (-item["active_ads"], _fold(item["page_name"]), item.get("page_id", "")),
    )[:5]

    copy_examples: list[str] = []
    seen_copies: set[str] = set()
    for ad in top_ads + ads:
        copy = ad.get("copy", "").strip()
        folded = _fold(copy)
        if copy and folded not in seen_copies:
            seen_copies.add(folded)
            copy_examples.append(copy)
        if len(copy_examples) == 5:
            break

    sample_capped = len(raw_ads) >= SAMPLE_LIMIT
    return {
        "source": "Facebook Ad Library via Apify",
        "query": keyword,
        "sample_limit": SAMPLE_LIMIT,
        "sample_capped": sample_capped,
        "raw_ads_count": len(raw_ads),
        "filtered_out_count": filtered_out,
        "duplicates_removed": duplicate_count,
        "total_ads": len(ads),
        "ad_ids": [ad["ad_archive_id"] for ad in ads if ad["ad_archive_id"]],
        "top_ads": top_ads,
        "advertisers": advertisers,
        "market_proven": any(item["active_ads"] >= 5 for item in advertiser_groups.values()),
        "copy_examples": copy_examples,
        "saturation": "alta" if len(ads) > 50 else "média" if len(ads) >= 10 else "baixa",
    }


def fetch(keyword: str) -> dict[str, Any]:
    """Run the Apify actor once and return a deterministic normalized result."""
    token = _get_token()
    if not token:
        return {"error": "APIFY_API_TOKEN não definido em .env"}

    library_url = (
        f"{FACEBOOK_AD_LIBRARY_URL}?active_status=active&ad_type=all&country=ALL&"
        f"is_targeted_country=false&media_type=all&q={quote(keyword, safe='')}&"
        "search_type=keyword_unordered"
    )
    params = {"clean": "true", "format": "json"}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"urls": [library_url], "count": SAMPLE_LIMIT}

    try:
        response = requests.post(
            APIFY_RUN_URL,
            params=params,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as exc:
        # Keep request details, including any accidental credential-bearing
        # exception text, out of logs and returned errors.
        logger.warning("Apify Ads: erro de rede")
        return {"error": f"Apify: erro de rede ({type(exc).__name__})"}

    if response.status_code not in (200, 201):
        logger.warning("Apify Ads: HTTP %s", response.status_code)
        return {"error": f"Apify: HTTP {response.status_code}"}

    try:
        payload_data = response.json()
    except Exception:
        return {"error": "Apify: resposta não é JSON válido"}

    raw_ads = _extract_ads(payload_data)
    valid_empty = isinstance(payload_data, list) or (
        isinstance(payload_data, dict)
        and any(key in payload_data and isinstance(payload_data[key], list) for key in ("data", "items", "results", "ads"))
    )
    if not raw_ads and not valid_empty:
        return {"error": "Apify: resposta sem lista de anúncios"}
    result = _normalise(keyword, raw_ads)
    result["query"] = keyword
    return result
