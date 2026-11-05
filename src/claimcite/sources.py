"""Look identifiers up in public registries.

- doi.org handle API: does the DOI exist at all (any registrar)?
- PubMed E-utilities: title, year, abstract, publication types (biomedical papers)
- Crossref: title and year for DOIs that are not in PubMed
"""

from __future__ import annotations

import html
import json
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from .models import Record
from .net import NotFound, http_get

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_TAG = re.compile(r"<[^>]+>")


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() or None


def parse_pubmed(xml: str) -> dict[str, Record]:
    out: dict[str, Record] = {}
    for art in ET.fromstring(xml).iter("PubmedArticle"):
        pmid = art.findtext("MedlineCitation/PMID")
        if not pmid:
            continue
        year = art.findtext(".//Article/Journal/JournalIssue/PubDate/Year") or art.findtext(
            ".//Article/ArticleDate/Year"
        )
        if not year:
            medline = art.findtext(".//PubDate/MedlineDate") or ""
            year = medline[:4] if medline[:4].isdigit() else None
        years = []
        for y in (year, art.findtext(".//Article/ArticleDate/Year")):
            if y and int(y) not in years:
                years.append(int(y))
        doi = next(
            (i.text for i in art.iterfind("PubmedData/ArticleIdList/ArticleId") if i.get("IdType") == "doi"),
            None,
        ) or next((e.text for e in art.iter("ELocationID") if e.get("EIdType") == "doi"), None)
        abstract = " ".join(filter(None, (_text(a) for a in art.iter("AbstractText")))) or None
        mesh = {d.text for d in art.iter("DescriptorName")}
        out[pmid] = Record(
            source="pubmed",
            title=_text(art.find(".//Article/ArticleTitle")),
            year=int(year) if year else None,
            years=years,
            doi=doi.lower() if doi else None,
            pmid=pmid,
            abstract=abstract,
            pub_types=[p.text for p in art.iter("PublicationType") if p.text],
            humans="Humans" in mesh,
            animals="Animals" in mesh,
        )
    return out


def clean_markup(s: str | None) -> str | None:
    """Crossref titles and abstracts carry JATS/HTML markup, sometimes escaped twice."""
    if not s:
        return None
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<xref\b[^>]*>.*?</xref>", "", s, flags=re.DOTALL)  # footnote markers
    s = re.sub(r"\s*(<su[bp]\b)", r"\1", s)  # "D <sub>3</sub>" -> "D3"
    s = re.sub(r"<(?:jats:)?(?:p|title|sec)\b[^>]*>", " ", s)  # block tags separate words
    return re.sub(r"\s+", " ", _TAG.sub("", s)).strip() or None


def parse_crossref(payload: str) -> Record:
    msg = json.loads(payload)["message"]
    years = []
    for key in ("issued", "published-print", "published-online", "published"):
        y = ((msg.get(key) or {}).get("date-parts") or [[None]])[0][0]
        if y and y not in years:
            years.append(y)
    return Record(
        source="crossref",
        title=clean_markup((msg.get("title") or [None])[0]),
        year=years[0] if years else None,
        years=years,
        doi=(msg.get("DOI") or "").lower() or None,
        abstract=clean_markup(msg.get("abstract")),
    )


class LookupFailed(Exception):
    """A registry could not be reached; the citation is neither confirmed nor refuted."""


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _safe(fn):
    def run(arg):
        try:
            return fn(arg)
        except Exception as e:  # noqa: BLE001 - one bad lookup must not sink the whole report
            return LookupFailed(f"{type(e).__name__}: {e}")

    return run


class Lookup:
    """Resolves DOIs and PMIDs to Records, with an in-memory cache.

    Call `prefetch` with everything in a document first: it batches PubMed requests
    (NCBI allows 3 requests/second) and runs the other registries in parallel.
    """

    def __init__(self, http: Callable[[str], str] = http_get, workers: int = 8):
        self.http = http
        self.workers = workers
        self._cache: dict[tuple[str, str], Record | LookupFailed | None] = {}
        key = os.environ.get("NCBI_API_KEY")
        self._ncbi_key = f"&api_key={key}" if key else ""

    def doi(self, doi: str) -> Record | None:
        return self._get("doi", doi)

    def pmid(self, pmid: str) -> Record | None:
        return self._get("pmid", pmid)

    def _get(self, kind: str, ident: str) -> Record | None:
        if (kind, ident) not in self._cache:
            self.prefetch(**{f"{kind}s": [ident]})
        value = self._cache[(kind, ident)]
        if isinstance(value, LookupFailed):
            raise value
        return value

    def prefetch(self, dois: list[str] = (), pmids: list[str] = ()) -> None:
        dois = [d for d in dict.fromkeys(dois) if ("doi", d) not in self._cache]
        pmids = [p for p in dict.fromkeys(pmids) if ("pmid", p) not in self._cache]
        if not dois and not pmids:
            return

        # 1. Does each DOI exist at all? doi.org knows every registrar.
        with ThreadPoolExecutor(self.workers) as pool:
            exists = dict(zip(dois, pool.map(_safe(self._doi_exists), dois)))
        live = []
        for d, e in exists.items():
            if e is True:
                live.append(d)
            else:
                self._cache[("doi", d)] = e if isinstance(e, LookupFailed) else None

        # 2. PubMed, in batches: DOIs -> PMIDs, then one efetch for all PMIDs.
        records: dict[str, Record] = {}
        pubmed_error = None
        try:
            ids = list(pmids)
            for chunk in _chunks(live, 40):
                term = quote(" OR ".join(f"{d}[doi]" for d in chunk), safe="")
                found = json.loads(
                    self.http(
                        f"{EUTILS}/esearch.fcgi?db=pubmed&term={term}&retmode=json&retmax=200{self._ncbi_key}"
                    )
                )
                ids += found.get("esearchresult", {}).get("idlist", [])
            for chunk in _chunks(list(dict.fromkeys(ids)), 150):
                records.update(
                    parse_pubmed(
                        self.http(
                            f"{EUTILS}/efetch.fcgi?db=pubmed&id={','.join(chunk)}&retmode=xml{self._ncbi_key}"
                        )
                    )
                )
        except Exception as e:  # noqa: BLE001
            pubmed_error = LookupFailed(f"PubMed: {type(e).__name__}: {e}")

        for p in pmids:
            self._cache[("pmid", p)] = records.get(p) if pubmed_error is None else pubmed_error
        by_doi = {r.doi: r for r in records.values() if r.doi}
        rest = [d for d in live if d not in by_doi]
        for d in live:
            if d in by_doi:
                self._cache[("doi", d)] = by_doi[d]

        # 3. Everything else: Crossref, in parallel.
        with ThreadPoolExecutor(self.workers) as pool:
            for d, r in zip(rest, pool.map(_safe(self._crossref), rest)):
                self._cache[("doi", d)] = r

    def _doi_exists(self, doi: str) -> bool:
        try:
            handle = json.loads(self.http(f"https://doi.org/api/handles/{quote(doi, safe='/')}"))
        except NotFound:
            return False
        return handle.get("responseCode") == 1

    def _crossref(self, doi: str) -> Record:
        try:
            return parse_crossref(self.http(f"https://api.crossref.org/works/{quote(doi, safe='/')}"))
        except NotFound:
            # Registered (e.g. DataCite, CNKI) but no metadata we can read yet.
            return Record(source="doi.org", doi=doi)
