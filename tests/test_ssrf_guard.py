"""
Tests for the SSRF guard (``pipeline/adapters/ssrf_guard.py``).

No live network: literal IPv4/IPv6 addresses are validated without DNS, and
every hostname-based case patches the resolver seam ``_resolve_addresses``
to a fixed answer or a failure, so no test performs a real lookup.
"""
import socket
from unittest.mock import patch

import pytest

from pipeline.adapters import ssrf_guard
from pipeline.adapters.ssrf_guard import validate_target


def _url_for(addr: str) -> str:
    """Wrap an IPv6 literal in brackets; IPv4 literals pass through."""
    return f"https://[{addr}]/x" if ":" in addr else f"https://{addr}/x"


class TestInputShape:
    """Non-string, empty and control/whitespace input never reaches parsing."""

    def test_non_string_rejected(self):
        for bad in (None, 12345, 3.14, ["https://example.com/"], b"https://example.com/"):
            ok, reason = validate_target(bad)
            assert ok is False
            assert reason == "not-a-string"

    def test_empty_rejected(self):
        assert validate_target("") == (False, "empty")

    def test_whitespace_and_control_chars_rejected(self):
        for bad in (
            "https://exa mple.com/",
            "https://example.com/\n",
            "https://example.com/\t",
            "https://example.com/\x00",
            "https://example.com/\x1f",
            "https://example.com/\x7f",
            "   ",
        ):
            ok, reason = validate_target(bad)
            assert ok is False
            assert reason == "control-chars"

    def test_never_raises_on_garbage(self):
        for bad in (
            None,
            3.14,
            b"bytes",
            "\ud800",
            "https://\ud800/",
            "http://[::1",
            "https://a b.com/",
            "ftp://x",
            "",
            "https://example.com:99999/x",
        ):
            ok, reason = validate_target(bad)
            assert ok is False
            assert isinstance(reason, str) and reason


class TestSchemeAndUserinfo:
    def test_non_http_schemes_rejected(self):
        for bad in (
            "ftp://example.com/x",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "mailto:a@b.c",
            "gopher://example.com/1",
            "data:text/html,x",
        ):
            ok, reason = validate_target(bad)
            assert ok is False
            assert reason == "unsupported-scheme"

    def test_scheme_case_insensitive_accepted(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=["93.184.216.34"]):
            assert validate_target("HTTPS://EXAMPLE.COM/x") == (True, "ok")
            assert validate_target("http://example.com/x") == (True, "ok")

    def test_userinfo_rejected(self):
        for bad in (
            "https://user@example.com/",
            "https://user:pass@example.com/",
            "https://user:pass@example.com:8443/x",
            "https://user%40x:pass@example.com/",
            "https://@example.com/",  # vazio mas ainda userinfo syntax
        ):
            ok, reason = validate_target(bad)
            assert ok is False
            assert reason == "userinfo"


class TestHostAndPort:
    def test_missing_host_rejected(self):
        for bad in ("https:///path", "http://", "https://?x=1", "https://#frag"):
            ok, reason = validate_target(bad)
            assert ok is False
            assert reason == "missing-host"

    def test_invalid_ports_rejected(self):
        for bad in (
            "https://example.com:99999/x",
            "https://example.com:abc/x",
            "https://example.com:0/x",
            "https://example.com:-1/x",
        ):
            ok, reason = validate_target(bad)
            assert ok is False
            assert reason == "invalid-port"

    def test_malformed_ipv6_brackets_rejected(self):
        assert validate_target("https://[::1") == (False, "invalid-url")

    def test_custom_valid_port_accepted(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=["93.184.216.34"]):
            assert validate_target("https://example.com:8443/x") == (True, "ok")

    def test_resolver_receives_host_and_port(self):
        seen = {}

        def fake_resolve(host, port):
            seen["host"] = host
            seen["port"] = port
            return ["93.184.216.34"]

        with patch.object(ssrf_guard, "_resolve_addresses", side_effect=fake_resolve):
            assert validate_target("https://example.com:8443/x") == (True, "ok")
        assert seen == {"host": "example.com", "port": 8443}


class TestLiteralAddresses:
    """Literais resolvem sem DNS: o seam devolve o próprio endereço."""

    @pytest.mark.parametrize(
        "addr",
        [
            "127.0.0.1",  # loopback
            "10.0.0.1",  # private 10/8
            "172.16.0.1",  # private 172.16/12
            "192.168.1.1",  # private 192.168/16
            "169.254.169.254",  # link-local / metadata IP
            "0.0.0.0",  # unspecified
            "100.64.0.1",  # CGNAT
            "192.0.2.1",  # TEST-NET-1 (documentação)
            "198.51.100.1",  # TEST-NET-2
            "203.0.113.1",  # TEST-NET-3
            "240.0.0.1",  # reservado
            "224.0.0.1",  # IPv4 multicast (is_global=True na stdlib)
            "239.255.255.250",  # IPv4 multicast administratively scoped
            "::1",  # IPv6 loopback
            "::ffff:127.0.0.1",  # IPv4-mapped loopback
            "::ffff:8.8.8.8",  # IPv4-mapped reservado, apesar do IPv4 público
            "fe80::1",  # IPv6 link-local
            "fc00::1",  # IPv6 ULA
            "ff02::1",  # IPv6 multicast (is_global=True na stdlib)
            "2001:db8::1",  # IPv6 documentation
        ],
    )
    def test_non_public_literal_rejected(self, addr):
        ok, reason = validate_target(_url_for(addr))
        assert ok is False
        assert reason == "non-global-address"

    @pytest.mark.parametrize(
        "addr",
        [
            "1.1.1.1",
            "8.8.8.8",
            "93.184.216.34",
            "2606:4700:4700::1111",
            "2001:4860:4860::8888",
        ],
    )
    def test_public_literal_accepted(self, addr):
        assert validate_target(_url_for(addr)) == (True, "ok")


class TestDnsResolution:
    """Hostnames resolvem pelo seam — nunca DNS real."""

    def test_public_dns_accepted(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=["93.184.216.34"]):
            assert validate_target("https://example.com/") == (True, "ok")

    def test_private_dns_rejected(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=["10.0.0.1"]):
            ok, reason = validate_target("https://internal.example/")
            assert ok is False
            assert reason == "non-global-address"

    def test_mixed_public_private_dns_rejected(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=["93.184.216.34", "10.0.0.1"]):
            ok, reason = validate_target("https://mixed.example/")
            assert ok is False
            assert reason == "mixed-dns"

    def test_empty_dns_answer_rejected(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=[]):
            ok, reason = validate_target("https://no-answers.example/")
            assert ok is False
            assert reason == "dns-empty"

    def test_dns_error_rejected(self):
        with patch.object(ssrf_guard, "_resolve_addresses", side_effect=socket.gaierror("getaddrinfo failed")):
            ok, reason = validate_target("https://nxdomain.example/")
            assert ok is False
            assert reason == "dns-error"

    def test_resolver_exception_never_propagates(self):
        with patch.object(ssrf_guard, "_resolve_addresses", side_effect=UnicodeError("host não codificável")):
            ok, reason = validate_target("https://bad-host.example/")
            assert ok is False
            assert reason == "dns-error"

    def test_resolved_invalid_address_rejected(self):
        with patch.object(ssrf_guard, "_resolve_addresses", return_value=["not-an-ip"]):
            ok, reason = validate_target("https://weird.example/")
            assert ok is False
            assert reason == "invalid-address"
