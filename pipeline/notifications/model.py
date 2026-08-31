"""Renderer-neutral notification models with deterministic identity.

Slice 1 of the Telegram rich outbound: the weekly summary and the per-product
messages are modelled here without Telegram credentials or transport concerns.
Public investigation actions are explicit label/URL pairs; private Bot API URLs
remain forbidden. Identity is deterministic — never random:

* ``product`` (kind=product only) reuses the keyword-hash semantics of the
  pack writer (``pipeline.output.pack._keyword_hash``: SHA-256 of the raw
  UTF-8 keyword, first 12 hex chars);
* ``item_id`` derives from run_id/kind/product/order;
* ``batch_id`` derives from run_id/mode;
* the batch fixes the summary at order 0 and keeps products in deterministic
  input order (1..N).

Every item also preserves a complete plain-text ``audit_text`` for stdout and
operational records, derived from the structured content unless the caller
supplies its own.

chat_id, bot tokens and private Bot API URLs are deliberately absent from
every model in this package and can never reach a rendered fragment.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any, Sequence
from urllib.parse import urlsplit

KIND_SUMMARY = "summary"
KIND_PRODUCT = "product"
VALID_KINDS = frozenset({KIND_SUMMARY, KIND_PRODUCT})

_PRODUCT_ID_LENGTH = 12  # same length as pipeline.output.pack._keyword_hash
_ITEM_ID_LENGTH = 16
_BATCH_ID_LENGTH = 16


def stable_product_id(keyword: str) -> str:
    """Stable product identity: SHA-256 (first 12 hex chars) of the raw
    UTF-8 keyword — the exact semantics of
    ``pipeline.output.pack._keyword_hash``, replicated here so notification
    identity and pack filenames can never drift apart."""
    return hashlib.sha256(keyword.encode("utf-8")).hexdigest()[:_PRODUCT_ID_LENGTH]


def stable_batch_id(run_id: str, mode: str) -> str:
    """Deterministic batch identity for one (run, mode) pair."""
    return hashlib.sha256(f"{run_id}:{mode}".encode("utf-8")).hexdigest()[:_BATCH_ID_LENGTH]


def stable_item_id(run_id: str, kind: str, *, product: str | None = None, order: int = 0) -> str:
    """Deterministic item identity from run_id/kind/product/order.

    The summary (no product) and every product (stable product id plus its
    position in the batch) get distinct, repeatable ids.  No randomness, no
    wall-clock input: the same run rebuilds the same ids.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"kind inválido: {kind!r}")
    seed = ":".join((run_id, kind, product or "", str(order)))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_ITEM_ID_LENGTH]


def _validate_field(field_: "NotificationField") -> None:
    if field_.label is not None and not isinstance(field_.label, str):
        raise ValueError(f"label inválido: {field_.label!r}")
    if not isinstance(field_.value, str):
        raise ValueError(f"value inválido: {field_.value!r}")


@dataclass(frozen=True)
class NotificationField:
    """A label/value pair, or a plain paragraph when ``label`` is None.

    ``is_header`` marks the header row of a table section: the Rich renderer
    emits it as ``<th>``, the HTML fallback flattens it like any other pair.
    Values are plain strings (placeholders already resolved); every renderer
    escapes them.
    """

    value: str
    label: str | None = None
    is_header: bool = False


@dataclass(frozen=True)
class NotificationSection:
    """A named block of fields shared by both renderers.

    ``as_table`` renders as a bordered, striped, compact table in Rich and
    flat ``label: value`` lines in the fallback. ``as_details`` renders as
    ``<details><summary>…`` in Rich (requires a heading) and as a flat
    section in the fallback.
    """

    heading: str | None = None
    fields: tuple[NotificationField, ...] = ()
    as_table: bool = False
    as_details: bool = False

    def __post_init__(self) -> None:
        if self.heading is not None and not isinstance(self.heading, str):
            raise ValueError(f"heading inválido: {self.heading!r}")
        fields = tuple(self.fields)
        object.__setattr__(self, "fields", fields)
        for field_ in fields:
            _validate_field(field_)
        if self.as_details and not self.heading:
            raise ValueError("section as_details exige heading (summary do <details>)")
        if self.as_table and self.as_details:
            raise ValueError("section não pode ser table e details ao mesmo tempo")


def _field_line(field: NotificationField) -> str:
    if field.label:
        return f"{field.label}: {field.value}"
    return field.value


@dataclass(frozen=True)
class NotificationAction:
    """One public external investigation link; never a callback or command."""

    label: str
    url: str

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("action label vazio")
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("action url vazio")
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError(f"action url inválido: {self.url!r}")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("action url não pode conter credenciais")
        if (
            parsed.hostname.casefold() == "api.telegram.org"
            and parsed.path.casefold().startswith("/bot")
        ):
            raise ValueError("action url não pode conter endpoint privado da Bot API")


def render_audit_text(item: "NotificationItem") -> str:
    """Complete plain-text rendering of one item — title, subtitle, header
    fields, sections, actions, footer fields and footer lines in order.
    Deterministic and renderer-neutral; this is the full audit text preserved
    per item."""
    lines = [item.title]
    if item.subtitle:
        lines.append(item.subtitle)
    if item.header_fields:
        lines.append("")
        lines.extend(_field_line(field) for field in item.header_fields)
    for section in item.sections:
        lines.append("")
        if section.heading:
            lines.append(section.heading)
        lines.extend(_field_line(field) for field in section.fields)
    if item.actions:
        lines.append("")
        lines.append("Investigar")
        lines.extend(f"{action.label}: {action.url}" for action in item.actions)
    if item.footer_fields:
        lines.append("")
        lines.extend(_field_line(field) for field in item.footer_fields)
    if item.footer:
        lines.append("")
        lines.extend(item.footer)
    return "\n".join(lines)


