"""Fail-closed loader for the isolated Offer + Creative Arbitrage config."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pipeline.arbitrage.models import (
    COMPETITION_KEYS,
    CREATIVE_SCORE_KEYS,
    ECONOMICS_KEYS,
    EVIDENCE_KEYS,
    FACT_KEYS,
    FX_RATE_KEYS,
    LANDING_KEYS,
    LINK_KEYS,
    META_STRUCTURE_KEYS,
    MONETIZATION_MODELS,
    OFFER_KEYS,
    PERMISSION_STATES,
    PERMISSIONS_KEYS,
    PRODUCT_TYPES,
    TOP_LEVEL_KEYS,
    TRENDS_KEYS,
    US_READINESS_KEYS,
    Competition,
    CreativeScores,
    Economics,
    Evidence,
    Fact,
    FxRate,
    Landing,
    Links,
    MetaStructure,
    Offer,
    Permissions,
    Trends,
    USReadiness,
    ArbitrageConfig,
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2}))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_SOURCE_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$")
_SAFE_STABLE_REF_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_SAFE_URL_SCHEMES = frozenset({"http", "https"})
_FACT_STATUSES = frozenset({"KNOWN", "CONTRADICTED"})


class ArbitrageConfigError(ValueError):
    """The arbitrage config is missing, malformed, or internally inconsistent."""


class _DuplicateKeyError(Exception):
    pass


class _NonFiniteJsonError(Exception):
    pass


class _InvalidConfig(Exception):
    pass


def load_config(path: str | Path) -> ArbitrageConfig:
    """Load and validate one arbitrage config without leaking file contents."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(
                handle,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_json,
            )
    except (OSError, UnicodeError):
        raise ArbitrageConfigError("cannot read arbitrage config") from None
    except (_DuplicateKeyError, _NonFiniteJsonError, json.JSONDecodeError):
        raise ArbitrageConfigError("invalid arbitrage config JSON") from None

    try:
        return _parse_config(data)
    except _InvalidConfig as exc:
        raise ArbitrageConfigError(f"invalid arbitrage config: {exc}") from None
    except (TypeError, ValueError, OverflowError):
        raise ArbitrageConfigError("invalid arbitrage config") from None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _reject_non_finite_json(_: str) -> None:
    raise _NonFiniteJsonError


def _parse_config(raw: Any) -> ArbitrageConfig:
    _require_object(raw, "top level")
    _require_exact_keys(raw, TOP_LEVEL_KEYS, "top level")
    if raw["schema_version"] != 1 or isinstance(raw["schema_version"], bool):
        _invalid("schema_version")
    if raw["base_currency"] != "EUR":
        _invalid("base_currency")
    fx_rates_raw = raw["fx_rates"]
    offers_raw = raw["offers"]
    if not isinstance(fx_rates_raw, list):
        _invalid("fx_rates")
    if not isinstance(offers_raw, list):
        _invalid("offers")

    fx_rates = tuple(_parse_fx_rate(item, index) for index, item in enumerate(fx_rates_raw))
    _ensure_unique((rate.currency for rate in fx_rates), "FX currency")
    offers = tuple(_parse_offer(item, index) for index, item in enumerate(offers_raw))
    _ensure_unique((offer.offer_id for offer in offers), "offer ID")
    return ArbitrageConfig(
        schema_version=1,
        base_currency="EUR",
        fx_rates=fx_rates,
        offers=offers,
    )


def _parse_fx_rate(raw: Any, index: int) -> FxRate:
    where = f"FX rate {index}"
    _require_object(raw, where)
    _require_exact_keys(raw, FX_RATE_KEYS, where)
    currency = _parse_currency(raw["currency"], where)
    eur_per_unit = _parse_number(raw["eur_per_unit"], where, positive=True)
    if currency == "EUR" and eur_per_unit != 1:
        _invalid(where)
    source_ref = _parse_id(raw["source_ref"], where)
    observed_at = _parse_date(raw["observed_at"], where)
    return FxRate(currency, eur_per_unit, source_ref, observed_at)


