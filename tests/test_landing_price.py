"""
Testes do adapter Landing Price.

Usa fixtures HTML simuladas — sem chamadas reais à rede.
"""
import json
import pytest
import os
from unittest.mock import patch, Mock

from pipeline import quota
from pipeline.adapters import landing_price


def _isolate_quota(monkeypatch, tmp_path, *, firecrawl_credits=0, firecrawl_limit=None):
    """Point the real quota counter at a temp file (never touch data/quota.json)."""
    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": firecrawl_credits,
        "firecrawl_limit": firecrawl_limit or quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(landing_price.quota, "QUOTA_FILE", str(quota_file))
    return quota_file

# ── Fixtures HTML ──────────────────────────────────────────────────────────

HTML_WITH_JSONLD = """<!DOCTYPE html>
<html>
<head>
    <title>Produto Exemplo</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Mochila Personalizada Premium",
        "offers": {
            "@type": "Offer",
            "price": "45.99",
            "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock"
        }
    }
    </script>
</head>
<body><h1>Mochila Personalizada</h1><p>A melhor mochila do mercado.</p></body>
</html>"""

HTML_WITH_MULTIPLE_JSONLD = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Pack Pro",
        "offers": [
            {"@type": "Offer", "price": "29.99", "priceCurrency": "EUR"},
            {"@type": "Offer", "price": "59.99", "priceCurrency": "EUR"}
        ]
    }
    </script>
</head>
<body><h1>Pack Pro</h1></body>
</html>"""

HTML_WITH_AGGREGATE_OFFER = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Product Range",
        "offers": {
            "@type": "AggregateOffer",
            "lowPrice": "19.99",
            "highPrice": "89.99",
            "priceCurrency": "EUR"
        }
    }
    </script>
</head>
<body><h1>Product Range</h1></body>
</html>"""

HTML_REGEX_EURO_ONLY = """<!DOCTYPE html>
<html>
<body>
    <h1>Produto</h1>
    <p class="price">Apenas <strong>€39,99</strong> com portes grátis!</p>
    <p>Também disponível por 49,99€ na cor premium.</p>
    <p>Pack família: EUR 79.99</p>
</body>
</html>"""

HTML_NO_PRICES = """<!DOCTYPE html>
<html>
<body>
    <h1>Página sem preços</h1>
    <p>Apenas texto descritivo, sem valores monetários.</p>
</body>
</html>"""

HTML_WITH_ARS_JSONLD = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Vela Aromática Artesanal",
        "offers": {
            "@type": "Offer",
            "price": "14500",
            "priceCurrency": "ARS",
            "availability": "https://schema.org/InStock"
        }
    }
    </script>
</head>
<body><h1>Vela Aromática</h1></body>
</html>"""

HTML_WITH_USD_JSONLD = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "offers": {"@type": "Offer", "price": "24.99", "priceCurrency": "usd"}
    }
    </script>
</head>
<body><h1>US Product</h1></body>
</html>"""

HTML_WITH_MIXED_CURRENCY_JSONLD = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "offers": [
            {"@type": "Offer", "price": "29.99", "priceCurrency": "EUR"},
            {"@type": "Offer", "price": "15000", "priceCurrency": "ARS"}
        ]
    }
    </script>
</head>
<body><h1>Dual Currency</h1></body>
</html>"""

HTML_WITH_JSONLD_NO_CURRENCY = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "offers": {"@type": "Offer", "price": "45.99"}
    }
    </script>
</head>
<body><h1>Sem moeda</h1></body>
</html>"""

HTML_WITH_UNLABELED_AND_EURO_TEXT = """<!DOCTYPE html>
<html>
<head>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Product",
        "offers": {"@type": "Offer", "price": "45.99"}
    }
    </script>
</head>
<body><h1>Sem moeda no JSON-LD</h1><p>Preço final: <strong>€49,99</strong></p></body>
</html>"""


# ── Tests: JSON-LD extraction ──────────────────────────────────────────────

