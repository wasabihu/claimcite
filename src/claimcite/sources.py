"""Look identifiers up in public registries.

- doi.org handle API: does the DOI exist at all (any registrar)?
- PubMed E-utilities: title, year, abstract, publication types, retractions
- Crossref: title, year and retraction notices for DOIs that are not in PubMed
- OpenAlex: fallback metadata and reconstructed abstracts (`abstract_inverted_index`) across all fields
- arXiv Atom API: title, publication year and abstract for arXiv preprints and 10.48550 DOIs
- Title search (Crossref, then PubMed) for references written out without a DOI or PMID
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import threading
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.error import HTTPError
from urllib.parse import quote

from .cache import cache_key
from .models import Record, Reference
from .net import NotFound, http_get
from .text import compare_titles, title_ratio, words

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
CROSSREF_SEARCH = "https://api.crossref.org/works?rows=5&select=DOI,title,issued,published-print,published-online&query.bibliographic="
OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_SELECT = "id,doi,ids,title,publication_year,is_retracted,abstract_inverted_index"
ARXIV_API = "https://export.arxiv.org/api/query?id_list="
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"
ET.register_namespace("", "http://www.w3.org/2005/Atom")
ET.register_namespace("arxiv", "http://arxiv.org/schemas/atom")
_DOI_IN_TEXT = re.compile(r"""\b(10\.\d{4,9}/[^\s<>"']+)""")
_TAG = re.compile(r"<[^>]+>")


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() or None


def _retraction_doi(node: ET.Element) -> str | None:
    retraction_doi = None
    for cc in node.iter("CommentsCorrections"):
        if cc.get("RefType") == "RetractionIn":
            src_text = cc.findtext("RefSource") or ""
            if m := _DOI_IN_TEXT.search(src_text):
                cand = m.group(1).rstrip(".,;:")
                while cand.endswith(")") and cand.count(")") > cand.count("("):
                    cand = cand[:-1].rstrip(".,;:")
                retraction_doi = cand.lower()
    return retraction_doi


def _norm_pmcid(raw: str | None) -> str | None:
    if not raw:
        return None
    val = raw.strip().upper()
    if not val:
        return None
    if val.isdigit():
        return f"PMC{val}"
    return val if val.startswith("PMC") else None


def parse_pubmed(xml: str) -> dict[str, Record]:
    out: dict[str, Record] = {}
    root = ET.fromstring(xml)
    for art in root.iter("PubmedArticle"):
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
            if y and y.isdigit() and int(y) not in years:
                years.append(int(y))
        doi = next(
            (i.text for i in art.iterfind("PubmedData/ArticleIdList/ArticleId") if i.get("IdType") == "doi"),
            None,
        ) or next((e.text for e in art.iter("ELocationID") if e.get("EIdType") == "doi"), None)
        pmcid = _norm_pmcid(
            next(
                (i.text for i in art.iterfind("PubmedData/ArticleIdList/ArticleId") if i.get("IdType") == "pmc"),
                None,
            )
        )
        abstract = " ".join(filter(None, (_text(a) for a in art.iter("AbstractText")))) or None
        mesh = {d.text for d in art.iter("DescriptorName")}
        pub_types = [p.text for p in art.iter("PublicationType") if p.text]
        out[pmid] = Record(
            source="pubmed",
            title=_text(art.find(".//Article/ArticleTitle")),
            year=years[0] if years else None,
            years=years,
            doi=doi.lower() if doi else None,
            pmid=pmid,
            pmcid=pmcid,
            abstract=abstract,
            pub_types=pub_types,
            humans="Humans" in mesh,
            animals="Animals" in mesh,
            retracted="Retracted Publication" in pub_types,
            retraction_doi=_retraction_doi(art),
        )
    for book_art in root.iter("PubmedBookArticle"):
        doc = book_art.find("BookDocument")
        if doc is None:
            continue
        pmid = doc.findtext("PMID")
        if not pmid:
            continue
        years = []
        for raw_y in (
            doc.findtext("Book/PubDate/Year"),
            (doc.findtext("Book/PubDate/MedlineDate") or "")[:4],
            doc.findtext("ContributionDate/Year"),
            doc.findtext("DateRevised/Year"),
        ):
            if raw_y and raw_y.isdigit() and int(raw_y) not in years:
                years.append(int(raw_y))
        doi = (
            next(
                (i.text for i in doc.iterfind("ArticleIdList/ArticleId") if i.get("IdType") == "doi"),
                None,
            )
            or next(
                (
                    i.text
                    for i in book_art.iterfind("PubmedBookData/ArticleIdList/ArticleId")
                    if i.get("IdType") == "doi"
                ),
                None,
            )
            or next((e.text for e in doc.iter("ELocationID") if e.get("EIdType") == "doi"), None)
        )
        pmcid = _norm_pmcid(
            next((i.text for i in doc.iterfind("ArticleIdList/ArticleId") if i.get("IdType") == "pmc"), None)
            or next(
                (
                    i.text
                    for i in book_art.iterfind("PubmedBookData/ArticleIdList/ArticleId")
                    if i.get("IdType") == "pmc"
                ),
                None,
            )
        )
        abstract = " ".join(filter(None, (_text(a) for a in doc.iter("AbstractText")))) or None
        pub_types = [p.text for p in doc.iter("PublicationType") if p.text]
        mesh = {d.text for d in book_art.iter("DescriptorName")}
        out[pmid] = Record(
            source="pubmed",
            title=_text(doc.find("ArticleTitle")) or _text(doc.find("Book/BookTitle")),
            year=years[0] if years else None,
            years=years,
            doi=doi.lower() if doi else None,
            pmid=pmid,
            pmcid=pmcid,
            abstract=abstract,
            pub_types=pub_types,
            humans="Humans" in mesh,
            animals="Animals" in mesh,
            retracted="Retracted Publication" in pub_types,
            retraction_doi=_retraction_doi(book_art),
        )
    return out


def clean_markup(s: str | None) -> str | None:
    """Crossref titles, abstracts and JATS full-text XML carry markup, sometimes escaped twice."""
    if not s:
        return None
    s = html.unescape(html.unescape(s))
    s = re.sub(r"<xref\b[^>]*>.*?</xref>", "", s, flags=re.DOTALL)  # footnote markers
    s = re.sub(r"\s*(<su[bp]\b)", r"\1", s)  # "D <sub>3</sub>" -> "D3"
    s = re.sub(r"<(?:jats:)?(?:p|title|sec|li|td|th|tr|caption|label)\b[^>]*>", " ", s)
    return re.sub(r"\s+", " ", _TAG.sub("", s)).strip() or None


def parse_jats_fulltext(xml: str) -> str | None:
    """Extract clean body and float text from JATS full-text XML, excluding front/back matter."""
    if not xml or not xml.lstrip().startswith("<"):
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    # Some publishers (e.g. BMJ via Europe PMC) nest <ref-list> inside <body>; strip it first.
    for parent in root.iter():
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] in ("ref-list", "back"):
                parent.remove(child)
    chunks = []
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1]
        if local in ("body", "floats-group"):
            chunks.append(ET.tostring(el, encoding="unicode"))
    if not chunks:
        return None
    return clean_markup(" ".join(chunks))