def _parse_offer(raw: Any, index: int) -> Offer:
    where = f"offer {index}"
    _require_object(raw, where)
    _require_exact_keys(raw, OFFER_KEYS, where)

    offer_id = _parse_id(raw["offer_id"], where)
    name = _parse_text(raw["name"], where)
    brand = _parse_text(raw["brand"], where)
    market_id = _parse_id(raw["market_id"], where)
    research_keyword = _parse_text(raw["research_keyword"], where)
    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        _invalid(where)
    monetization_model = raw["monetization_model"]
    if monetization_model not in MONETIZATION_MODELS:
        _invalid(where)
    product_type = raw["product_type"]
    if product_type not in PRODUCT_TYPES:
        _invalid(where)

    evidence = _parse_evidence_registry(raw["evidence"], where)
    evidence_refs = frozenset(item.source_ref for item in evidence)
    links = _parse_links(raw["links"], where, evidence_refs)
    economics = _parse_economics(raw["economics"], where, evidence_refs)
    permissions = _parse_permissions(raw["permissions"], where, evidence_refs)
    us_readiness = _parse_us_readiness(raw["us_readiness"], where, evidence_refs)
    landing = _parse_landing(raw["landing"], where, evidence_refs)
    creative_scores = _parse_creative_scores(raw["creative_scores"], where, evidence_refs)
    meta_structure = _parse_meta_structure(raw["meta_structure"], where, evidence_refs)
    trends = _parse_trends(raw["trends"], where, evidence_refs)
    competition = _parse_competition(raw["competition"], where, evidence_refs)

    _validate_cross_field_states(
        economics=economics,
        us_readiness=us_readiness,
        meta_structure=meta_structure,
    )
    return Offer(
        offer_id=offer_id,
        name=name,
        brand=brand,
        market_id=market_id,
        research_keyword=research_keyword,
        enabled=enabled,
        monetization_model=monetization_model,
        product_type=product_type,
        links=links,
        economics=economics,
        permissions=permissions,
        us_readiness=us_readiness,
        landing=landing,
        creative_scores=creative_scores,
        meta_structure=meta_structure,
        trends=trends,
        competition=competition,
        evidence=evidence,
    )


def _parse_evidence_registry(raw: Any, where: str) -> tuple[Evidence, ...]:
    if not isinstance(raw, list):
        _invalid(where)
    entries: list[Evidence] = []
    for index, item in enumerate(raw):
        entry_where = f"{where} evidence {index}"
        _require_object(item, entry_where)
        _require_exact_keys(item, EVIDENCE_KEYS, entry_where)
        source_ref = _parse_id(item["source_ref"], entry_where)
        source_type = _parse_text(item["source_type"], entry_where)
        if not _SAFE_SOURCE_TYPE_RE.fullmatch(source_type):
            _invalid(entry_where)
        url_raw = item["url"]
        stable_ref_raw = item["stable_ref"]
        if (url_raw is None) == (stable_ref_raw is None):
            _invalid(entry_where)
        url = None if url_raw is None else _parse_url(url_raw, entry_where)
        stable_ref = (
            None
            if stable_ref_raw is None
            else _parse_stable_ref(stable_ref_raw, entry_where)
        )
        observed_at = _parse_date(item["observed_at"], entry_where)
        captured_at = _parse_date(item["captured_at"], entry_where)
        pack_id = None if item["pack_id"] is None else _parse_id(item["pack_id"], entry_where)
        content_sha256 = item["content_sha256"]
        if not isinstance(content_sha256, str) or not _SHA256_RE.fullmatch(content_sha256):
            _invalid(entry_where)
        excerpt = item["excerpt"]
        if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 1000:
            _invalid(entry_where)
        if any(ord(char) < 32 and char not in "\t\n\r" for char in excerpt):
            _invalid(entry_where)
        entries.append(
            Evidence(
                source_ref=source_ref,
                source_type=source_type,
                url=url,
                stable_ref=stable_ref,
                observed_at=observed_at,
                captured_at=captured_at,
                pack_id=pack_id,
                content_sha256=content_sha256,
                excerpt=excerpt.strip(),
            )
        )
    _ensure_unique((entry.source_ref for entry in entries), f"{where} source_ref")
    return tuple(entries)


