"""Absence placeholders selected by field meaning."""

from __future__ import annotations

import pytest

from pipeline.notifications import (
    DOCUMENTATION_MISSING,
    MISSING,
    NOT_APPLICABLE,
    PENDING_DOCUMENTATION,
    RESEARCH_NEEDED,
    SUPPLIER_MISSING,
    TO_VALIDATE,
    placeholder_for,
    render_html,
    render_rich,
    resolve_value,
    visible_text,
)
from tests.fixtures.telegram.build_helpers import synthetic_batch


def test_placeholder_for_known_meanings():
    assert placeholder_for("missing") == MISSING
    assert placeholder_for("unvalidated") == TO_VALIDATE
    assert placeholder_for("documentation_missing") == DOCUMENTATION_MISSING
    assert placeholder_for("research_needed") == RESEARCH_NEEDED
    assert placeholder_for("supplier_missing") == SUPPLIER_MISSING
    assert placeholder_for("pending_documentation") == PENDING_DOCUMENTATION
    assert placeholder_for("not_applicable") == NOT_APPLICABLE


def test_placeholder_for_unknown_meaning_raises():
    with pytest.raises(ValueError, match="desconhecido"):
        placeholder_for("bogus")


def test_resolve_null_and_blank_to_placeholder():
    assert resolve_value(None, "missing") == MISSING
    assert resolve_value("", "unvalidated") == TO_VALIDATE
    assert resolve_value("   ", "documentation_missing") == DOCUMENTATION_MISSING


def test_resolve_real_value_passes_through():
    assert resolve_value("87/100", "missing") == "87/100"
    assert resolve_value("AVANÇAR", "unvalidated") == "AVANÇAR"


def test_resolve_non_string_non_null_raises():
    with pytest.raises(ValueError, match="value"):
        resolve_value(87, "missing")


def test_placeholders_reach_both_rendered_fragments():
    batch = synthetic_batch()
    # Every product carries all four core absence meanings.
    for item in batch.items[1:]:
        rich_html = render_rich(item)["payload"]["rich_message"]["html"]
        html_text = render_html(item)["payload"]["text"]
        for placeholder in (MISSING, TO_VALIDATE, DOCUMENTATION_MISSING, RESEARCH_NEEDED):
            assert placeholder in visible_text(rich_html)
            assert placeholder in visible_text(html_text)
    # The summary resolves its missing full-report URL.
    summary = batch.items[0]
    assert MISSING in visible_text(render_rich(summary)["payload"]["rich_message"]["html"])
    assert MISSING in visible_text(render_html(summary)["payload"]["text"])
