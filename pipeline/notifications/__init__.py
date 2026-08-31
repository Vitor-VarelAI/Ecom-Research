"""Telegram notification models and renderers — slice 1.

Public surface:

* models with deterministic identity and full audit text
  (:class:`NotificationBatch`, :class:`NotificationItem`,
  :class:`NotificationSection`, :class:`NotificationField`);
* the Rich (``sendRichMessage``) and normal-HTML (``sendMessage``) renderers,
  returning payload fragments without chat_id/token/reply_markup;
* size guards (Rich 32768 / HTML 4096 visible characters);
* absence placeholders selected by field meaning;
* a semantic-parity helper proving both renderers preserve the same
  meaningful title/section/field/value text.

No network calls are made anywhere in this package; live acceptance of Rich
messages in a separate test chat is still pending and out of scope.
"""

from pipeline.notifications.limits import (
    MAX_HTML_LENGTH,
    MAX_RICH_LENGTH,
    NotificationTooLargeError,
    guard_size,
    visible_text,
)
from pipeline.notifications.model import (
    KIND_PRODUCT,
    KIND_SUMMARY,
    NotificationAction,
    NotificationBatch,
    NotificationField,
    NotificationItem,
    NotificationSection,
    render_audit_text,
    stable_batch_id,
    stable_item_id,
    stable_product_id,
)
from pipeline.notifications.builder import build_batch, notification_snapshot
from pipeline.notifications.parity import (
    assert_semantic_parity,
    content_tokens,
    semantic_parity,
    semantic_tokens,
)
from pipeline.notifications.placeholders import (
    DOCUMENTATION_MISSING,
    MISSING,
    NOT_APPLICABLE,
    PENDING_DOCUMENTATION,
    RESEARCH_NEEDED,
    SUPPLIER_MISSING,
    TO_VALIDATE,
    placeholder_for,
    resolve_value,
)
from pipeline.notifications.render_html import render_html, render_html_batch
from pipeline.notifications.render_rich import render_rich, render_rich_batch

__all__ = [
    "KIND_PRODUCT",
    "KIND_SUMMARY",
    "MAX_HTML_LENGTH",
    "MAX_RICH_LENGTH",
    "MISSING",
    "TO_VALIDATE",
    "DOCUMENTATION_MISSING",
    "RESEARCH_NEEDED",
    "SUPPLIER_MISSING",
    "PENDING_DOCUMENTATION",
    "NOT_APPLICABLE",
    "NotificationAction",
    "NotificationBatch",
    "NotificationField",
    "NotificationItem",
    "NotificationSection",
    "NotificationTooLargeError",
    "assert_semantic_parity",
    "build_batch",
    "content_tokens",
    "guard_size",
    "notification_snapshot",
    "placeholder_for",
    "render_audit_text",
    "render_html",
    "render_html_batch",
    "render_rich",
    "render_rich_batch",
    "resolve_value",
    "semantic_parity",
    "semantic_tokens",
    "stable_batch_id",
    "stable_item_id",
    "stable_product_id",
    "visible_text",
]
