# Changelog

## 0.6.0 (2026-11-18)

- **Numbered & footnote citation audit (`closes #11`)**: flag dangling `[n]` / `[^note]` markers in the body with no matching reference entry (reporting all cited line numbers in Markdown and `used_lines` in JSON) as well as numbered/footnote entries never cited in the text (`uncited: true` / `error`), while guarding against false alarms on statistical/IQR intervals (such as `60 [52-68] years`, `[0, 1]`, or `[1-5] scale`), quote-internal bracket numbers, Markdown reference links, and unnumbered bibliographies with no body markers.
- **OpenAlex batch lookup & inverted-index abstracts (`closes #4`)**: query `api.openalex.org` in batches of up to 50 DOIs (`filter=doi:a|b|...`) whenever PubMed has no record and Crossref is missing metadata (such as DataCite DOIs) or lacks an abstract, reconstruct plain abstracts from `abstract_inverted_index`, and merge publication years, PMIDs/PMCIDs, and `is_retracted` signals (with `OPENALEX_API_KEY` and `OPENALEX_MAILTO` optional and stripped from cache keys and error logs).
- **Expanded 26-case offline benchmark**: added 4 offline cases covering dangling body markers, uncited footnote references, non-Crossref DataCite resolution via OpenAlex, and Crossref-without-abstract enrichment via OpenAlex `abstract_inverted_index`, backed by 8 new real recorded fixtures in [`tests/fixtures/`](tests/fixtures/).

## 0.5.0 (2026-11-18)

