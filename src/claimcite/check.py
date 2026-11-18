"""Compare what the document says about each citation with what the registry says."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

from .evidence import UNKNOWN, evidence_level
from .extract import extract_references
from .models import Finding, Record, Reference
from .sources import Lookup, LookupFailed
from .text import CJK, compare_titles, quote_found
from .web import check_page

__all__ = ["check_documents", "check_reference", "check_text", "compare_titles", "quote_found"]

_RETRACTED_PREFIX = re.compile(
    r"^(?:\[?\s*retracted(?:\s+article)?\s*\]?\s*[:—–-]\s*|retracted\s*:\s*)",
    re.IGNORECASE,
)


def check_reference(ref: Reference, lookup: Lookup, web: bool = True, search: bool = True) -> Finding:
    if ref.kind == "text":
        return _check_text_reference(ref, lookup, search)
    if ref.kind == "url" and not web:
        return Finding(ref, status="unchecked")
    if ref.kind == "url":
        page_http = getattr(lookup, "web_http", getattr(lookup, "http", None))
        result = check_page(ref, page_http)
        status = {"alive": "ok", "dead": "warning", "blocked": "unchecked", "failed": "unchecked"}[
            result.state
        ]
        if result.state == "alive" and any(ok is False for _, ok in result.quotes):
            status = "warning"
        return Finding(ref, status=status, web=result)

    try:
        if ref.kind == "doi":
            record = lookup.doi(ref.id)
        elif ref.kind == "pmid":
            record = lookup.pmid(ref.id)
        else:
            record = lookup.arxiv(ref.id)
    except LookupFailed as e:
        return Finding(ref, status="unchecked", error=str(e))
    if record is None:
        return Finding(ref, status="error", exists=False)
    return _compare(Finding(ref, status="ok", exists=True, record=record), lookup)


def _check_text_reference(ref: Reference, lookup: Lookup, search: bool) -> Finding:
    if CJK.search(ref.title or ref.context):
        return Finding(ref, status="unchecked", note="cjk")
    if not search:
        return Finding(ref, status="unchecked", note="search_off")
    if not ref.title:
        return Finding(ref, status="unchecked", note="no_title")
    try:
        hit = lookup.find(ref)
    except LookupFailed as e:
        return Finding(ref, status="unchecked", error=str(e))
    if hit is None:
        if ref.book:  # most books are in neither registry: not finding one says nothing
            return Finding(ref, status="unchecked", note="book")
        # Not an error: reports and reworded titles are missing from both registries too.
        return Finding(ref, status="warning", note="not_found")
    via, record = hit
    full: Record | None = None
    try:
        if record.doi:
            full = lookup.doi(record.doi)
        elif record.pmid:
            full = lookup.pmid(record.pmid)
    except LookupFailed:
        pass  # the search result already has the title and year
    f = Finding(ref, status="ok", exists=True, record=full or record, found_via=via)
    return _compare(f, lookup)


def _compare(f: Finding, lookup: Lookup | None = None) -> Finding:
    ref, record = f.ref, f.record
    f.title_match = compare_titles(ref.title, record.title)
    f.retracted = bool(record.retracted)
    if f.retracted and record.title and f.title_match in ("partial", "mismatch"):
        # Publishers prepend "RETRACTED: " to the registered title after retraction.
        cleaned_title = _RETRACTED_PREFIX.sub("", record.title).strip()
        if cleaned_title and cleaned_title != record.title:
            f.title_match = compare_titles(ref.title, cleaned_title)
    years = record.years or ([record.year] if record.year else [])
    if ref.year and years:
        # Papers have online, print and issue years; any of them, give or take one, is fine.
        f.year_match = any(abs(ref.year - y) <= 1 for y in years)
    text = " ".join(filter(None, [record.title, record.abstract]))
    f.quotes = [(q, quote_found(q, record.abstract and text)) for q in ref.quotes]
    if (
        f.quotes
        and any(ok is False or (ok is None and not record.abstract and not CJK.search(q)) for q, ok in f.quotes)
        and callable(getattr(lookup, "fulltext", None))
    ):
        ft = lookup.fulltext(record)
        if ft:
            f.fulltext_checked = True
            f.quotes = [(q, ok if ok is True else quote_found(q, ft)) for q, ok in f.quotes]
    f.evidence = (
        evidence_level(record.pub_types, record.humans, record.animals)
        if record.source == "pubmed"
        else UNKNOWN
    )

    if f.retracted or f.title_match == "mismatch":
        f.status = "error"
    elif f.title_match == "partial" or f.year_match is False or any(ok is False for _, ok in f.quotes):
        f.status = "warning"
    return f


def check_documents(
    texts: dict[str, str],
    lookup: Lookup | None = None,
    web: bool = True,
    search: bool = True,
) -> dict[str, list[Finding]]:
    """Verify multiple documents in two passes: extract and batch-prefetch across all files first."""
    lookup = lookup or Lookup()
    refs_by_path = {path: extract_references(text) for path, text in texts.items()}
    all_refs = [r for refs in refs_by_path.values() for r in refs]
    if not all_refs:
        return {path: [] for path in texts}

    dois = [r.id for r in all_refs if r.kind == "doi"]
    pmids = [r.id for r in all_refs if r.kind == "pmid"]
    arxivs = [r.id for r in all_refs if r.kind == "arxiv"]
    if search:
        # One search at a time (Crossref's limit); Lookup.find memoizes repeated contexts.
        for r in all_refs:
            if r.kind == "text" and r.title and not CJK.search(r.title or r.context):
                try:
                    hit = lookup.find(r)
                except LookupFailed:
                    continue
                if hit and hit[1].doi:
                    dois.append(hit[1].doi)
    lookup.prefetch(dois=dois, pmids=pmids, arxivs=arxivs)

    with ThreadPoolExecutor(max_workers=lookup.workers) as pool:
        flat_findings = list(pool.map(lambda ref: check_reference(ref, lookup, web, search), all_refs))

    out: dict[str, list[Finding]] = {}
    idx = 0
    for path, refs in refs_by_path.items():
        nxt = idx + len(refs)
        out[path] = flat_findings[idx:nxt]
        idx = nxt
    return out


def check_text(
    text: str, lookup: Lookup | None = None, web: bool = True, search: bool = True
) -> list[Finding]:
    return check_documents({"": text}, lookup=lookup, web=web, search=search)[""]
