from pipeline.adapters import trends


def test_fetch_related_is_one_quota_counted_cached_call(monkeypatch, tmp_path):
    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.setattr(trends, "CACHE_DIR", str(tmp_path))
    calls = []
    spends = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"related_queries": {"rising": [{"query": "mochila escolar", "value": 300}, {"query": "mochila premium", "value": "Breakout"}]}}

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    monkeypatch.setattr(trends.requests, "get", get)
    monkeypatch.setattr(trends.quota, "can_spend", lambda n=1: True)
    monkeypatch.setattr(trends.quota, "spend", lambda n=1: spends.append(n))

    first = trends.fetch_related("mochila")
    second = trends.fetch_related("mochila")

    assert first["related_queries"][1]["score"] == "Breakout"
    assert second == first
    assert len(calls) == 1
    assert spends == [1]
    assert calls[0][1]["params"]["data_type"] == "RELATED_QUERIES"
