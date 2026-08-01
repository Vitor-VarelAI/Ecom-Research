"""Deterministic, advisory keyword suggestions from Google Trends related queries."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse


_INTERROGATIVES = {
    "como",
    "qual",
    "quais",
    "quando",
    "onde",
    "porque",
    "porquê",
    "por que",
    "qué",
    "que",
    "quien",
    "quién",
    "donde",
    "dónde",
    "cuando",
    "cómo",
    "why",
    "what",
    "which",
    "when",
    "where",
    "who",
    "how",
    "pourquoi",
    "comment",
    "quand",
}


def _fold(text: Any) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _numeric_score(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if "," in text and "." not in text and re.search(r",\d{3}(?:\D|$)", text):
        text = text.replace(",", "")
    elif "." in text and "," not in text and re.search(r"\.\d{3}(?:\D|$)", text):
        text = text.replace(".", "")
    else:
        text = text.replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def _is_breakout(value: Any, item: dict[str, Any]) -> bool:
    candidates = [value, item.get("type"), item.get("growth"), item.get("trend")]
    return any(_fold(candidate) == "breakout" for candidate in candidates)


def _is_url(value: str) -> bool:
    if value.startswith(("http://", "https://", "www.")):
        return True
    if re.match(r"^[\w.-]+\.[A-Za-z]{2,}(?:/|$)", value):
        return True
    try:
        return bool(urlparse(value).scheme and urlparse(value).netloc)
    except ValueError:
        return False


def _is_interrogative(value: str) -> bool:
    if "?" in value:
        return True
    first_word = _fold(value).split(maxsplit=1)[0] if value.strip() else ""
    return first_word in {_fold(word) for word in _INTERROGATIVES}


def extract_keyword_suggestions(
    source_keyword: str,
    trends_data: dict[str, Any] | None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return ranked proposed alternatives without mutating any configuration.

    Breakout queries always precede numeric queries; numeric queries then sort
    descending by their extracted value.  Input order is retained for ties.
    """
    if limit <= 0 or not trends_data or "error" in trends_data:
        return []

    raw_queries = trends_data.get("related_queries", [])
    if not isinstance(raw_queries, list):
        return []

    candidates: list[tuple[int, bool, float, dict[str, Any]]] = []
    seen: set[str] = set()
    source_folded = _fold(source_keyword)

    for index, item in enumerate(raw_queries):
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword", item.get("query", "")) or "").strip()
        folded = _fold(keyword)
        if not folded or folded == source_folded or folded in seen:
            continue
        if _is_url(keyword) or _is_interrogative(keyword):
            continue

        raw_score = item.get("score", item.get("value", item.get("extracted_value", "")))
        breakout = _is_breakout(raw_score, item)
        numeric = _numeric_score(raw_score)
        seen.add(folded)
        result_score: int | float | str
        if breakout:
            result_score = "Breakout"
        elif numeric.is_integer():
            result_score = int(numeric)
        else:
            result_score = numeric
        suggestion = {
            "keyword": keyword,
            "score": result_score,
            "status": "proposed",
        }
        candidates.append((index, breakout, numeric, suggestion))

    candidates.sort(key=lambda candidate: (not candidate[1], -candidate[2], candidate[0]))
    return [candidate[3] for candidate in candidates[:limit]]
