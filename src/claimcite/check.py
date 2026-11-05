"""Compare what the document says about each citation with what the registry says."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .evidence import UNKNOWN, evidence_level
from .extract import extract_references
from .models import Finding, Reference
from .sources import Lookup, LookupFailed

_CJK = re.compile(r"[\u3400-\u9fff]")
_ELLIPSIS = re.compile(r"\s*(?:…+|\.\.\.+)\s*")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower().replace("·", ".").replace("×", "x")
    s = re.sub(r"[‐-―−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def _words(s: str) -> str:
    return re.sub(r"[^\w]+", " ", _norm(s)).strip()


def compare_titles(stated: str | None, actual: str | None) -> str:
    if not stated:
        return "not_stated"
    if not actual:
        return "unknown"
    if _CJK.search(stated) and not _CJK.search(actual):
        return "not_comparable"
    a, b = _words(stated), _words(actual)
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= 0.9 or (len(a) > 20 and (a in b or b in a)):
        return "match"
    if ratio >= 0.6:
        return "partial"
    return "mismatch"


def quote_found(quote: str, haystack: str | None) -> bool | None:
    if not haystack:
        return None
    hay = _norm(haystack)
    return all(_norm(part) in hay for part in _ELLIPSIS.split(quote) if part.strip())


def check_reference(ref: Reference, lookup: Lookup) -> Finding:
    if ref.kind == "url":
        return Finding(ref, status="unchecked")

    try:
        record = lookup.doi(ref.id) if ref.kind == "doi" else lookup.pmid(ref.id)
    except LookupFailed as e:
        return Finding(ref, status="unchecked", error=str(e))
    if record is None:
        return Finding(ref, status="error", exists=False)

    if getattr(record, "retracted", False):
        return Finding(ref, status="error", exists=True, record=record, retracted=record.retraction_doi or "retracted")

    f = Finding(ref, status="ok", exists=True, record=record)
    f.title_match = compare_titles(ref.title, record.title)
    years = record.years or ([record.year] if record.year else [])
    if ref.year and years:
        # Papers have online, print and issue years; any of them, give or take one, is fine.
        f.year_match = any(abs(ref.year - y) <= 1 for y in years)
    text = " ".join(filter(None, [record.title, record.abstract]))
    f.quotes = [(q, quote_found(q, record.abstract and text)) for q in ref.quotes]
    f.evidence = (
        evidence_level(record.pub_types, record.humans, record.animals) if record.source == "pubmed" else UNKNOWN
    )

    if f.title_match == "mismatch":
        f.status = "error"
    elif f.title_match == "partial" or f.year_match is False or any(ok is False for _, ok in f.quotes):
        f.status = "warning"
    return f


def check_text(text: str, lookup: Lookup | None = None) -> list[Finding]:
    lookup = lookup or Lookup()
    refs = extract_references(text)
    lookup.prefetch(dois=[r.id for r in refs if r.kind == "doi"], pmids=[r.id for r in refs if r.kind == "pmid"])
    return [check_reference(ref, lookup) for ref in refs]
