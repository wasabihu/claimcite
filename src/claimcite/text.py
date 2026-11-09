"""Shared text normalisation for both registry and web-page checks."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

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
    """True/False when we could look, None when there was nothing to look in.

    A quote with Chinese in it cannot be found word for word in a text with no Chinese: it is a
    translation or paraphrase, so the answer is None, not False.
    """
    if not haystack or (CJK.search(quote) and not CJK.search(haystack)):
        return None
    hay = letters(haystack)
    parts = [letters(part) for part in ELLIPSIS.split(quote)]
    return all(part in hay for part in parts if part)


def compare_titles(stated: str | None, actual: str | None) -> str:
    if not stated:
        return "not_stated"
    if not actual:
        return "unknown"
    if CJK.search(stated) and not CJK.search(actual):
        return "not_comparable"
    a, b = words(stated), words(actual)
    ratio = title_ratio(stated, actual)
    if ratio >= 0.9 or (len(a) > 20 and (a in b or b in a)):
        return "match"
    if ratio >= 0.6:
        return "partial"
    return "mismatch"


def title_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, words(a), words(b)).ratio()
