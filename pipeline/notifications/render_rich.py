"""Rich renderer: ``sendRichMessage`` payload fragments without credentials.

Produces the method + payload fragment for the official ``sendRichMessage``
endpoint: top-level ``chat_id`` (added by the delivery layer, never here),
``rich_message={html: ...}`` with exactly one of html/markdown/blocks (we
emit html only), and no ``reply_markup`` in this release.

Only structural tags the official Bot API Rich HTML supports are used:
``h1``/``h2``, ``p``, ``hr``, ``b``, ``table bordered`` with ``tr``/``th``/
``td``, and ``details``/``summary``.  Every external string (title, subtitle,
headings, labels, values, footer) is HTML-escaped.
"""

from __future__ import annotations

import html
from typing import Any

from pipeline.notifications.limits import MAX_RICH_LENGTH, guard_size
from pipeline.notifications.model import (
    NotificationBatch,
    NotificationField,
    NotificationItem,
    NotificationSection,
)

_METHOD = "sendRichMessage"


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _paragraph(field: NotificationField) -> str:
    if field.label is None:
        return f"<p>{_escape(field.value)}</p>"
    return f"<p><b>{_escape(field.label)}:</b> {_escape(field.value)}</p>"


def _section_html(section: NotificationSection) -> str:
    if section.as_details:
        heading = section.heading or ""
        parts: list[str] = [f"<summary>{_escape(heading)}</summary>"]
        parts.extend(_paragraph(field) for field in section.fields)
        return "<details>\n" + "\n".join(parts) + "\n</details>"
    if section.as_table:
        rows: list[str] = []
        for field in section.fields:
            if field.is_header:
                cells = [
                    f"<th>{_escape(field.label)}</th>",
                    f"<th>{_escape(field.value)}</th>",
                ]
            elif field.label is None:
                cells = [f'<td colspan="2">{_escape(field.value)}</td>']
            else:
                cells = [
                    f"<td>{_escape(field.label)}</td>",
                    f"<td>{_escape(field.value)}</td>",
                ]
            rows.append("<tr>\n" + "\n".join(cells) + "\n</tr>")
        table = "<table bordered>\n" + "\n".join(rows) + "\n</table>"
        if section.heading:
            return f"<h2>{_escape(section.heading)}</h2>\n{table}"
        return table
    parts = []
    if section.heading:
        parts.append(f"<h2>{_escape(section.heading)}</h2>")
    parts.extend(_paragraph(field) for field in section.fields)
    # Newlines between blocks keep word boundaries in the visible text.
    return "\n".join(parts)


def _rich_html(item: NotificationItem) -> str:
    parts: list[str] = [f"<h1>{_escape(item.title)}</h1>"]
    if item.subtitle:
        parts.append(f"<p>{_escape(item.subtitle)}</p>")
    parts.extend(_paragraph(field) for field in item.header_fields)
    if item.sections or item.footer_fields or item.footer:
        parts.append("<hr/>")
    parts.extend(_section_html(section) for section in item.sections)
    parts.extend(_paragraph(field) for field in item.footer_fields)
    parts.extend(f"<p>{_escape(line)}</p>" for line in item.footer)
    # Newlines between blocks keep word boundaries in the visible text
    # (Telegram collapses them visually) and make parity checks exact.
    return "\n".join(parts)


def render_rich(item: NotificationItem) -> dict[str, Any]:
    """Render one item to a ``sendRichMessage`` payload fragment.

    The fragment carries no ``chat_id``, no token and no ``reply_markup``.
    Raises :class:`NotificationTooLargeError` when the visible text would
    exceed ``MAX_RICH_LENGTH`` — the content is never truncated.
    """
    fragment_html = _rich_html(item)
    guard_size(fragment_html, MAX_RICH_LENGTH, renderer=_METHOD)
    return {"method": _METHOD, "payload": {"rich_message": {"html": fragment_html}}}


def render_rich_batch(batch: NotificationBatch) -> list[dict[str, Any]]:
    """Render every item in batch order; each fragment is guarded
    independently (summary then products, deterministic order)."""
    return [render_rich(item) for item in batch.items]