class TestJsonLdExtraction:
    """Extrai entradas {price, currency} — a moeda vem do priceCurrency."""

    def test_extract_single_product_offer(self):
        entries = landing_price._extract_jsonld_entries(HTML_WITH_JSONLD)
        assert entries is not None
        assert {"price": 45.99, "currency": "EUR"} in entries

    def test_extract_multiple_offers(self):
        entries = landing_price._extract_jsonld_entries(HTML_WITH_MULTIPLE_JSONLD)
        assert entries is not None
        assert {"price": 29.99, "currency": "EUR"} in entries
        assert {"price": 59.99, "currency": "EUR"} in entries

    def test_extract_aggregate_offer(self):
        entries = landing_price._extract_jsonld_entries(HTML_WITH_AGGREGATE_OFFER)
        assert entries is not None
        assert {"price": 19.99, "currency": "EUR"} in entries
        assert {"price": 89.99, "currency": "EUR"} in entries

    def test_extract_ars_currency(self):
        entries = landing_price._extract_jsonld_entries(HTML_WITH_ARS_JSONLD)
        assert entries is not None
        assert {"price": 14500.0, "currency": "ARS"} in entries

    def test_extract_usd_currency_is_normalized(self):
        """priceCurrency minúsculo ("usd") normaliza para "USD"."""
        entries = landing_price._extract_jsonld_entries(HTML_WITH_USD_JSONLD)
        assert entries is not None
        assert {"price": 24.99, "currency": "USD"} in entries

    def test_aggregate_offer_with_currency(self):
        resolved = landing_price._resolve_currency(landing_price._extract_jsonld_entries(HTML_WITH_AGGREGATE_OFFER))
        assert resolved == ([19.99, 89.99], "EUR")

    def test_no_jsonld_returns_none(self):
        entries = landing_price._extract_jsonld_entries(HTML_NO_PRICES)
        assert entries is None or entries == []

    def test_invalid_json_in_ld_script(self):
        html = '<script type="application/ld+json">{invalid}</script>'
        entries = landing_price._extract_jsonld_entries(html)
        # Should not crash — returns None or empty list
        assert entries is None or entries == []

    def test_missing_currency_entry_is_kept_with_none(self):
        """Preço JSON-LD sem priceCurrency fica com currency=None — nunca EUR."""
        entries = landing_price._extract_jsonld_entries(HTML_WITH_JSONLD_NO_CURRENCY)
        assert entries is not None
        assert entries == [{"price": 45.99, "currency": None}]
        assert landing_price._resolve_currency(entries) is None

    def test_mixed_currency_page_resolves_to_none(self):
        """EUR + ARS na mesma página é ambíguo — resolve para None (cai no regex)."""
        entries = landing_price._extract_jsonld_entries(HTML_WITH_MIXED_CURRENCY_JSONLD)
        assert entries is not None
        assert landing_price._resolve_currency(entries) is None

    def test_symbol_currency_is_not_accepted(self):
        """"$" não é código ISO-like — normaliza para None."""
        assert landing_price._normalize_currency("$") is None
        assert landing_price._normalize_currency("R$") is None
        assert landing_price._normalize_currency("") is None
        assert landing_price._normalize_currency(None) is None
        assert landing_price._normalize_currency("EUR") == "EUR"
        assert landing_price._normalize_currency("ars") == "ARS"


# ── Tests: Regex extraction ────────────────────────────────────────────────

class TestRegexExtraction:
    def test_extract_euro_prefix(self):
        prices = landing_price._extract_regex_price("Custa €39,99 na loja.")
        assert prices is not None
        assert 39.99 in prices

    def test_extract_euro_suffix(self):
        prices = landing_price._extract_regex_price("Preço: 29,99€")
        assert prices is not None
        assert 29.99 in prices

    def test_extract_eur_keyword(self):
        prices = landing_price._extract_regex_price("Apenas 15 EUR")
        assert prices is not None
        assert 15.0 in prices

    def test_extract_multiple_formatos(self):
        prices = landing_price._extract_regex_price(HTML_REGEX_EURO_ONLY)
        assert prices is not None
        assert 39.99 in prices
        assert 49.99 in prices
        assert 79.99 in prices

    def test_no_euro_returns_none(self):
        prices = landing_price._extract_regex_price("Sem preços em euros aqui. $50 only.")
        assert prices is None or prices == []

    def test_ignores_absurd_values(self):
        """Preços fora do intervalo 0.01-99999 devem ser ignorados."""
        prices = landing_price._extract_regex_price("€0.00 e €999999.99")
        assert prices is None or prices == [] or 999999.99 not in prices


# ── Tests: fetch_price ─────────────────────────────────────────────────────

