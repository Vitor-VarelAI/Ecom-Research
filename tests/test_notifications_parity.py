"""Semantic parity between the Rich and HTML-fallback renderers."""

from __future__ import annotations

import pytest

from pipeline.notifications import (
    KIND_PRODUCT,
    NotificationField,
    NotificationItem,
    NotificationSection,
    assert_semantic_parity,
    content_tokens,
    render_html,
    render_rich,
    semantic_parity,
    semantic_tokens,
)
from tests.fixtures.telegram.build_helpers import synthetic_batch


def test_semantic_tokens_are_casefolded_and_punctuation_free():
    fragment = {
        "payload": {
            "rich_message": {
                "html": (
                    "<h2>Evidência Global</h2>\n"
                    "<p>PT / GB / US</p>\n"
                    "<p><b>Score:</b> 87/100</p>\n"
                    "<p>Maternelle Perú · 4 variantes</p>"
                )
            }
        }
    }
    assert semantic_tokens(fragment) == (
        "evidência",
        "global",
        "pt",
        "gb",
        "us",
        "score",
        "87/100",
        "maternelle",
        "perú",
        "4",
        "variantes",
    )


def test_semantic_tokens_handle_html_fallback_fragments():
    fragment = {
        "payload": {
            "text": "<b>COMERCIAL</b>\n<b>Margem:</b> POR VALIDAR\n<b>Legal Gate — UE/Portugal</b>",
            "parse_mode": "HTML",
        }
    }
    assert semantic_tokens(fragment) == (
        "comercial",
        "margem",
        "por",
        "validar",
        "legal",
        "gate",
        "ue/portugal",
    )


def test_every_batch_item_has_semantic_parity():
    batch = synthetic_batch(run_id="run-parity")
    for item in batch.items:
        assert semantic_parity(item) is True
        assert_semantic_parity(item)  # must not raise


def test_both_renderers_reproduce_content_tokens():
    for item in synthetic_batch(run_id="run-parity").items:
        expected = content_tokens(item)
        assert semantic_tokens(render_rich(item)) == expected
        assert semantic_tokens(render_html(item)) == expected


def test_parity_survives_external_string_escaping():
    item = NotificationItem(
        kind=KIND_PRODUCT,
        title='caneca <"teste"> & \'apóstrofo\'',
        product="abc",
        header_fields=(NotificationField(value="1 < 2 > 0", label="Score & Nota"),),
        sections=(
            NotificationSection(
                heading="A & <B>",
                fields=(NotificationField(value="<script>alert('x')</script>", label="Limite"),),
            ),
        ),
        footer=("#caneca #teste",),
    )
    assert semantic_parity(item) is True
    assert_semantic_parity(item)


def test_parity_helper_detects_meaningful_divergence():
    item = NotificationItem(
        kind=KIND_PRODUCT,
        title="caneca",
        product="abc",
        header_fields=(NotificationField(value="87/100", label="Score"),),
    )
    expected = content_tokens(item)
    tampered = render_rich(item)
    tampered["payload"]["rich_message"]["html"] = tampered["payload"]["rich_message"]["html"].replace(
        "Score", "ScoreX"
    )
    assert semantic_tokens(tampered) != expected
    assert semantic_parity(item) is True
