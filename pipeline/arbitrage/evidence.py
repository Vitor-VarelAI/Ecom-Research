"""Read-only evidence resolution for the isolated arbitrage contract.

This module deliberately has no network or legacy-pipeline dependency.  It
only resolves immutable config facts and reads already-written JSON packs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from ipaddress import ip_address
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pipeline.arbitrage.models import (
    ArbitrageConfig,
    Competition,
    CreativeScores,
    Economics,
    Evidence,
    Fact,
    Landing,
    Links,
    MetaStructure,
    Offer,
    Permissions,
    Trends,
    USReadiness,
)

KNOWN = "KNOWN"
UNKNOWN = "UNKNOWN"
STALE = "STALE"
CONTRADICTED = "CONTRADICTED"
SIGNAL_STATES = (KNOWN, UNKNOWN, STALE, CONTRADICTED)

FRESHNESS_DAYS = {
    "landing": 7,
    "meta": 14,
    "trends": 30,
    "competition": 30,
    "commercial": 30,
    "fx": 7,
}

_SOURCE_PRIORITY = {
    "official_terms": 70,
    "affiliate_network": 60,
    "merchant_page": 50,
    "meta_ads_library": 40,
    "landing_page": 40,
    "research_pack": 30,
    "manual_review": 20,
    "unknown": 0,
}
SOURCE_PRIORITY = MappingProxyType(_SOURCE_PRIORITY)

_GROUP_TYPES = {
    "links": Links,
    "economics": Economics,
    "permissions": Permissions,
    "us_readiness": USReadiness,
    "landing": Landing,
    "creative_scores": CreativeScores,
    "meta_structure": MetaStructure,
    "trends": Trends,
    "competition": Competition,
}


def _require_as_of(as_of: date) -> date:
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise TypeError("as_of must be a date")
    return as_of


def _parse_observed_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.date()
        return date.fromisoformat(value)
    except ValueError:
        return None


def _freeze(value: Any) -> Any:
    """Copy nested values into immutable, deterministic containers."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=repr, ensure_ascii=False)


def _source_key(source_type: str | None) -> str:
    if not isinstance(source_type, str):
        return "unknown"
    normalized = source_type.strip().casefold().replace("-", "_").replace(" ", "_")
    if normalized in _SOURCE_PRIORITY:
        return normalized
    if "official" in normalized and "term" in normalized:
        return "official_terms"
    if "affiliate" in normalized or "network" in normalized:
        return "affiliate_network"
    if "merchant" in normalized:
        return "merchant_page"
    if "meta" in normalized or "facebook" in normalized:
        return "meta_ads_library"
    if "landing" in normalized:
        return "landing_page"
    if "research" in normalized or "pack" in normalized:
        return "research_pack"
    if "manual" in normalized:
        return "manual_review"
    return "unknown"


def source_priority(source_type: str | None) -> int:
    """Return the deterministic source priority used by candidate merging."""

    return _SOURCE_PRIORITY[_source_key(source_type)]


@dataclass(frozen=True, slots=True)
class ResolvedSignal:
    """An immutable value plus its evidence state and provenance."""

    value: Any
    state: str
    source_refs: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    observed_at: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in SIGNAL_STATES:
            raise ValueError(f"invalid signal state: {self.state}")
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "source_refs", tuple(sorted(set(self.source_refs))))
        object.__setattr__(self, "source_types", tuple(sorted(set(self.source_types))))
        object.__setattr__(self, "observed_at", tuple(sorted(set(self.observed_at))))

    @property
    def status(self) -> str:
        """Alias for callers that use the config Fact vocabulary."""

        return self.state

    @property
    def is_known(self) -> bool:
        return self.state == KNOWN

    @property
    def priority(self) -> int:
        return max((source_priority(item) for item in self.source_types), default=0)


