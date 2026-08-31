"""Normal HTML fallback (sendMessage) renderer tests: payload shape,
escaping, no Rich-only tags, public action links, absence of reply_markup/media,
and the 4096-char size guard after entities parsing."""

from __future__ import annotations

import json

import pytest

from pipeline.notifications import (
    KIND_PRODUCT,
    MAX_HTML_LENGTH,
    NotificationAction,
    NotificationField,
    NotificationItem,
    NotificationSection,
    NotificationTooLargeError,
    render_html,
    render_html_batch,
    visible_text,
)
from tests.fixtures.telegram.build_helpers import synthetic_batch

_METHOD = "sendMessage"

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
        assert needle not in serialized, f"proibido no fragmento HTML: {needle!r}"
    assert "<img" not in serialized


# ── Payload shape ─────────────────────────────────────────────────────────


def test_html_fragment_exact_shape_without_credentials():
    fragment = render_html(_simple_item())
    assert set(fragment) == {"method", "payload"}
    assert fragment["method"] == _METHOD
    assert set(fragment["payload"]) == {"text", "parse_mode", "link_preview_options"}
    assert fragment["payload"]["parse_mode"] == "HTML"
    assert fragment["payload"]["link_preview_options"] == {"is_disabled": True}
    _assert_no_forbidden_content(fragment)


def test_html_bodyless_item_exact_text():
    item = NotificationItem(kind=KIND_PRODUCT, title="caneca", product="abc")
    fragment = render_html(item)
    assert fragment == {
        "method": _METHOD,
        "payload": {
            "text": "<b>caneca</b>",
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
    }


def test_html_batch_renders_every_item_in_order():
    batch = synthetic_batch(run_id="run-x")
    fragments = render_html_batch(batch)
    assert len(fragments) == len(batch.items)
    assert all(fragment["method"] == _METHOD for fragment in fragments)
    for item, fragment in zip(batch.items, fragments):
        assert fragment["payload"]["text"].startswith(f"<b>{item.title}</b>")
        assert fragment["payload"]["parse_mode"] == "HTML"
        _assert_no_forbidden_content(fragment)


# ── Escaping ──────────────────────────────────────────────────────────────


def test_html_escapes_angles_ampersands_and_quotes():
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
    text = render_html(item)["payload"]["text"]
    assert "<script>" not in text
    assert "<b>bold</b>" not in text  # the raw <b> inside the value is escaped
    assert "&lt;b&gt;bold&lt;/b&gt;" in text
    assert "&amp;" in text
    assert "&quot;" in text
    assert "&#x27;" in text
    assert "1 &lt; 2 &gt; 0" in text
    visible = visible_text(text)
    assert 'caneca <"teste"> & \'apóstrofo\'' in visible
    assert '<b>bold</b> & "x"' in visible
    assert 'alert("x")' in visible


def test_html_actions_are_normal_escaped_links_with_previews_disabled():
    item = _simple_item(
        actions=(
            NotificationAction(
                label='Abrir <pesquisa> & "agora"',
                url="https://research.example/search?q=mochila&market=PT",
            ),
        ),
    )
    fragment = render_html(item)
    text = fragment["payload"]["text"]
    _assert_no_forbidden_content(fragment)
    assert (
        '<a href="https://research.example/search?q=mochila&amp;market=PT">'
        'Abrir &lt;pesquisa&gt; &amp; &quot;agora&quot;</a>'
    ) in text
    assert fragment["payload"]["link_preview_options"] == {"is_disabled": True}
    assert "reply_markup" not in text
    assert "callback_data" not in text
    assert "media" not in text


# ── No Rich-only structural tags ──────────────────────────────────────────


def test_html_has_no_rich_structural_tags():
    batch = synthetic_batch()
    for item in batch.items:
        text = render_html(item)["payload"]["text"]
        for tag in ("<h1", "<h2", "<hr", "<table", "<tr", "<td", "<th", "<details", "<summary"):
            assert tag not in text, f"tag Rich no fallback: {tag!r}"
        assert text.startswith("<b>")
    # Table header flattens like any other pair, in visible text.
    summary = render_html(batch.items[0])["payload"]["text"]
    assert "<b>Estado:</b> Total" in summary
    assert "Estado: Total" in visible_text(summary)


# ── Size guard ────────────────────────────────────────────────────────────


def test_html_rejects_oversized_content_without_truncating():
    item = _simple_item(
        sections=(
            NotificationSection(
                heading="h", fields=(NotificationField(value="a" * MAX_HTML_LENGTH, label="v"),)
            ),
        ),
    )
    with pytest.raises(NotificationTooLargeError) as exc:
        render_html(item)
    assert "4096" in str(exc.value)
    # The audit text is untouched by the failed render.
    assert "a" * MAX_HTML_LENGTH in item.audit_text


def test_html_accepts_content_exactly_at_the_limit():
    def with_value(value: str) -> NotificationItem:
        return _simple_item(
            sections=(
                NotificationSection(
                    heading="h", fields=(NotificationField(value=value, label="v"),)
                ),
            ),
        )

    base = len(visible_text(render_html(with_value(""))["payload"]["text"]))
    value = "a" * (MAX_HTML_LENGTH - base)
    fragment = render_html(with_value(value))  # must not raise
    assert len(visible_text(fragment["payload"]["text"])) == MAX_HTML_LENGTH


def test_html_guard_counts_visible_text_not_raw_markup():
    # 3000 ampersands render as 15000 raw characters but count as 3000
    # visible: under 4096, accepted even though the raw payload is huge.
    item = _simple_item(
        sections=(
            NotificationSection(
                heading="h", fields=(NotificationField(value="&" * 3000, label="v"),)
            ),
        ),
    )
    fragment = render_html(item)
    text = fragment["payload"]["text"]
    assert len(text) > MAX_HTML_LENGTH
    assert len(visible_text(text)) < MAX_HTML_LENGTH
    # Pushing the visible text over the limit fails.
    big = _simple_item(
        sections=(
            NotificationSection(
                heading="h",
                fields=(NotificationField(value=("&" * 3000) + ("x" * 2000), label="v"),),
            ),
        ),
    )
    with pytest.raises(NotificationTooLargeError):
        render_html(big)
