"""Semantic parity between the Rich and HTML-fallback renderers.

Both renderers derive from the same renderer-neutral model.  These helpers
make parity verifiable without coupling tests to private markup details:

* :func:`semantic_tokens` — the ordered meaningful tokens (title, section
  headings, field labels and values, footer) of any rendered fragment,
  renderer-agnostic: tags removed, entities decoded, punctuation-only tokens
  dropped, casefolded;
* :func:`content_tokens` — the same stream straight from the model, the
  reference both renderers must reproduce;
* :func:`semantic_parity` / :func:`assert_semantic_parity` — the check.
"""

from __future__ import annotations

import re
from typing import Any

from pipeline.notifications.limits import visible_text
from pipeline.notifications.model import NotificationItem
from pipeline.notifications.render_html import render_html
from pipeline.notifications.render_rich import render_rich

# Leading/trailing punctuation (colons after labels, hashtag '#' prefixes)
# is presentation, not meaning: strip it before comparing tokens.
_EDGE_PUNCTUATION = re.compile(r"^[^\w]+|[^\w]+$")


def _fragment_html(fragment: dict[str, Any]) -> str:
    payload = fragment["payload"]
    rich = payload.get("rich_message")
    if isinstance(rich, dict) and "html" in rich:
        return rich["html"]
    return payload["text"]


def _tokens(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for token in text.split():
        token = _EDGE_PUNCTUATION.sub("", token)
        if any(char.isalnum() for char in token):
            tokens.append(token.casefold())
    return tuple(tokens)


def semantic_tokens(fragment: dict[str, Any]) -> tuple[str, ...]:
    """Ordered meaningful tokens of a rendered fragment, renderer-agnostic."""
    return _tokens(visible_text(_fragment_html(fragment)))


def _field_text(field: Any) -> str:
    if field.label is None:
        return field.value
    return f"{field.label} {field.value}"


def content_tokens(item: NotificationItem) -> tuple[str, ...]:
    """Ordered meaningful tokens straight from the renderer-neutral model —
    the reference the rendered fragments must reproduce."""
    parts: list[str] = [item.title]
    if item.subtitle:
        parts.append(item.subtitle)
    parts.extend(_field_text(field) for field in item.header_fields)
    for section in item.sections:
        if section.heading:
            parts.append(section.heading)
        parts.extend(_field_text(field) for field in section.fields)
    parts.extend(_field_text(field) for field in item.footer_fields)
    parts.extend(item.footer)
    return _tokens(" ".join(parts))


def semantic_parity(item: NotificationItem) -> bool:
    """True when both renderers reproduce the model's meaningful text."""
    expected = content_tokens(item)
    return (
        semantic_tokens(render_rich(item)) == expected
        and semantic_tokens(render_html(item)) == expected
    )


def assert_semantic_parity(item: NotificationItem) -> None:
    """Raise ``AssertionError`` (with the diverging token streams) when the
    Rich and HTML-fallback renderers do not preserve the model's meaningful
    title/section/field/value text."""
    expected = content_tokens(item)
    rich = semantic_tokens(render_rich(item))
    html_tokens = semantic_tokens(render_html(item))
    if rich != expected or html_tokens != expected:
        raise AssertionError(
            f"paridade semântica quebrada para item {item.item_id}\n"
            f"esperado: {expected}\nrich: {rich}\nhtml: {html_tokens}"
        )
