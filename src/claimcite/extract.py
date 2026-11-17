"""Find citations in plain text / Markdown and the metadata the author wrote next to them.

Supported citation forms:
  - DOI: https://doi.org/10.x/y, doi:10.x/y, or a bare 10.x/y
  - PMID: "PMID: 123" or https://pubmed.ncbi.nlm.nih.gov/123
  - arXiv: "arXiv:1706.03762" or https://arxiv.org/abs/1706.03762
  - any other http(s) link (fetched and checked for availability and quoted text)

Soft-wrapped paragraphs and indented multi-line list items are unwrapped while keeping exact
source line numbers. Quotes and years written either before or in the same sentence after an
identifier are attached, and numbered markers (``[1]``, ``[^note]``, ``[1-3]``) as well as
unambiguous author-year citations (``(Hölzel et al., 2011)``, ``Hölzel et al. (2011)``) link
body quotes to the matching reference-list entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Reference

_ASCII_TOKEN = r"[^\s<>\"'\u0080-\uffff]+"
# No re.I here: case-folding would make the non-ASCII exclusion also drop "s" and "k" (ſ, K).
DOI_RE = re.compile(r"(?i:https?://(?:dx\.)?doi\.org/|\bdoi:\s*)?\b(10\.\d{4,9}/" + _ASCII_TOKEN + ")")
PMID_RE = re.compile(
    r"\bPMID:?\s*(\d{1,9})\b|https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})/?", re.IGNORECASE
)
_ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?"
ARXIV_RE = re.compile(
    r"(?:\barxiv:\s*|https?://(?:export\.)?arxiv\.org/(?:abs|pdf|html)/)("
    + _ARXIV_ID
    + r")(?:/|\.pdf)?(?!\.\d|[\w-])",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://" + _ASCII_TOKEN)
# A publisher link carries the DOI followed by the name of the page: .../10.3389/fpsyg.2021.744551/full
PAGE_SUFFIX = re.compile(
    r"(?:/(?:full|abstract|fulltext|pdf|epdf|pdfdirect|html|summary|references|figures))+$|\.pdf$",
    re.IGNORECASE,
)

YEAR_RE = re.compile(r"\((\d{4})(?:-\d{1,2}(?:-\d{1,2})?)?[a-z]?\)")
RIGHT_YEAR_RE = re.compile(r"(?:\(|[,，]\s*)((?:1[89]|20)\d{2})[a-z]?\s*[)）]")
TITLE_RE = re.compile(r"\(\d{4}[^)]*\)\.\s*(.+?)(?:\.\s|\.?\s*$|[：:]\s*[「“\"])")
# "Journal of X, 41(2), 181-202": the author gave the journal, not the title.
JOURNAL_ONLY = re.compile(r"^[^.?!]{3,150},\s*\d+\s*(?:\(\d+\))?\s*(?:,\s*[eE]?\d+(?:[-–]\d+)?)?$")
# "Is it true? Psychological Science, 26": title ends at the question mark.
TITLE_THEN_JOURNAL = re.compile(r"^(.+?[?!])\s+[A-Z][^?!]*,\s*\d+.*$")
QUOTE_RES = [re.compile(r"「(.+?)」"), re.compile(r"“(.+?)”"), re.compile(r"\"([^\"]{4,}?)\"")]
LEADING_NOISE = re.compile(
    r"^(?:\s|[.；;,，、]|（[^）]*）|\([^)]*\)(?=\s*[；;])|[-*]\s*|来源[：:]|Sources?:)+", re.IGNORECASE
)

# A reference-list entry: "[1] …", "[^note]: …", "1. …" or "1) …" at the start of a line.
DEF_RE = re.compile(r"^\s*(?:\[(\^?[\w-]+)\]:?|(\d{1,3})[.)])\s+")
# A marker in the body: [1], [^note], [1, 2], [1-3]. Not a Markdown link "[x](…)", "[x][y]" or "[x]:".
MARK_RE = re.compile(r"\[(\^[\w-]+|\d{1,3}(?:\s*[-–,]\s*\d{1,3})*)\](?![(:\[])")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
SENTENCE_END = re.compile(r"[。！？!?]|\.(?=\s|$)")
CLAUSE_STOP = re.compile(r"[。！？!?；;]|(?<!\b(?:al|vs|cf))\.(?=\s|$)", re.IGNORECASE)

_TRAILING = ".,;:!?"
_PAIRS = ((")", "("), ("]", "["), ("}", "{"))

# A heading that starts a reference list: "## References", "**参考文献**", "Sources:" on its own line.
REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:\d+[.、]?\s*)?"
    r"(?:references?|bibliography|works cited|literature cited|cited literature|sources|citations"
    r"|参考文献|参考资料|引用文献|文献|资料来源|引用来源|出处)"
    r"\s*[:：]?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
HEADING = re.compile(r"^\s*#{1,6}\s")
LIST_MARK = re.compile(r"^\s*(?:[-*+]\s+|\[(?:\^?[\w-]+)\]:?\s+|\d{1,3}[.)]\s+)")
ANY_YEAR = re.compile(r"(?<!\d)(1[89]\d{2}|20\d{2})(?!\d)")
# Chinese national standard GB/T 7714: "作者. 题名[J]. 刊名, 2020, 52(3): 301-310."
GBT_TITLE = re.compile(r"(?:^|[.。]\s*)([^.。\[\]]+?)\s*\[(?:[JMCDNRSPGZA]|EB)(?:/OL)?\]")
# MLA: Smith, John. "Title of the Paper." Journal …
MLA_TITLE = re.compile(r"[“\"]([^”\"]{8,}?)[.,]?[”\"]")
# Harvard without the full stop: "Smith, J. (2019) Title. Journal"
HARVARD_TITLE = re.compile(r"\((?:1[89]|20)\d{2}[a-z]?\)\s+([^.?!]{8,}[?!]?)")
# Vancouver / PubMed style: "Hölzel BK, Carmody J, et al. Title. Journal. 2011;191(1):36-43."
AUTHORS_END = re.compile(r"(?:\bet al|\b[A-Z]{1,3}|等)$")

_FIRST_SURNAME = re.compile(
    r"^([A-ZÀ-ÖØ-Þa-zà-öø-ÿ][\w'’-]{1,30}|[\u4e00-\u9fff]{2,4})"
    r"(?=\s+(?:[A-Z]{1,4}\b|et\s+al\b|and\b|&|等)|[.,，\s(（])"
)
_PAREN_GROUP = re.compile(r"[\(（]([^()（）]{4,120}?)[\)）]")
_PAREN_CITE = re.compile(
    r"^\s*(?:see\s+|e\.g\.,?\s+|cf\.\s+)?"
    r"([A-ZÀ-ÖØ-Þ][\w'’-]{1,30}|[\u4e00-\u9fff]{2,4})"
    r"(?:\s+(?:et\s+al\.?|等|(?:&|and)\s+[A-ZÀ-ÖØ-Þ][\w'’-]{1,30}))?"
    r"\s*[,，]?\s*((?:1[89]|20)\d{2})[a-z]?\s*$",
    re.IGNORECASE,
)
_NARRATIVE_CITE = re.compile(
    r"\b([A-ZÀ-ÖØ-Þ][\w'’-]{1,30}|[\u4e00-\u9fff]{2,4})"
    r"(?:\s+(?:et\s+al\.?|等|(?:&|and)\s+[A-ZÀ-ÖØ-Þ][\w'’-]{1,30}))?"
    r"\s*[\(（]((?:1[89]|20)\d{2})[a-z]?[\)）]"
)


@dataclass
class _Block:
    text: str
    char_lines: list[int]
    start_line: int
    end_line: int
    in_ref_section: bool
    is_list_item: bool
    is_table: bool
    label: str | None


def _build_blocks(text: str) -> list[_Block]:
    blocks: list[_Block] = []
    cur_parts: list[tuple[int, str]] = []
    cur_in_ref = False
    cur_is_list = False
    cur_label: str | None = None
    in_ref = False
    fenced = False

    def flush() -> None:
        nonlocal cur_parts, cur_is_list, cur_label
        if not cur_parts:
            return
        joined_chars: list[str] = []
        char_lines: list[int] = []
        for idx, (ln, part) in enumerate(cur_parts):
            if idx > 0:
                joined_chars.append(" ")
                char_lines.append(ln)
            joined_chars.append(part)
            char_lines.extend([ln] * len(part))
        blocks.append(
            _Block(
                text="".join(joined_chars),
                char_lines=char_lines,
                start_line=cur_parts[0][0],
                end_line=cur_parts[-1][0],
                in_ref_section=cur_in_ref,
                is_list_item=cur_is_list,
                is_table=False,
                label=cur_label,
            )
        )
        cur_parts = []
        cur_is_list = False
        cur_label = None

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(raw_line):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if not raw_line.strip():
            flush()
            continue
        if REF_HEADING.match(raw_line):
            flush()
            in_ref = True
            continue
        if HEADING.match(raw_line):
            flush()
            in_ref = False
            continue
        masked = INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), raw_line)
        if masked.lstrip().startswith("|"):
            flush()
            blocks.append(
                _Block(
                    text=masked,
                    char_lines=[lineno] * len(masked),
                    start_line=lineno,
                    end_line=lineno,
                    in_ref_section=in_ref,
                    is_list_item=False,
                    is_table=True,
                    label=None,
                )
            )
            continue
        if LIST_MARK.match(raw_line):
            flush()
            def_m = DEF_RE.match(raw_line)
            cur_in_ref = in_ref
            cur_is_list = True
            cur_label = (def_m.group(1) or def_m.group(2)) if def_m else None
            cur_parts = [(lineno, masked.strip())]
            continue
        if cur_parts:
            if cur_is_list and raw_line[:1].isspace():
                cur_parts.append((lineno, masked.strip()))
                continue
            if not cur_is_list and not in_ref:
                cur_parts.append((lineno, masked.strip()))
                continue
            flush()
        cur_in_ref = in_ref
        cur_is_list = False
        cur_label = None
        cur_parts = [(lineno, masked.strip())]

    flush()
    return blocks


def _clean_id(raw: str) -> str:
    while True:
        trimmed = raw.rstrip(_TRAILING + "*")
        for close_ch, open_ch in _PAIRS:
            while trimmed.endswith(close_ch) and trimmed.count(close_ch) > trimmed.count(open_ch):
                trimmed = trimmed[:-1].rstrip(_TRAILING + "*")
        if trimmed == raw:
            return raw
        raw = trimmed


def _clean_arxiv(raw: str) -> str:
    return re.sub(r"v\d+$", "", raw.strip().lower())


def _inside_link(line: str, pos: int) -> bool:
    """True when the DOI at pos is part of another site's address, not written out on its own."""
    return any(m.start() < pos < m.end() for m in URL_RE.finditer(line))