@dataclass(frozen=True, slots=True)
class ResolvedGroup:
    """A fixed-order group of resolved signals with attribute access."""

    name: str
    signals: tuple[tuple[str, ResolvedSignal], ...]

    @classmethod
    def from_mapping(cls, name: str, values: Mapping[str, ResolvedSignal]) -> "ResolvedGroup":
        return cls(name=name, signals=tuple((key, values[key]) for key in sorted(values)))

    def __getattr__(self, key: str) -> ResolvedSignal:
        for name, signal in self.signals:
            if name == key:
                return signal
        raise AttributeError(key)

    def get(self, key: str, default: ResolvedSignal | None = None) -> ResolvedSignal | None:
        for name, signal in self.signals:
            if name == key:
                return signal
        return default

    def as_dict(self) -> dict[str, ResolvedSignal]:
        return {name: signal for name, signal in self.signals}


@dataclass(frozen=True, slots=True)
class PackMetadata:
    path: str
    filename: str
    pack_date: date
    market_id: str
    research_keyword: str
    pack_id: str | None
    completed_at: str | None
    generated_at: str | None

    @property
    def date(self) -> date:
        return self.pack_date

    @property
    def timestamp(self) -> str | None:
        return self.completed_at or self.generated_at

    @property
    def source_ref(self) -> str:
        return f"research_pack:{self.filename}"


@dataclass(frozen=True, slots=True)
class ResearchPack:
    metadata: PackMetadata
    data: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OfferEvidence:
    """Resolved evidence groups consumed by gates, scoring, and the weekly CLI."""

    offer_id: str
    market_id: str
    research_keyword: str
    links: ResolvedGroup
    economics: ResolvedGroup
    permissions: ResolvedGroup
    us_readiness: ResolvedGroup
    landing: ResolvedGroup
    creative_scores: ResolvedGroup
    meta_structure: ResolvedGroup
    trends: ResolvedGroup
    competition: ResolvedGroup
    fx_rate: ResolvedSignal
    selected_pack: PackMetadata | None

    @property
    def pack_metadata(self) -> PackMetadata | None:
        return self.selected_pack

    @property
    def groups(self) -> tuple[ResolvedGroup, ...]:
        return (
            self.links,
            self.economics,
            self.permissions,
            self.us_readiness,
            self.landing,
            self.creative_scores,
            self.meta_structure,
            self.trends,
            self.competition,
        )


def _unknown(reason: str = "no evidence") -> ResolvedSignal:
    return ResolvedSignal(None, UNKNOWN, reason=reason)


def _evidence_map(evidence: Offer | Iterable[Evidence]) -> dict[str, Evidence]:
    entries = evidence.evidence if isinstance(evidence, Offer) else evidence
    return {item.source_ref: item for item in entries}


def resolve_fact(
    fact: Fact | None,
    evidence: Offer | Iterable[Evidence],
    as_of: date,
    *,
    max_age_days: int = FRESHNESS_DAYS["commercial"],
) -> ResolvedSignal:
    """Resolve one config Fact through all of its referenced Evidence.

    A contradicted Fact remains contradicted.  Every referenced Evidence row
    is required for a known Fact; a stale or future row makes that Fact stale,
    while a missing or malformed reference is unknown.
    """

    as_of = _require_as_of(as_of)
    if fact is None:
        return _unknown("fact is absent")
    refs = tuple(fact.source_refs)
    registry = _evidence_map(evidence)
    selected = [registry.get(ref) for ref in refs]
    source_types = tuple(item.source_type for item in selected if item is not None)
    observed_at = tuple(item.observed_at for item in selected if item is not None)

    if fact.status == CONTRADICTED:
        return ResolvedSignal(
            None,
            CONTRADICTED,
            refs,
            source_types,
            observed_at,
            "config fact is contradicted",
        )
    if fact.status != KNOWN:
        return _unknown("unsupported fact status")
    if not refs or any(item is None for item in selected):
        return ResolvedSignal(None, UNKNOWN, refs, source_types, observed_at, "evidence ref missing")

    invalid = False
    for item in selected:
        observed = _parse_observed_date(item.observed_at)
        if observed is None:
            return ResolvedSignal(None, UNKNOWN, refs, source_types, observed_at, "observed_at invalid")
        age = (as_of - observed).days
        if age < 0 or age > max_age_days:
            invalid = True
    if invalid:
        return ResolvedSignal(
            fact.value,
            STALE,
            refs,
            source_types,
            observed_at,
            "evidence is stale or from the future",
        )
    return ResolvedSignal(fact.value, KNOWN, refs, source_types, observed_at)