class TestFetchPrice:
    def test_fetch_with_jsonld(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_JSONLD

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/product")
            assert result is not None
            assert "prices" in result
            assert 45.99 in result["prices"]
            assert result["currency"] == "EUR"
            assert result["source_url"] == "https://example.com/product"
            assert result["via"] == "http"

    def test_fetch_with_jsonld_ars(self):
        """Preço JSON-LD em ARS devolve a moeda real, não EUR."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_ARS_JSONLD

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://aromasspirit.com.ar/producto")
            assert result is not None
            assert result["currency"] == "ARS"
            assert 14500.0 in result["prices"]

    def test_fetch_jsonld_without_currency_falls_through_to_euro_regex(self):
        """JSON-LD sem moeda explícita não é assumido EUR — cai no regex €/EUR."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_UNLABELED_AND_EURO_TEXT

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/unlabeled")
            assert result is not None
            assert result["currency"] == "EUR"
            # O preço JSON-LD sem moeda é descartado; só o € explícito do texto entra.
            assert result["prices"] == [49.99]

    def test_fetch_jsonld_without_currency_and_no_euro_fails_safely(self):
        """JSON-LD sem moeda e sem €/EUR no texto → None, nunca EUR assumido."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_JSONLD_NO_CURRENCY

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/nocurrency")
            assert result is None

    def test_fetch_mixed_currency_jsonld_falls_through_to_regex(self):
        """EUR+ARS no mesmo JSON-LD é ambíguo — cai no regex EUR explícito."""
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_MIXED_CURRENCY_JSONLD

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/dual")
            assert result is None  # sem €/EUR no texto, nada combinável

    def test_fetch_with_regex_fallback(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_REGEX_EURO_ONLY

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/product")
            assert result is not None
            assert "prices" in result
            assert 39.99 in result["prices"]
            assert result["currency"] == "EUR"
            assert result["via"] == "http"

    def test_fetch_no_prices_returns_none(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_NO_PRICES

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/noprices")
            assert result is None

    def test_fetch_timeout_is_graceful(self):
        """Timeout na landing não deve crashar — tenta Firecrawl, devolve None se sem key."""
        with patch.object(landing_price.requests, "get", side_effect=landing_price.requests.Timeout), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://slow-site.com")
            assert result is None

    def test_fetch_connection_error_is_graceful(self):
        with patch.object(landing_price.requests, "get", side_effect=landing_price.requests.ConnectionError), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://offline-site.com")
            assert result is None

    def test_fetch_http_error_returns_none(self):
        mock_resp = Mock()
        mock_resp.status_code = 404

        with patch.object(landing_price.requests, "get", return_value=mock_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://example.com/404")
            assert result is None

    def test_fetch_empty_url_returns_none(self):
        result = landing_price.fetch_price("")
        assert result is None

    def test_ad_library_url_is_never_fetched(self):
        with patch.object(landing_price.requests, "get", side_effect=AssertionError("Ad Library URL fetched")), \
             patch.object(landing_price, "_fetch_via_firecrawl", side_effect=AssertionError("Ad Library URL sent to Firecrawl")):
            assert landing_price.fetch_price("https://www.facebook.com/ads/library/?id=123") is None


# ── Tests: Firecrawl fallback (nível 2) ─────────────────────────────────────

class TestFirecrawlFallback:
    """Testa o fallback para Firecrawl quando HTTP não encontra preço."""

    def test_http_fails_firecrawl_finds_price(self):
        """HTTP não encontra preço → Firecrawl invocado → credit contado."""
        mock_http_resp = Mock()
        mock_http_resp.status_code = 200
        mock_http_resp.text = HTML_NO_PRICES  # sem preços no HTML estático

        firecrawl_result = {
            "source_url": "https://example.com/js-product",
            "prices": [49.99],
            "via": "firecrawl",
        }

        with patch.object(landing_price.requests, "get", return_value=mock_http_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=firecrawl_result):
            result = landing_price.fetch_price("https://example.com/js-product")
            assert result is not None
            assert result["via"] == "firecrawl"
            assert 49.99 in result["prices"]

    def test_firecrawl_not_called_when_http_finds_price(self):
        """Se HTTP encontra preço, Firecrawl NÃO é invocado."""
        mock_http_resp = Mock()
        mock_http_resp.status_code = 200
        mock_http_resp.text = HTML_WITH_JSONLD  # tem preço

        with patch.object(landing_price.requests, "get", return_value=mock_http_resp), \
             patch.object(landing_price, "_fetch_via_firecrawl", side_effect=AssertionError("Firecrawl não deve ser chamado")):
            result = landing_price.fetch_price("https://example.com/product")
            assert result is not None
            assert result["via"] == "http"

    def test_firecrawl_no_key_returns_none(self):
        """Sem FIRECRAWL_KEY, _fetch_via_firecrawl devolve None."""
        with patch.dict(os.environ, {}, clear=True):
            # Garantir que FIRECRAWL_KEY não está definida
            os.environ.pop("FIRECRAWL_KEY", None)
            result = landing_price._fetch_via_firecrawl("https://example.com/test")
            assert result is None

    def test_firecrawl_quota_exhausted_returns_none(self, monkeypatch, tmp_path):
        """Sem credits Firecrawl, devolve None."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path, firecrawl_credits=quota.FIRECRAWL_LIMIT)

        result = landing_price._fetch_via_firecrawl("https://example.com/test")
        assert result is None
        # Nada foi cobrado numa reserva negada.
        assert quota.get_firecrawl_usage()["credits"] == quota.FIRECRAWL_LIMIT

    def test_firecrawl_success_counts_credit(self, monkeypatch, tmp_path):
        """Firecrawl bem-sucedido gasta 1 credit."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"html": HTML_WITH_JSONLD, "markdown": ""}
        }

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
            assert result is not None
            assert result["via"] == "firecrawl"
            assert 45.99 in result["prices"]
            assert result["currency"] == "EUR"
            assert quota.get_firecrawl_usage()["credits"] == 1  # 1 credit gasto

    def test_firecrawl_ars_currency(self, monkeypatch, tmp_path):
        """Firecrawl também devolve a moeda real do JSON-LD (ARS, não EUR)."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)

        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"html": HTML_WITH_ARS_JSONLD, "markdown": ""}
        }

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://aromasspirit.com.ar/producto")
            assert result is not None
            assert result["currency"] == "ARS"
            assert 14500.0 in result["prices"]

    def test_firecrawl_http_error_charges_nothing(self, monkeypatch, tmp_path):
        """Resposta HTTP != 200 → None e zero credits (como a contabilidade antiga)."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 500

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is None
        assert quota.get_firecrawl_usage()["credits"] == 0

    def test_firecrawl_timeout_both_levels_graceful(self):
        """Timeout nos 2 níveis → None, sem crash."""
        with patch.object(landing_price.requests, "get", side_effect=landing_price.requests.Timeout), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price("https://very-slow.com")
            assert result is None


# ── Tests: fetch_prices_from_ads ───────────────────────────────────────────

class TestFetchPricesFromAds:
    def test_no_ads_returns_error(self):
        result = landing_price.fetch_prices_from_ads({"top_ads": []})
        assert "error" in result

    def test_ads_with_no_links_returns_error(self):
        ads_data = {
            "top_ads": [
                {"advertiser": "X", "landing_url": "", "days_active": 30},
            ]
        }
        result = landing_price.fetch_prices_from_ads(ads_data)
        assert "error" in result
        assert "landing_url indisponível" in result["error"]

    def test_error_in_ads_data(self):
        result = landing_price.fetch_prices_from_ads({"error": "no data"})
        assert "error" in result

    def test_fetches_from_top_ads_with_links(self):
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://ex.com/a", "days_active": 100},
                {"advertiser": "B", "landing_url": "https://ex.com/b", "days_active": 80},
            ]
        }

        mock_resp_a = Mock()
        mock_resp_a.status_code = 200
        mock_resp_a.text = HTML_WITH_JSONLD  # €45.99
        mock_resp_b = Mock()
        mock_resp_b.status_code = 200
        mock_resp_b.text = HTML_REGEX_EURO_ONLY  # €39.99, €49.99, €79.99

        def mock_get(url, **kwargs):
            if "ex.com/a" in url:
                return mock_resp_a
            return mock_resp_b

        with patch.object(landing_price.requests, "get", side_effect=mock_get):
            result = landing_price.fetch_prices_from_ads(ads_data)
            assert "price_range" in result
            pr = result["price_range"]
            assert pr["min"] == 39.99
            assert pr["max"] == 79.99
            assert pr["currency"] == "EUR"
            assert pr["sources"] == 2

    def test_all_fetches_fail_gracefully(self):
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://ex.com/a", "days_active": 100},
            ]
        }

        with patch.object(landing_price.requests, "get", side_effect=landing_price.requests.Timeout):
            result = landing_price.fetch_prices_from_ads(ads_data)
            assert "error" in result
            assert "landings não legíveis" in result["error"]

    def test_max_three_fetches(self):
        """Apenas os primeiros 3 ads são usados para fetch de preços."""
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://ex.com/1", "days_active": 100},
                {"advertiser": "B", "landing_url": "https://ex.com/2", "days_active": 90},
                {"advertiser": "C", "landing_url": "https://ex.com/3", "days_active": 80},
                {"advertiser": "D", "landing_url": "https://ex.com/4", "days_active": 70},  # ignorado
            ]
        }

        fetch_count = [0]

        def mock_get(url, **kwargs):
            fetch_count[0] += 1
            m = Mock()
            m.status_code = 200
            m.text = HTML_WITH_JSONLD
            return m

        with patch.object(landing_price.requests, "get", side_effect=mock_get):
            result = landing_price.fetch_prices_from_ads(ads_data)
            assert fetch_count[0] == 3  # só 3 fetches
            assert "price_range" in result
            assert result["price_range"]["sources"] == 3

    def test_same_currency_aggregation_with_provenance(self):
        """Duas landings ARS agregam num range ARS com proveniência por fonte."""
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://aromasspirit.com.ar/velas", "days_active": 100},
                {"advertiser": "B", "landing_url": "https://velasya.com.ar/pack", "days_active": 80},
            ]
        }

        mock_resp_a = Mock()
        mock_resp_a.status_code = 200
        mock_resp_a.text = HTML_WITH_ARS_JSONLD  # 14500 ARS
        mock_resp_b = Mock()
        mock_resp_b.status_code = 200
        mock_resp_b.text = """<html><body><script type="application/ld+json">
            {"@type": "Product", "offers": {"@type": "Offer",
             "price": "45000", "priceCurrency": "ARS"}}
            </script></body></html>"""

        def mock_get(url, **kwargs):
            if "aromasspirit" in url:
                return mock_resp_a
            return mock_resp_b

        with patch.object(landing_price.requests, "get", side_effect=mock_get):
            result = landing_price.fetch_prices_from_ads(ads_data)
            assert "price_range" in result
            pr = result["price_range"]
            assert pr["currency"] == "ARS"
            assert pr["min"] == 14500.0
            assert pr["max"] == 45000.0
            assert pr["sources"] == 2
            prov = result["source_provenance"]
            assert len(prov) == 2
            assert {p["domain"] for p in prov} == {"aromasspirit.com.ar", "velasya.com.ar"}
            assert all(p["via"] == "http" for p in prov)
            assert all(p["currency"] == "ARS" for p in prov)
            assert all(p["min"] <= p["max"] for p in prov)

    def test_mixed_currency_aggregation_is_rejected(self):
        """Landings em ARS + EUR nunca viram um price_range — erro explícito."""
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://aromasspirit.com.ar/velas", "days_active": 100},
                {"advertiser": "B", "landing_url": "https://loja.pt/velas", "days_active": 80},
            ]
        }

        mock_resp_ars = Mock()
        mock_resp_ars.status_code = 200
        mock_resp_ars.text = HTML_WITH_ARS_JSONLD
        mock_resp_eur = Mock()
        mock_resp_eur.status_code = 200
        mock_resp_eur.text = HTML_WITH_JSONLD

        def mock_get(url, **kwargs):
            if "aromasspirit" in url:
                return mock_resp_ars
            return mock_resp_eur

        with patch.object(landing_price.requests, "get", side_effect=mock_get):
            result = landing_price.fetch_prices_from_ads(ads_data)
            assert "price_range" not in result
            assert "error" in result
            assert "ARS" in result["error"] and "EUR" in result["error"]
            assert result["mixed_currencies"] == ["ARS", "EUR"]
            # A proveniência fica disponível para auditoria mesmo em erro.
            assert len(result["source_provenance"]) == 2

    def test_landing_without_currency_is_not_counted(self):
        """JSON-LD sem moeda não entra na agregação (nunca vira EUR)."""
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://ex.com/nocurrency", "days_active": 100},
                {"advertiser": "B", "landing_url": "https://ex.com/euro", "days_active": 80},
            ]
        }

        mock_resp_no = Mock()
        mock_resp_no.status_code = 200
        mock_resp_no.text = HTML_WITH_JSONLD_NO_CURRENCY  # sem moeda → descartado
        mock_resp_eur = Mock()
        mock_resp_eur.status_code = 200
        mock_resp_eur.text = HTML_WITH_JSONLD  # 45.99 EUR

        def mock_get(url, **kwargs):
            if "nocurrency" in url:
                return mock_resp_no
            return mock_resp_eur

        with patch.object(landing_price.requests, "get", side_effect=mock_get):
            result = landing_price.fetch_prices_from_ads(ads_data)
            assert "price_range" in result
            pr = result["price_range"]
            assert pr["currency"] == "EUR"
            assert pr["sources"] == 1
            assert pr["min"] == 45.99 and pr["max"] == 45.99
            assert len(result["source_provenance"]) == 1

    def test_provenance_has_no_secrets_and_only_public_urls(self):
        """Proveniência persiste só URL pública + domain/via/currency/min/max."""
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://loja.com/producto?utm_source=fb", "days_active": 100},
            ]
        }
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_JSONLD

        with patch.object(landing_price.requests, "get", return_value=mock_resp):
            result = landing_price.fetch_prices_from_ads(ads_data)
        prov = result["source_provenance"]
        assert len(prov) == 1
        entry = prov[0]
        assert entry["source_url"].startswith("https://")
        assert entry["domain"] == "loja.com"
        assert set(entry) == {"source_url", "domain", "via", "currency", "min", "max"}
        serialized = str(entry)
        assert "Bearer" not in serialized and "api_key" not in serialized
        assert "token" not in serialized and "Authorization" not in serialized
        assert entry["currency"] == "EUR"


# ── Tests: URL sanitizer (source_url / provenance nunca levam segredos) ─────

class TestSanitizeUrl:
    """_sanitize_url reduz uma URL à forma pública mínima persistível."""

    def test_strips_query_fragment_and_userinfo(self):
        out = landing_price._sanitize_url(
            "https://user:secret@example.com:8080/produto?api_key=abc123&token=xyz#frag"
        )
        assert out == "https://example.com:8080/produto"

    def test_strips_query_with_secret_values(self):
        out = landing_price._sanitize_url("https://loja.com/p?token=super-secreto&utm_source=fb")
        assert out == "https://loja.com/p"
        assert "super-secreto" not in (out or "")

    def test_keeps_safe_public_url_unchanged(self):
        assert landing_price._sanitize_url("https://example.com/produto") == "https://example.com/produto"
        assert landing_price._sanitize_url("http://example.com") == "http://example.com"
        assert landing_price._sanitize_url("https://loja.com/p?x=1") == "https://loja.com/p"

    def test_normalizes_scheme_and_hostname_case(self):
        out = landing_price._sanitize_url("HTTPS://ExAmple.COM/Produto")
        assert out == "https://example.com/Produto"

    def test_drops_default_port(self):
        # Porta por defeito é redundante — URL continua a ser "ordinary".
        assert landing_price._sanitize_url("https://example.com:443/p") == "https://example.com/p"

    def test_rejects_invalid_and_non_http_urls(self):
        assert landing_price._sanitize_url("") is None
        assert landing_price._sanitize_url("not a url") is None
        assert landing_price._sanitize_url("ftp://example.com/x") is None
        assert landing_price._sanitize_url("javascript:alert(1)") is None
        assert landing_price._sanitize_url("https://") is None
        assert landing_price._sanitize_url("example.com/path") is None  # sem scheme

    def test_rejects_invalid_port(self):
        assert landing_price._sanitize_url("https://example.com:99999/x") is None
        assert landing_price._sanitize_url("https://example.com:0/x") is None
        assert landing_price._sanitize_url("https://example.com:abc/x") is None

    def test_rejects_ad_library_urls(self):
        assert landing_price._sanitize_url("https://www.facebook.com/ads/library/?id=123") is None
        assert landing_price._sanitize_url("https://m.facebook.com/ads/library") is None

    def test_userinfo_only_never_persists(self):
        out = landing_price._sanitize_url("https://user@example.com/p")
        assert out == "https://example.com/p"
        assert "user" not in (out or "")


class TestSanitizedFetchAndProvenance:
    """Fetches usam a URL original; o que é devolvido/persistido é sanitizado."""

    def test_fetch_price_fetches_original_but_returns_sanitized_source_url(self):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_JSONLD
        original = "https://loja.com/produto?api_key=SECRET-VALUE&utm_source=fb#top"
        fetched = []

        def spy_get(url, **kwargs):
            fetched.append(url)
            return mock_resp

        with patch.object(landing_price.requests, "get", side_effect=spy_get), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            result = landing_price.fetch_price(original)
        assert result is not None
        assert fetched == [original]  # o fetch usa a URL original
        assert result["source_url"] == "https://loja.com/produto"
        assert "SECRET-VALUE" not in str(result)

    def test_fetch_price_rejects_non_http_without_fetching(self):
        with patch.object(landing_price.requests, "get", side_effect=AssertionError("não deve haver fetch")), \
             patch.object(landing_price, "_fetch_via_firecrawl", side_effect=AssertionError("não deve haver Firecrawl")):
            assert landing_price.fetch_price("ftp://example.com/x") is None
            assert landing_price.fetch_price("https://example.com:99999/x") is None

    def test_provenance_source_url_is_sanitized_no_secret_values(self):
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "landing_url": "https://user:pass@loja.com/p?api_key=TOPSECRET&token=SECRET2#frag", "days_active": 100},
            ]
        }
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.text = HTML_WITH_JSONLD

        with patch.object(landing_price.requests, "get", return_value=mock_resp):
            result = landing_price.fetch_prices_from_ads(ads_data)
        prov = result["source_provenance"]
        assert len(prov) == 1
        entry = prov[0]
        assert entry["source_url"] == "https://loja.com/p"
        assert entry["domain"] == "loja.com"
        blob = str(result)
        assert "TOPSECRET" not in blob and "SECRET2" not in blob
        assert "user:" not in blob and "pass" not in blob

    def test_firecrawl_result_source_url_is_sanitized(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"html": HTML_WITH_JSONLD, "markdown": ""}}
        original = "https://loja.com/js?api_key=hush#x"

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl(original)
        assert result is not None
        assert result["source_url"] == "https://loja.com/js"
        assert "hush" not in str(result)


# ── Tests: Firecrawl JSON defensivo (200 malformado nunca crasha) ──────────

class TestFirecrawlJsonGuard:
    """Raiz/`data` não-dict (list/null/string/falta) → None, sem AttributeError.

    Semântica de quota preservada: um HTTP 200 cobra o credit mesmo que o
    corpo não seja utilizável (como antes); best-effort: nunca levanta.
    """

    @pytest.mark.parametrize(
        "bad_root",
        [
            [{"data": {"html": "<html></html>"}}],
            None,
            "apenas texto",
            42,
        ],
    )
    def test_non_dict_root_returns_none_and_charges(self, monkeypatch, tmp_path, bad_root):
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = bad_root

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is None
        assert quota.get_firecrawl_usage()["credits"] == 1  # HTTP 200 cobra, como antes

    @pytest.mark.parametrize(
        "bad_data",
        [
            {"data": ["html"]},
            {"data": None},
            {"data": "html-string"},
            {"data": 7},
        ],
    )
    def test_non_dict_data_returns_none_without_attribute_error(self, monkeypatch, tmp_path, bad_data):
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = bad_data

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is None
        assert quota.get_firecrawl_usage()["credits"] == 1

    def test_missing_data_key_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True}

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is None
        assert quota.get_firecrawl_usage()["credits"] == 1

    def test_malformed_json_text_returns_none_without_exception(self, monkeypatch, tmp_path):
        """resp.json() a lançar ValueError (JSON inválido) → None, sem crash."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("não é JSON")

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is None
        assert quota.get_firecrawl_usage()["credits"] == 1

    @pytest.mark.parametrize(
        "html,markdown",
        [
            (["<html><body>€29,99</body></html>"], ""),  # html lista
            ({"node": "€29,99"}, ""),  # html dict
            (7, ""),  # html scalar
            ("", {"price": "€9,99"}),  # markdown dict
            ("", ["Só hoje €19,99"]),  # markdown lista
            ("", 42),  # markdown scalar
        ],
    )
    def test_non_string_html_markdown_become_empty_never_reach_regex(
        self, monkeypatch, tmp_path, html, markdown
    ):
        """data.html / data.markdown não-strings (scalar/list/dict) tornam-se
        strings vazias e nunca chegam ao parse JSON-LD/regex — None gracioso,
        nunca TypeError de finditer sobre listas/dicts."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"html": html, "markdown": markdown}}

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is None
        assert quota.get_firecrawl_usage()["credits"] == 1

    def test_string_markdown_still_parses_when_html_is_non_string(self, monkeypatch, tmp_path):
        """html não-string degrada a vazio, mas um markdown string válido com
        preço EUR explícito continua a ser parseado (não perde dados)."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": {"html": {"node": "x"}, "markdown": "Só hoje €19,99"}}

        with patch.object(landing_price.requests, "post", return_value=mock_resp):
            result = landing_price._fetch_via_firecrawl("https://example.com/js-heavy")
        assert result is not None
        assert result["prices"] == [19.99]
        assert result["currency"] == "EUR"
        assert result["via"] == "firecrawl"


