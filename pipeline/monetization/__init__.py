"""Generic affiliate-monetization foundation (stdlib only, product-agnostic).

Public surface for the research pipeline: ``load_catalog`` (strict,
fail-closed config loading), ``evaluate_gate`` (pure eligibility/emission)
and ``render_markdown`` (pack section).  The committed
``config/affiliate_offers.json`` configures one unapproved copper product
with five ``candidate`` offers (no env references, no active offers), so
the gate stays dark until the operator approves the product and activates
a fully validated, unexpired offer whose ``affiliate_url_env`` is set.
"""

from __future__ import annotations

from pipeline.monetization.config import load as load_catalog
from pipeline.monetization.gate import evaluate as evaluate_gate
from pipeline.monetization.model import (
    DISCLOSURE_PT_PT,
    OFFER_ROLES,
    OFFER_STATUSES,
    AffiliateCatalog,
    Offer,
    ProductOffers,
)
from pipeline.monetization.render import render_markdown

__all__ = [
    "DISCLOSURE_PT_PT",
    "OFFER_ROLES",
    "OFFER_STATUSES",
    "AffiliateCatalog",
    "Offer",
    "ProductOffers",
    "evaluate_gate",
    "load_catalog",
    "render_markdown",
]