def resolve_candidates(candidates: Iterable[ResolvedSignal | None]) -> ResolvedSignal:
    """Resolve competing signals without silently choosing a tied disagreement."""

    signals = tuple(item for item in candidates if item is not None)
    if not signals:
        return _unknown("no candidates")
    if any(item.state == CONTRADICTED for item in signals):
        return _combine(signals, CONTRADICTED, None, "contradicted evidence")

    known = [item for item in signals if item.state == KNOWN]
    if known:
        best_priority = max(item.priority for item in known)
        winners = [item for item in known if item.priority == best_priority]
        values = {_canonical(item.value) for item in winners}
        if len(values) > 1:
            return _combine(winners, CONTRADICTED, None, "same-priority evidence disagrees")
        return _combine(winners, KNOWN, winners[0].value, winners[0].reason)

    stale = [item for item in signals if item.state == STALE]
    if stale:
        best_priority = max(item.priority for item in stale)
        winner = next(item for item in stale if item.priority == best_priority)
        return _combine(stale, STALE, winner.value, winner.reason)
    return _combine(signals, UNKNOWN, None, "all candidates are unknown")


def _combine(
    signals: Iterable[ResolvedSignal],
    state: str,
    value: Any,
    reason: str | None,
) -> ResolvedSignal:
    materialized = tuple(signals)
    return ResolvedSignal(
        value,
        state,
        tuple(ref for item in materialized for ref in item.source_refs),
        tuple(source for item in materialized for source in item.source_types),
        tuple(observed for item in materialized for observed in item.observed_at),
        reason,
    )


def resolve_fx(currency: str | None, config: ArbitrageConfig, as_of: date) -> ResolvedSignal:
    """Resolve a config FX rate into EUR without falling back to zero."""

    as_of = _require_as_of(as_of)
    if currency == config.base_currency == "EUR":
        return ResolvedSignal(1.0, KNOWN, ("base_currency:EUR",), ("base_currency",), (as_of.isoformat(),))
    if not isinstance(currency, str):
        return _unknown("currency is unknown")
    rate = next((item for item in config.fx_rates if item.currency == currency), None)
    if rate is None:
        return _unknown("FX rate is missing")
    observed = _parse_observed_date(rate.observed_at)
    if observed is None:
        return ResolvedSignal(None, UNKNOWN, (rate.source_ref,), ("fx",), (rate.observed_at,), "FX observed_at invalid")
    age = (as_of - observed).days
    if age < 0 or age > FRESHNESS_DAYS["fx"]:
        return ResolvedSignal(
            rate.eur_per_unit,
            STALE,
            (rate.source_ref,),
            ("fx",),
            (rate.observed_at,),
            "FX rate is stale or from the future",
        )
    return ResolvedSignal(
        rate.eur_per_unit,
        KNOWN,
        (rate.source_ref,),
        ("fx",),
        (rate.observed_at,),
    )


def convert_to_eur(
    amount: float | int | None,
    currency: str | None,
    config: ArbitrageConfig,
    as_of: date,
) -> float | None:
    signal = resolve_fx(currency, config, as_of)
    if signal.state != KNOWN or isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if not math.isfinite(float(amount)):
        return None
    return float(amount) * float(signal.value)


def is_safe_public_url(value: Any, *, allow_query: bool = False) -> str | None:
    """Return a canonical public URL, or None for an unsafe/malformed value."""

    if not isinstance(value, str) or value != value.strip() or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        if parsed.username is not None or parsed.password is not None:
            return None
        if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
            return None
        if parsed.fragment or (parsed.query and not allow_query):
            return None
        host = hostname.casefold()
        if host == "localhost" or host.endswith(".local"):
            return None
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
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    netloc = host
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    if port not in (None, default_port):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), netloc, path, parsed.query if allow_query else "", ""))


def _reject_non_standard_json(value: str) -> Any:
    raise ValueError(value)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle, parse_constant=_reject_non_standard_json)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        return parsed.timestamp()
    return parsed.replace(tzinfo=None).timestamp()


