"""
SSRF guard for attacker-controlled fetch targets (landing URLs, redirects).

``validate_target(url)`` is a total, non-raising function that decides
whether one fetch hop may be requested.  It enforces, in order:

1. string input, non-empty, no ASCII control/whitespace characters;
2. scheme is http or https;
3. no embedded userinfo (username/password);
4. a present, parseable host and a valid port (1-65535);
5. every literal or DNS-resolved address must be public unicast: ``is_global``
   and not private, loopback, link-local, reserved, multicast or unspecified;
6. a hostname whose DNS answers mix public and private addresses is
   rejected (``mixed-dns``) rather than silently using the public one.

Reason codes are stable and safe to log: they are short identifiers, never
URLs, hosts, addresses or exception text.  The resolver is a single
patchable seam, ``_resolve_addresses(host, port)``, so tests never perform
live DNS and production resolution stays confined to one function.

Residual limitation (documented, not eliminated): the preflight DNS lookup
plus the per-hop re-checks do *not* eliminate the DNS-rebinding TOCTOU — a
resolver that answers public during validation and private during the
actual connect defeats this guard.  Connection-pinning / custom TLS
transports are deliberately out of scope.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

_SAFE_SCHEMES = frozenset({"http", "https"})


def _resolve_addresses(host: str, port: int | None) -> list[str]:
    """Resolver seam: ``host`` → list of IP address strings (validation only).

    Literal IPv4/IPv6 addresses pass through unchanged (no DNS).  Hostnames
    are resolved with ``getaddrinfo`` bounded to ``SOCK_STREAM`` on the
    parsed port (or 0 when absent), so the family matches the actual fetch;
    results are deduplicated in resolution order.  Tests patch this function
    to avoid live DNS.  ``validate_target`` treats any exception from this
    seam as a DNS failure, so production callers never see one here.
    """
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, port or 0, type=socket.SOCK_STREAM)
    seen: set[str] = set()
    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    return addresses


def has_forbidden_url_characters(value: Any) -> bool:
    """Reject parser-smuggling characters before URL normalization."""
    return not isinstance(value, str) or any(
        ord(char) < 0x20 or ord(char) == 0x7F or char == " " for char in value
    )


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True only for ordinary public unicast addresses.

    ``is_global`` alone is insufficient: Python also marks multicast ranges
    and some IPv4-mapped/reserved addresses as global.  Those categories are
    never valid landing targets and are rejected explicitly.
    """
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


def validate_target(url: Any) -> tuple[bool, str]:
    """Validate one fetch target (initial landing or redirect hop) for SSRF.

    Returns ``(True, "ok")`` for a safe public http(s) target, otherwise
    ``(False, reason)`` with a stable, log-safe reason code.  Never raises
    on any input: every failure mode rejects (fail-closed).

    The reason codes are part of the contract and are safe to log verbatim:
    ``not-a-string``, ``empty``, ``control-chars``, ``invalid-url``,
    ``unsupported-scheme``, ``userinfo``, ``missing-host``, ``invalid-port``,
    ``dns-error``, ``dns-empty``, ``invalid-address``, ``non-global-address``,
    ``mixed-dns``.
    """
    if not isinstance(url, str):
        return False, "not-a-string"
    if not url:
        return False, "empty"
    # Validate before urlparse: parsers may silently strip these characters.
    if has_forbidden_url_characters(url):
        return False, "control-chars"
    try:
        parsed = urlparse(url)
    except ValueError:
        return False, "invalid-url"
    if parsed.scheme.casefold() not in _SAFE_SCHEMES:
        return False, "unsupported-scheme"
    if parsed.username is not None or parsed.password is not None:
        return False, "userinfo"
    host = parsed.hostname
    if not host:
        return False, "missing-host"
    try:
        port = parsed.port
    except ValueError:
        return False, "invalid-port"
    if port is not None and not (1 <= port <= 65535):
        return False, "invalid-port"
    try:
        addresses = _resolve_addresses(host, port)
    except Exception:  # gaierror, OSError, UnicodeError… — the seam must never crash the guard
        return False, "dns-error"
    if not addresses:
        return False, "dns-empty"
    public_flags: list[bool] = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False, "invalid-address"
        public_flags.append(_is_public_address(ip))
    if all(public_flags):
        return True, "ok"
    if any(public_flags):
        # Some answers public, some private: an attacker-controlled resolver
        # could route the real connect to the private one.  Fail closed.
        return False, "mixed-dns"
    return False, "non-global-address"
