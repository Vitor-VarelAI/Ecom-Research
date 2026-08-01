"""Mocked contract and normalization tests for the Apify ads adapter."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from pipeline.adapters import apify_ads


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def ad(ad_id, copy="Mochila personalizada com entrega rápida", **overrides):
    value = {
        "ad_archive_id": ad_id,
        "page_id": "page-1",
        "page_name": "Loja Ágil",
        "ad_creation_time": "2026-01-01T00:00:00+00:00",
        "ad_creative_bodies": [copy],
        "ad_snapshot_url": f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else "",
        "snapshot_links": [{"url": f"https://loja.example/{ad_id or 'same'}"}],
        "publisher_platforms": ["facebook", "instagram"],
        "countries": ["PT", "ES"],
        "active_status": "ACTIVE",
    }
    value.update(overrides)
    return value


def test_missing_token_does_not_call_or_log_token(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: called.append(True))

    result = apify_ads.fetch("mochila")

    assert result == {"error": "APIFY_API_TOKEN não definido em .env"}
    assert called == []


def test_request_contract_and_normalized_fields(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    captured = {}
    payload = [ad("1")]

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response(201, payload)

    monkeypatch.setattr(apify_ads.requests, "post", post)
    result = apify_ads.fetch("Mochila personalizada")

    assert captured["url"] == apify_ads.APIFY_RUN_URL
    assert captured["headers"] == {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    assert captured["params"] == {"clean": "true", "format": "json"}
    assert captured["timeout"] == (10, 135)
    assert captured["json"]["count"] == 100
    library_url = captured["json"]["urls"][0]
    parsed = parse_qs(urlparse(library_url).query)
    assert parsed["active_status"] == ["active"]
    assert parsed["ad_type"] == ["all"]
    assert parsed["country"] == ["ALL"]
    assert parsed["is_targeted_country"] == ["false"]
    assert parsed["media_type"] == ["all"]
    assert parsed["q"] == ["Mochila personalizada"]
    assert parsed["search_type"] == ["keyword_unordered"]

    assert result["source"] == "Facebook Ad Library via Apify"
    assert result["query"] == "Mochila personalizada"
    assert result["sample_limit"] == 100
    assert result["total_ads"] == 1
    assert result["ad_ids"] == ["1"]
    top = result["top_ads"][0]
    assert set(("ad_archive_id", "advertiser", "page_id", "days_active", "reported_active_time", "link", "landing_url", "copy", "publisher_platforms", "countries")) <= set(top)
    assert top["link"].startswith("https://www.facebook.com/ads/library/")
    assert top["landing_url"] == "https://loja.example/1"


def test_filters_relevance_inactive_and_deduplicates(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    rows = [
        ad("1", copy="Mochila personalizada com nome"),
        ad("1", copy="Mochila personalizada duplicada"),
        ad("2", copy="Mochila personalizada outra", ad_snapshot_url="https://www.facebook.com/ads/library/?id=2"),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="", snapshot_links=[{"url": "https://loja.example/shared"}]),
        ad(None, copy="Mochila personalizada mesma", ad_snapshot_url="", snapshot_links=[{"url": "https://loja.example/shared"}]),
        ad("inactive", copy="Mochila personalizada", active_status="INACTIVE"),
        ad("irrelevant", copy="Caneca azul", page_name="Outra loja"),
    ]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))

    result = apify_ads.fetch("mochila personalizada")

    assert result["total_ads"] == 3
    assert result["filtered_out_count"] == 2
    assert result["duplicates_removed"] == 2
    assert result["ad_ids"] == ["1", "2"]
    assert result["advertisers"][0]["active_ads"] == 3


def test_long_query_requires_sixty_percent_and_single_token_requires_token(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    rows = [
        ad("exact", copy="Saco vermelho premium"),
        ad("two-of-three", copy="Saco vermelho em promoção", page_name="Loja"),
        ad("one-of-three", copy="Saco azul", page_name="Loja"),
        ad("single-no", copy="Produto azul", page_name="Loja"),
    ]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))

    result = apify_ads.fetch("saco vermelho premium")

    assert result["ad_ids"] == ["exact", "two-of-three"]
    assert result["filtered_out_count"] == 2


def test_empty_list_is_valid_success_and_sample_cap_reaches_alta(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, {"data": []}))
    empty = apify_ads.fetch("inexistente")
    assert "error" not in empty
    assert empty["total_ads"] == 0
    assert empty["filtered_out_count"] == 0

    rows = [ad(str(index), copy=f"Mochila premium {index}") for index in range(51)]
    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, rows))
    saturated = apify_ads.fetch("mochila")
    assert saturated["total_ads"] == 51
    assert saturated["saturation"] == "alta"
    assert saturated["sample_capped"] is False


def test_source_failures_are_errors_and_only_one_post(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "test-token")
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        raise apify_ads.requests.Timeout("not logged")

    monkeypatch.setattr(apify_ads.requests, "post", post)
    result = apify_ads.fetch("mochila")
    assert "error" in result
    assert calls == [True]

    monkeypatch.setattr(apify_ads.requests, "post", lambda *args, **kwargs: Response(200, ValueError("bad json")))
    assert "error" in apify_ads.fetch("mochila")
