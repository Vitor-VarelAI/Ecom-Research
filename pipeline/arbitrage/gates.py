"""Pure hard-gate decisions for Offer First arbitrage candidates."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from pipeline.arbitrage.evidence import CONTRADICTED, KNOWN, STALE, UNKNOWN
from pipeline.arbitrage.heuristics import (
    AFFILIATE_LIKE_MODELS,
    RESELLER_LIKE_MODELS,
    SUPPORTED_MONETIZATION_MODELS,
    evidence_reason,
    evidence_state,
    known_bool,
    known_decimal,
    known_nonempty,
    select_gross_spread,
    select_payout,
    signal,
    signal_state,
    signal_value,
)
from pipeline.arbitrage.models import Offer

PASS = "PASS"
FAIL = "FAIL"
_COMMERCIAL_RIGHTS = (
    "brand_name_use",
    "brand_assets",
    "brand_bidding",
    "map_policy",
)
_KNOWN_POLICY_STATES = frozenset({"YES", "NO", "NOT_APPLICABLE"})


@dataclass(frozen=True, slots=True)
class GateResult:
    """One immutable gate outcome and its short audit reason."""

    status: str
    reason: str
    evidence_state: str | None = None
    applicable: bool = True
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.status not in {PASS, FAIL, UNKNOWN, CONTRADICTED}:
            raise ValueError(f"invalid gate status: {self.status}")
        if self.evidence_state is not None and self.evidence_state not in {
            KNOWN,
            UNKNOWN,
            STALE,
            CONTRADICTED,
        }:
            raise ValueError(f"invalid evidence state: {self.evidence_state}")

    @property
    def passed(self) -> bool:
        return self.status == PASS

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    @property
    def unresolved(self) -> bool:
        return self.status in {UNKNOWN, CONTRADICTED}


def _result(
    status: str,
    reason: str,
    *,
    evidence_state_value: str | None = None,
    applicable: bool = True,
    blocking: bool = True,
) -> GateResult:
    return GateResult(
        status=status,
        reason=reason,
        evidence_state=evidence_state_value,
        applicable=applicable,
        blocking=blocking,
    )


def _uncertain(subject: str, items: tuple[Any, ...]) -> GateResult:
    state = evidence_state(items)
    if state == CONTRADICTED:
        return _result(
            CONTRADICTED,
            f"{subject} evidence is contradicted",
            evidence_state_value=CONTRADICTED,
        )
    if state == STALE:
        return _result(
            UNKNOWN,
            f"{subject} evidence is stale",
            evidence_state_value=STALE,
        )
    return _result(
        UNKNOWN,
        f"{subject} evidence is missing or unverified",
        evidence_state_value=UNKNOWN,
    )


def _permission_result(
    item: Any,
    *,
    subject: str,
    yes_reason: str | None = None,
    no_reason: str | None = None,
    blocking: bool = True,
) -> GateResult:
    state = signal_state(item)
    if state == CONTRADICTED:
        return _result(
            CONTRADICTED,
            f"{subject} evidence is contradicted",
            evidence_state_value=CONTRADICTED,
            blocking=blocking,
        )
    if state == STALE:
        return _result(
            UNKNOWN,
            f"{subject} evidence is stale",
            evidence_state_value=STALE,
            blocking=blocking,
        )
    if state != KNOWN:
        return _result(
            UNKNOWN,
            f"{subject} is missing or needs verification",
            evidence_state_value=UNKNOWN,
            blocking=blocking,
        )
    value = signal_value(item)
    if value == "YES":
        return _result(PASS, yes_reason or f"{subject} is permitted")
    if value == "NO":
        return _result(
            FAIL,
            no_reason or f"{subject} is not permitted",
            evidence_state_value=KNOWN,
            blocking=blocking,
        )
    return _result(
        UNKNOWN,
        f"{subject} needs verification",
        evidence_state_value=KNOWN,
        blocking=blocking,
    )


def _url_result(item: Any, *, subject: str) -> GateResult:
    state = signal_state(item)
    if state == CONTRADICTED:
        return _result(
            CONTRADICTED,
            f"{subject} evidence is contradicted",
            evidence_state_value=CONTRADICTED,
        )
    if state == STALE:
        return _result(
            UNKNOWN,
            f"{subject} evidence is stale",
            evidence_state_value=STALE,
        )
    if state != KNOWN:
        return _result(
            UNKNOWN,
            f"{subject} is missing or unverified",
            evidence_state_value=UNKNOWN,
        )
    if known_nonempty(item):
        return _result(PASS, f"{subject} is known")
    return _result(UNKNOWN, f"{subject} is empty", evidence_state_value=KNOWN)


def _monetization_access(offer: Offer, evidence: Any) -> GateResult:
    model = getattr(offer, "monetization_model", None)
    if model not in SUPPORTED_MONETIZATION_MODELS:
        return _result(FAIL, "monetization model is unsupported", evidence_state_value=KNOWN)
    if not bool(getattr(offer, "enabled", False)):
        return _result(
            UNKNOWN,
            "offer is disabled; re-enable before testing",
            evidence_state_value=UNKNOWN,
        )
    return _url_result(
        signal(getattr(evidence, "links", None), "program_url"),
        subject="program access",
    )


def _commercial_rights(evidence: Any) -> GateResult:
    permissions = getattr(evidence, "permissions", None)
    items = tuple(signal(permissions, name) for name in _COMMERCIAL_RIGHTS)
    if any(signal_state(item) == CONTRADICTED for item in items):
        return _result(
            CONTRADICTED,
            "commercial rights evidence is contradicted",
            evidence_state_value=CONTRADICTED,
        )
    if any(signal_state(item) == STALE for item in items):
        return _result(
            UNKNOWN,
            "commercial rights evidence is stale",
            evidence_state_value=STALE,
        )
    if any(
        signal_state(item) != KNOWN
        or signal_value(item) not in _KNOWN_POLICY_STATES
        for item in items
    ):
        return _result(
            UNKNOWN,
            "commercial rights are missing or need verification",
            evidence_state_value=UNKNOWN,
        )
    return _result(PASS, "commercial rights policies are known")


def _payout_spread(offer: Offer, evidence: Any) -> GateResult:
    economics = getattr(evidence, "economics", None)
    model = getattr(offer, "monetization_model", None)
    if model in AFFILIATE_LIKE_MODELS:
        selected = select_payout(economics)
        if selected.state == KNOWN:
            if selected.value > 0:
                return _result(PASS, f"positive payout proven via {selected.source}")
            return _result(FAIL, "expected payout is not positive", evidence_state_value=KNOWN)
        return _uncertain(
            "payout",
            (
                signal(economics, "expected_payout_per_order"),
                signal(economics, "cpa_payout"),
                signal(economics, "aov"),
                signal(economics, "retail_price"),
                signal(economics, "affiliate_commission_pct"),
            ),
        )
    if model in RESELLER_LIKE_MODELS:
        selected = select_gross_spread(economics)
        if selected.state == KNOWN:
            if selected.value > 0:
                return _result(PASS, f"positive gross spread proven via {selected.source}")
            return _result(FAIL, "gross spread is not positive", evidence_state_value=KNOWN)
        return _uncertain(
            "gross spread",
            (
                signal(economics, "gross_spread"),
                signal(economics, "aov"),
                signal(economics, "retail_price"),
                signal(economics, "wholesale_cost"),
            ),
        )
    return _result(FAIL, "monetization model is unsupported", evidence_state_value=KNOWN)


def _paid_social(evidence: Any) -> GateResult:
    permissions = getattr(evidence, "permissions", None)
    meta = signal(permissions, "meta_ads")
    tiktok = signal(permissions, "tiktok_ads")
    values = (meta, tiktok)
    if any(signal_state(item) == KNOWN and signal_value(item) == "YES" for item in values):
        return _result(PASS, "Meta or TikTok paid social is permitted")
    if all(signal_state(item) == KNOWN and signal_value(item) == "NO" for item in values):
        return _result(FAIL, "Meta and TikTok paid social are both prohibited")
    return _uncertain("paid social", values)


def _ad_links(evidence: Any) -> GateResult:
    links = getattr(evidence, "links", None)
    meta = getattr(evidence, "meta_structure", None)
    candidates = (
        signal(links, "ad_links"),
        signal(meta, "ad_links"),
    )
    if any(known_nonempty(item) for item in candidates):
        return _result(PASS, "at least one ad link is known")
    return _uncertain("ad links", candidates)


def _us_traffic(offer: Offer, evidence: Any) -> GateResult:
    market_id = str(getattr(offer, "market_id", "")).upper()
    if market_id != "US":
        return _result(
            PASS,
            "US traffic is not applicable outside US mode",
            applicable=False,
        )
    return _permission_result(
        signal(getattr(evidence, "permissions", None), "us_traffic"),
        subject="US traffic",
    )


def _landing(evidence: Any) -> GateResult:
    links = getattr(evidence, "links", None)
    landing = getattr(evidence, "landing", None)
    url = signal(links, "landing_url")
    functional = signal(landing, "functional_score")
    states = (url, functional)
    state = evidence_state(states)
    if state == CONTRADICTED:
        return _result(
            CONTRADICTED,
            "landing evidence is contradicted",
            evidence_state_value=CONTRADICTED,
        )
    if state == STALE:
        return _result(
            UNKNOWN,
            "landing evidence is stale",
            evidence_state_value=STALE,
        )
    if state != KNOWN or not known_nonempty(url) or known_decimal(functional) is None:
        return _result(
            UNKNOWN,
            "landing URL or functional score is missing",
            evidence_state_value=UNKNOWN,
        )
    score = known_decimal(functional)
    if score is not None and score > 0:
        return _result(PASS, "landing URL is functional")
    return _result(
        FAIL,
        "landing functional score is zero",
        evidence_state_value=KNOWN,
        blocking=False,
    )


def _physical_us_fulfilment(offer: Offer, evidence: Any) -> GateResult:
    if (
        getattr(offer, "product_type", None) != "physical"
        or str(getattr(offer, "market_id", "")).upper() != "US"
    ):
        return _result(
            PASS,
            "physical US fulfilment is not applicable",
            applicable=False,
        )

    readiness = getattr(evidence, "us_readiness", None)
    required = {
        "US fulfilment": signal(readiness, "us_fulfilment"),
        "delivery_max_days": signal(readiness, "delivery_max_days"),
        "shipping cost": signal(readiness, "shipping_cost"),
        "returns address": signal(readiness, "us_returns_address"),
        "checkout in USD": signal(readiness, "checkout_in_usd"),
    }
    contradicted = tuple(item for item in required.values() if signal_state(item) == CONTRADICTED)
    if contradicted:
        return _result(
            CONTRADICTED,
            "physical US fulfilment evidence is contradicted",
            evidence_state_value=CONTRADICTED,
        )
    stale = tuple(item for item in required.values() if signal_state(item) == STALE)
    if stale:
        return _result(
            UNKNOWN,
            "physical US fulfilment evidence is stale",
            evidence_state_value=STALE,
        )
    fulfilment = required["US fulfilment"]
    if signal_state(fulfilment) == KNOWN and signal_value(fulfilment) is False:
        return _result(FAIL, "US fulfilment is explicitly false", evidence_state_value=KNOWN)
    if signal_state(fulfilment) != KNOWN or signal_value(fulfilment) is not True:
        return _result(UNKNOWN, "US fulfilment is missing", evidence_state_value=UNKNOWN)

    delivery = known_decimal(required["delivery_max_days"])
    shipping = known_decimal(required["shipping cost"])
    returns_address = required["returns address"]
    checkout = required["checkout in USD"]
    if delivery is None or shipping is None or shipping < 0:
        return _result(UNKNOWN, "delivery or shipping cost is missing", evidence_state_value=UNKNOWN)
    if not known_nonempty(returns_address):
        return _result(UNKNOWN, "US returns address is missing", evidence_state_value=UNKNOWN)
    if not known_bool(checkout, True):
        if signal_state(checkout) == KNOWN and signal_value(checkout) is False:
            return _result(FAIL, "checkout is not in USD", evidence_state_value=KNOWN)
        return _result(UNKNOWN, "USD checkout proof is missing", evidence_state_value=UNKNOWN)
    if (
        getattr(offer, "monetization_model", None) == "dropshipping"
        and delivery > 10
    ):
        return _result(
            FAIL,
            "dropshipping delivery exceeds 10 days",
            evidence_state_value=KNOWN,
        )
    return _result(PASS, "physical US fulfilment is ready")


@dataclass(frozen=True, slots=True)
class GateReport:
    """Immutable named gate results with fail-closed aggregate helpers."""

    monetization_access: GateResult
    commercial_rights: GateResult
    payout_spread: GateResult
    paid_social: GateResult
    custom_creatives: GateResult
    us_traffic: GateResult
    landing: GateResult
    ad_links: GateResult
    physical_us_fulfilment: GateResult
    google_ads: GateResult

    @property
    def results(self) -> Mapping[str, GateResult]:
        return MappingProxyType(
            {
                "monetization_access": self.monetization_access,
                "commercial_rights": self.commercial_rights,
                "payout_spread": self.payout_spread,
                "paid_social": self.paid_social,
                "custom_creatives": self.custom_creatives,
                "us_traffic": self.us_traffic,
                "landing": self.landing,
                "ad_links": self.ad_links,
                "physical_us_fulfilment": self.physical_us_fulfilment,
                "google_ads": self.google_ads,
            }
        )

    @property
    def hard_results(self) -> Mapping[str, GateResult]:
        return MappingProxyType(
            {
                name: result
                for name, result in self.results.items()
                if result.blocking
            }
        )

    @property
    def any_fail(self) -> bool:
        return any(result.status == FAIL for result in self.hard_results.values())

    @property
    def any_unknown(self) -> bool:
        return any(result.status == UNKNOWN for result in self.hard_results.values())

    @property
    def any_contradicted(self) -> bool:
        return any(
            result.status == CONTRADICTED for result in self.hard_results.values()
        )

    @property
    def stale_gates(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, result in self.hard_results.items()
            if result.evidence_state == STALE
        )

    @property
    def contradicted_gates(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, result in self.hard_results.items()
            if result.status == CONTRADICTED
        )

    @property
    def overall_status(self) -> str:
        if self.any_fail:
            return FAIL
        if self.any_contradicted:
            return CONTRADICTED
        if self.any_unknown:
            return UNKNOWN
        return PASS

    @property
    def status(self) -> str:
        return self.overall_status

    @property
    def all_pass(self) -> bool:
        return self.overall_status == PASS

    def __getitem__(self, name: str) -> GateResult:
        return self.results[name]


def evaluate_gates(offer: Offer, evidence: Any) -> GateReport:
    """Evaluate every hard gate without mutating the offer or evidence."""

    permissions = getattr(evidence, "permissions", None)
    return GateReport(
        monetization_access=_monetization_access(offer, evidence),
        commercial_rights=_commercial_rights(evidence),
        payout_spread=_payout_spread(offer, evidence),
        paid_social=_paid_social(evidence),
        custom_creatives=_permission_result(
            signal(permissions, "custom_creatives"),
            subject="custom creatives",
        ),
        us_traffic=_us_traffic(offer, evidence),
        landing=_landing(evidence),
        ad_links=_ad_links(evidence),
        physical_us_fulfilment=_physical_us_fulfilment(offer, evidence),
        google_ads=_permission_result(
            signal(permissions, "google_ads"),
            subject="Google Ads",
            blocking=False,
        ),
    )


gate_report = evaluate_gates
run_gates = evaluate_gates

__all__ = [
    "CONTRADICTED",
    "FAIL",
    "GateReport",
    "GateResult",
    "PASS",
    "UNKNOWN",
    "evaluate_gates",
    "gate_report",
    "run_gates",
]