def _parse_links(raw: Any, where: str, evidence_refs: frozenset[str]) -> Links:
    _require_object(raw, f"{where} links")
    _require_exact_keys(raw, LINK_KEYS, f"{where} links")
    return Links(
        program_url=_parse_fact(
            raw["program_url"],
            f"{where} program_url",
            evidence_refs,
            _validate_url_value,
        ),
        offer_url=_parse_fact(
            raw["offer_url"],
            f"{where} offer_url",
            evidence_refs,
            _validate_url_value,
        ),
        landing_url=_parse_fact(
            raw["landing_url"],
            f"{where} landing_url",
            evidence_refs,
            _validate_url_value,
        ),
        ad_links=_parse_fact(
            raw["ad_links"],
            f"{where} ad_links",
            evidence_refs,
            _validate_url_list_value,
        ),
    )


def _parse_economics(raw: Any, where: str, evidence_refs: frozenset[str]) -> Economics:
    _require_object(raw, f"{where} economics")
    _require_exact_keys(raw, ECONOMICS_KEYS, f"{where} economics")
    number_fields = (
        "retail_price",
        "aov",
        "cpa_payout",
        "expected_payout_per_order",
        "recurring_commission",
        "wholesale_cost",
        "gross_spread",
        "monthly_fees",
        "minimum_order",
        "payment_fees",
        "fulfilment_cost",
        "shipping_cost",
        "expected_return_cost",
        "other_variable_cost",
        "expected_cpa",
    )
    values = {
        field: _parse_fact(
            raw[field],
            f"{where} {field}",
            evidence_refs,
            _nonnegative_number_value,
        )
        for field in number_fields
    }
    values["affiliate_commission_pct"] = _parse_fact(
        raw["affiliate_commission_pct"],
        f"{where} affiliate_commission_pct",
        evidence_refs,
        _percentage_value,
    )
    values["cookie_days"] = _parse_fact(
        raw["cookie_days"],
        f"{where} cookie_days",
        evidence_refs,
        _nonnegative_integer_value,
    )
    values["refund_rate"] = _parse_fact(
        raw["refund_rate"],
        f"{where} refund_rate",
        evidence_refs,
        _percentage_value,
    )
    values["refund_policy"] = _parse_fact(
        raw["refund_policy"],
        f"{where} refund_policy",
        evidence_refs,
        _text_value,
    )
    values["currency"] = _parse_fact(
        raw["currency"],
        f"{where} currency",
        evidence_refs,
        _currency_value,
    )
    return Economics(**values)


def _parse_permissions(raw: Any, where: str, evidence_refs: frozenset[str]) -> Permissions:
    _require_object(raw, f"{where} permissions")
    _require_exact_keys(raw, PERMISSIONS_KEYS, f"{where} permissions")
    return Permissions(
        **{
            field: _parse_fact(
                raw[field],
                f"{where} {field}",
                evidence_refs,
                _permission_value,
            )
            for field in PERMISSIONS_KEYS
        }
    )


def _parse_us_readiness(raw: Any, where: str, evidence_refs: frozenset[str]) -> USReadiness:
    _require_object(raw, f"{where} us_readiness")
    _require_exact_keys(raw, US_READINESS_KEYS, f"{where} us_readiness")
    values: dict[str, Fact | None] = {
        "us_warehouse": _parse_fact(
            raw["us_warehouse"], f"{where} us_warehouse", evidence_refs, _boolean_value
        ),
        "us_fulfilment": _parse_fact(
            raw["us_fulfilment"], f"{where} us_fulfilment", evidence_refs, _boolean_value
        ),
        "delivery_min_days": _parse_fact(
            raw["delivery_min_days"],
            f"{where} delivery_min_days",
            evidence_refs,
            _nonnegative_integer_value,
        ),
        "delivery_max_days": _parse_fact(
            raw["delivery_max_days"],
            f"{where} delivery_max_days",
            evidence_refs,
            _nonnegative_integer_value,
        ),
        "us_returns_address": _parse_fact(
            raw["us_returns_address"],
            f"{where} us_returns_address",
            evidence_refs,
            _text_value,
        ),
        "shipping_cost": _parse_fact(
            raw["shipping_cost"],
            f"{where} shipping_cost",
            evidence_refs,
            _nonnegative_number_value,
        ),
        "checkout_in_usd": _parse_fact(
            raw["checkout_in_usd"],
            f"{where} checkout_in_usd",
            evidence_refs,
            _boolean_value,
        ),
    }
    return USReadiness(**values)


