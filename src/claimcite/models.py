from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Reference:
    """One citation as written in the document."""

    kind: str  # "doi" | "pmid" | "url" | "text" (written out, no identifier: looked up by title)
    id: str  # normalised DOI (lower case), PMID digits, or the URL
    line: int
    year: int | None = None
    title: str | None = None  # title as the author wrote it
    quotes: list[str] = field(default_factory=list)  # text the author says comes from the source
    context: str = ""  # the citation text this was parsed from
    book: bool = False  # a written-out reference that looks like a book (no volume or pages)


@dataclass
class Record:
    """What a registry (PubMed, Crossref) says the identifier points to."""

    source: str
    title: str | None = None
    year: int | None = None
    years: list[int] = field(default_factory=list)  # every known publication year (online, print, issue)
    doi: str | None = None
    pmid: str | None = None
    abstract: str | None = None
    pub_types: list[str] = field(default_factory=list)
    humans: bool = False
    animals: bool = False


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
    quotes: list[tuple[str, bool | None]] = field(default_factory=list)


@dataclass
class Finding:
    ref: Reference
    status: str  # "ok" | "warning" | "error" | "unchecked"
    exists: bool | None = None
    record: Record | None = None
    title_match: str = "not_stated"  # match | partial | mismatch | not_stated | not_comparable | unknown
    year_match: bool | None = None
    quotes: list[tuple[str, bool | None]] = field(default_factory=list)
    evidence: Evidence | None = None
    error: str | None = None  # set when a registry could not be reached
    web: WebResult | None = None
    found_via: str | None = None  # "crossref" | "pubmed": a written-out reference found by title search
    note: str | None = None  # not_found | book | cjk | no_title | search_off (written-out references only)
