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


def quote_found(quote: str, haystack: str | None) -> bool | None:
    """True/False when we could look, None when there was nothing to look in."""
    if not haystack:
        return None
    hay = norm(haystack)
    return all(norm(part) in hay for part in ELLIPSIS.split(quote) if part.strip())
