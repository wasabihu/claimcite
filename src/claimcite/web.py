"""Check web-page citations: is the page alive, and does it contain the quoted text?

Deliberately conservative: a dead link or a missing quote is a *warning*, never an
error. Pages change, some sites block bots, and we must not cry wolf.
"""

from __future__ import annotations

from collections.abc import Callable
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

from .models import Reference, WebResult
from .net import USER_AGENT, NotFound
from .text import quote_found


class _TextExtractor(HTMLParser):
    """Collect visible text, skipping script/style/noscript/template bodies."""

    _SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._depth = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if not self._depth:
            self.parts.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def _robots_url(url: str) -> str:
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, "/robots.txt", "", "", ""))


def _robots_allows(url: str, http: Callable[[str], str]) -> bool:
    """Only an explicit disallow blocks us; a missing robots.txt never does."""
    try:
        body = http(_robots_url(url))
    except Exception:  # noqa: BLE001 - an unreachable robots.txt must not block the check
        return True
    parser = RobotFileParser()
    try:
        parser.parse(body.splitlines())
    except Exception:  # noqa: BLE001 - malformed robots.txt is treated as absent
        return True
    return parser.can_fetch(USER_AGENT, url)


def check_page(ref: Reference, http: Callable[[str], str]) -> WebResult:
    unknown = [(q, None) for q in ref.quotes]
    if not _robots_allows(ref.id, http):
        return WebResult(state="blocked", quotes=unknown)
    try:
        body = http(ref.id)
    except NotFound:
        return WebResult(state="dead", status_code=404, quotes=unknown)
    except Exception as e:  # noqa: BLE001 - one bad page must not sink the whole report
        return WebResult(state="failed", detail=type(e).__name__, quotes=unknown)

    parser = _TextExtractor()
    try:
        parser.feed(body)
        page_text = parser.text()
    except Exception:  # noqa: BLE001 - malformed HTML: fall back to the raw text
        page_text = body
    quotes = [(q, quote_found(q, page_text)) for q in ref.quotes]
    return WebResult(state="alive", quotes=quotes)