def parse_crossref(payload: str) -> Record:
    return _crossref_record(json.loads(payload)["message"])


def parse_crossref_search(payload: str) -> list[Record]:
    return [_crossref_record(item) for item in json.loads(payload)["message"].get("items", [])]


def _crossref_record(msg: dict) -> Record:
    years = []
    for key in ("issued", "published-print", "published-online", "published"):
        y = ((msg.get(key) or {}).get("date-parts") or [[None]])[0][0]
        if y and y not in years:
            years.append(y)
    # Crossref records retractions on the retracted paper via `updated-by` and
    # `relation.is-retracted-by` (`update-to` sits on the retraction notice itself).
    retracted = False
    retraction_doi = None
    for upd in msg.get("updated-by") or []:
        if str(upd.get("type", "")).lower() == "retraction":
            retracted = True
            retraction_doi = (upd.get("DOI") or upd.get("doi") or "").lower() or retraction_doi
    rel = msg.get("relation") or {}
    for entry in rel.get("is-retracted-by") or []:
        retracted = True
        ident = (entry.get("id") or entry.get("DOI") or entry.get("doi") or "").lower()
        if ident and (entry.get("id-type") == "doi" or ident.startswith("10.")):
            retraction_doi = ident or retraction_doi
    return Record(
        source="crossref",
        title=clean_markup((msg.get("title") or [None])[0]),
        year=years[0] if years else None,
        years=years,
        doi=(msg.get("DOI") or "").lower() or None,
        abstract=clean_markup(msg.get("abstract")),
        retracted=retracted,
        retraction_doi=retraction_doi,
    )


