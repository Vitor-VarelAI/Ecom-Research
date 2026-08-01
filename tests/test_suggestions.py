from pipeline.suggestions import extract_keyword_suggestions


def test_breakout_then_numeric_and_advisory_status():
    result = extract_keyword_suggestions(
        "mochila personalizada",
        {
            "related_queries": [
                {"query": "mochila barata", "value": 900},
                {"query": "mochila premium", "value": "Breakout"},
                {"query": "mochila escolar", "extracted_value": 1200},
            ]
        },
    )
    assert [item["keyword"] for item in result] == ["mochila premium", "mochila escolar", "mochila barata"]
    assert all(item["status"] == "proposed" for item in result)
    assert result[0]["score"] == "Breakout"


def test_removes_source_duplicates_urls_interrogatives_and_empty():
    result = extract_keyword_suggestions(
        "Caneca personalizada",
        {
            "related_queries": [
                {"keyword": "caneca personalizada", "score": 100},
                {"keyword": "caneca personalizada", "score": 90},
                {"keyword": "https://example.com", "score": 1000},
                {"keyword": "Como fazer caneca", "score": 800},
                {"keyword": "Caneca personalizada", "score": 700},
                {"keyword": "caneca premium", "score": 20},
            ]
        },
    )
    assert [item["keyword"] for item in result] == ["caneca premium"]


def test_error_or_limit_returns_empty():
    assert extract_keyword_suggestions("x", {"error": "offline"}) == []
    assert extract_keyword_suggestions("x", {"related_queries": [{"keyword": "one", "score": 1}]}, limit=0) == []
