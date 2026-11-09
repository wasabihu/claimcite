"""Find citations in plain text / Markdown and the metadata the author wrote next to them.

Supported citation forms:
  - DOI: https://doi.org/10.x/y, doi:10.x/y, or a bare 10.x/y
  - PMID: "PMID: 123" or https://pubmed.ncbi.nlm.nih.gov/123
  - any other http(s) link (fetched and checked for availability and quoted text)

The text between two citations on the same line is treated as the description of the
second one, e.g. ``Author (2020). Title. Journal：「quoted words」. <https://doi.org/...>``

Numbered citations work too: a quote in the body followed by ``[1]``, ``[^note]``, ``[1, 2]`` or ``[1-3]``
is checked against the reference-list entry with that label (``[1] …``, ``1. …`` or ``[^note]: …``).
"""

from __future__ import annotations

import re

from .models import Reference

_ASCII_TOKEN = r"[^\s<>\"'\u0080-\uffff]+"
# No re.I here: case-folding would make the non-ASCII exclusion also drop "s" and "k" (ſ, K).
DOI_RE = re.compile(r"(?i:https?://(?:dx\.)?doi\.org/|\bdoi:\s*)?\b(10\.\d{4,9}/" + _ASCII_TOKEN + ")")
PMID_RE = re.compile(
    r"\bPMID:?\s*(\d{1,9})\b|https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})/?", re.IGNORECASE
)
URL_RE = re.compile(r"https?://" + _ASCII_TOKEN)

YEAR_RE = re.compile(r"\((\d{4})(?:-\d{1,2}(?:-\d{1,2})?)?[a-z]?\)")
TITLE_RE = re.compile(r"\(\d{4}[^)]*\)\.\s*(.+?)(?:\.\s|\.?\s*$|[：:]\s*[「“\"])")
# "Journal of X, 41(2), 181-202": the author gave the journal, not the title.
JOURNAL_ONLY = re.compile(r"^[^.?!]{3,150},\s*\d+\s*(?:\(\d+\))?\s*(?:,\s*[eE]?\d+(?:[-–]\d+)?)?$")
# "Is it true? Psychological Science, 26": title ends at the question mark.
TITLE_THEN_JOURNAL = re.compile(r"^(.+?[?!])\s+[A-Z][^?!]*,\s*\d+.*$")
QUOTE_RES = [re.compile(r"「(.+?)」"), re.compile(r"“(.+?)”"), re.compile(r"\"([^\"]{4,}?)\"")]
LEADING_NOISE = re.compile(
    r"^(?:\s|[；;,，、]|（[^）]*）|\([^)]*\)(?=\s*[；;])|[-*]\s*|来源[：:]|Sources?:)+", re.IGNORECASE
)

# A reference-list entry: "[1] …", "[^note]: …", "1. …" or "1) …" at the start of a line.
DEF_RE = re.compile(r"^\s*(?:\[(\^?[\w-]+)\]:?|(\d{1,3})[.)])\s+")
# A marker in the body: [1], [^note], [1, 2], [1-3]. Not a Markdown link "[x](…)", "[x][y]" or "[x]:".
MARK_RE = re.compile(r"\[(\^[\w-]+|\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)\](?![(:\[])")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
SENTENCE_END = re.compile(r"[。！？!?]|\.(?=\s)")

_TRAILING = ".,;:!?"


def _clean_id(raw: str) -> str:
    raw = raw.rstrip(_TRAILING)
    while raw.endswith(")") and raw.count(")") > raw.count("("):
        raw = raw[:-1].rstrip(_TRAILING)
    return raw


def _describe(segment: str) -> tuple[int | None, str | None, list[str], str]:
    seg = LEADING_NOISE.sub("", segment).rstrip(" <[(")
    year_m = YEAR_RE.search(seg)
    title_m = TITLE_RE.search(seg)
    title = title_m.group(1).strip().rstrip(".") if title_m else None
    if title and JOURNAL_ONLY.match(title):
        title = None
    elif title and (m := TITLE_THEN_JOURNAL.match(title)):
        title = m.group(1)
    quotes = []
    for rx in QUOTE_RES:
        quotes += [q.strip() for q in rx.findall(seg)]
    if title and any(title == q or q in title for q in quotes):
        title = None
    return (int(year_m.group(1)) if year_m else None), title or None, quotes, seg.strip()


def extract_references(text: str) -> list[Reference]:
    refs: list[Reference] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        hits: list[tuple[int, int, str, str]] = []  # start, end, kind, id
        for m in DOI_RE.finditer(line):
            hits.append((m.start(), m.end(), "doi", _clean_id(m.group(1)).lower()))
        for m in PMID_RE.finditer(line):
            if not any(s <= m.start() < e for s, e, *_ in hits):
                hits.append((m.start(), m.end(), "pmid", m.group(1) or m.group(2)))
        for m in URL_RE.finditer(line):
            if not any(s <= m.start() < e or m.start() <= s < m.end() for s, e, *_ in hits):
                hits.append((m.start(), m.end(), "url", _clean_id(m.group(0))))
        hits.sort()
        prev_end = 0
        for start, end, kind, ident in hits:
            year, title, quotes, ctx = _describe(line[prev_end:start])
            refs.append(Reference(kind, ident, lineno, year, title, quotes, ctx))
            prev_end = end
    _attach_numbered_quotes(text, refs)
    return refs


def _labels(raw: str) -> list[str]:
    """``1-3`` -> 1, 2, 3; ``1, 4`` -> 1, 4; ``^note`` -> ^note."""
    if raw.startswith("^"):
        return [raw]
    out: list[str] = []
    for part in re.split(r"\s*,\s*", raw):
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", part)
        if m and 0 < int(m[2]) - int(m[1]) < 50:
            out += [str(i) for i in range(int(m[1]), int(m[2]) + 1)]
        else:
            out.append(part.strip())
    return out


def _quotes_before(line: str, start: int, end: int) -> list[str]:
    """Quotes in the sentence that the marker at ``end`` closes, not earlier than ``start``."""
    spans = sorted(
        (m.start(), m.end(), m.group(1).strip()) for rx in QUOTE_RES for m in rx.finditer(line, start, end)
    )
    for m in SENTENCE_END.finditer(line, start, end):
        inside = any(s <= m.start() < e for s, e, _ in spans)
        if not inside and line[m.end() : end].strip():
            start = m.end()  # a sentence ended before the marker; its quotes belong to something else
    return [q for s, _, q in spans if s >= start]


def _attach_numbered_quotes(text: str, refs: list[Reference]) -> None:
    lines = text.splitlines()
    by_label: dict[str, Reference] = {}
    defs: set[int] = set()
    for lineno, line in enumerate(lines, start=1):
        m = DEF_RE.match(line)
        first = next((r for r in refs if r.line == lineno), None)
        if m and first:
            by_label.setdefault(m.group(1) or m.group(2), first)
            defs.add(lineno)
    if not by_label:
        return
    fenced = False
    for lineno, line in enumerate(lines, start=1):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced or lineno in defs:
            continue
        line = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)  # `x[1]` is code, not a citation
        prev = 0
        for m in MARK_RE.finditer(line):
            quotes = _quotes_before(line, prev, m.start())
            prev = m.end()
            for label in _labels(m.group(1)):
                ref = by_label.get(label)
                if ref:
                    ref.quotes += [q for q in quotes if q not in ref.quotes]
