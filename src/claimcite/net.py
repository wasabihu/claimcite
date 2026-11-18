"""The only place that touches the network. Swap `http_get` out in tests."""

from __future__ import annotations

import codecs
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import __version__

USER_AGENT = f"claimcite/{__version__} (+https://github.com/wasabihu/claimcite)"

# NCBI allows 3 requests/second without an API key; be polite to everyone.
# Crossref's public pool: one request at a time, one list query a second (x-concurrency-limit: 1,
# x-rate-limit-limit: 1 on /works?filter=…). Lookup sends it one batch after another.
# arXiv asks automated harvesters for at least 3 seconds between requests.
_MIN_INTERVAL = {
    "eutils.ncbi.nlm.nih.gov": 0.4,
    "api.crossref.org": 1.0,
    "export.arxiv.org": 3.0,
    "www.ebi.ac.uk": 0.4,
}
_next_slot: dict[str, float] = {}
_lock = threading.Lock()
# Web pages are fetched from many sites that did not sign up for this; one request a second each.
WEB_INTERVAL = 1.0
# Registry answers are small; anything bigger is a download we do not need in full.
MAX_BYTES = 10 * 1024 * 1024
_PEEK_BYTES = 1024
_BINARY_TYPES = (
    "application/pdf",
    "application/zip",
    "application/gzip",
    "application/x-gzip",
    "application/octet-stream",
    "image/",
    "audio/",
    "video/",
)
_HEADER_CHARSET = re.compile(r"charset\s*=\s*[\"']?([a-zA-Z0-9._-]+)", re.IGNORECASE)
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*[\"']?\s*([a-zA-Z0-9._-]+)", re.IGNORECASE)
_DEFAULT_URLOPEN = urllib.request.urlopen


def polite(host: str, interval: float = WEB_INTERVAL) -> None:
    """Rate-limit a host that has no explicit limit yet."""
    with _lock:
        _MIN_INTERVAL.setdefault(host, interval)


class NotFound(Exception):
    """The server answered 404."""


_FAKE_IP_V4 = ipaddress.ip_network("198.18.0.0/15")


def _is_global_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    return ip.is_global


def _is_allowed_resolved_ip(addr: str) -> bool:
    """Allow global IPs, plus RFC 2544 benchmarking range 198.18.0.0/15 used by TUN Fake-IP DNS."""
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    return ip.is_global or (isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_V4)


def _ensure_safe_url(url: str, *, resolve_dns: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise urllib.error.URLError(f"unsupported URL scheme: {parsed.scheme or 'none'}")
    host = (parsed.hostname or "").strip().lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        raise urllib.error.URLError(f"disallowed host: {host or 'empty'}")
    try:
        ip_ok = _is_global_ip(host)
    except ValueError:
        ip_ok = None
    if ip_ok is False:
        raise urllib.error.URLError(f"non-public IP blocked: {host}")
    if ip_ok is None and resolve_dns:
        infos = socket.getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
        if not infos or not all(_is_allowed_resolved_ip(info[4][0]) for info in infos):
            raise urllib.error.URLError(f"host resolves to non-public IP: {host}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _ensure_safe_url(newurl, resolve_dns=(urllib.request.urlopen is _DEFAULT_URLOPEN))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SAFE_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def _normalise_charset(name: str | None) -> str | None:
    if not name:
        return None
    low = name.strip().lower()
    if low in ("gb2312", "gbk", "gb18030"):
        return "gb18030"
    try:
        return codecs.lookup(low).name
    except LookupError:
        return None


def _decode_body(raw: bytes, content_type: str) -> str:
    declared = None
    if m := _HEADER_CHARSET.search(content_type):
        declared = _normalise_charset(m.group(1))
    if not declared and (m_meta := _META_CHARSET.search(raw[:2048])):
        declared = _normalise_charset(m_meta.group(1).decode("ascii", errors="ignore"))
    candidates = [c for c in (declared, "utf-8", "gb18030") if c]
    seen: set[str] = set()
    for enc in candidates:
        if enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_response(resp) -> str:
    headers = getattr(resp, "headers", None)
    ctype = (headers.get("Content-Type", "") if headers and hasattr(headers, "get") else "") or ""
    ctype_low = ctype.lower()
    head = resp.read(_PEEK_BYTES)
    trimmed = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if trimmed.startswith(b"%PDF-") or b"\x00" in head:
        return head.decode("latin-1")
    if any(ctype_low.startswith(prefix) for prefix in _BINARY_TYPES):
        return "\x00" + head.decode("latin-1")
    rest = resp.read(MAX_BYTES - len(head))
    return _decode_body(head + rest, ctype)


def http_get(url: str, timeout: float = 20.0, retries: int = 3) -> str:
    use_real_net = urllib.request.urlopen is _DEFAULT_URLOPEN
    _ensure_safe_url(url, resolve_dns=use_real_net)
    host = urlparse(url).netloc
    for attempt in range(retries + 1):
        with _lock:  # reserve the next free slot for this host, safe across threads
            now = time.monotonic()
            slot = max(now, _next_slot.get(host, 0))
            _next_slot[host] = slot + _MIN_INTERVAL.get(host, 0)
        if slot > now:
            time.sleep(slot - now)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            opener = _SAFE_OPENER.open if use_real_net else urllib.request.urlopen
            with opener(req, timeout=timeout) as resp:
                return _read_response(resp)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                retry_after = e.headers.get("Retry-After", "") if e.headers else ""
                time.sleep(max(2**attempt, int(retry_after) if retry_after.isdigit() else 0))
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")
