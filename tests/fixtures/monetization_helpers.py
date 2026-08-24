"""Shared fixtures for the monetization slice (config/gate/pack/run).

Configs are written under a tmp root and loaded through the real strict
loader so gate/run tests exercise validation end to end; dataclass builders
let the gate matrix construct catalogs directly.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pipeline.monetization.config import load as load_catalog
from pipeline.monetization.model import AffiliateCatalog, Offer, ProductOffers

PRODUCT_ID_MOCHILA = "e942e8bf7f30"  # stable_id("mochila personalizada")


def offer(
    offer_id: str = "mochila-pt-primary",
    *,
    role: str = "primary",
    merchant: str = "Merchant X",
    status: str = "active",
    destination_url: str = "https://merchant.example/mochila-personalizada",
    affiliate_url_env: str = "MOCHILA_AFFILIATE_TAG",
    commission_type: str = "percentage",
    commission_value: float = 5.0,
    commission_currency: str | None = None,
    cookie_duration_days: int = 30,
    payout_eligibility: str = "60 dias após confirmação de venda",
    shipping_markets: tuple[str, ...] = ("PT",),
    asset_rights: str = "imagens fornecidas pelo merchant",
    traffic_policy: str = "sem branded keywords",
    evidence_url: str = "https://merchant.example/affiliate-program",
    checked_at: str = "2026-08-24",
    valid_until: str = "2026-12-31",
) -> dict[str, Any]:
    return {
        "offer_id": offer_id,
        "role": role,
        "merchant": merchant,
        "status": status,
        "destination_url": destination_url,
        "affiliate_url_env": affiliate_url_env,
        "commission_type": commission_type,
        "commission_value": commission_value,
        "commission_currency": commission_currency,
        "cookie_duration_days": cookie_duration_days,
        "payout_eligibility": payout_eligibility,
        "shipping_markets": list(shipping_markets),
        "asset_rights": asset_rights,
        "traffic_policy": traffic_policy,
        "evidence_url": evidence_url,
        "checked_at": checked_at,
        "valid_until": valid_until,
    }


def product_config(
    product_id: str = PRODUCT_ID_MOCHILA,
    *,
    market: str = "PT",
    approved: bool = True,
    offers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "market": market,
        "approved": approved,
        "offers": offers if offers is not None else [offer()],
    }


def write_affiliate_config(tmp_path: Path, products: list[dict[str, Any]]) -> AffiliateCatalog:
    """Write ``config/affiliate_offers.json`` under ``tmp_path`` and load it
    through the real strict loader (fail-closed: returns None when invalid)."""
    path = write_raw_config(tmp_path, {"schema_version": 1, "products": products})
    return load_catalog(path)


def write_raw_config(tmp_path: Path, payload: Any) -> str:
    """Write an arbitrary payload as the affiliate config and return its path."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "affiliate_offers.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ── Direct dataclass builders for the pure gate matrix ─────────────────────


def offer_model(**overrides: Any) -> Offer:
    base = dict(
        offer_id="mochila-pt-primary",
        role="primary",
        merchant="Merchant X",
        status="active",
        destination_url="https://merchant.example/mochila-personalizada",
        affiliate_url_env="MOCHILA_AFFILIATE_TAG",
        commission_type="percentage",
        commission_value=5.0,
        commission_currency=None,
        cookie_duration_days=30,
        payout_eligibility="60 dias após confirmação de venda",
        shipping_markets=("PT",),
        asset_rights="imagens fornecidas pelo merchant",
        traffic_policy="sem branded keywords",
        evidence_url="https://merchant.example/affiliate-program",
        checked_at=date(2026, 8, 24),
        valid_until=date(2026, 12, 31),
    )
    base.update(overrides)
    return Offer(**base)


def product_model(**overrides: Any) -> ProductOffers:
    base = dict(
        product_id=PRODUCT_ID_MOCHILA,
        market="PT",
        approved=True,
        offers=(offer_model(),),
    )
    base.update(overrides)
    return ProductOffers(**base)


def catalog_model(**overrides: Any) -> AffiliateCatalog:
    base = dict(schema_version=1, products=(product_model(),))
    base.update(overrides)
    return AffiliateCatalog(**base)
