"""Normal HTML fallback renderer: ``sendMessage`` fragments without
credentials.

Produces the method + payload fragment for ``sendMessage`` with
``text`` (HTML), ``parse_mode="HTML"`` and ``link_preview_options`` with
``is_disabled=True``.  No ``reply_markup`` in this release, no ``chat_id``
(the delivery layer adds it), no Rich-only structural tags: headings become
bold uppercase lines, tables and ``details`` are flattened to ``label: value``
text, exactly as the fallback contract requires.
"""

from __future__ import annotations

import html
from typing import Any

from pipeline.notifications.limits import MAX_HTML_LENGTH, guard_size
from pipeline.notifications.model import (
    NotificationBatch,
    NotificationField,
    NotificationItem,
)

_METHOD = "sendMessage"


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _field_line(field: NotificationField) -> str:
    if field.label is None:
        return _escape(field.value)
    return f"<b>{_escape(field.label)}:</b> {_escape(field.value)}"


def _html_text(item: NotificationItem) -> str:
    blocks: list[str] = [f"<b>{_escape(item.title)}</b>"]
    if item.subtitle:
        blocks.append(_escape(item.subtitle))
    if item.header_fields:
        blocks.append("\n".join(_field_line(field) for field in item.header_fields))
    for section in item.sections:
        block: list[str] = []
        if section.heading:
            block.append(f"<b>{_escape(section.heading.upper())}</b>")
        block.extend(_field_line(field) for field in section.fields)
        blocks.append("\n".join(block))
    if item.footer_fields:
        blocks.append("\n".join(_field_line(field) for field in item.footer_fields))
    if item.footer:
        blocks.append("\n".join(_escape(line) for line in item.footer))
    return "\n\n".join(blocks)


def render_html(item: NotificationItem) -> dict[str, Any]:
    """Render one item to a ``sendMessage`` payload fragment: HTML ``text``,
    ``parse_mode=HTML``, link preview disabled, no ``reply_markup``.

    Raises :class:`NotificationTooLargeError` when the visible text would
    exceed ``MAX_HTML_LENGTH`` — the content is never truncated.
    """
    text = _html_text(item)
    guard_size(text, MAX_HTML_LENGTH, renderer=_METHOD)
    return {
        "method": _METHOD,
        "payload": {
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
    }


def render_html_batch(batch: NotificationBatch) -> list[dict[str, Any]]:
    """Render every item in batch order; each fragment is guarded
    independently (summary then products, deterministic order)."""
    return [render_html(item) for item in batch.items]