def _quotes_before(line: str, start: int, end: int) -> list[str]:
    """Quotes in the sentence that closes at ``end``, not earlier than ``start``."""
    spans = sorted(
        (m.start(), m.end(), re.sub(r"\s+", " ", m.group(1)).strip())
        for rx in QUOTE_RES
        for m in rx.finditer(line, start, end)
    )
    for m in SENTENCE_END.finditer(line, start, end):
        inside = any(s <= m.start() < e for s, e, _ in spans)
        if not inside and any(ch.isalnum() for ch in line[m.end() : end]):
            start = m.end()  # a sentence ended before the citation; its quotes belong to something else
    return [q for s, _, q in spans if s >= start]


def _has_own_marker(after: str) -> bool:
    rest = after.lstrip()
    if MARK_RE.match(rest):
        return True
    if pm := _PAREN_GROUP.match(rest):
        return any(_PAREN_CITE.match(part) for part in re.split(r"\s*[;；]\s*", pm.group(1)))
    return False


def _quotes_in_clause(clause: str) -> list[str]:
    spans = sorted(
        (m.start(), m.end(), re.sub(r"\s+", " ", m.group(1)).strip())
        for rx in QUOTE_RES
        for m in rx.finditer(clause)
    )
    return [q for _, end, q in spans if not _has_own_marker(clause[end:])]


