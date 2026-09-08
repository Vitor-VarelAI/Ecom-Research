from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from pipeline.arbitrage.openseo import ACCEPT_HEADER, OpenSeoClient, OpenSeoError


class _Response:
    def __init__(self, text: str, content_type: str) -> None:
        self.status_code = 200
        self.text = text
        self.headers = {"content-type": content_type}


class _Session:
    def __init__(self, response: _Response | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, endpoint: str, **kwargs: Any) -> _Response:
        self.calls.append({"endpoint": endpoint, **kwargs})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.mark.parametrize(
    ("content_type", "text", "expected"),
    [
        (
            "application/json",
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"structuredContent": {"source": "json"}},
                }
            ),
            {"source": "json"},
        ),
        (
            "text/event-stream",
            'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"source":"sse"}}}\n\ndata: [DONE]\n\n',
            {"source": "sse"},
        ),
    ],
)
def test_client_decodes_json_and_sse_without_retrying(
    content_type: str,
    text: str,
    expected: dict[str, str],
) -> None:
    session = _Session(_Response(text, content_type))
    client = OpenSeoClient("http://127.0.0.1:3001/mcp", session=session)

    assert client.call_tool("get_keyword_metrics", {"projectId": "project-123"}) == expected

    assert len(session.calls) == 1
    request = session.calls[0]
    assert request["headers"]["Accept"] == ACCEPT_HEADER
    assert request["json"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "get_keyword_metrics",
            "arguments": {"projectId": "project-123"},
        },
    }


def test_client_does_not_retry_transport_failures() -> None:
    session = _Session(error=requests.ConnectionError("secret transport detail"))
    client = OpenSeoClient("http://127.0.0.1:3001/mcp", session=session)

    with pytest.raises(OpenSeoError, match="OpenSEO request failed"):
        client.call_tool("get_serp_results", {})

    assert len(session.calls) == 1
