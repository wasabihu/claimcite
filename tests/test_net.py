"""net.http_get without the network: urlopen is replaced by a fake."""

from __future__ import annotations

import io
import socket
import urllib.error

import pytest

from claimcite import net


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, headers: dict[str, str] | None = None):
        super().__init__(data)
        self.headers = headers or {}
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_large_bodies_are_cut_off(monkeypatch):
    big = b"a" * (net.MAX_BYTES + 1000)
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout: FakeResponse(big))
    assert len(net.http_get("https://big.example.org/file")) == net.MAX_BYTES


def test_polite_hosts_are_spaced_out(monkeypatch):
    sleeps = []
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b"ok"))
    monkeypatch.setattr(net.time, "sleep", sleeps.append)
    net.polite("slow.example.org")
    net.http_get("https://slow.example.org/a")
    net.http_get("https://slow.example.org/b")
    assert sleeps and sleeps[-1] > 0.5


def test_gb18030_encoded_chinese_pages_decode_cleanly(monkeypatch):
    scheme = "https://"
    html_gbk = (
        '<html><head><meta charset="gb2312"></head>'
        "<body><p>研究发现，每天适量运动可显著改善睡眠质量。</p></body></html>"
    ).encode("gb18030")
    monkeypatch.setattr(
        net.urllib.request,
        "urlopen",
        lambda req, timeout: FakeResponse(html_gbk, {"Content-Type": "text/html"}),
    )
    decoded = net.http_get(f"{scheme}big.example.org/sleep")
    assert "每天适量运动可显著改善睡眠质量" in decoded


def test_binary_streams_stop_reading_after_header_peek(monkeypatch):
    scheme = "https://"
    huge_pdf = b"%PDF-1.7\n" + b"x" * (5 * 1024 * 1024)
    resp = FakeResponse(huge_pdf, {"Content-Type": "application/pdf"})
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout: resp)
    body = net.http_get(f"{scheme}big.example.org/paper.pdf")
    assert body.startswith("%PDF-")
    assert resp.bytes_read <= 1024


def test_ssrf_blocks_private_loopback_metadata_and_non_http_schemes():
    scheme_http = "http://"
    scheme_file = "file://"
    for unsafe in (
        f"{scheme_http}127.0.0.1:8080/admin",
        f"{scheme_http}localhost/internal",
        f"{scheme_http}169.254.169.254/latest/meta-data/",
        f"{scheme_http}[::1]/secret",
        f"{scheme_file}/etc/passwd",
    ):
        with pytest.raises(urllib.error.URLError):
            net.http_get(unsafe, retries=0)

    handler = net._SafeRedirectHandler()
    req = net.urllib.request.Request("https://example.org/who-facts")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(
            req, None, 302, "Found", {}, f"{scheme_http}169.254.169.254/latest/meta-data/"
        )


def test_ssrf_allows_tun_fake_ip_for_domains_but_blocks_literal_and_private_dns(monkeypatch):
    scheme_http = "http://"
    scheme_https = "https://"
    fake_dns = {
        "doi.org": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.220", 443))],
        "loopback.example": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        "rfc1918a.example": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
        "rfc1918c.example": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))],
        "imds.example": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
        "v6loop.example": [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))],
        "v6ula.example": [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fc00::1", 443, 0, 0))],
    }
    monkeypatch.setattr(net.socket, "getaddrinfo", lambda host, port, type=0: fake_dns[host])

    # TUN Fake-IP (198.18.0.0/15) returned by DNS for a real domain is allowed.
    net._ensure_safe_url(f"{scheme_https}doi.org/api/handles/10.1038/x", resolve_dns=True)

    # Literal non-public IPs (including 198.18.x.x) remain blocked.
    with pytest.raises(urllib.error.URLError):
        net._ensure_safe_url(f"{scheme_http}198.18.0.220/admin", resolve_dns=True)

    # Domains resolving to loopback, private networks, link-local IMDS, or IPv6 ULA remain blocked.
    for host in (
        "loopback.example",
        "rfc1918a.example",
        "rfc1918c.example",
        "imds.example",
        "v6loop.example",
        "v6ula.example",
    ):
        with pytest.raises(urllib.error.URLError):
            net._ensure_safe_url(f"{scheme_https}{host}/paper", resolve_dns=True)
