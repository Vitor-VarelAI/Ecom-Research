"""Size limits and guards for Telegram message fragments.

The official Bot API caps a rich message at 32768 characters and a plain
HTML message at 4096 characters, in both cases *after entities parsing*
(i.e. tags removed and entities decoded — the visible text the client
renders).  These guards enforce that limit and fail loudly instead of
truncating content, so audit data is never silently cut.
"""

from __future__ import annotations

import html
import re

#: sendRichMessage rich_message.html limit (visible characters).
MAX_RICH_LENGTH = 32768
#: sendMessage text limit (visible characters, parse_mode=HTML).
MAX_HTML_LENGTH = 4096

_TAG_RE = re.compile(r"<[^>]*>")


class NotificationTooLargeError(ValueError):
    """Raised when a rendered fragment would exceed the Telegram limit.

    The fragment is never emitted and the underlying content (audit text
    included) is never truncated; the caller decides how to fail before
    delivery.
    """


def visible_text(fragment_html: str) -> str:
    """Visible text of an HTML fragment: tags removed, entities decoded.

    This is the length Telegram counts ("after entities parsing").  A value
    of ``&`` x 3000 renders as ``&amp;`` x 3000 (15000 raw characters) but
    counts as 3000 visible characters.
    """
    return html.unescape(_TAG_RE.sub("", fragment_html))


def guard_size(fragment_html: str, limit: int, *, renderer: str) -> None:
    """Raise :class:`NotificationTooLargeError` when the visible text
    exceeds ``limit`` (exactly ``limit`` is still accepted)."""
    length = len(visible_text(fragment_html))
    if length > limit:
        raise NotificationTooLargeError(
            f"fragmento {renderer} com {length} caracteres visíveis "
            f"excede o limite de {limit}"
        )