# ── Tests: _safe_float rejeita NaN/Infinity ────────────────────────────────

class TestSafeFloatNonFinite:
    """NaN/Infinity em JSON-LD nunca entram em preços/provenance."""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"])
    def test_non_finite_values_rejected(self, bad):
        assert landing_price._safe_float(bad) is None

    @pytest.mark.parametrize("good", [0.01, 1, 45.99, "12.5", 99999])
    def test_finite_positive_accepted(self, good):
        value = landing_price._safe_float(good)
        assert value is not None
        assert value > 0

    def test_zero_and_negative_rejected(self):
        assert landing_price._safe_float(0) is None
        assert landing_price._safe_float(-1) is None
        assert landing_price._safe_float(None) is None
        assert landing_price._safe_float("abc") is None

    def test_jsonld_nan_price_never_enters_range(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type": "Product", "offers": {"@type": "Offer", "price": "NaN", '
            '"priceCurrency": "EUR"}}'
            "</script>"
        )
        entries = landing_price._extract_jsonld_entries(html)
        assert entries is None or all("price" not in e or e.get("price") is None for e in entries)


# ── Tests: logs de falha de landing usam URL sanitizada, nunca a crua ──────

class TestLandingFailureLogsSanitized:
    def test_http_failure_log_uses_sanitized_url_not_raw(self, caplog):
        import logging
        raw = "https://user:pass@loja.com/p?api_key=TOPSECRET&utm=x#frag"
        with caplog.at_level(logging.DEBUG, logger="pipeline.adapters.landing_price"), \
             patch.object(landing_price.requests, "get", side_effect=landing_price.requests.ConnectionError("boom")), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            assert landing_price.fetch_price(raw) is None
        assert "TOPSECRET" not in caplog.text
        assert "user:pass" not in caplog.text
        assert "api_key" not in caplog.text
        assert "https://loja.com/p" in caplog.text  # forma sanitizada aparece

    def test_firecrawl_failure_log_uses_sanitized_url_not_raw(self, monkeypatch, tmp_path, caplog):
        import logging
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        raw = "https://loja.com/js?api_key=hush#x"
        with caplog.at_level(logging.DEBUG, logger="pipeline.adapters.landing_price"), \
             patch.object(landing_price.requests, "post", side_effect=landing_price.requests.ConnectionError("boom")):
            assert landing_price._fetch_via_firecrawl(raw) is None
        assert "hush" not in caplog.text
        assert "api_key" not in caplog.text
        assert "https://loja.com/js" in caplog.text

    def test_http_failure_log_never_echoes_exception_text(self, caplog):
        """A ConnectionError whose message embeds the raw request URL must not
        echo the raw userinfo/query/fragment nor the exception body — only the
        sanitized URL and the exception type are logged."""
        import logging
        raw = "https://user:secret@loja.com/p?api_key=LEAKEDTOKEN&utm=cred#frag"
        boom = landing_price.requests.ConnectionError(
            f"HTTPSConnectionPool(host='loja.com'): Max retries exceeded with url: {raw} (Caused by ConnectTimeoutError)"
        )
        with caplog.at_level(logging.DEBUG, logger="pipeline.adapters.landing_price"), \
             patch.object(landing_price.requests, "get", side_effect=boom), \
             patch.object(landing_price, "_fetch_via_firecrawl", return_value=None):
            assert landing_price.fetch_price(raw) is None
        assert "LEAKEDTOKEN" not in caplog.text
        assert "user:secret" not in caplog.text
        assert "utm=cred" not in caplog.text
        assert "#frag" not in caplog.text
        assert "Max retries exceeded" not in caplog.text  # corpo da exceção nunca aparece
        assert "ConnectTimeoutError" not in caplog.text
        assert "ConnectionError" in caplog.text  # tipo da exceção aparece
        assert "https://loja.com/p" in caplog.text  # forma sanitizada (sem userinfo/query) aparece

    def test_firecrawl_failure_log_never_echoes_exception_text(self, monkeypatch, tmp_path, caplog):
        """O mesmo para o nível Firecrawl: corpo da exceção com URL crua nunca
        aparece nos logs — só URL sanitizada e tipo da exceção."""
        import logging
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        _isolate_quota(monkeypatch, tmp_path)
        raw = "https://user:hush@loja.com/js?api_key=HUSHED#x"
        boom = landing_price.requests.ConnectionError(
            f"Connection broken: connection refused to {raw} (underlying transport)"
        )
        with caplog.at_level(logging.DEBUG, logger="pipeline.adapters.landing_price"), \
             patch.object(landing_price.requests, "post", side_effect=boom):
            assert landing_price._fetch_via_firecrawl(raw) is None
        assert "HUSHED" not in caplog.text
        assert "user:hush" not in caplog.text
        assert "api_key" not in caplog.text
        assert "#x" not in caplog.text
        assert "connection refused" not in caplog.text  # corpo da exceção nunca aparece
        assert "ConnectionError" in caplog.text
        assert "https://loja.com/js" in caplog.text