def _first_clause(tail: str, has_next_hit: bool) -> str:
    spans = [(m.start(), m.end()) for rx in QUOTE_RES for m in rx.finditer(tail)]
    for m in CLAUSE_STOP.finditer(tail):
        if not any(s <= m.start() < e for s, e in spans):
            return tail[: m.start()]
    return "" if has_next_hit else tail


def _describe(segment: str) -> tuple[int | None, str | None, list[str], str]:
    seg = LEADING_NOISE.sub("", segment).rstrip(" <[(")
    year_m = YEAR_RE.search(seg)
    title_m = TITLE_RE.search(seg)
    title = title_m.group(1).strip().rstrip(".") if title_m else None
    if title and JOURNAL_ONLY.match(title):
        title = None
    elif title and (m := TITLE_THEN_JOURNAL.match(title)):
        title = m.group(1)
    quotes = _quotes_before(seg, 0, len(seg))
    if title and any(title == q or q in title for q in quotes):
        title = None
    return (int(year_m.group(1)) if year_m else None), title or None, quotes, seg.strip()


def extract_references(text: str) -> list[Reference]:
    blocks = _build_blocks(text)
    refs: list[Reference] = []
    block_first_ref: dict[int, Reference] = {}
    blocks_with_ids: set[int] = set()

    for b_idx, blk in enumerate(blocks):
        line = blk.text
        hits: list[tuple[int, int, str, str]] = []
        for m in DOI_RE.finditer(line):
            doi = _clean_id(m.group(1))
            if m.start() == m.start(1) and _inside_link(line, m.start()):
                doi = PAGE_SUFFIX.sub("", doi)
            hits.append((m.start(), m.end(), "doi", doi.lower()))
        for m in PMID_RE.finditer(line):
            if not any(s <= m.start() < e for s, e, *_ in hits):
                hits.append((m.start(), m.end(), "pmid", m.group(1) or m.group(2)))
        for m in ARXIV_RE.finditer(line):
            if not any(s <= m.start() < e or m.start() <= s < m.end() for s, e, *_ in hits):
                hits.append((m.start(), m.end(), "arxiv", _clean_arxiv(m.group(1))))
        for m in URL_RE.finditer(line):
            if not any(s <= m.start() < e or m.start() <= s < m.end() for s, e, *_ in hits):
                hits.append((m.start(), m.end(), "url", _clean_id(m.group(0))))
        if not hits:
            continue
        blocks_with_ids.add(b_idx)
        hits.sort()
        prev_end = 0
        for idx, (start, end, kind, ident) in enumerate(hits):
            year, title, quotes, ctx = _describe(line[prev_end:start])
            next_start = hits[idx + 1][0] if idx + 1 < len(hits) else len(line)
            right = _first_clause(line[end:next_start], has_next_hit=(idx + 1 < len(hits)))
            consumed_right = 0
            if right:
                if year is None and (ry := (RIGHT_YEAR_RE.search(right) or YEAR_RE.search(right))):
                    year = int(ry.group(1))
                    consumed_right = len(right)
                if not quotes and (rq := _quotes_in_clause(right)):
                    quotes = rq
                    consumed_right = len(right)
            lineno = blk.char_lines[start] if start < len(blk.char_lines) else blk.start_line
            ref = Reference(kind, ident, lineno, year, title, quotes, ctx)
            refs.append(ref)
            block_first_ref.setdefault(b_idx, ref)
            prev_end = end + consumed_right

    for b_idx, blk in enumerate(blocks):
        if b_idx in blocks_with_ids or blk.is_table:
            continue
        labelled = blk.label is not None and bool(re.match(r"^\s*\[\^?[\w-]+\]:?\s", blk.text))
        if not (blk.in_ref_section or labelled):
            continue
        entry = LIST_MARK.sub("", blk.text, count=1).replace("*", "").strip()
        year_m = ANY_YEAR.search(entry)
        if len(entry) < 20 or not year_m:
            continue
        stated_year, title, quotes, ctx = _describe(entry)
        if not title:
            title, quotes = _text_title(entry, quotes)
        year = stated_year or int(year_m.group(1))
        ref = Reference(
            "text",
            ctx,
            blk.start_line,
            year,
            title,
            quotes,
            ctx,
            book=_looks_like_book(entry, title),
        )
        refs.append(ref)
        block_first_ref.setdefault(b_idx, ref)

    refs.sort(key=lambda r: r.line)
    _attach_numbered_quotes(blocks, block_first_ref)
    _attach_author_year_quotes(blocks, blocks_with_ids, refs)
    return refs


