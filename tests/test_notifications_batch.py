"""Notification batch: deterministic order, identity and validation."""

from __future__ import annotations

import pytest

from pipeline.notifications import (
    KIND_PRODUCT,
    KIND_SUMMARY,
    NotificationBatch,
    NotificationItem,
    stable_batch_id,
    stable_product_id,
)
from tests.fixtures.telegram.build_helpers import (
    load_fixture,
    product_item,
    summary_item,
    synthetic_batch,
)


def _item(kind: str, *, order: int = 0, product: str | None = None, run_id: str = "r") -> NotificationItem:
    return NotificationItem(
        kind=kind, title="t", run_id=run_id, batch_id="b", order=order, product=product
    )


# ── Ordering and identity ─────────────────────────────────────────────────


def test_build_orders_summary_first_then_products_in_input_order():
    batch = synthetic_batch(run_id="run-1", mode="sunday")
    assert [item.kind for item in batch.items] == [
        KIND_SUMMARY,
        *(KIND_PRODUCT,) * len(load_fixture()["products"]),
    ]
    assert [item.order for item in batch.items] == list(range(len(batch.items)))
    assert batch.items[0].product is None
    expected_products = [stable_product_id(p["keyword"]) for p in load_fixture()["products"]]
    assert [item.product for item in batch.items[1:]] == expected_products
    assert batch.items[0].title == "RESEARCH SEMANAL"
    assert [item.title for item in batch.items[1:]] == [
        p["keyword"] for p in load_fixture()["products"]
    ]


def test_build_is_deterministic_across_rebuilds():
    first = synthetic_batch(run_id="run-1", mode="sunday")
    second = synthetic_batch(run_id="run-1", mode="sunday")
    assert first.items == second.items
    assert first.batch_id == second.batch_id
    assert first.audit_text == second.audit_text
    assert [item.item_id for item in first.items] == [item.item_id for item in second.items]


def test_batch_id_is_deterministic_and_mode_sensitive():
    assert stable_batch_id("r", "sunday") == stable_batch_id("r", "sunday")
    assert stable_batch_id("r", "sunday") != stable_batch_id("r", "monthly")
    assert stable_batch_id("r", "sunday") != stable_batch_id("r2", "sunday")
    assert len(stable_batch_id("r", "sunday")) == 16


def test_keyword_mode_is_single_product_without_summary():
    batch = NotificationBatch.build(
        mode="keyword", run_id="r", products=[product_item(load_fixture()["products"][0])]
    )
    assert len(batch.items) == 1
    assert batch.items[0].kind == KIND_PRODUCT
    assert batch.items[0].order == 0


def test_batch_audit_text_joins_item_audits_in_order():
    batch = synthetic_batch(run_id="run-1")
    assert batch.audit_text == "\n\n".join(item.audit_text for item in batch.items)
    assert batch.audit_text.startswith("RESEARCH SEMANAL")
    assert batch.audit_text.endswith("#velas_aromaticas #avancar #pesquisa_fornecedor")


def test_batch_items_carry_batch_identity():
    batch = synthetic_batch(run_id="run-1", mode="sunday")
    for item in batch.items:
        assert item.run_id == "run-1"
        assert item.batch_id == batch.batch_id


# ── Validation ────────────────────────────────────────────────────────────


def test_batch_rejects_empty_items():
    with pytest.raises(ValueError, match="sem itens"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=())


def test_batch_rejects_more_than_one_summary():
    items = (_item(KIND_SUMMARY, order=0), _item(KIND_SUMMARY, order=1))
    with pytest.raises(ValueError, match="summary"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=items)


def test_batch_rejects_summary_not_first():
    items = (_item(KIND_PRODUCT, order=0, product="p1"), _item(KIND_SUMMARY, order=1))
    with pytest.raises(ValueError, match="summary"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=items)


def test_batch_rejects_non_contiguous_orders():
    items = (_item(KIND_PRODUCT, order=0, product="p1"), _item(KIND_PRODUCT, order=2, product="p2"))
    with pytest.raises(ValueError, match="contíguos"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=items)


def test_batch_rejects_duplicate_item_ids():
    first = NotificationItem(
        kind=KIND_PRODUCT, title="t", run_id="r", batch_id="b", order=0, product="p1"
    )
    duplicate = NotificationItem(
        kind=KIND_PRODUCT, title="t", run_id="r", batch_id="b", order=0, product="p1"
    )
    with pytest.raises(ValueError, match="duplicados"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=(first, duplicate))


def test_batch_rejects_run_id_mismatch():
    items = (_item(KIND_PRODUCT, order=0, product="p1", run_id="other"),)
    with pytest.raises(ValueError, match="run_id"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=items)


def test_batch_rejects_batch_id_mismatch():
    item = NotificationItem(
        kind=KIND_PRODUCT, title="t", run_id="r", batch_id="wrong", order=0, product="p1"
    )
    with pytest.raises(ValueError, match="batch_id"):
        NotificationBatch(run_id="r", mode="sunday", batch_id="b", items=(item,))


def test_build_rejects_wrong_kind_args():
    product = _item(KIND_PRODUCT, product="p1")
    with pytest.raises(ValueError, match="summary"):
        NotificationBatch.build(mode="sunday", run_id="r", summary=product)
    summary = _item(KIND_SUMMARY)
    with pytest.raises(ValueError, match="product"):
        NotificationBatch.build(mode="sunday", run_id="r", products=[summary])
