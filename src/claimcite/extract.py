"""Find citations in plain text / Markdown and the metadata the author wrote next to them.

Supported citation forms:
  - DOI: https://doi.org/10.x/y, doi:10.x/y, or a bare 10.x/y
  - PMID: "PMID: 123" or https://pubmed.ncbi.nlm.nih.gov/123
  - any other http(s) link (reported, not yet verified)

The text between two citations on the same line is treated as the description of the
second one, e.g. ``Author (2020). Title. Journal：「quoted words」. <https://doi.org/...>``
"""

from __future__ import annotations

import re

from .models import Reference

_ASCII_TOKEN = r"[^\s<>\"'\u0080-\uffff]+"
# No re.I here: case-folding would make the non-ASCII exclusion also drop "s" and "k" (ſ, K).
DOI_RE = re.compile(r"(?i:https?://(?:dx\.)?doi\.org/|\bdoi:\s*)?\b(10\.\d{4,9}/" + _ASCII_TOKEN + ")")
PMID_RE = re.compile(r"\bPMID:?\s*(\d{1,9})\b|https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})/?", re.IGNORECASE)
URL_RE = re.compile(r"https?://" + _ASCII_TOKEN)

YEAR_RE = re.compile(r"\((\d{4})(?:-\d{1,2}(?:-\d{1,2})?)?[a-z]?\)")
TITLE_RE = re.compile(r"\(\d{4}[^)]*\)\.\s*(.+?)(?:\.\s|\.?\s*$|[：:]\s*[「“\"])")
# "Journal of X, 41(2), 181-202": the author gave the journal, not the title.
JOURNAL_ONLY = re.compile(r"^[^.?!]{3,150},\s*\d+\s*(?:\(\d+\))?\s*(?:,\s*[eE]?\d+(?:[-–]\d+)?)?$")
# "Is it true? Psychological Science, 26": title ends at the question mark.
TITLE_THEN_JOURNAL = re.compile(r"^(.+?[?!])\s+[A-Z][^?!]*,\s*\d+.*$")
QUOTE_RES = [re.compile(r"「(.+?)」"), re.compile(r"“(.+?)”"), re.compile(r"\"([^\"]{4,}?)\"")]
LEADING_NOISE = re.compile(r"^(?:\s|[；;,，、]|（[^）]*）|\([^)]*\)(?=\s*[；;])|[-*]\s*|来源[：:]|Sources?:|\[\^?\d+\]:?|\^?\d+\.\s)+", re.IGNORECASE)

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
    lines = text.splitlines()
    # usage tracking: number -> set of lines where [n]/[^n] is cited (excluding definitions)
    usage: dict[str, set[int]] = {}
    def_num_re = re.compile(r"^\s*\[?\^?(\d+)\]?\s*[:.]?\s")
    cite_re = re.compile(r"\[\^?(\d+)\]")
    def_lines: set[int] = set()
    for lineno, line in enumerate(lines, start=1):
        if def_num_re.match(line):
            def_lines.add(lineno)
    for lineno, line in enumerate(lines, start=1):
        if lineno in def_lines:
            continue
        for m in cite_re.finditer(line):
            usage.setdefault(m.group(1), set()).add(lineno)

    refs: list[Reference] = []
    for lineno, line in enumerate(lines, start=1):
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
        # detect definition number for this line
        dm = def_num_re.match(line)
        def_number = dm.group(1) if dm else None
        for start, end, kind, ident in hits:
            year, title, quotes, ctx = _describe(line[prev_end:start])
            used = sorted(usage.get(def_number, ())) if def_number else []
            refs.append(Reference(kind, ident, lineno, year, title, quotes, ctx, used))
            prev_end = end
    return refs