def _looks_like_book(entry: str, title: str | None) -> bool:
    """GB/T "[M]", or nothing but a publisher after the title: "Thinking, fast and slow. Farrar, Straus and Giroux." """
    if re.search(r"\[M\]", entry):
        return True
    if not title or title not in entry:
        return False
    rest = ANY_YEAR.sub("", entry[entry.index(title) + len(title) :])
    return not re.search(r"\d", rest)


def _text_title(entry: str, quotes: list[str]) -> tuple[str | None, list[str]]:
    if m := GBT_TITLE.search(entry):
        return m.group(1).strip(), quotes
    if m := MLA_TITLE.search(entry):
        title = m.group(1).strip()
        return title, [q for q in quotes if q.rstrip(".,") != title]
    if m := HARVARD_TITLE.search(entry):
        return m.group(1).strip(), quotes
    parts = re.split(r"\.\s+", entry)
    if len(parts) >= 3 and AUTHORS_END.search(parts[0].strip()):
        return parts[1].strip(), quotes
    return None, quotes


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


def _attach_numbered_quotes(blocks: list[_Block], block_first_ref: dict[int, Reference]) -> None:
    by_label: dict[str, Reference] = {}
    def_blocks: set[int] = set()
    for b_idx, blk in enumerate(blocks):
        ref = block_first_ref.get(b_idx)
        if blk.label and ref:
            by_label.setdefault(blk.label, ref)
            def_blocks.add(b_idx)
    if not by_label:
        return
    for b_idx, blk in enumerate(blocks):
        if b_idx in def_blocks or blk.in_ref_section:
            continue
        prev = 0
        for m in MARK_RE.finditer(blk.text):
            quotes = _quotes_before(blk.text, prev, m.start())
            prev = m.end()
            for label in _labels(m.group(1)):
                ref = by_label.get(label)
                if ref:
                    ref.quotes += [q for q in quotes if q not in ref.quotes]


