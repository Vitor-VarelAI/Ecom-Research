"""Notification model: deterministic identity, validation and audit text."""

from __future__ import annotations

import pytest

from pipeline.notifications import (
    KIND_PRODUCT,
    KIND_SUMMARY,
    NotificationField,
    NotificationItem,
    NotificationSection,
    render_audit_text,
    stable_item_id,
    stable_product_id,
)
from pipeline.output import pack
from tests.fixtures.telegram.build_helpers import synthetic_batch


# ── Stable identity ────────────────────────────────────────────────────────


def test_product_id_reuses_keyword_hash_semantics():
    for keyword in ("mochila personalizada", "Camisola Portugal", "velas aromáticas"):
        assert stable_product_id(keyword) == pack._keyword_hash(keyword)
        assert len(stable_product_id(keyword)) == 12
        assert stable_product_id(keyword) == stable_product_id(keyword)


def test_item_id_is_deterministic_from_run_kind_product_order():
    base = stable_item_id("run-1", KIND_PRODUCT, product="abc123", order=2)
    assert base == stable_item_id("run-1", KIND_PRODUCT, product="abc123", order=2)
    assert base != stable_item_id("run-2", KIND_PRODUCT, product="abc123", order=2)
    assert base != stable_item_id("run-1", KIND_PRODUCT, product="abd456", order=2)
    assert base != stable_item_id("run-1", KIND_PRODUCT, product="abc123", order=3)
    assert base != stable_item_id("run-1", KIND_SUMMARY, order=2)
    assert len(base) == 16


def test_item_id_has_no_randomness_across_instances():
    first = NotificationItem(
        kind=KIND_PRODUCT, title="caneca", run_id="r", batch_id="b", order=1, product="p1"
    )
    second = NotificationItem(
        kind=KIND_PRODUCT, title="caneca", run_id="r", batch_id="b", order=1, product="p1"
    )
    assert first.item_id == second.item_id
    assert first.item_id == stable_item_id("r", KIND_PRODUCT, product="p1", order=1)


def test_summary_and_product_item_ids_differ_in_same_batch():
    batch = synthetic_batch(run_id="run-x")
    ids = [item.item_id for item in batch.items]
    assert len(set(ids)) == len(ids)


# ── Validation ────────────────────────────────────────────────────────────


def test_item_rejects_invalid_kind():
    with pytest.raises(ValueError, match="kind"):
        NotificationItem(kind="bogus", title="x")


def test_product_item_requires_product():
    with pytest.raises(ValueError, match="product"):
        NotificationItem(kind=KIND_PRODUCT, title="x")


def test_summary_item_rejects_product():
    with pytest.raises(ValueError, match="product"):
        NotificationItem(kind=KIND_SUMMARY, title="x", product="abc")


def test_item_rejects_empty_title():
    with pytest.raises(ValueError, match="title"):
        NotificationItem(kind=KIND_PRODUCT, title="   ", product="abc")


def test_item_rejects_negative_order():
    with pytest.raises(ValueError, match="order"):
        NotificationItem(kind=KIND_PRODUCT, title="x", product="abc", order=-1)


def test_item_rejects_non_string_field_value():
    with pytest.raises(ValueError, match="value"):
        NotificationItem(
            kind=KIND_PRODUCT,
            title="x",
            product="abc",
            header_fields=(NotificationField(value=87, label="Score"),),
        )


def test_item_rejects_non_string_footer_line():
    with pytest.raises(ValueError, match="footer"):
        NotificationItem(kind=KIND_PRODUCT, title="x", product="abc", footer=(42,))


def test_details_section_requires_heading():
    with pytest.raises(ValueError, match="heading"):
        NotificationSection(fields=(), as_details=True)


def test_section_cannot_be_table_and_details():
    with pytest.raises(ValueError, match="table"):
        NotificationSection(heading="h", as_table=True, as_details=True)


# ── Audit text ────────────────────────────────────────────────────────────


def test_item_audit_text_is_complete_and_deterministic():
    item = NotificationItem(
        kind=KIND_PRODUCT,
        title="mochila personalizada",
        product="abc",
        header_fields=(
            NotificationField(value="87/100", label="Score"),
            NotificationField(value="AVANÇAR", label="Sinal advisory"),
        ),
        sections=(
            NotificationSection(
                heading="Comercial",
                fields=(
                    NotificationField(value="FORNECEDOR EM FALTA", label="Dropshipping"),
                    NotificationField(value="POR VALIDAR", label="Margem"),
                ),
            ),
        ),
        footer_fields=(NotificationField(value="16/08/2026", label="Última atualização"),),
        footer=("#mochila #avancar",),
    )
    audit = item.audit_text
    assert audit.startswith(
        "mochila personalizada\n\nScore: 87/100\nSinal advisory: AVANÇAR"
    )
    assert "Comercial\nDropshipping: FORNECEDOR EM FALTA\nMargem: POR VALIDAR" in audit
    assert "Última atualização: 16/08/2026" in audit
    assert audit.endswith("#mochila #avancar")
    assert audit == render_audit_text(item)
    rebuilt = NotificationItem(
        kind=KIND_PRODUCT,
        title="mochila personalizada",
        product="abc",
        header_fields=(
            NotificationField(value="87/100", label="Score"),
            NotificationField(value="AVANÇAR", label="Sinal advisory"),
        ),
        sections=(
            NotificationSection(
                heading="Comercial",
                fields=(
                    NotificationField(value="FORNECEDOR EM FALTA", label="Dropshipping"),
                    NotificationField(value="POR VALIDAR", label="Margem"),
                ),
            ),
        ),
        footer_fields=(NotificationField(value="16/08/2026", label="Última atualização"),),
        footer=("#mochila #avancar",),
    )
    assert rebuilt.audit_text == audit


def test_custom_audit_text_is_preserved():
    item = NotificationItem(
        kind=KIND_PRODUCT,
        title="caneca",
        product="abc",
        audit_text="digest textual original do pack",
    )
    assert item.audit_text == "digest textual original do pack"
