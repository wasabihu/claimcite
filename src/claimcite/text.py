"""Shared text normalisation for both registry and web-page checks."""

from __future__ import annotations

import re
import unicodedata

CJK = re.compile(r"[\u3400-\u9fff]")
ELLIPSIS = re.compile(r"\s*(?:…+|\.\.\.+)\s*")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower().replace("·", ".").replace("×", "x")
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def words(s: str) -> str:
    return re.sub(r"[^\w]+", " ", norm(s)).strip()


def letters(s: str) -> str:
    """Only letters and digits: quotation-mark style, punctuation and spacing differ
    between a page and a faithful quote of it (“ vs ", "26 个" vs "26个", "( NSAIDs )")."""
    return re.sub(r"[\W_]+", "", norm(s))


def quote_found(quote: str, haystack: str | None) -> bool | None:
    """True/False when we could look, None when there was nothing to look in."""
    if not haystack:
        return None
    hay = letters(haystack)
    parts = [letters(part) for part in ELLIPSIS.split(quote)]
    return all(part in hay for part in parts if part)