def _parse_landing(raw: Any, where: str, evidence_refs: frozenset[str]) -> Landing:
    _require_object(raw, f"{where} landing")
    _require_exact_keys(raw, LANDING_KEYS, f"{where} landing")
    return Landing(
        functional_score=_parse_fact(
            raw["functional_score"],
            f"{where} functional_score",
            evidence_refs,
            _score_value,
        ),
        quality_score=_parse_fact(
            raw["quality_score"],
            f"{where} quality_score",
            evidence_refs,
            _score_value,
        ),
    )


def _parse_creative_scores(
    raw: Any, where: str, evidence_refs: frozenset[str]
) -> CreativeScores:
    _require_object(raw, f"{where} creative_scores")
    _require_exact_keys(raw, CREATIVE_SCORE_KEYS, f"{where} creative_scores")
    return CreativeScores(
        **{
            field: _parse_fact(
                raw[field],
                f"{where} {field}",
                evidence_refs,
                _score_value,
            )
            for field in CREATIVE_SCORE_KEYS
        }
    )


def _parse_meta_structure(
    raw: Any, where: str, evidence_refs: frozenset[str]
) -> MetaStructure:
    _require_object(raw, f"{where} meta_structure")
    _require_exact_keys(raw, META_STRUCTURE_KEYS, f"{where} meta_structure")
    values: dict[str, Fact | None] = {}
    for field in (
        "advertiser_count",
        "active_ads",
        "oldest_ad_days",
        "ads_older_than_30d",
        "creative_concept_count",
    ):
        values[field] = _parse_fact(
            raw[field], f"{where} {field}", evidence_refs, _nonnegative_integer_value
        )
    values["variants_per_concept"] = _parse_fact(
        raw["variants_per_concept"],
        f"{where} variants_per_concept",
        evidence_refs,
        _nonnegative_number_value,
    )
    for field in (
        "video_share",
        "image_share",
        "carousel_share",
        "ugc_share",
        "polished_share",
    ):
        values[field] = _parse_fact(
            raw[field], f"{where} {field}", evidence_refs, _percentage_value
        )
    for field in ("hooks", "observed_offers", "landing_pages"):
        values[field] = _parse_fact(
            raw[field], f"{where} {field}", evidence_refs, _string_list_value
        )
    values["ad_links"] = _parse_fact(
        raw["ad_links"], f"{where} ad_links", evidence_refs, _validate_url_list_value
    )
    return MetaStructure(**values)


def _parse_trends(raw: Any, where: str, evidence_refs: frozenset[str]) -> Trends:
    _require_object(raw, f"{where} trends")
    _require_exact_keys(raw, TRENDS_KEYS, f"{where} trends")
    return Trends(
        demand_score=_parse_fact(
            raw["demand_score"], f"{where} demand_score", evidence_refs, _score_value
        )
    )


def _parse_competition(raw: Any, where: str, evidence_refs: frozenset[str]) -> Competition:
    _require_object(raw, f"{where} competition")
    _require_exact_keys(raw, COMPETITION_KEYS, f"{where} competition")
    return Competition(
        structure_score=_parse_fact(
            raw["structure_score"],
            f"{where} structure_score",
            evidence_refs,
            _score_value,
        )
    )


def _parse_fact(
    raw: Any,
    where: str,
    evidence_refs: frozenset[str],
    validator: Callable[[Any], Any],
) -> Fact | None:
    if raw is None:
        return None
    _require_object(raw, where)
    _require_exact_keys(raw, FACT_KEYS, where)
    status = raw["status"]
    if status not in _FACT_STATUSES:
        _invalid(where)
    refs_raw = raw["source_refs"]
    if not isinstance(refs_raw, list) or not refs_raw:
        _invalid(where)
    refs = tuple(_parse_id(ref, where) for ref in refs_raw)
    _ensure_unique(refs, f"{where} source_refs")
    if any(ref not in evidence_refs for ref in refs):
        _invalid(where)
    value = raw["value"]
    if status == "KNOWN":
        if value is None:
            _invalid(where)
        value = validator(value)
    else:
        if value is not None or len(refs) < 2:
            _invalid(where)
    return Fact(value=value, status=status, source_refs=refs)