@dataclass(frozen=True)
class NotificationItem:
    """One deliverable message: the weekly summary or a single product.

    ``item_id`` is always deterministic from run_id/kind/product/order and is
    recomputed whenever identity fields change (e.g. by
    :meth:`NotificationBatch.build`).  ``audit_text`` defaults to the complete
    plain-text rendering of the structured content.

    A bare item (run_id/batch_id empty, order 0) is a valid content spec used
    by :meth:`NotificationBatch.build`; only a batch enforces full identity.
    """

    kind: str
    title: str
    run_id: str = ""
    batch_id: str = ""
    order: int = 0
    product: str | None = None
    subtitle: str | None = None
    header_fields: tuple[NotificationField, ...] = ()
    sections: tuple[NotificationSection, ...] = ()
    actions: tuple[NotificationAction, ...] = ()
    footer_fields: tuple[NotificationField, ...] = ()
    footer: tuple[str, ...] = ()
    audit_text: str | None = None
    item_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ValueError(f"kind inválido: {self.kind!r} (esperado {sorted(VALID_KINDS)})")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title vazio")
        if not isinstance(self.order, int) or isinstance(self.order, bool) or self.order < 0:
            raise ValueError(f"order inválido: {self.order!r}")
        if self.kind == KIND_PRODUCT and not self.product:
            raise ValueError("item product exige product (stable_product_id)")
        if self.kind == KIND_SUMMARY and self.product:
            raise ValueError("item summary não pode ter product")
        if self.subtitle is not None and not isinstance(self.subtitle, str):
            raise ValueError(f"subtitle inválido: {self.subtitle!r}")

        header_fields = tuple(self.header_fields)
        sections = tuple(self.sections)
        actions = tuple(self.actions)
        footer_fields = tuple(self.footer_fields)
        footer = tuple(self.footer)
        object.__setattr__(self, "header_fields", header_fields)
        object.__setattr__(self, "sections", sections)
        object.__setattr__(self, "actions", actions)
        object.__setattr__(self, "footer_fields", footer_fields)
        object.__setattr__(self, "footer", footer)
        for field_ in (*header_fields, *footer_fields):
            if not isinstance(field_, NotificationField):
                raise ValueError(f"campo inválido: {field_!r}")
            _validate_field(field_)
        for section in sections:
            if not isinstance(section, NotificationSection):
                raise ValueError(f"section inválida: {section!r}")
        for action in actions:
            if not isinstance(action, NotificationAction):
                raise ValueError(f"action inválida: {action!r}")
        for line in footer:
            if not isinstance(line, str):
                raise ValueError(f"footer inválido: {line!r}")

        object.__setattr__(
            self,
            "item_id",
            stable_item_id(self.run_id, self.kind, product=self.product, order=self.order),
        )
        if self.audit_text is None:
            object.__setattr__(self, "audit_text", render_audit_text(self))


@dataclass(frozen=True)
class NotificationBatch:
    """Ordered batch of items for one run: optional summary (order 0)
    followed by products in deterministic input order (1..N).

    ``audit_text`` is the complete plain-text rendering of every item in
    order.  All identity is validated: contiguous orders, summary first and
    unique, one run_id, one batch_id, unique item_ids.
    """

    run_id: str
    mode: str
    batch_id: str
    items: tuple[NotificationItem, ...]
    audit_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("run_id vazio")
        if not isinstance(self.mode, str) or not self.mode:
            raise ValueError("mode vazio")
        if not isinstance(self.batch_id, str) or not self.batch_id:
            raise ValueError("batch_id vazio")
        items = tuple(self.items)
        object.__setattr__(self, "items", items)
        if not items:
            raise ValueError("batch sem itens")

        summaries = [item for item in items if item.kind == KIND_SUMMARY]
        if len(summaries) > 1:
            raise ValueError("batch com mais de um summary")
        item_ids = [item.item_id for item in items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("item_ids duplicados no batch")
        for index, item in enumerate(items):
            if item.order != index:
                raise ValueError(f"orders não contíguos: item {index} tem order {item.order}")
            if item.run_id != self.run_id:
                raise ValueError(f"run_id do item {item.item_id} difere do batch")
            if item.batch_id != self.batch_id:
                raise ValueError(f"batch_id do item {item.item_id} difere do batch")
        if summaries and items[0] is not summaries[0]:
            raise ValueError("summary tem de ser o primeiro item (order 0)")

        if self.audit_text is None:
            object.__setattr__(
                self, "audit_text", "\n\n".join(item.audit_text for item in items)
            )

    @classmethod
    def build(
        cls,
        *,
        mode: str,
        run_id: str,
        summary: NotificationItem | None = None,
        products: Sequence[NotificationItem] = (),
    ) -> "NotificationBatch":
        """Build a batch assigning deterministic identity: the summary (if
        any) becomes order 0, products keep input order (1..N).  All items
        inherit the batch run_id and batch_id; item_ids follow."""
        if summary is not None and summary.kind != KIND_SUMMARY:
            raise ValueError("summary tem de ser kind=summary")
        products = tuple(products)
        for product in products:
            if product.kind != KIND_PRODUCT:
                raise ValueError("produtos têm de ser kind=product")
        batch = stable_batch_id(run_id, mode)
        items: list[NotificationItem] = []
        first_product_order = 1 if summary is not None else 0
        if summary is not None:
            items.append(replace(summary, run_id=run_id, batch_id=batch, order=0))
        for index, product in enumerate(products, start=first_product_order):
            items.append(replace(product, run_id=run_id, batch_id=batch, order=index))
        return cls(run_id=run_id, mode=mode, batch_id=batch, items=tuple(items))