def _crossref_url(doi: str) -> str:
    return f"https://api.crossref.org/works/{quote(doi, safe='/')}"


def _norm_oa_doi(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", raw.strip(), flags=re.IGNORECASE)
    return cleaned.strip().lower() or None


def reconstruct_abstract(inverted_index: dict | None) -> str | None:
    """Rebuild plain abstract text from an OpenAlex ``abstract_inverted_index`` mapping."""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None
    pairs: list[tuple[int, str]] = []
    for token, positions in inverted_index.items():
        if not isinstance(token, str) or not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int) and not isinstance(pos, bool) and pos >= 0:
                pairs.append((pos, token))
    if not pairs:
        return None
    pairs.sort(key=lambda item: item[0])
    return clean_markup(" ".join(token for _, token in pairs))


def _openalex_record(item: dict) -> Record:
    pub_year = item.get("publication_year")
    years = [pub_year] if isinstance(pub_year, int) and not isinstance(pub_year, bool) else []
    ids = item.get("ids") or {}
    pmid_m = re.search(r"(\d+)/?$", str(ids.get("pmid") or ""))
    pmcid_m = re.search(r"(?:pmc)?(\d+)/?$", str(ids.get("pmcid") or ""), re.IGNORECASE)
    return Record(
        source="openalex",
        title=clean_markup(item.get("title") or item.get("display_name")),
        year=years[0] if years else None,
        years=years,
        doi=_norm_oa_doi(item.get("doi")),
        pmid=pmid_m.group(1) if pmid_m else None,
        pmcid=_norm_pmcid(f"PMC{pmcid_m.group(1)}") if pmcid_m else None,
        abstract=reconstruct_abstract(item.get("abstract_inverted_index")),
        retracted=bool(item.get("is_retracted")),
    )


def parse_openalex(payload: str) -> Record:
    return _openalex_record(json.loads(payload))


def _openalex_url(doi: str) -> str:
    return f"{OPENALEX_API}/https://doi.org/{quote(doi, safe='/')}?select={OPENALEX_SELECT}"


def _arxiv_url(arxiv_id: str) -> str:
    return f"{ARXIV_API}{quote(arxiv_id, safe='/')}&max_results=1"