def _pack_timestamp(payload: Mapping[str, Any]) -> float | None:
    for key in ("completed_at", "generated_at"):
        parsed = _parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _pack_candidate(path: Path, payload: Mapping[str, Any], as_of: date) -> ResearchPack | None:
    pack_date = _parse_observed_date(payload.get("date"))
    if pack_date is None or pack_date > as_of:
        return None
    market_id = payload.get("market_id")
    keyword = payload.get("research_keyword") or payload.get("keyword")
    if not isinstance(market_id, str) or not isinstance(keyword, str):
        return None
    if not any(key in payload for key in ("facebook_ads", "trends", "keyword_planner", "landing_price")):
        return None
    if "run_id" in payload and payload.get("artifact_complete") is not True:
        return None
    completed_at = payload.get("completed_at")
    generated_at = payload.get("generated_at")
    if completed_at is not None and not isinstance(completed_at, str):
        completed_at = None
    if generated_at is not None and not isinstance(generated_at, str):
        generated_at = None
    metadata = PackMetadata(
        path=str(path),
        filename=path.name,
        pack_date=pack_date,
        market_id=market_id,
        research_keyword=keyword,
        pack_id=payload.get("pack_id") or payload.get("run_id"),
        completed_at=completed_at,
        generated_at=generated_at,
    )
    return ResearchPack(metadata, _freeze(payload))


def latest_pack(
    data_dir: str | Path,
    market_id: str,
    research_keyword: str,
    as_of: date,
) -> ResearchPack | None:
    """Select the newest complete exact-scope JSON pack without writing."""

    as_of = _require_as_of(as_of)
    root = Path(data_dir)
    if not root.is_dir():
        return None
    candidates: list[ResearchPack] = []
    for path in sorted(root.rglob("*.json"), key=lambda item: item.relative_to(root).as_posix()):
        if "runs" in path.relative_to(root).parts:
            continue
        payload = _load_json(path)
        if payload is None:
            continue
        if payload.get("market_id") != market_id:
            continue
        if not (payload.get("research_keyword") == research_keyword or payload.get("keyword") == research_keyword):
            continue
        candidate = _pack_candidate(path, payload, as_of)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None

    def sort_key(item: ResearchPack) -> tuple[date, float, str]:
        timestamp = _pack_timestamp(item.data)
        if timestamp is None:
            timestamp = float("-inf")
        return item.metadata.pack_date, timestamp, item.metadata.filename

    return max(candidates, key=sort_key)


def find_latest_pack(
    data_dir: str | Path,
    *,
    market_id: str,
    research_keyword: str,
    as_of: date,
) -> ResearchPack | None:
    return latest_pack(data_dir, market_id, research_keyword, as_of)


def _freshness_days(group: str, field: str) -> int:
    if group == "links":
        if field == "landing_url":
            return FRESHNESS_DAYS["landing"]
        if field == "ad_links":
            return FRESHNESS_DAYS["meta"]
        return FRESHNESS_DAYS["commercial"]
    if group == "landing":
        return FRESHNESS_DAYS["landing"]
    if group == "meta_structure":
        return FRESHNESS_DAYS["meta"]
    if group == "trends":
        return FRESHNESS_DAYS["trends"]
    if group == "competition":
        return FRESHNESS_DAYS["competition"]
    return FRESHNESS_DAYS["commercial"]


def _refresh_derived_signal(
    signal: ResolvedSignal,
    as_of: date,
    max_age_days: int,
) -> ResolvedSignal:
    if signal.state != KNOWN:
        return signal
    if not signal.observed_at:
        return ResolvedSignal(
            None,
            UNKNOWN,
            signal.source_refs,
            signal.source_types,
            signal.observed_at,
            "derived evidence has no observed_at",
        )
    for observed_raw in signal.observed_at:
        observed = _parse_observed_date(observed_raw)
        if observed is None:
            return ResolvedSignal(
                None,
                UNKNOWN,
                signal.source_refs,
                signal.source_types,
                signal.observed_at,
                "derived evidence observed_at invalid",
            )
        age = (as_of - observed).days
        if age < 0 or age > max_age_days:
            return ResolvedSignal(
                signal.value,
                STALE,
                signal.source_refs,
                signal.source_types,
                signal.observed_at,
                "derived evidence is stale or from the future",
            )
    return signal


