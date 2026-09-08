"""Small, non-retrying OpenSEO MCP JSON-RPC client."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import requests

DEFAULT_MCP_URL = "http://127.0.0.1:3001/mcp"
ACCEPT_HEADER = "application/json, text/event-stream"

DATAFORSEO_MARKETS: Mapping[str, Mapping[str, Any]] = {
    "US": {"market_id": "US", "location_code": 2840, "language_code": "en"},
    "PT": {"market_id": "PT", "location_code": 2620, "language_code": "pt"},
    "GB": {"market_id": "GB", "location_code": 2826, "language_code": "en"},
}


class OpenSeoError(RuntimeError):
    """Sanitized OpenSEO transport or tool error."""


@dataclass(slots=True)
class OpenSeoClient:
    """One-shot MCP client; it deliberately performs no automatic retries."""

    endpoint: str
    timeout: float = 30.0
    session: requests.Session | None = None
    _next_id: int = field(init=False, default=1)

    def __post_init__(self) -> None:
        if not self.endpoint.strip():
            raise OpenSeoError("OpenSEO endpoint is empty")
        if self.session is None:
            self.session = requests.Session()

    def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": dict(arguments),
            },
        }
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                headers={
                    "Accept": ACCEPT_HEADER,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise OpenSeoError(f"OpenSEO request failed for {tool_name}") from None

        status_code = getattr(response, "status_code", 0)
        if not 200 <= status_code < 300:
            raise OpenSeoError(
                f"OpenSEO returned HTTP {status_code} for {tool_name}"
            )
        envelope = _decode_response(response)
        if "error" in envelope:
            raise OpenSeoError(f"OpenSEO JSON-RPC error for {tool_name}")
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise OpenSeoError(f"OpenSEO result missing for {tool_name}")
        if result.get("isError") is True:
            raise OpenSeoError(f"OpenSEO tool error for {tool_name}")
        if "structuredContent" not in result or result["structuredContent"] is None:
            raise OpenSeoError(
                f"OpenSEO structured content missing for {tool_name}"
            )
        return result["structuredContent"]


def _decode_response(response: Any) -> Mapping[str, Any]:
    text = getattr(response, "text", "")
    content_type = str(
        getattr(response, "headers", {}).get("content-type", "")
    ).lower()
    candidates: list[Any] = []
    if "text/event-stream" in content_type or text.lstrip().startswith("data:"):
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                candidates.append(json.loads(data))
            except (TypeError, json.JSONDecodeError):
                raise OpenSeoError("OpenSEO returned invalid SSE JSON") from None
    else:
        try:
            candidates.append(json.loads(text))
        except (TypeError, json.JSONDecodeError):
            try:
                candidates.append(response.json())
            except (AttributeError, ValueError, TypeError):
                raise OpenSeoError("OpenSEO returned invalid JSON") from None

    for candidate in reversed(candidates):
        if isinstance(candidate, Mapping):
            return candidate
    raise OpenSeoError("OpenSEO returned an empty response")


__all__ = [
    "ACCEPT_HEADER",
    "DATAFORSEO_MARKETS",
    "DEFAULT_MCP_URL",
    "OpenSeoClient",
    "OpenSeoError",
]