def parse_arxiv(xml: str) -> dict[str, Record]:
    out: dict[str, Record] = {}
    root = ET.fromstring(xml)
    for entry in root.findall(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        if "/abs/" not in entry_id or "/api/errors" in entry_id:
            continue
        aid = re.sub(r"v\d+$", "", entry_id.split("/abs/", 1)[1]).lower()
        years = []
        for tag in (f"{_ATOM_NS}published", f"{_ATOM_NS}updated"):
            raw = (entry.findtext(tag) or "").strip()
            if len(raw) >= 4 and raw[:4].isdigit() and int(raw[:4]) not in years:
                years.append(int(raw[:4]))
        doi_raw = (entry.findtext(f"{_ARXIV_NS}doi") or "").strip().lower() or None
        out[aid] = Record(
            source="arxiv",
            title=_text(entry.find(f"{_ATOM_NS}title")),
            year=years[0] if years else None,
            years=years,
            doi=doi_raw,
            arxiv=aid,
            abstract=_text(entry.find(f"{_ATOM_NS}summary")),
        )
    return out


def _cache_arxiv_entries(xml: str, store: Callable[[str, str], None]) -> None:
    root = ET.fromstring(xml)
    for entry in root.findall(f"{_ATOM_NS}entry"):
        entry_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        if "/abs/" not in entry_id or "/api/errors" in entry_id:
            continue
        aid = re.sub(r"v\d+$", "", entry_id.split("/abs/", 1)[1]).lower()
        feed = ET.Element(f"{_ATOM_NS}feed")
        feed.append(entry)
        store(_arxiv_url(aid), ET.tostring(feed, encoding="unicode"))


class LookupFailed(Exception):
    """A registry could not be reached; the citation is neither confirmed nor refuted."""


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _safe(fn):
    def run(arg):
        try:
            return fn(arg)
        except AssertionError:
            raise
        except Exception as e:  # noqa: BLE001 - one bad lookup must not sink the whole report
            return LookupFailed(f"{type(e).__name__}: {cache_key(str(e))}")

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
        self._found: dict[str, tuple[str, Record] | LookupFailed | None] = {}
        self._web_lock = threading.Lock()
        self._web_cache: dict[str, str | BaseException] = {}
        self._web_inflight: dict[str, threading.Event] = {}
        self._ft_lock = threading.Lock()
        self._fulltext: dict[str, tuple[str | None, str | None]] = {}
        self._ft_inflight: dict[str, threading.Event] = {}
        key = os.environ.get("NCBI_API_KEY")
        self._ncbi_key = f"&api_key={key}" if key else ""
        oa_mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
        oa_key = os.environ.get("OPENALEX_API_KEY", "").strip()
        oa_parts = []
        if oa_mailto:
            oa_parts.append(f"&mailto={quote(oa_mailto, safe='')}")
        if oa_key:
            oa_parts.append(f"&api_key={quote(oa_key, safe='')}")
        self._openalex_params = "".join(oa_parts)

    def web_http(self, url: str) -> str:
        """Fetch an ordinary web URL once per Lookup run without writing HTML to the 30-day disk cache."""
        with self._web_lock:
            if url in self._web_cache:
                cached = self._web_cache[url]
                if isinstance(cached, BaseException):
                    raise cached
                return cached
            inflight = self._web_inflight.get(url)
            if inflight is None:
                inflight = threading.Event()
                self._web_inflight[url] = inflight
                owner = True
            else:
                owner = False
        if not owner:
            inflight.wait()
            with self._web_lock:
                cached = self._web_cache[url]
            if isinstance(cached, BaseException):
                raise cached
            return cached
        raw_http = getattr(self.http, "http", self.http)
        try:
            body = raw_http(url)
        except BaseException as exc:
            with self._web_lock:
                self._web_cache[url] = exc
                self._web_inflight.pop(url, None)
            inflight.set()
            raise
        with self._web_lock:
            self._web_cache[url] = body
            self._web_inflight.pop(url, None)
        inflight.set()
        return body

    def doi(self, doi: str) -> Record | None:
        return self._get("doi", doi)

    def pmid(self, pmid: str) -> Record | None:
        return self._get("pmid", pmid)

    def arxiv(self, arxiv_id: str) -> Record | None:
        return self._get("arxiv", arxiv_id)

    def fulltext(self, record: Record) -> str | None:
        """Fetch open-access JATS full text on demand from PMC / Europe PMC."""
        if record.pmcid:
            key = record.pmcid.upper()
        elif record.source in ("crossref", "openalex") and record.doi:
            key = f"doi:{record.doi.lower()}"
        else:
            return None

        with self._ft_lock:
            if key in self._fulltext:
                pmcid, body = self._fulltext[key]
                if pmcid and not record.pmcid:
                    record.pmcid = pmcid
                return body
            inflight = self._ft_inflight.get(key)
            if inflight is None:
                inflight = threading.Event()
                self._ft_inflight[key] = inflight
                owner = True
            else:
                owner = False

        if not owner:
            inflight.wait()
            with self._ft_lock:
                pmcid, body = self._fulltext.get(key, (None, None))
            if pmcid and not record.pmcid:
                record.pmcid = pmcid
            return body

        pmcid: str | None = _norm_pmcid(record.pmcid)
        body: str | None = None
        try:
            pmcid, body = self._fetch_fulltext(record, pmcid)
        except Exception as e:  # noqa: BLE001 - optional check must never crash the run
            print(
                f"claimcite: full-text lookup failed ({type(e).__name__}: {cache_key(str(e))})",
                file=sys.stderr,
            )
        finally:
            with self._ft_lock:
                self._fulltext[key] = (pmcid, body)
                self._ft_inflight.pop(key, None)
            inflight.set()
        if pmcid and not record.pmcid:
            record.pmcid = pmcid
        return body

    def _fetch_fulltext(self, record: Record, pmcid: str | None) -> tuple[str | None, str | None]:
        if pmcid:
            num = pmcid.removeprefix("PMC")
            body = parse_jats_fulltext(
                self.http(f"{EUTILS}/efetch.fcgi?db=pmc&id={num}&retmode=xml{self._ncbi_key}")
            )
            if body:
                return pmcid, body
            hit = self._epmc_oa_hit(f"PMCID:{pmcid}")
            if hit and (hit.get("pmcid") or "").upper() == pmcid:
                return pmcid, self._fetch_epmc_xml(pmcid)
            return pmcid, None

        if record.doi:
            hit = self._epmc_oa_hit(f'DOI:"{record.doi}"')
            if hit and (hit.get("doi") or "").lower() == record.doi.lower():
                found_pmcid = _norm_pmcid(hit.get("pmcid"))
                if found_pmcid:
                    return found_pmcid, self._fetch_epmc_xml(found_pmcid)
        return None, None

    def _epmc_oa_hit(self, query: str) -> dict | None:
        url = f"{EPMC_API}/search?query={quote(query, safe=':')}&format=json&pageSize=1"
        data = json.loads(self.http(url))
        results = (data.get("resultList") or {}).get("result") or []
        if not results:
            return None
        hit = results[0]
        if hit.get("inEPMC") == "Y" and hit.get("isOpenAccess") == "Y":
            return hit
        return None

    def _fetch_epmc_xml(self, pmcid: str) -> str | None:
        return parse_jats_fulltext(self.http(f"{EPMC_API}/{pmcid}/fullTextXML"))

    def _get(self, kind: str, ident: str) -> Record | None:
        if (kind, ident) not in self._cache:
            arg = {"doi": "dois", "pmid": "pmids", "arxiv": "arxivs"}[kind]
            self.prefetch(**{arg: [ident]})
        value = self._cache[(kind, ident)]
        if isinstance(value, LookupFailed):
            raise value
        return value

    def prefetch(self, dois: list[str] = (), pmids: list[str] = (), arxivs: list[str] = ()) -> None:
        dois = [d for d in dict.fromkeys(dois) if ("doi", d) not in self._cache]
        pmids = [p for p in dict.fromkeys(pmids) if ("pmid", p) not in self._cache]
        arxivs = [a for a in dict.fromkeys(arxivs) if ("arxiv", a) not in self._cache]
        if not dois and not pmids and not arxivs:
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

        # Route DataCite arXiv DOIs (10.48550/arXiv.*) straight to the arXiv API.
        arxiv_dois = {
            d: re.sub(r"v\d+$", "", d.removeprefix("10.48550/arxiv."))
            for d in live
            if d.startswith("10.48550/arxiv.")
        }
        regular_live = [d for d in live if d not in arxiv_dois]
        arxiv_todo = list(
            dict.fromkeys([*arxivs, *(a for a in arxiv_dois.values() if ("arxiv", a) not in self._cache)])
        )
        for a, r in self._arxiv_many(arxiv_todo).items():
            self._cache[("arxiv", a)] = r
        for d, aid in arxiv_dois.items():
            rec = self._cache[("arxiv", aid)]
            if isinstance(rec, LookupFailed):
                self._cache[("doi", d)] = rec
            elif rec is None:
                self._cache[("doi", d)] = Record(source="doi.org", doi=d)
            else:
                self._cache[("doi", d)] = replace(rec, doi=rec.doi or d)

        # 2. PubMed, in batches: DOIs -> PMIDs, then efetch per chunk with isolated failure handling.
        records: dict[str, Record] = {}
        pmid_errors: dict[str, LookupFailed] = {}
        ids = list(pmids)
        for chunk in _chunks(regular_live, 40):
            term = quote(" OR ".join(f'"{d.replace(chr(34), "")}"[doi]' for d in chunk), safe="")
            try:
                found = json.loads(
                    self.http(
                        f"{EUTILS}/esearch.fcgi?db=pubmed&term={term}&retmode=json&retmax=200{self._ncbi_key}"
                    )
                )
                ids += found.get("esearchresult", {}).get("idlist", [])
            except Exception as e:  # noqa: BLE001 - DOIs still fall through to Crossref below
                print(
                    f"claimcite: PubMed lookup failed, falling back to Crossref ({type(e).__name__}: {cache_key(str(e))})",
                    file=sys.stderr,
                )
        for chunk in _chunks(list(dict.fromkeys(ids)), 150):
            try:
                records.update(
                    parse_pubmed(
                        self.http(
                            f"{EUTILS}/efetch.fcgi?db=pubmed&id={','.join(chunk)}&retmode=xml{self._ncbi_key}"
                        )
                    )
                )
            except Exception as e:  # noqa: BLE001
                err = LookupFailed(f"PubMed: {type(e).__name__}: {cache_key(str(e))}")
                for p in chunk:
                    pmid_errors[p] = err

        for p in pmids:
            self._cache[("pmid", p)] = pmid_errors[p] if p in pmid_errors else records.get(p)
        by_doi = {r.doi: r for r in records.values() if r.doi}
        rest = [d for d in regular_live if d not in by_doi]
        for d in regular_live:
            if d in by_doi:
                self._cache[("doi", d)] = by_doi[d]

        # 3. Crossref (20 DOIs/request), then OpenAlex (50 DOIs/request) for misses or missing abstracts.
        cr_records = self._crossref_many(rest)
        need_oa = [
            d
            for d in rest
            if isinstance(cr_records[d], LookupFailed)
            or cr_records[d].source == "doi.org"
            or not cr_records[d].abstract
        ]
        oa_records = self._openalex_many(need_oa) if need_oa else {}
        warned_oa = False
        for d in rest:
            cr = cr_records[d]
            oa = oa_records.get(d)
            if isinstance(oa, LookupFailed):
                if not warned_oa:
                    print(f"claimcite: OpenAlex lookup failed ({oa})", file=sys.stderr)
                    warned_oa = True
                self._cache[("doi", d)] = cr
            elif isinstance(cr, LookupFailed) or cr.source == "doi.org":
                self._cache[("doi", d)] = oa if isinstance(oa, Record) else cr
            elif isinstance(oa, Record):
                years = list(cr.years)
                if oa.year and oa.year not in years:
                    years.append(oa.year)
                self._cache[("doi", d)] = replace(
                    cr,
                    year=cr.year or oa.year,
                    years=years,
                    pmid=cr.pmid or oa.pmid,
                    pmcid=cr.pmcid or oa.pmcid,
                    abstract=cr.abstract or oa.abstract,
                    retracted=cr.retracted or oa.retracted,
                )
            else:
                self._cache[("doi", d)] = cr

    def find(self, ref: Reference) -> tuple[str, Record] | None:
        """Search for a reference written out without an identifier: ("crossref" | "pubmed", record).

        None when neither registry has a paper with that title. Raises LookupFailed when a search
        failed and nothing was found, so a network error is never reported as "does not exist".
        Searches run one at a time: Crossref allows one concurrent search per client.
        """
        if ref.context not in self._found:
            try:
                self._found[ref.context] = self._find(ref)
            except LookupFailed as e:
                self._found[ref.context] = e
        value = self._found[ref.context]
        if isinstance(value, LookupFailed):
            raise value
        return value

    def _find(self, ref: Reference) -> tuple[str, Record] | None:
        failed = None
        for via, search in (("crossref", self._search_crossref), ("pubmed", self._search_pubmed)):
            try:
                hit = best_match(ref, search(ref))
            except Exception as e:  # noqa: BLE001 - try the other registry before giving up
                failed = LookupFailed(f"{via}: {type(e).__name__}: {cache_key(str(e))}")
                continue
            if hit:
                return via, hit
        if failed:
            raise failed
        return None

    def _search_crossref(self, ref: Reference) -> list[Record]:
        query = quote(ref.context[:300], safe="")
        return parse_crossref_search(self.http(CROSSREF_SEARCH + query))

    def _search_pubmed(self, ref: Reference) -> list[Record]:
        # Punctuation such as ":" breaks PubMed's phrase search; words alone match the title field.
        term = quote(f"{words(ref.title)}[ti]", safe="")
        found = json.loads(
            self.http(f"{EUTILS}/esearch.fcgi?db=pubmed&term={term}&retmode=json&retmax=5{self._ncbi_key}")
        )
        ids = found.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        records = parse_pubmed(
            self.http(f"{EUTILS}/efetch.fcgi?db=pubmed&id={','.join(ids)}&retmode=xml{self._ncbi_key}")
        )
        for pmid, record in records.items():
            self._cache.setdefault(("pmid", pmid), record)
        return list(records.values())

    def _doi_exists(self, doi: str) -> bool:
        try:
            handle = json.loads(self.http(f"https://doi.org/api/handles/{quote(doi, safe='/')}"))
        except NotFound:
            return False
        return handle.get("responseCode") == 1

    def _crossref(self, doi: str) -> Record:
        try:
            return parse_crossref(self.http(_crossref_url(doi)))
        except NotFound:
            # Registered (e.g. DataCite, CNKI) but no metadata we can read yet.
            return Record(source="doi.org", doi=doi)

    def _crossref_many(self, dois: list[str]) -> dict[str, Record | LookupFailed]:
        """One request per 20 DOIs instead of one each. Answers already in the on-disk cache are
        used as they are, and each work in a batch answer is cached under its own URL, so adding
        one citation to a document does not refetch the rest."""
        cached = getattr(self.http, "get", None)
        store = getattr(self.http, "put", None)
        out: dict[str, Record | LookupFailed] = {}
        todo = []
        for d in dois:
            body = cached(_crossref_url(d)) if cached else None
            if body is not None:
                out[d] = _safe(parse_crossref)(body)
            elif "," in d:  # a comma would split the filter; ask for this one on its own
                out[d] = _safe(self._crossref)(d)
            else:
                todo.append(d)
        for chunk in _chunks(todo, 20):
            filters = ",".join(f"doi:{d}" for d in chunk)
            url = f"https://api.crossref.org/works?filter={quote(filters, safe=':/,()')}&rows={len(chunk)}"
            try:
                items = json.loads(self.http(url))["message"]["items"]
            except HTTPError as e:
                if e.code != 400:
                    out.update(dict.fromkeys(chunk, LookupFailed(f"Crossref: HTTP {e.code}")))
                    continue
                # One malformed DOI rejects the whole batch: ask for each on its own.
                out.update({d: _safe(self._crossref)(d) for d in chunk})
                continue
            except Exception as e:  # noqa: BLE001
                out.update(dict.fromkeys(chunk, LookupFailed(f"Crossref: {type(e).__name__}: {cache_key(str(e))}")))
                continue
            found = {(item.get("DOI") or "").lower(): item for item in items}
            for d in chunk:
                item = found.get(d)
                if item is None:
                    out[d] = Record(source="doi.org", doi=d)  # registered elsewhere, as a 404 would say
                    continue
                if store:
                    store(_crossref_url(d), json.dumps({"message": item}))
                out[d] = _crossref_record(item)
        return out

    def _openalex(self, doi: str) -> Record | None:
        try:
            item = json.loads(self.http(f"{_openalex_url(doi)}{self._openalex_params}"))
        except NotFound:
            return None
        return _openalex_record(item)

    def _openalex_many(self, dois: list[str]) -> dict[str, Record | LookupFailed | None]:
        """Batch up to 50 DOIs per OpenAlex filter request; cache each work under its own URL."""
        cached = getattr(self.http, "get", None)
        store = getattr(self.http, "put", None)
        out: dict[str, Record | LookupFailed | None] = {}
        todo: list[str] = []
        for d in dois:
            body = cached(_openalex_url(d)) if cached else None
            if body is not None:
                out[d] = _safe(parse_openalex)(body)
            elif "|" in d or "," in d:
                out[d] = _safe(self._openalex)(d)
            else:
                todo.append(d)
        for chunk in _chunks(todo, 50):
            joined = "|".join(chunk)
            url = (
                f"{OPENALEX_API}?filter=doi:{quote(joined, safe=':/|()')}"
                f"&per-page={len(chunk)}&select={OPENALEX_SELECT}{self._openalex_params}"
            )
            try:
                items = json.loads(self.http(url)).get("results") or []
            except AssertionError:
                raise
            except HTTPError as e:
                if e.code != 400:
                    out.update(dict.fromkeys(chunk, LookupFailed(f"OpenAlex: HTTP {e.code}")))
                    continue
                out.update({d: _safe(self._openalex)(d) for d in chunk})
                continue
            except Exception as e:  # noqa: BLE001
                out.update(
                    dict.fromkeys(chunk, LookupFailed(f"OpenAlex: {type(e).__name__}: {cache_key(str(e))}"))
                )
                continue
            found = {doi_k: item for item in items if (doi_k := _norm_oa_doi(item.get("doi")))}
            for d in chunk:
                item = found.get(d.lower())
                if item is None:
                    out[d] = None
                    continue
                if store:
                    store(_openalex_url(d), json.dumps(item))
                out[d] = _openalex_record(item)
        return out

    def _arxiv_many(self, arxiv_ids: list[str]) -> dict[str, Record | LookupFailed | None]:
        cached = getattr(self.http, "get", None)
        store = getattr(self.http, "put", None)
        out: dict[str, Record | LookupFailed | None] = {}
        todo = []
        for aid in arxiv_ids:
            body = cached(_arxiv_url(aid)) if cached else None
            if body is not None:
                parsed = _safe(parse_arxiv)(body)
                out[aid] = parsed if isinstance(parsed, LookupFailed) else parsed.get(aid)
            else:
                todo.append(aid)
        for chunk in _chunks(todo, 40):
            id_list = ",".join(quote(a, safe="/") for a in chunk)
            url = f"{ARXIV_API}{id_list}&max_results={len(chunk)}"
            try:
                body = self.http(url)
                parsed = parse_arxiv(body)
            except Exception as e:  # noqa: BLE001
                out.update(dict.fromkeys(chunk, LookupFailed(f"arXiv: {type(e).__name__}: {cache_key(str(e))}")))
                continue
            if store and parsed:
                _cache_arxiv_entries(body, store)
            for aid in chunk:
                out[aid] = parsed.get(aid)
        return out


def best_match(ref: Reference, candidates: list[Record]) -> Record | None:
    """The candidate whose title matches the one written in the text.

    A partial title must agree on the year. A book must agree on the year and on the whole title: a
    search for a book mostly finds reviews of it, titled "Thinking, Fast and Slow. New York: … 499 pages."
    """
    best = None
    for c in candidates:
        m = compare_titles(ref.title, c.title)
        years = c.years or ([c.year] if c.year else [])
        same_year = bool(ref.year and any(abs(ref.year - y) <= 1 for y in years))
        if (m == "partial" or ref.book) and not same_year:
            continue
        if ref.book and title_ratio(ref.title, c.title or "") < 0.9:
            continue
        if m == "match":
            return c
        if m == "partial" and best is None:
            best = c
    return best