def _resolve_group(
    offer: Offer,
    group_name: str,
    as_of: date,
    pack_signals: Mapping[str, Mapping[str, ResolvedSignal]],
) -> ResolvedGroup:
    model = getattr(offer, group_name)
    derived = pack_signals.get(group_name, {})
    values: dict[str, ResolvedSignal] = {}
    for field in fields(_GROUP_TYPES[group_name]):
        name = field.name
        fact = getattr(model, name)
        if fact is not None:
            values[name] = resolve_fact(
                fact,
                offer.evidence,
                as_of,
                max_age_days=_freshness_days(group_name, name),
            )
        else:
            derived_signal = derived.get(name)
            values[name] = (
                _unknown(f"{group_name}.{name} is absent")
                if derived_signal is None
                else _refresh_derived_signal(
                    derived_signal,
                    as_of,
                    _freshness_days(group_name, name),
                )
            )
    return ResolvedGroup.from_mapping(group_name, values)


def build_offer_evidence(
    offer: Offer,
    config: ArbitrageConfig,
    as_of: date,
    data_dir: str | Path,
) -> OfferEvidence:
    """Resolve one offer into immutable groups for gates and scoring."""

    as_of = _require_as_of(as_of)
    pack = latest_pack(data_dir, offer.market_id, offer.research_keyword, as_of)
    pack_signals: dict[str, Mapping[str, ResolvedSignal]] = {}
    if pack is not None:
        from pipeline.arbitrage.adapters.landing import derive_landing
        from pipeline.arbitrage.adapters.meta import derive_meta
        from pipeline.arbitrage.adapters.trends import derive_trends

        source_ref = pack.metadata.source_ref
        observed_at = pack.metadata.pack_date.isoformat()
        pack_signals["meta_structure"] = derive_meta(
            pack.data, source_ref=source_ref, observed_at=observed_at
        )
        pack_signals["trends"] = derive_trends(
            pack.data, source_ref=source_ref, observed_at=observed_at
        )
        pack_signals["landing"] = derive_landing(
            pack.data, source_ref=source_ref, observed_at=observed_at
        )

    groups = {
        name: _resolve_group(offer, name, as_of, pack_signals)
        for name in (
            "links",
            "economics",
            "permissions",
            "us_readiness",
            "landing",
            "creative_scores",
            "meta_structure",
            "trends",
            "competition",
        )
    }
    currency = groups["economics"].currency
    currency_value = currency.value if currency.state == KNOWN else None
    fx_rate = resolve_fx(currency_value, config, as_of)
    return OfferEvidence(
        offer_id=offer.offer_id,
        market_id=offer.market_id,
        research_keyword=offer.research_keyword,
        links=groups["links"],
        economics=groups["economics"],
        permissions=groups["permissions"],
        us_readiness=groups["us_readiness"],
        landing=groups["landing"],
        creative_scores=groups["creative_scores"],
        meta_structure=groups["meta_structure"],
        trends=groups["trends"],
        competition=groups["competition"],
        fx_rate=fx_rate,
        selected_pack=None if pack is None else pack.metadata,
    )


Signal = ResolvedSignal
ResolvedFact = ResolvedSignal
select_latest_pack = latest_pack
fx_to_eur = convert_to_eur

__all__ = [
    "CONTRADICTED",
    "FRESHNESS_DAYS",
    "KNOWN",
    "OfferEvidence",
    "PackMetadata",
    "ResearchPack",
    "ResolvedFact",
    "ResolvedGroup",
    "ResolvedSignal",
    "SIGNAL_STATES",
    "STALE",
    "Signal",
    "UNKNOWN",
    "build_offer_evidence",
    "convert_to_eur",
    "find_latest_pack",
    "fx_to_eur",
    "is_safe_public_url",
    "latest_pack",
    "resolve_candidates",
    "resolve_fact",
    "resolve_fx",
    "select_latest_pack",
    "source_priority",
    "SOURCE_PRIORITY",
]
