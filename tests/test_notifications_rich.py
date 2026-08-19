"""Rich (sendRichMessage) renderer tests: payload shape, escaping, structural
tags, absence of reply_markup/media/URLs, and the 32768-char size guard."""

from __future__ import annotations

import json
import re

import pytest

from pipeline.notifications import (
    KIND_PRODUCT,
    MAX_RICH_LENGTH,
    NotificationField,
    NotificationItem,
    NotificationSection,
    NotificationTooLargeError,
    render_rich,
    render_rich_batch,
    visible_text,
)
from tests.fixtures.telegram.build_helpers import synthetic_batch

_METHOD = "sendRichMessage"

_FORBIDDEN = (
    "chat_id",
    "token",
    "api.telegram.org",
    "reply_markup",
    "callback",
    "inline_keyboard",
    "media",
    "image_url",
)


def _simple_item(**overrides) -> NotificationItem:
    base: dict = dict(
        kind=KIND_PRODUCT,
        title="caneca",
        product="abc",
        header_fields=(NotificationField(value="87/100", label="Score"),),
        sections=(
            NotificationSection(
                heading="Comercial",
                fields=(NotificationField(value="FORNECEDOR EM FALTA", label="Dropshipping"),),
            ),
        ),
        footer=("#caneca",),
    )
    base.update(overrides)
    return NotificationItem(**base)


def _assert_no_forbidden_content(fragment: dict) -> None:
    serialized = json.dumps(fragment)
    for needle in _FORBIDDEN:
        assert needle not in serialized, f"proibido no fragmento Rich: {needle!r}"
    assert "<a " not in serialized
    assert "<img" not in serialized
    assert '<a href' not in serialized


# ── Payload shape ─────────────────────────────────────────────────────────


def test_rich_fragment_exact_shape_without_credentials():
    fragment = render_rich(_simple_item())
    assert set(fragment) == {"method", "payload"}
    assert fragment["method"] == _METHOD
    assert set(fragment["payload"]) == {"rich_message"}
    assert set(fragment["payload"]["rich_message"]) == {"html"}
    _assert_no_forbidden_content(fragment)


def test_rich_bodyless_item_exact_html():
    item = NotificationItem(kind=KIND_PRODUCT, title="caneca", product="abc")
    fragment = render_rich(item)
    assert fragment == {
        "method": _METHOD,
        "payload": {"rich_message": {"html": "<h1>caneca</h1>"}},
    }


def test_rich_batch_renders_every_item_in_order():
    batch = synthetic_batch(run_id="run-x")
    fragments = render_rich_batch(batch)
    assert len(fragments) == len(batch.items)
    assert all(fragment["method"] == _METHOD for fragment in fragments)
    for item, fragment in zip(batch.items, fragments):
        assert fragment["payload"]["rich_message"]["html"].startswith(f"<h1>{item.title}</h1>")
        assert set(fragment["payload"]) == {"rich_message"}
        _assert_no_forbidden_content(fragment)


# ── Escaping ──────────────────────────────────────────────────────────────


def test_rich_escapes_angles_ampersands_and_quotes():
    item = _simple_item(
        title='caneca <"teste"> & \'apóstrofo\'',
        header_fields=(NotificationField(value='<b>bold</b> & "x"', label="Score & Nota"),),
        sections=(
            NotificationSection(
                heading="A & <B>",
                fields=(NotificationField(value="1 < 2 > 0", label="Limite"),),
            ),
        ),
        footer=('<script>alert("x")</script>',),
    )
    html_text = render_rich(item)["payload"]["rich_message"]["html"]
    assert "<script>" not in html_text
    assert "<b>bold</b>" not in html_text  # the raw <b> inside the value is escaped
    assert "&lt;b&gt;bold&lt;/b&gt;" in html_text
    assert "&amp;" in html_text
    assert "&quot;" in html_text
    assert "&#x27;" in html_text
    assert "1 &lt; 2 &gt; 0" in html_text
    # Round-trip: visible text preserves the original characters exactly.
    visible = visible_text(html_text)
    assert 'caneca <"teste"> & \'apóstrofo\'' in visible
    assert '<b>bold</b> & "x"' in visible
    assert 'alert("x")' in visible


# ── Structural tags ───────────────────────────────────────────────────────


def test_rich_uses_only_supported_structural_tags():
    batch = synthetic_batch()
    allowed = {
        "h1", "h2", "p", "b", "hr", "table", "tr", "th", "td", "details", "summary",
    }
    for item in batch.items:
        html_text = render_rich(item)["payload"]["rich_message"]["html"]
        tags = set(re.findall(r"<(\w+)", html_text))
        assert tags <= allowed, f"tags fora da whitelist: {tags - allowed}"


def test_rich_emits_heading_divider_table_and_details():
    batch = synthetic_batch()
    summary_html = render_rich(batch.items[0])["payload"]["rich_message"]["html"]
    assert "<h1>RESEARCH SEMANAL</h1>" in summary_html
    assert "<hr/>" in summary_html
    assert "<table bordered>" in summary_html
    assert "<th>Estado</th>" in summary_html
    assert "<summary>ALTERAÇÕES LEGAIS</summary>" in summary_html
    product_html = render_rich(batch.items[1])["payload"]["rich_message"]["html"]
    assert "<h2>Comercial</h2>" in product_html
    assert "<td>Dropshipping</td>" in product_html


# ── Size guard ────────────────────────────────────────────────────────────


def test_rich_rejects_oversized_content_without_truncating():
    item = _simple_item(
        sections=(
            NotificationSection(
                heading="h", fields=(NotificationField(value="a" * MAX_RICH_LENGTH, label="v"),)
            ),
        ),
    )
    with pytest.raises(NotificationTooLargeError) as exc:
        render_rich(item)
    assert "32768" in str(exc.value)
    # The audit text is untouched by the failed render.
    assert "a" * MAX_RICH_LENGTH in item.audit_text


def test_rich_accepts_content_exactly_at_the_limit():
    # Base visible length (title, header fields, heading, label) computed
    # dynamically so the boundary is exact regardless of structure.
    def with_value(value: str) -> NotificationItem:
        return _simple_item(
            sections=(
                NotificationSection(
                    heading="h", fields=(NotificationField(value=value, label="v"),)
                ),
            ),
        )

    base = len(visible_text(render_rich(with_value(""))["payload"]["rich_message"]["html"]))
    value = "a" * (MAX_RICH_LENGTH - base)
    fragment = render_rich(with_value(value))  # must not raise
    assert len(visible_text(fragment["payload"]["rich_message"]["html"])) == MAX_RICH_LENGTH


def test_rich_guard_counts_visible_text_not_raw_markup():
    # 20000 ampersands render as 100000 raw characters (&amp; each) but count
    # as 20000 visible: over the raw limit, under the visible limit.
    item = _simple_item(
        sections=(
            NotificationSection(
                heading="h", fields=(NotificationField(value="&" * 20000, label="v"),)
            ),
        ),
    )
    fragment = render_rich(item)
    html_text = fragment["payload"]["rich_message"]["html"]
    assert len(html_text) > MAX_RICH_LENGTH  # raw markup alone is over the limit
    assert len(visible_text(html_text)) < MAX_RICH_LENGTH  # visible text is fine
