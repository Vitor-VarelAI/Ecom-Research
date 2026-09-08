"""Deterministic Offer First commercial and 48H scoring."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any, Mapping

from pipeline.arbitrage.evidence import CONTRADICTED, KNOWN, STALE, UNKNOWN
from pipeline.arbitrage.gates import GateReport, evaluate_gates
from pipeline.arbitrage.heuristics import (
    AFFILIATE_LIKE_MODELS,
    average_decimals,
    band_from_0_to_10,
    band_from_percent,
    known_decimal,
    known_nonempty,
    numeric_state,
    select_payout,
    select_sale_price,
    signal,
    signal_state,
    signal_value,
    to_decimal,
)
from pipeline.arbitrage.models import ArbitrageConfig, Offer

SCORING_VERSION = "OfferFirstV1"
WEIGHTS: Mapping[str, int] = MappingProxyType(
    {
        "economics": 25,
        "creative": 20,
        "paid": 15,
        "meta": 15,
        "landing": 10,
        "us": 5,
        "trends": 5,
        "competition": 5,
    }
)
H48_WEIGHTS: Mapping[str, int] = MappingProxyType(
    {
        "economics": 25,
        "creative": 30,
        "paid": 25,
        "meta": 5,
        "landing": 5,
        "us": 5,
        "budget_fit": 5,
    }
)
H48_CREATIVE_FIELDS = (
    "hookability",
    "demo_potential",
    "ugc_potential",
    "storytelling_potential",
    "angle_breadth",
    "creative_gap",
)
ALL_CREATIVE_FIELDS = (
    "visual_transformation",
    "hookability",
    "problem_awareness",
    "demo_potential",
    "ugc_potential",
    "storytelling_potential",
    "ai_creative_potential",
    "before_after_potential",
    "angle_breadth",
    "creative_gap",
)
META_FIELDS = (
    "advertiser_count",
    "active_ads",
    "oldest_ad_days",
    "ads_older_than_30d",
    "creative_concept_count",
    "variants_per_concept",
)
INVENTORY_OWNING_MODELS = frozenset({"reseller", "direct_fulfilment"})


@dataclass(frozen=True, slots=True)
class EconomicsCalculation:
    """Per-order economics before paid acquisition."""

    state: str
    contribution_eur: Decimal | None
    break_even_cpa_eur: Decimal | None
    headroom: Decimal | None
    economics_score: int | None
    selected_payout_eur: Decimal | None = None
    sale_price_eur: Decimal | None = None
    excluded_monthly_fees_eur: Decimal | None = None
    missing_fields: tuple[str, ...] = ()
    reason: str = ""

    @property
    def contribution(self) -> Decimal | None:
        return self.contribution_eur

    @property
    def break_even_cpa(self) -> Decimal | None:
        return self.break_even_cpa_eur

    @property
    def excluded_monthly_fee_eur(self) -> Decimal | None:
        return self.excluded_monthly_fees_eur


@dataclass(frozen=True, slots=True)
class ScoreCard:
    """Commercial and 48H scores, diagnostics, and their evidence gaps."""

    score: int | None
    diagnostic_score: int
    known_weight: int
    components: Mapping[str, int | None]
    unknown_components: tuple[str, ...]
    economics: EconomicsCalculation
    score_48h: int | None
    diagnostic_score_48h: int
    known_weight_48h: int
    components_48h: Mapping[str, int | None]
    unknown_components_48h: tuple[str, ...]
    gates: GateReport
    version: str = SCORING_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "components", MappingProxyType(dict(self.components)))
        object.__setattr__(
            self,
            "components_48h",
            MappingProxyType(dict(self.components_48h)),
        )
        object.__setattr__(self, "unknown_components", tuple(self.unknown_components))
        object.__setattr__(
            self,
            "unknown_components_48h",
            tuple(self.unknown_components_48h),
        )

    @property
    def commercial_score(self) -> int | None:
        return self.score

    @property
    def h48_score(self) -> int | None:
        return self.score_48h

    @property
    def forty_eight_hour_score(self) -> int | None:
        return self.score_48h

    @property
    def commercial_components(self) -> Mapping[str, int | None]:
        return self.components

    @property
    def incomplete(self) -> bool:
        return bool(self.unknown_components or self.unknown_components_48h)


def _state_for_missing(items: tuple[Any, ...]) -> str:
    states = tuple(signal_state(item) for item in items)
    if CONTRADICTED in states:
        return CONTRADICTED
    if STALE in states:
        return STALE
    return UNKNOWN


def _fx(evidence: Any) -> Decimal | None:
    fx = getattr(evidence, "fx_rate", None)
    if signal_state(fx) != KNOWN:
        return None
    value = to_decimal(signal_value(fx))
    return value if value is not None and value > 0 else None


def _money(item: Any, fx_rate: Decimal) -> Decimal | None:
    value = known_decimal(item)
    return None if value is None else value * fx_rate


def _monthly_fee(economics: Any, fx_rate: Decimal) -> Decimal | None:
    item = signal(economics, "monthly_fees")
    return _money(item, fx_rate) if signal_state(item) == KNOWN else None


def _missing_cost(
    economics: Any,
    names: tuple[str, ...],
    fx_rate: Decimal,
) -> tuple[dict[str, Decimal], tuple[str, ...], str]:
    values: dict[str, Decimal] = {}
    missing: list[str] = []
    states: list[Any] = []
    for name in names:
        item = signal(economics, name)
        states.append(item)
        value = _money(item, fx_rate)
        if value is None:
            missing.append(name)
        else:
            values[name] = value
    if missing:
        return values, tuple(missing), _state_for_missing(tuple(states))
    return values, (), KNOWN


def _economics_band(headroom: Decimal | None) -> int | None:
    if headroom is None:
        return None
    if headroom < Decimal("1"):
        return 0
    if headroom < Decimal("1.25"):
        return 25
    if headroom < Decimal("1.5"):
        return 50
    if headroom < Decimal("2"):
        return 75
    return 100


def calculate_economics(
    offer: Offer,
    config_or_evidence: ArbitrageConfig | Any,
    evidence_or_config: Any | None = None,
) -> EconomicsCalculation:
    """Calculate contribution in EUR using the resolved, fresh FX signal.

    The second and third positional arguments accept either
    ``(evidence)`` or ``(config, evidence)`` / ``(evidence, config)``.  The
    config is consumed for interface compatibility; the resolved FX signal is
    the source of truth for the calculation.
    """

    if hasattr(config_or_evidence, "fx_rate"):
        evidence = config_or_evidence
    elif evidence_or_config is not None and hasattr(evidence_or_config, "fx_rate"):
        evidence = evidence_or_config
    else:
        evidence = config_or_evidence

    economics = getattr(evidence, "economics", None)
    fx_rate = _fx(evidence)
    if fx_rate is None:
        return EconomicsCalculation(
            state=signal_state(getattr(evidence, "fx_rate", None)),
            contribution_eur=None,
            break_even_cpa_eur=None,
            headroom=None,
            economics_score=None,
            excluded_monthly_fees_eur=None,
            reason="fresh FX rate is missing, stale, or contradicted",
        )

    monthly_fees_signal = numeric_state(
        signal(economics, "monthly_fees"),
        subject="monthly_fees",
    )
    monthly_fees = (
        monthly_fees_signal.value * fx_rate
        if monthly_fees_signal.state == KNOWN
        else None
    )

    model = getattr(offer, "monetization_model", None)
    if model in AFFILIATE_LIKE_MODELS:
        payout = select_payout(economics)
        payout_eur = (
            payout.value * fx_rate if payout.state == KNOWN else None
        )
        costs, missing, state = _missing_cost(
            economics,
            ("payment_fees", "other_variable_cost", "expected_return_cost"),
            fx_rate,
        )
        if payout_eur is None:
            state = _state_for_missing(
                (
                    signal(economics, "expected_payout_per_order"),
                    signal(economics, "cpa_payout"),
                    signal(economics, "aov"),
                    signal(economics, "retail_price"),
                    signal(economics, "affiliate_commission_pct"),
                )
            )
            return EconomicsCalculation(
                state=state,
                contribution_eur=None,
                break_even_cpa_eur=None,
                headroom=None,
                economics_score=None,
                selected_payout_eur=None,
                excluded_monthly_fees_eur=monthly_fees,
                missing_fields=("payout",) + missing,
                reason="payout proof is missing",
            )
        if missing:
            return EconomicsCalculation(
                state=state,
                contribution_eur=None,
                break_even_cpa_eur=None,
                headroom=None,
                economics_score=None,
                selected_payout_eur=payout_eur,
                excluded_monthly_fees_eur=monthly_fees,
                missing_fields=missing,
                reason="per-order cost evidence is incomplete",
            )
        contribution = payout_eur - sum(costs.values(), Decimal("0"))
    else:
        sale = select_sale_price(economics)
        wholesale = numeric_state(
            signal(economics, "wholesale_cost"),
            subject="wholesale_cost",
        )
        required = (
            "fulfilment_cost",
            "shipping_cost",
            "payment_fees",
            "expected_return_cost",
            "other_variable_cost",
        )
        costs, missing, state = _missing_cost(economics, required, fx_rate)
        refund = numeric_state(
            signal(economics, "refund_rate"),
            subject="refund_rate",
        )
        minimum_order = numeric_state(
            signal(economics, "minimum_order"),
            subject="minimum_order",
        )
        if sale.state != KNOWN:
            missing = ("sale_price",) + missing
        if wholesale.state != KNOWN:
            missing = ("wholesale_cost",) + missing
        if refund.state != KNOWN:
            missing = ("refund_rate",) + missing
        if model in {"reseller", "dropshipping", "direct_fulfilment"} and minimum_order.state != KNOWN:
            missing = ("minimum_order",) + missing
        if missing:
            missing_unique = tuple(dict.fromkeys(missing))
            missing_state = state
            if missing_state == KNOWN:
                missing_state = _state_for_missing(
                    (
                        signal(economics, "aov"),
                        signal(economics, "retail_price"),
                        signal(economics, "wholesale_cost"),
                        signal(economics, "refund_rate"),
                        signal(economics, "minimum_order"),
                    )
                )
            return EconomicsCalculation(
                state=missing_state,
                contribution_eur=None,
                break_even_cpa_eur=None,
                headroom=None,
                economics_score=None,
                sale_price_eur=(
                    sale.value * fx_rate if sale.state == KNOWN else None
                ),
                excluded_monthly_fees_eur=monthly_fees,
                missing_fields=missing_unique,
                reason="physical per-order evidence is incomplete",
            )
        sale_eur = sale.value * fx_rate
        wholesale_eur = wholesale.value * fx_rate
        retained_revenue = sale_eur * (
            Decimal("1") - refund.value / Decimal("100")
        )
        contribution = retained_revenue - wholesale_eur - sum(
            costs.values(), Decimal("0")
        )
        payout_eur = None
        sale_price_eur = sale_eur

    expected_cpa = numeric_state(
        signal(economics, "expected_cpa"),
        subject="expected_cpa",
    )
    expected_cpa_eur = (
        expected_cpa.value * fx_rate
        if expected_cpa.state == KNOWN
        else None
    )
    headroom = (
        contribution / expected_cpa_eur
        if expected_cpa_eur is not None and expected_cpa_eur > 0
        else None
    )
    band = _economics_band(headroom)
    state = KNOWN if headroom is not None else _state_for_missing(
        (signal(economics, "expected_cpa"),)
    )
    return EconomicsCalculation(
        state=state,
        contribution_eur=contribution,
        break_even_cpa_eur=contribution,
        headroom=headroom,
        economics_score=band,
        selected_payout_eur=locals().get("payout_eur"),
        sale_price_eur=locals().get("sale_price_eur"),
        excluded_monthly_fees_eur=monthly_fees,
        reason=(
            "contribution and headroom calculated"
            if state == KNOWN
            else "expected CPA evidence is unresolved"
        ),
    )


def _score_dimension(value: Any) -> int | None:
    return band_from_0_to_10(value)


def _creative_component(evidence: Any, fields: tuple[str, ...]) -> int | None:
    group = getattr(evidence, "creative_scores", None)
    values = []
    for name in fields:
        item = signal(group, name)
        if signal_state(item) != KNOWN:
            return None
        value = to_decimal(signal_value(item))
        if value is None:
            return None
        values.append(value)
    average = average_decimals(values)
    return _score_dimension(average)


def _permission_component(evidence: Any) -> int | None:
    permissions = getattr(evidence, "permissions", None)
    channels = (
        signal(permissions, "meta_ads"),
        signal(permissions, "tiktok_ads"),
    )
    custom = signal(permissions, "custom_creatives")
    rights = tuple(
        signal(permissions, name)
        for name in (
            "brand_name_use",
            "brand_assets",
            "brand_bidding",
            "map_policy",
        )
    )
    if signal_state(custom) != KNOWN:
        return None
    if any(
        signal_state(item) != KNOWN
        or signal_value(item) not in {"YES", "NO", "NOT_APPLICABLE"}
        for item in rights
    ):
        return None
    if signal_value(custom) == "NO":
        return 0
    if signal_value(custom) != "YES":
        return None
    if all(signal_state(item) == KNOWN for item in channels):
        channel_count = sum(signal_value(item) == "YES" for item in channels)
    elif any(
        signal_state(item) == KNOWN and signal_value(item) == "YES"
        for item in channels
    ):
        channel_count = 1
    else:
        return None
    channel_score = {0: 0, 1: 50, 2: 75, 3: 100}[channel_count]
    return band_from_percent((Decimal(channel_score) + Decimal("100")) / Decimal("2"))


def _meta_component(evidence: Any) -> int | None:
    group = getattr(evidence, "meta_structure", None)
    raw_values = []
    for name in META_FIELDS:
        item = signal(group, name)
        if signal_state(item) != KNOWN:
            return None
        value = to_decimal(signal_value(item))
        if value is None or value < 0:
            return None
        if name == "advertiser_count":
            score = 0 if value == 0 else 25 if value == 1 else 50 if value == 2 else 75 if value <= 5 else 100
        elif name == "active_ads":
            score = 0 if value == 0 else 25 if value <= 4 else 50 if value <= 9 else 75 if value <= 19 else 100
        elif name == "oldest_ad_days":
            score = 0 if value < 7 else 25 if value < 15 else 50 if value < 30 else 75 if value < 60 else 100
        elif name == "ads_older_than_30d":
            score = 0 if value == 0 else 25 if value == 1 else 50 if value <= 4 else 75 if value <= 9 else 100
        elif name == "creative_concept_count":
            score = 0 if value == 0 else 25 if value == 1 else 50 if value <= 3 else 75 if value <= 5 else 100
        else:
            score = 0 if value == 0 else 25 if value == 1 else 50 if value == 2 else 75 if value <= 4 else 100
        raw_values.append(Decimal(score))
    return band_from_percent(average_decimals(raw_values))


def _landing_component(evidence: Any) -> int | None:
    group = getattr(evidence, "landing", None)
    functional = signal(group, "functional_score")
    quality = signal(group, "quality_score")
    if signal_state(functional) != KNOWN or signal_state(quality) != KNOWN:
        return None
    functional_score = _score_dimension(signal_value(functional))
    quality_score = _score_dimension(signal_value(quality))
    if functional_score is None or quality_score is None:
        return None
    return min(functional_score, quality_score)


def _us_component(offer: Offer, evidence: Any) -> int | None:
    if (
        getattr(offer, "product_type", None) != "physical"
        or str(getattr(offer, "market_id", "")).upper() != "US"
    ):
        return 100
    group = getattr(evidence, "us_readiness", None)
    items = (
        signal(group, "us_warehouse"),
        signal(group, "us_fulfilment"),
        signal(group, "delivery_max_days"),
        signal(group, "us_returns_address"),
        signal(group, "checkout_in_usd"),
    )
    if any(signal_state(item) != KNOWN for item in items):
        return None
    warehouse = 100 if signal_value(items[0]) is True else 0
    fulfilment = 100 if signal_value(items[1]) is True else 0
    delivery = to_decimal(signal_value(items[2]))
    if delivery is None:
        return None
    delivery_score = (
        100 if delivery <= 3 else
        75 if delivery <= 7 else
        50 if delivery <= 10 else
        25 if delivery <= 14 else
        0
    )
    returns = 100 if known_nonempty(items[3]) else 0
    checkout = 100 if signal_value(items[4]) is True else 0
    return band_from_percent(
        average_decimals(
            (
                Decimal(warehouse),
                Decimal(fulfilment),
                Decimal(delivery_score),
                Decimal(returns),
                Decimal(checkout),
            )
        )
    )


def _secondary_component(evidence: Any, group_name: str, field: str) -> int | None:
    item = signal(getattr(evidence, group_name, None), field)
    if signal_state(item) != KNOWN:
        return None
    return _score_dimension(signal_value(item))


def _budget_fit_component(
    offer: Offer,
    evidence: Any,
    economics: EconomicsCalculation,
) -> int | None:
    """Score only launch cash evidence that is already known.

    The commitment uses monthly fees and, for inventory-owning models, the
    known MOQ multiplied by known wholesale cost. It never treats an absent
    value as zero and does not infer a test budget or traffic volume.
    """

    monthly_fees = economics.excluded_monthly_fees_eur
    if monthly_fees is None:
        return None

    commitment = monthly_fees
    if getattr(offer, "monetization_model", None) in INVENTORY_OWNING_MODELS:
        fx_rate = _fx(evidence)
        if fx_rate is None:
            return None
        economics_group = getattr(evidence, "economics", None)
        minimum_order = known_decimal(signal(economics_group, "minimum_order"))
        wholesale_cost = known_decimal(signal(economics_group, "wholesale_cost"))
        if minimum_order is None or wholesale_cost is None:
            return None
        commitment += minimum_order * wholesale_cost * fx_rate

    if commitment <= Decimal("20"):
        return 100
    if commitment <= Decimal("50"):
        return 75
    if commitment <= Decimal("100"):
        return 50
    if commitment <= Decimal("250"):
        return 25
    return 0


def _components(
    offer: Offer,
    evidence: Any,
    economics: EconomicsCalculation,
    *,
    creative_fields: tuple[str, ...],
) -> dict[str, int | None]:
    return {
        "economics": economics.economics_score,
        "creative": _creative_component(evidence, creative_fields),
        "paid": _permission_component(evidence),
        "meta": _meta_component(evidence),
        "landing": _landing_component(evidence),
        "us": _us_component(offer, evidence),
        "trends": _secondary_component(evidence, "trends", "demand_score"),
        "competition": _secondary_component(
            evidence,
            "competition",
            "structure_score",
        ),
    }


def _aggregate(
    components: Mapping[str, int | None],
    weights: Mapping[str, int] = WEIGHTS,
) -> tuple[int, int, int, tuple[str, ...]]:
    raw = Decimal("0")
    known_weight = 0
    unknown: list[str] = []
    for name, weight in weights.items():
        value = components.get(name)
        if weight == 0:
            continue
        if value is None:
            unknown.append(name)
            continue
        known_weight += weight
        raw += Decimal(value) * Decimal(weight) / Decimal("100")
    diagnostic = int(raw.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return diagnostic, known_weight, tuple(unknown), tuple(unknown)


def _resolve_score_args(
    config_or_evidence: ArbitrageConfig | Any,
    evidence_or_config: Any | None,
) -> tuple[Any, ArbitrageConfig | None]:
    if hasattr(config_or_evidence, "fx_rate"):
        return config_or_evidence, (
            evidence_or_config if isinstance(evidence_or_config, ArbitrageConfig) else None
        )
    return evidence_or_config, (
        config_or_evidence if isinstance(config_or_evidence, ArbitrageConfig) else None
    )


def score_offer(
    offer: Offer,
    config_or_evidence: ArbitrageConfig | Any,
    evidence_or_config: Any | None = None,
    *,
    gates: GateReport | None = None,
) -> ScoreCard:
    evidence, _config = _resolve_score_args(config_or_evidence, evidence_or_config)
    gate_report = gates or evaluate_gates(offer, evidence)
    economics = calculate_economics(offer, _config or evidence, evidence)
    commercial_components = _components(
        offer,
        evidence,
        economics,
        creative_fields=ALL_CREATIVE_FIELDS,
    )
    h48_components = _components(
        offer,
        evidence,
        economics,
        creative_fields=H48_CREATIVE_FIELDS,
    )
    h48_components["budget_fit"] = _budget_fit_component(
        offer,
        evidence,
        economics,
    )
    diagnostic, known_weight, unknown, _ = _aggregate(commercial_components)
    diagnostic_48h, known_weight_48h, unknown_48h, _ = _aggregate(
        h48_components,
        H48_WEIGHTS,
    )
    commercial_complete = gate_report.all_pass and not unknown
    h48_complete = gate_report.all_pass and not unknown_48h
    return ScoreCard(
        score=diagnostic if commercial_complete else None,
        diagnostic_score=diagnostic,
        known_weight=known_weight,
        components=commercial_components,
        unknown_components=unknown,
        economics=economics,
        score_48h=diagnostic_48h if h48_complete else None,
        diagnostic_score_48h=diagnostic_48h,
        known_weight_48h=known_weight_48h,
        components_48h=h48_components,
        unknown_components_48h=unknown_48h,
        gates=gate_report,
    )


calculate_score = score_offer
build_scorecard = score_offer

__all__ = [
    "ALL_CREATIVE_FIELDS",
    "EconomicsCalculation",
    "H48_CREATIVE_FIELDS",
    "H48_WEIGHTS",
    "INVENTORY_OWNING_MODELS",
    "SCORING_VERSION",
    "ScoreCard",
    "WEIGHTS",
    "build_scorecard",
    "calculate_economics",
    "calculate_score",
    "score_offer",
]
