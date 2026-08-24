"""Markdown rendering of the emitted monetization payload (pack section).

Two escaping tiers keep the section both safe and readable:

* ``md_escape`` — full CommonMark punctuation escaping for free-text
  operator strings (merchant, market, disclosure): a configured value can
  never inject emphasis, links, code or block structure.  Whitespace runs
  collapse to a single space so multi-line values cannot break bullets.
* ``url_escape`` — targeted neutralization of raw HTML/code plus Markdown
  link/emphasis delimiters for validated public URLs and bounded identifiers
  (offer_id, role, env NAME), while keeping ``_``/``-``/``.`` readable.

The section is concise: fixed disclosure, market, then per-offer
role/merchant/commission/cookie and the public destination/evidence URLs.
No tracked affiliate URL value exists in the payload to render — only the
variable NAME (``affiliate_link_ref``).
"""

from __future__ import annotations

from typing import Any

# CommonMark escapable punctuation; ``\\`` first so real backslashes cannot
# re-escape the escaping.
_ESCAPABLE = set("\\`*_{}[]()#+-.!|<>")
# Structural/HTML/link-neutralizing chars for validated URLs and bounded ids.
_URL_ESCAPABLE = set("\\`<>[]()*")


def md_escape(value: Any) -> str:
    """Full escape for free-text operator strings (safe Markdown inline)."""
    text = " ".join(str(value).split())
    return "".join(f"\\{char}" if char in _ESCAPABLE else char for char in text)


def url_escape(value: Any) -> str:
    """Minimal escape for validated URLs and bounded identifiers."""
    text = " ".join(str(value).split())
    return "".join(f"\\{char}" if char in _URL_ESCAPABLE else char for char in text)


def _commission_text(offer: dict[str, Any]) -> str:
    value = offer["commission_value"]
    text = f"{value:g}"
    if offer["commission_type"] == "percentage":
        text += "%"
        if offer.get("commission_currency"):
            text += f" ({offer['commission_currency']})"
    else:
        # Fixed commissions always carry a validated 3-letter currency.
        text = f"{offer['commission_currency']} {text}"
    return text


def render_markdown(monetization: dict[str, Any]) -> str:
    """The ``## Monetização afiliada`` section, or an empty string when the
    payload is None (default behavior: no monetization section at all)."""
    if not monetization:
        return ""
    lines = ["## Monetização afiliada", md_escape(monetization["disclosure"])]
    lines.append(f"- Mercado: {md_escape(monetization['market'])}")
    for offer in monetization["offers"]:
        lines.append(
            f"- Oferta: {url_escape(offer['offer_id'])} — papel: {url_escape(offer['role'])}"
        )
        lines.append(f"  - Merchant: {md_escape(offer['merchant'])}")
        lines.append(f"  - Comissão: {_commission_text(offer)}")
        lines.append(f"  - Cookie: {offer['cookie_duration_days']} dias")
        lines.append(
            f"  - Destino: {url_escape(offer['destination_url'])} "
            f"(ref: {url_escape(offer['affiliate_link_ref'])})"
        )
        lines.append(f"  - Evidência: {url_escape(offer['evidence_url'])}")
    return "\n".join(lines)
