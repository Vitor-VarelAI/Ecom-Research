"""
Testes do adapter Landing Price.

Usa fixtures HTML simuladas — sem chamadas reais à rede.
"""
import pytest
import os
from unittest.mock import patch, Mock

from pipeline.adapters import landing_price

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


# ── Tests: JSON-LD extraction ──────────────────────────────────────────────

class TestJsonLdExtraction:
    def test_extract_single_product_offer(self):
        prices = landing_price._extract_jsonld_price(HTML_WITH_JSONLD)
        assert prices is not None
        assert 45.99 in prices

    def test_extract_multiple_offers(self):
        prices = landing_price._extract_jsonld_price(HTML_WITH_MULTIPLE_JSONLD)
        assert prices is not None
        assert 29.99 in prices
        assert 59.99 in prices

    def test_extract_aggregate_offer(self):
        prices = landing_price._extract_jsonld_price(HTML_WITH_AGGREGATE_OFFER)
        assert prices is not None
        assert 19.99 in prices
        assert 89.99 in prices

    def test_no_jsonld_returns_none(self):
        prices = landing_price._extract_jsonld_price(HTML_NO_PRICES)
        assert prices is None or prices == []

    def test_invalid_json_in_ld_script(self):
        html = '<script type="application/ld+json">{invalid}</script>'
        prices = landing_price._extract_jsonld_price(html)
        # Should not crash — returns None or empty list
        assert prices is None or prices == []


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
            assert result["via"] == "http"

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

    def test_firecrawl_quota_exhausted_returns_none(self, monkeypatch):
        """Sem credits Firecrawl, devolve None."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        monkeypatch.setattr(landing_price.quota, "can_spend_firecrawl", lambda n=1: False)

        result = landing_price._fetch_via_firecrawl("https://example.com/test")
        assert result is None

    def test_firecrawl_success_counts_credit(self, monkeypatch):
        """Firecrawl bem-sucedido gasta 1 credit."""
        monkeypatch.setenv("FIRECRAWL_KEY", "fake-key")
        monkeypatch.setattr(landing_price.quota, "can_spend_firecrawl", lambda n=1: True)

        spend_count = [0]
        def mock_spend(n=1):
            spend_count[0] += n
        monkeypatch.setattr(landing_price.quota, "spend_firecrawl", mock_spend)

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
            assert spend_count[0] == 1  # 1 credit gasto

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
                {"advertiser": "X", "link": "", "days_active": 30},
            ]
        }
        result = landing_price.fetch_prices_from_ads(ads_data)
        assert "error" in result
        assert "link de destino indisponível" in result["error"]

    def test_error_in_ads_data(self):
        result = landing_price.fetch_prices_from_ads({"error": "no data"})
        assert "error" in result

    def test_fetches_from_top_ads_with_links(self):
        ads_data = {
            "top_ads": [
                {"advertiser": "A", "link": "https://ex.com/a", "days_active": 100},
                {"advertiser": "B", "link": "https://ex.com/b", "days_active": 80},
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
                {"advertiser": "A", "link": "https://ex.com/a", "days_active": 100},
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
                {"advertiser": "A", "link": "https://ex.com/1", "days_active": 100},
                {"advertiser": "B", "link": "https://ex.com/2", "days_active": 90},
                {"advertiser": "C", "link": "https://ex.com/3", "days_active": 80},
                {"advertiser": "D", "link": "https://ex.com/4", "days_active": 70},  # ignorado
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