def _validate_cross_field_states(
    *,
    economics: Economics,
    us_readiness: USReadiness,
    meta_structure: MetaStructure,
) -> None:
    monetary_fields = (
        economics.retail_price,
        economics.aov,
        economics.cpa_payout,
        economics.expected_payout_per_order,
        economics.recurring_commission,
        economics.wholesale_cost,
        economics.gross_spread,
        economics.monthly_fees,
        economics.payment_fees,
        economics.fulfilment_cost,
        economics.shipping_cost,
        economics.expected_return_cost,
        economics.other_variable_cost,
        economics.expected_cpa,
    )
    if any(_is_known(item) for item in monetary_fields) and not _is_known(economics.currency):
        _invalid("economics currency")

    minimum = us_readiness.delivery_min_days
    maximum = us_readiness.delivery_max_days
    if _is_known(minimum) and _is_known(maximum) and minimum.value > maximum.value:
        _invalid("delivery range")

    shares = (
        meta_structure.video_share,
        meta_structure.image_share,
        meta_structure.carousel_share,
    )
    if sum(item.value for item in shares if _is_known(item)) > 100:
        _invalid("Meta creative shares")


def _is_known(value: Fact | None) -> bool:
    return value is not None and value.status == "KNOWN"


def _require_object(value: Any, where: str) -> None:
    if not isinstance(value, dict):
        _invalid(where)


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], where: str) -> None:
    if set(value) != expected:
        _invalid(where)


def _parse_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        _invalid(where)
    return value


def _parse_text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(where)
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        _invalid(where)
    return value.strip()


def _parse_currency(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        _invalid(where)
    return value


def _parse_date(value: Any, where: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        _invalid(where)
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                _invalid(where)
        else:
            date.fromisoformat(value)
    except ValueError:
        _invalid(where)
    return value


def _parse_number(value: Any, where: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(where)
    try:
        result = float(value)
    except (OverflowError, ValueError):
        _invalid(where)
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        _invalid(where)
    return result


def _parse_url(value: Any, where: str) -> str:
    if not isinstance(value, str):
        _invalid(where)
    if value != value.strip() or any(char.isspace() for char in value):
        _invalid(where)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        _invalid(where)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        _invalid(where)
    if parsed.scheme.casefold() not in _SAFE_URL_SCHEMES or not hostname:
        _invalid(where)
    if parsed.username is not None or parsed.password is not None:
        _invalid(where)
    if parsed.query or parsed.fragment or "?" in value or "#" in value:
        _invalid(where)
    host = hostname.casefold()
    if host == "localhost" or host.endswith(".local"):
        _invalid(where)
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        _invalid(where)
    if port is not None and not 1 <= port <= 65535:
        _invalid(where)
    if ":" in host and not host.startswith("["):
        netloc_host = f"[{host}]"
    else:
        netloc_host = host
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    netloc = netloc_host if port in (None, default_port) else f"{netloc_host}:{port}"
    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), netloc, path, "", ""))


def _parse_stable_ref(value: Any, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or not _SAFE_STABLE_REF_RE.fullmatch(value)
    ):
        _invalid(where)
    return value.strip()


def _validate_url_value(value: Any) -> str:
    return _parse_url(value, "URL fact")


def _validate_url_list_value(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        _invalid("URL list fact")
    return tuple(_parse_url(item, "URL list fact") for item in value)


def _text_value(value: Any) -> str:
    return _parse_text(value, "text fact")


def _string_list_value(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        _invalid("string list fact")
    return tuple(_parse_text(item, "string list fact") for item in value)


def _boolean_value(value: Any) -> bool:
    if not isinstance(value, bool):
        _invalid("boolean fact")
    return value


def _currency_value(value: Any) -> str:
    return _parse_currency(value, "currency fact")


def _permission_value(value: Any) -> str:
    if value not in PERMISSION_STATES:
        _invalid("permission fact")
    return value


def _nonnegative_number_value(value: Any) -> float:
    return _parse_number(value, "number fact")


def _nonnegative_integer_value(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid("integer fact")
    return value


def _percentage_value(value: Any) -> float:
    result = _parse_number(value, "percentage fact")
    if result > 100:
        _invalid("percentage fact")
    return result


def _score_value(value: Any) -> float:
    result = _parse_number(value, "score fact")
    if result > 10:
        _invalid("score fact")
    return result


def _ensure_unique(values: Any, what: str) -> None:
    materialized = tuple(values)
    if len(set(materialized)) != len(materialized):
        _invalid(what)


def _invalid(where: str) -> None:
    raise _InvalidConfig(f"invalid {where}")
