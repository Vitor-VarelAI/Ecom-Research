import json

from pipeline import quota
from pipeline.adapters import trends


def test_fetch_related_is_one_quota_counted_cached_call(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"related_queries": {"rising": [{"query": "mochila escolar", "value": 300}, {"query": "mochila premium", "value": "Breakout"}]}}

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    quota_file = tmp_path / "quota.json"
    quota_file.write_text(json.dumps({
        "month": quota._current_month(), "calls": 0, "limit": quota.QUOTA_LIMIT,
        "firecrawl_credits": 0, "firecrawl_limit": quota.FIRECRAWL_LIMIT,
    }), encoding="utf-8")
    monkeypatch.setattr(trends.quota, "QUOTA_FILE", str(quota_file))
    monkeypatch.setattr(trends.requests, "get", get)

    first = trends.fetch_related("mochila")
    second = trends.fetch_related("mochila")

    assert first["related_queries"][1]["score"] == "Breakout"
    assert "_cached_at" not in first
    assert second == first
    assert len(calls) == 1
    assert quota.get_usage()["calls"] == 1  # exactly one charge for one call
    assert calls[0][1]["params"]["data_type"] == "RELATED_QUERIES"
