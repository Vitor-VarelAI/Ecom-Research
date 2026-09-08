"""Small deterministic helpers shared by the arbitrage decision modules.

The helpers in this module deliberately operate on the resolved evidence
objects instead of reaching into config Facts or making network calls.  A
resolved signal is considered usable only when its state is ``KNOWN``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from pipeline.arbitrage.evidence import CONTRADICTED, KNOWN, STALE, UNKNOWN

EVIDENCE_STATES = (KNOWN, UNKNOWN, STALE, CONTRADICTED)
SUPPORTED_MONETIZATION_MODELS = frozenset(
    {
        "affiliate",
        "creator",
        "ambassador",
        "cpa",
        "revenue_share",
        "reseller",
        "dropshipping",
        "direct_fulfilment",
        "tiktok_shop_affiliate",
    }
)
AFFILIATE_LIKE_MODELS = frozenset(
    {
        "affiliate",
        "creator",
        "ambassador",
        "cpa",
        "revenue_share",
        "tiktok_shop_affiliate",
    }
)
RESELLER_LIKE_MODELS = frozenset(
    {"reseller", "dropshipping", "direct_fulfilment"}
)


@dataclass(frozen=True, slots=True)
class DerivedValue:
    """A deterministic value selected or derived from one evidence group."""

    value: Any
    state: str
    source: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.state not in EVIDENCE_STATES:
            raise ValueError(f"invalid evidence state: {self.state}")


def signal(group: Any, name: str) -> Any:
    """Return one resolved signal from a group, tolerating compact fixtures."""

    if group is None:
        return None
    return getattr(group, name, None)


def signal_state(item: Any) -> str:
    """Return a signal state without treating absent fields as known."""

    state = getattr(item, "state", None)
    if state in EVIDENCE_STATES:
        return state
    status = getattr(item, "status", None)
    if status in EVIDENCE_STATES:
        return status
    return UNKNOWN


def signal_value(item: Any) -> Any:
    return getattr(item, "value", None)


def known_signal(item: Any) -> bool:
    return signal_state(item) == KNOWN


def _state_priority(state: str) -> int:
    return {CONTRADICTED: 3, STALE: 2, UNKNOWN: 1, KNOWN: 0}.get(state, 1)


def evidence_state(items: Any) -> str:
    """Summarise uncertainty while preserving contradiction/stale precedence."""

    materialized = tuple(items)
    states = tuple(signal_state(item) for item in materialized)
    if not states:
        return UNKNOWN
    return max(states, key=_state_priority)


def evidence_reason(items: Any, *, subject: str) -> str:
    state = evidence_state(items)
    if state == CONTRADICTED:
        return f"{subject} evidence is contradicted"
    if state == STALE:
        return f"{subject} evidence is stale"
    if state == UNKNOWN:
        return f"{subject} evidence is missing or unverified"
    return f"{subject} evidence is known"


def to_decimal(value: Any) -> Decimal | None:
    """Convert one numeric input without doing financial math in binary floats."""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if not isfinite(value):
            return None
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    return result if result.is_finite() else None


def known_decimal(item: Any) -> Decimal | None:
    if not known_signal(item):
        return None
    return to_decimal(signal_value(item))


def known_nonempty(item: Any) -> bool:
    if not known_signal(item):
        return False
    value = signal_value(item)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(isinstance(entry, str) and entry.strip() for entry in value)
    return value is not None


def known_bool(item: Any, expected: bool | None = None) -> bool:
    if not known_signal(item) or not isinstance(signal_value(item), bool):
        return False
    return expected is None or signal_value(item) is expected


def numeric_state(item: Any, *, subject: str) -> DerivedValue:
    """Normalise one resolved numeric signal, preserving its evidence state."""

    state = signal_state(item)
    if state != KNOWN:
        return DerivedValue(None, state, reason=f"{subject} is {state.lower()}")
    value = to_decimal(signal_value(item))
    if value is None:
        return DerivedValue(None, UNKNOWN, reason=f"{subject} is not numeric")
    return DerivedValue(value, KNOWN, reason=f"{subject} is known")


def _candidate_state(items: tuple[Any, ...]) -> str:
    return evidence_state(items)


def select_payout(economics: Any) -> DerivedValue:
    """Select a direct payout, then derive retail/AOV times commission."""

    expected = signal(economics, "expected_payout_per_order")
    cpa = signal(economics, "cpa_payout")
    direct = (
        ("expected_payout_per_order", expected),
        ("cpa_payout", cpa),
    )

    for name, item in direct:
        if signal_state(item) == KNOWN:
            value = to_decimal(signal_value(item))
            if value is None:
                return DerivedValue(None, UNKNOWN, name, f"{name} is not numeric")
            return DerivedValue(value, KNOWN, name, f"selected {name}")

    sale_price = select_sale_price(economics)
    commission = numeric_state(
        signal(economics, "affiliate_commission_pct"),
        subject="affiliate_commission_pct",
    )
    if sale_price.state == KNOWN and commission.state == KNOWN:
        derived = sale_price.value * commission.value / Decimal("100")
        if derived > 0:
            return DerivedValue(
                derived,
                KNOWN,
                "sale_price_x_affiliate_commission_pct",
                "derived payout from sale price and commission",
            )
        return DerivedValue(derived, KNOWN, "sale_price_x_affiliate_commission_pct")

    state = _candidate_state(
        (
            expected,
            cpa,
            signal(economics, "aov"),
            signal(economics, "retail_price"),
            signal(economics, "affiliate_commission_pct"),
        )
    )
    return DerivedValue(None, state, reason="no payout proof")


def select_sale_price(economics: Any) -> DerivedValue:
    """Prefer known AOV and fall back to known retail price."""

    aov = numeric_state(signal(economics, "aov"), subject="aov")
    if aov.state == KNOWN:
        return DerivedValue(aov.value, KNOWN, "aov")
    retail = numeric_state(
        signal(economics, "retail_price"),
        subject="retail_price",
    )
    if retail.state == KNOWN:
        return DerivedValue(retail.value, KNOWN, "retail_price")
    return DerivedValue(
        None,
        _candidate_state((signal(economics, "aov"), signal(economics, "retail_price"))),
        reason="sale price is unknown",
    )


def select_gross_spread(economics: Any) -> DerivedValue:
    direct_signal = signal(economics, "gross_spread")
    if signal_state(direct_signal) == KNOWN:
        direct = to_decimal(signal_value(direct_signal))
        if direct is None:
            return DerivedValue(
                None,
                UNKNOWN,
                "gross_spread",
                "gross_spread is not numeric",
            )
        return DerivedValue(direct, KNOWN, "gross_spread")
    derived = _derive_spread(economics)
    if derived.state == KNOWN:
        return derived
    return DerivedValue(
        None,
        _candidate_state(
            (
                signal(economics, "gross_spread"),
                signal(economics, "aov"),
                signal(economics, "retail_price"),
                signal(economics, "wholesale_cost"),
            )
        ),
        reason="gross spread is unknown",
    )


def _derive_spread(economics: Any) -> DerivedValue:
    sale = select_sale_price(economics)
    wholesale = numeric_state(
        signal(economics, "wholesale_cost"),
        subject="wholesale_cost",
    )
    if sale.state == KNOWN and wholesale.state == KNOWN:
        return DerivedValue(
            sale.value - wholesale.value,
            KNOWN,
            "sale_price_minus_wholesale_cost",
        )
    return DerivedValue(
        None,
        evidence_state(
            (
                signal(economics, "aov"),
                signal(economics, "retail_price"),
                signal(economics, "wholesale_cost"),
            )
        ),
        reason="sale price or wholesale cost is unknown",
    )


def band_from_0_to_10(value: Any) -> int | None:
    """Map a 0..10 score to the conservative component bands."""

    score = to_decimal(value)
    if score is None:
        return None
    if score <= 0:
        return 0
    if score <= 2:
        return 25
    if score <= 5:
        return 50
    if score <= 7:
        return 75
    return 100


def band_from_percent(value: Any) -> int | None:
    """Map an already-normalised 0..100 average to a component band."""

    score = to_decimal(value)
    if score is None:
        return None
    if score < 25:
        return 0
    if score < 50:
        return 25
    if score < 75:
        return 50
    if score < 100:
        return 75
    return 100


def average_decimals(values: Any) -> Decimal | None:
    materialized = tuple(to_decimal(item) for item in values)
    if not materialized or any(item is None for item in materialized):
        return None
    return sum(materialized, Decimal("0")) / Decimal(len(materialized))


__all__ = [
    "AFFILIATE_LIKE_MODELS",
    "CONTRADICTED",
    "DerivedValue",
    "EVIDENCE_STATES",
    "KNOWN",
    "RESELLER_LIKE_MODELS",
    "STALE",
    "SUPPORTED_MONETIZATION_MODELS",
    "UNKNOWN",
    "average_decimals",
    "band_from_0_to_10",
    "band_from_percent",
    "evidence_reason",
    "evidence_state",
    "known_bool",
    "known_decimal",
    "known_nonempty",
    "known_signal",
    "numeric_state",
    "select_gross_spread",
    "select_payout",
    "select_sale_price",
    "signal",
    "signal_state",
    "signal_value",
    "to_decimal",
]