- **Open-access full-text quote fallback (PMC & Europe PMC, closes #10)**: when an abstract misses a quote (or no abstract is available in PubMed/Crossref), check NCBI PMC `efetch` and open-access Europe PMC JATS `fullTextXML` on demand—verified against real open-access cases (e.g. `10.1371/journal.pntd.0013247` and `10.1136/bmjgh-2024-018012`), skipping non-OA entries before calling `/fullTextXML`.
- **JATS markup & nested reference cleanup**: strip `<xref>` footnote markers and publisher `<ref-list>` sections nested inside `<body>` before matching quotes, while separating block tags cleanly (`<p>`, `<title>`, `<sec>`, `<td>`, `<li>`, etc.).
- **Richer reporting & JSON metadata**: distinguish warnings checked against both abstract and open-access full text in English and Chinese reports, add `Record.pmcid` (`registry.pmcid`) and `Finding.fulltext_checked` (`fulltext_checked`) to JSON output while keeping `found_in_abstract` backward-compatible.
- **Expanded 22-case offline benchmark**: added recorded real PMC open-access full-text hit and miss cases alongside 10 real fixtures in [`tests/fixtures/`](tests/fixtures/).

## 0.4.1 (2026-11-18)

- **TUN Fake-IP DNS compatibility**: allow public hostnames whose DNS resolves into `198.18.0.0/15` (used by Clash/Mihomo/Surge TUN Fake-IP proxies) while continuing to block literal non-public IPs and domains resolving to loopback, RFC 1918 private networks, link-local IMDS, or IPv6 ULA addresses.
- **PubMed error visibility & secret redaction**: emit a sanitized `stderr` warning when batch PubMed DOI `esearch` fails before falling back to Crossref, and strip `api_key` parameters from all `LookupFailed` error messages.

## 0.4.0 (2026-11-16)

- **Multi-line & author-year extraction**: unwrap soft-wrapped paragraphs and indented multi-line reference items while preserving exact source line numbers; attach years and quotes written in the same sentence after an identifier; link unambiguous `(Author, Year)` and `Author (Year)` body quotes to reference-list entries.
- **Zero-false-alarm extraction guards**: skip fenced code blocks and inline code spans in main identifier extraction, and trim unbalanced trailing brackets (`]`, `}`, `)`) and Markdown emphasis markers (`*`) from DOIs and URLs.
- **PubMed Bookshelf & SICI DOI precision**: parse `<PubmedBookArticle>` records (such as StatPearls and NCBI Bookshelf chapters/reports) instead of reporting valid PMIDs as missing; quote `[doi]` terms in PubMed `esearch` so parenthesized DOIs resolve in PubMed; isolate `efetch` errors per chunk.
- **Cross-file batch prefetch & cache split**: extract and deduplicate identifiers across all checked files before prefetching registries in one pass (`check_documents`); decouple CLI and MCP via `claimcite.workspace`; deduplicate ordinary web pages in memory per run without writing raw HTML to the 30-day SQLite registry cache, and prune expired cache rows on startup.
- **Network & MCP hardening**: block private, loopback, and cloud-metadata IPs and non-HTTP(S) redirects (SSRF protection); stop reading binary/PDF streams after a 1 KB header peek; decode `GB2312`/`GBK`/`GB18030` web pages cleanly; restrict MCP `check_files` to supported text/Markdown suffixes.

## 0.3.0 (2026-11-15)

- **Retraction detection**: flag retracted papers via PubMed (`Retracted Publication` / `RetractionIn`) and Crossref (`updated-by` / `relation.is-retracted-by`), reporting the retraction notice DOI when available without flagging retraction notices themselves.
- **arXiv & DataCite `10.48550/arXiv.*` support**: extract `arXiv:YYMM.NNNNN`, legacy archive IDs, and `arxiv.org/abs|pdf|html` URLs; verify titles, publication/update years, and abstracts via batched `export.arxiv.org` Atom queries with per-ID caching and a 3 s minimum request interval.
- **Zero-dependency MCP server & Agent Skill**: added `claimcite mcp` over stdio (supporting both newline-delimited JSON-RPC and `Content-Length` framing, exposing `check_citations` and `check_files`) plus [`skills/claimcite/SKILL.md`](skills/claimcite/SKILL.md).
- **20-case offline benchmark**: added [`benchmarks/`](benchmarks/README.md) (`benchmarks/cases.jsonl` and `benchmarks/run.py`) backed by recorded real fixtures in [`tests/fixtures/`](tests/fixtures/).
- **Scope clarity**: document explicitly that unquoted paraphrases are not checked.

## 0.2.1 (2026-11-09)

- A DOI inside a publisher link (`frontiersin.org/…/10.3389/fpsyg.2021.744551/full`, `…/10.1057/sj.2014.30.pdf`)
  no longer takes the page name with it. Before, a real paper was reported as a DOI that does not exist.
- `claimcite check docs/` checks the Markdown and text files in a folder; before, it stopped with a Python
  traceback. A missing or unreadable file is a one-line message and exit code 2.

## 0.2.0 (2026-11-09)

- A quote in Chinese next to an English abstract or web page is no longer a warning ("quoted text not found").
  It is a translation or paraphrase, so the report notes that it cannot be compared word for word.
- Crossref is asked for 20 DOIs per request, one request at a time, as its public pool requires
  (x-concurrency-limit: 1). Before, eight requests went out at once and some got HTTP 429. Each work is
  still cached on its own, so adding a citation does not refetch the others.
- References with no DOI or PMID (`Smith J (2019). Title. Journal, 3, 1-9.`) in a reference list are searched by
  title in Crossref, then PubMed. Before, they were skipped and the report said "0 ok · 0 warnings · 0 errors",
  which looked like a clean pass. Not found is a warning; books and Chinese references stay not checked.
  `--no-search` (Action input `search: "false"`) turns it off.
- A document with no citations at all now says so.

## 0.1.0 (2026-11-09)

First release on PyPI: `pipx install claimcite`.

- Check that each DOI and PMID exists (doi.org handle API for every registrar, PubMed).
- Compare the title and year written in the text with the registry (PubMed, then Crossref).
- Look for quoted text (`"…"`, `“…”`, `「…」`) in the abstract.
- Show the study type PubMed records: meta-analysis, RCT, observational, animal study…
- Web links: check that the page is alive and contains the quoted text, respecting `robots.txt`.
- Numbered citations: a quote followed by `[1]`, `[^1]`, `[1, 2]` or `[1-3]` is checked against that
  entry in the reference list.
- Check several files in one run; GitHub Action and pre-commit hook.
- Markdown report in English or Chinese, JSON for scripts, exit code 1 on errors for CI.
- 30-day on-disk cache of registry answers.
