"""Pack JSON/Markdown integration for the monetization payload.

By default (no payload) the pack is byte-unchanged: no ``monetization`` JSON
key, no ``## Monetização afiliada`` section.  When eligible, the raw JSON
carries the emission and the Markdown renders the concise disclosure/market/
role/merchant/commission/cookie/public-URL section with operator strings
escaped; the tracked affiliate env VALUE never appears in any rendered
content.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date

import pytest

from pipeline.output import pack
from pipeline.scoring import score
from tests.fixtures.monetization_helpers import offer_model, product_model

TRENDS = {"direction": "subida", "slope_pct": 100.0, "seasonal": False, "top_geos": [], "related_queries": []}
CSV = {"volume": 100000, "cpc": 0.5, "related_keywords": []}
ADS = {"source": "Facebook Ad Library via Apify", "total_ads": 3, "ad_ids": ["a1"],
       "top_ads": [{"ad_archive_id": "a1", "advertiser": "Loja", "page_id": "p1", "days_active": 180}],
       "advertisers": [{"page_id": "p1", "page_name": "Loja", "active_ads": 1, "oldest_ad_days": 180}],
       "saturation": "baixa"}

ENV_VALUE = "SECRET_VALUE_123"


@pytest.fixture
def temp_pack_dirs(monkeypatch):
    tmp = tempfile.mkdtemp()
    packs_dir, data_dir = os.path.join(tmp, "packs"), os.path.join(tmp, "data")
    os.makedirs(packs_dir)
    os.makedirs(data_dir)

    def temp_paths(keyword, run_date=None, mode=None, run_id=None, product_id=None):
        run_date = run_date or date.today()
        slug = pack._slugify(keyword)
        base = f"{run_date.isoformat()}--{slug}"
        return os.path.join(packs_dir, f"{base}.md"), os.path.join(data_dir, f"{base}.json")

    monkeypatch.setattr(pack, "_pack_paths", temp_paths)
    return tmp


def _read_raw(tmp) -> dict:
    raw_path = next(os.path.join(tmp, "data", name) for name in os.listdir(os.path.join(tmp, "data")))
    return json.load(open(raw_path, encoding="utf-8"))


def _emission(**overrides):
    """An emission exactly as the gate produces (env value set upstream)."""
    base = {
        "disclosure": "Divulgação de afiliado: esta página pode conter links de afiliado; "
                      "o operador pode receber uma comissão por compras efetuadas através "
                      "desses links, sem custo adicional para o comprador.",
        "market": "PT",
        "offers": [{
            "offer_id": "mochila-pt-primary",
            "role": "primary",
            "merchant": "Merchant X",
            "status": "active",
            "commission_type": "percentage",
            "commission_value": 5.0,
            "commission_currency": None,
            "cookie_duration_days": 30,
            "payout_eligibility": "60 dias após confirmação de venda",
            "shipping_markets": ["PT"],
            "asset_rights": "imagens fornecidas pelo merchant",
            "traffic_policy": "sem branded keywords",
            "destination_url": "https://merchant.example/mochila-personalizada",
            "evidence_url": "https://merchant.example/affiliate-program",
            "checked_at": "2026-08-24",
            "valid_until": "2026-12-31",
            "affiliate_link_ref": "MOCHILA_AFFILIATE_TAG",
        }],
    }
    base.update(overrides)
    return base


def test_default_pack_has_no_monetization_key_or_section(temp_pack_dirs):
    result = score.compute(TRENDS, CSV, ADS)
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result)
    content = open(path, encoding="utf-8").read()
    assert "## Monetização afiliada" not in content
    assert "afiliad" not in content
    raw = _read_raw(temp_pack_dirs)
    assert "monetization" not in raw


def test_explicit_none_payload_keeps_default_behavior(temp_pack_dirs):
    result = score.compute(TRENDS, CSV, ADS)
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=None)
    content = open(path, encoding="utf-8").read()
    assert "## Monetização afiliada" not in content
    assert "monetization" not in _read_raw(temp_pack_dirs)


def test_eligible_pack_renders_section_and_json_key(temp_pack_dirs):
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    assert "## Monetização afiliada" in content
    assert "Divulgação de afiliado" in content
    assert "- Mercado: PT" in content
    assert "- Oferta: mochila-pt-primary — papel: primary" in content
    assert "  - Merchant: Merchant X" in content
    assert "  - Comissão: 5%" in content
    assert "  - Cookie: 30 dias" in content
    assert "  - Destino: https://merchant.example/mochila-personalizada (ref: MOCHILA_AFFILIATE_TAG)" in content
    assert "  - Evidência: https://merchant.example/affiliate-program" in content
    raw = _read_raw(temp_pack_dirs)
    assert raw["monetization"] == emission


def test_fixed_commission_renders_currency(temp_pack_dirs):
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    emission["offers"][0]["commission_type"] = "fixed"
    emission["offers"][0]["commission_value"] = 2.5
    emission["offers"][0]["commission_currency"] = "EUR"
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    assert "  - Comissão: EUR 2.5" in content


def test_markdown_escapes_operator_strings(temp_pack_dirs):
    """Free-text operator strings are fully escaped so a configured value can
    never inject Markdown structure; bounded identifiers/URLs get only the
    minimal neutralization (``_``/``-``/``.`` stay readable)."""
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    emission["offers"][0]["merchant"] = "Merchant *X* [evil](https://evil.example) `code`"
    emission["offers"][0]["affiliate_link_ref"] = "TAG_WITH_UNDERSCORE"
    emission["market"] = "PT `code`"
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    assert "Merchant \\*X\\* \\[evil\\]\\(https://evil\\.example\\) \\`code\\`" in content
    assert "PT \\`code\\`" in content
    # Bounded identifiers stay readable: only the env NAME, underscore intact.
    assert "(ref: TAG_WITH_UNDERSCORE)" in content
    # The unescaped injection text never appears verbatim.
    assert "*X*" not in content
    assert "[evil](https://evil.example)" not in content


def test_url_html_chars_are_neutralized(temp_pack_dirs):
    """Validated URLs keep readability but raw HTML/code spans in the path
    are neutralized (defense in depth against a hostile operator config)."""
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    emission["offers"][0]["destination_url"] = "https://x.example/<script>alert(1)</script>"
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    assert "https://x.example/\\<script\\>alert\\(1\\)\\</script\\>" in content
    assert "<script>alert(1)</script>" not in content


def test_url_markdown_link_injection_is_escaped(temp_pack_dirs):
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    injection = "[safe](https://evil.example)"
    emission["offers"][0]["destination_url"] = f"https://merchant.example/{injection}"
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    assert injection not in content
    assert "\\[safe\\]\\(https://evil.example\\)" in content


def test_tracked_env_value_never_appears_in_rendered_content(temp_pack_dirs):
    """The emission carries only the env NAME; a real env VALUE set upstream
    never reaches Markdown or JSON (defense in depth: also assert the whole
    payload round-trip is clean)."""
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    emission["offers"][0]["affiliate_link_ref"] = "MOCHILA_AFFILIATE_TAG"
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    raw = _read_raw(temp_pack_dirs)
    assert ENV_VALUE not in content
    assert ENV_VALUE not in json.dumps(raw)
    assert "affiliate_link_ref" in raw["monetization"]["offers"][0]
    assert "MOCHILA_AFFILIATE_TAG" in content


def test_both_roles_render_concise_section(temp_pack_dirs):
    result = score.compute(TRENDS, CSV, ADS)
    emission = _emission()
    emission["offers"].append({
        "offer_id": "mochila-pt-fallback",
        "role": "trust_fallback",
        "merchant": "Merchant Y",
        "status": "active",
        "commission_type": "percentage",
        "commission_value": 7.5,
        "commission_currency": None,
        "cookie_duration_days": 45,
        "payout_eligibility": "30 dias",
        "shipping_markets": ["PT"],
        "asset_rights": "imagens próprias",
        "traffic_policy": "sem branded keywords",
        "destination_url": "https://merchant-y.example/produto",
        "evidence_url": "https://merchant-y.example/afiliados",
        "checked_at": "2026-08-24",
        "valid_until": "2026-12-31",
        "affiliate_link_ref": "MOCHILA_FALLBACK_TAG",
    })
    path = pack.generate("mochila personalizada", TRENDS, CSV, ADS, result, monetization=emission)
    content = open(path, encoding="utf-8").read()
    assert "papel: primary" in content
    assert "papel: trust_fallback" in content
    assert "Comissão: 7.5%" in content
    assert "Cookie: 45 dias" in content
    assert "https://merchant-y.example/produto" in content


def test_model_and_emission_shapes_agree():
    """The dataclass emission shape used by the gate matches what the pack
    renders (regression guard between fixtures and pack contract)."""
    emission = _emission()
    assert emission["offers"][0]["affiliate_link_ref"] == "MOCHILA_AFFILIATE_TAG"
    assert emission["offers"][0]["commission_type"] in ("percentage", "fixed")
    # All rendered fields exist in the emission.
    for key in ("disclosure", "market", "offers"):
        assert key in emission
    for key in ("offer_id", "role", "merchant", "commission_type", "commission_value",
                "commission_currency", "cookie_duration_days", "destination_url",
                "evidence_url", "affiliate_link_ref"):
        assert key in emission["offers"][0]


def test_fixture_offer_model_is_importable():
    """The dataclass builder used by the gate matrix stays constructible."""
    offer = offer_model()
    assert offer.affiliate_url_env == "MOCHILA_AFFILIATE_TAG"
    product = product_model()
    assert product.offers[0] is offer or product.offers[0].offer_id == offer.offer_id
