from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field


@dataclass
class Reference:
    """One citation as written in the document."""

    kind: str  # "doi" | "pmid" | "arxiv" | "url" | "xerj" | "text" | "marker"
    id: str  # normalised DOI (lower case), PMID digits, bare arXiv ID, URL, or xerj:// URI
    line: int
    year: int | None = None
    title: str | None = None  # title as the author wrote it
    quotes: list[str] = dc_field(default_factory=list)  # text the author says comes from the source
    context: str = ""  # the citation text this was parsed from
    book: bool = False  # a written-out reference that looks like a book (no volume or pages)
    label: str | None = None  # label in a numbered/footnote list ("1", "^note")
    used_lines: list[int] = dc_field(default_factory=list)  # body line numbers citing [label]
    uncited: bool = False  # True when a numbered/footnote entry is never cited in the text


@dataclass
class Record:
    """What a registry (PubMed, Crossref) says the identifier points to."""

    source: str
    title: str | None = None
    year: int | None = None
    years: list[int] = dc_field(default_factory=list)  # every known publication year (online, print, issue)
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv: str | None = None
    abstract: str | None = None
    pub_types: list[str] = dc_field(default_factory=list)
    humans: bool = False
    animals: bool = False
    retracted: bool = False
    retraction_doi: str | None = None


@dataclass(frozen=True)
class Evidence:
    key: str
    rank: int  # lower = stronger study design
    label_en: str
    label_zh: str


@dataclass
class WebResult:
    """What a web-page lookup found (issue #1)."""

    state: str  # "alive" | "dead" | "blocked" | "failed"
    status_code: int | None = None
    detail: str | None = None
    quotes: list[tuple[str, bool | None]] = dc_field(default_factory=list)


@dataclass
class XerjResult:
    """What a local XERJ span-verification lookup found."""

    state: str  # verified | quote_mismatch | offset_out_of_range | doc_not_found | field_missing | invalid_uri | auth_error | xerj_unconfigured | network_error
    index: str | None = None
    doc_id: str | None = None
    field: str | None = None
    offset_mode: str | None = None  # "b" | "c"
    start: int | None = None
    end: int | None = None
    matched_excerpt: str | None = None
    detail: str | None = None
    quotes: list[tuple[str, bool | None]] = dc_field(default_factory=list)


@dataclass
class Finding:
    ref: Reference
    status: str  # "ok" | "warning" | "error" | "unchecked"
    exists: bool | None = None
    retracted: bool = False
    record: Record | None = None
    title_match: str = "not_stated"  # match | partial | mismatch | not_stated | not_comparable | unknown
    year_match: bool | None = None
    quotes: list[tuple[str, bool | None]] = dc_field(default_factory=list)
    fulltext_checked: bool = False
    evidence: Evidence | None = None
    error: str | None = None  # set when a registry could not be reached
    web: WebResult | None = None
    xerj: XerjResult | None = None
    found_via: str | None = None  # "crossref" | "pubmed": a written-out reference found by title search
    note: str | None = None  # not_found | book | cjk | no_title | search_off (written-out references only)
    uncited: bool = False
