"""Network-free historical-pack evidence adapters."""

from pipeline.arbitrage.adapters.landing import derive_landing
from pipeline.arbitrage.adapters.meta import derive_meta
from pipeline.arbitrage.adapters.trends import demand_score, derive_trends

__all__ = ["demand_score", "derive_landing", "derive_meta", "derive_trends"]
