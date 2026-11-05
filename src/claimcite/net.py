"""The only place that touches the network. Swap `http_get` out in tests."""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import __version__

USER_AGENT = f"claimcite/{__version__} (+https://github.com/wasabihu/claimcite)"

# NCBI allows 3 requests/second without an API key; be polite to everyone.
_MIN_INTERVAL = {"eutils.ncbi.nlm.nih.gov": 0.4}
_next_slot: dict[str, float] = {}
_lock = threading.Lock()
# Web pages are fetched from many sites that did not sign up for this; one request a second each.
WEB_INTERVAL = 1.0
# Registry answers are small; anything bigger is a download we do not need in full.
MAX_BYTES = 10 * 1024 * 1024


def polite(host: str, interval: float = WEB_INTERVAL) -> None:
    """Rate-limit a host that has no explicit limit yet."""
    with _lock:
        _MIN_INTERVAL.setdefault(host, interval)


class NotFound(Exception):
    """The server answered 404."""


def http_get(url: str, timeout: float = 20.0, retries: int = 3) -> str:
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read(MAX_BYTES).decode("utf-8", errors="replace")
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