def _extract_first_surname(context: str) -> str | None:
    cleaned = LIST_MARK.sub("", context, count=1).replace("*", "").strip()
    cleaned = LEADING_NOISE.sub("", cleaned).strip()
    m = _FIRST_SURNAME.match(cleaned)
    return m.group(1).lower() if m else None


def _attach_author_year_quotes(
    blocks: list[_Block], blocks_with_ids: set[int], refs: list[Reference]
) -> None:
    by_key: dict[tuple[str, int], Reference | None] = {}
    for ref in refs:
        if ref.year is None:
            continue
        surname = _extract_first_surname(ref.context)
        if not surname:
            continue
        key = (surname, ref.year)
        if key not in by_key:
            by_key[key] = ref
        elif by_key[key] is not ref:
            by_key[key] = None  # ambiguous author-year pair: never guess
    if not any(v is not None for v in by_key.values()):
        return

    for b_idx, blk in enumerate(blocks):
        if blk.in_ref_section or blk.label is not None or b_idx in blocks_with_ids:
            continue
        line = blk.text
        prev = 0
        for pm in _PAREN_GROUP.finditer(line):
            saw_cite = False
            matched_refs: list[Reference] = []
            for part in re.split(r"\s*[;；]\s*", pm.group(1)):
                cm = _PAREN_CITE.match(part)
                if cm:
                    saw_cite = True
                    target = by_key.get((cm.group(1).lower(), int(cm.group(2))))
                    if target is not None:
                        matched_refs.append(target)
            if saw_cite:
                quotes = _quotes_before(line, prev, pm.start())
                prev = pm.end()
                for target in matched_refs:
                    target.quotes += [q for q in quotes if q not in target.quotes]

        for nm in _NARRATIVE_CITE.finditer(line):
            target = by_key.get((nm.group(1).lower(), int(nm.group(2))))
            if target is None:
                continue
            clause = _first_clause(line[nm.end() :], has_next_hit=False)
            for q in _quotes_in_clause(clause):
                if q not in target.quotes:
                    target.quotes.append(q)
