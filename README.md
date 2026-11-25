# claimcite

[![wasabihu/claimcite MCP server](https://glama.ai/mcp/servers/wasabihu/claimcite/badges/score.svg)](https://glama.ai/mcp/servers/wasabihu/claimcite)

**English** · [中文](README.zh-CN.md)

Check that every citation in a document is real, points to the paper the text says it does, and actually contains the words quoted from it.

AI-written articles, health advice and even papers increasingly cite sources that don't exist, or attach a real DOI to the wrong paper. Readers can't check dozens of references by hand. claimcite does it in one command.

```console
$ claimcite check examples/sample.md
# Citation check

1 ok · 2 warnings · 2 errors · 0 not checked

- **Error** · line 7 · `doi:10.1016/s0140-6736(10)61388-8`
  - Title does not match this identifier. Registry title: Worldwide burden of disease from
    exposure to second-hand smoke: a retrospective analysis of data from 192 countries.
  - Year differs: document 2021, registry 2011
- **Error** · line 9 · `doi:10.1001/jama.2022.99999`
  - Identifier does not exist
- **Warning** · line 11 · `pmid:33237064`
  - Study type (as tagged by PubMed): Systematic review / meta-analysis
  - Quoted text not found in the abstract (it may be in the full text): “calcium and vitamin D
    supplementation cured osteoporosis in all women”
...
```

## What it checks today

| Check | How |
|---|---|
| The DOI / PMID / arXiv ID exists | doi.org handle API, PubMed, arXiv Atom API (including `10.48550/arXiv.*`) |
| The paper has not been retracted | PubMed `Retracted Publication` / `RetractionIn`, Crossref `updated-by` & `relation.is-retracted-by`, OpenAlex `is_retracted` |
| The title written in the text matches the identifier | PubMed, Crossref, OpenAlex, or arXiv; fuzzy match (ignoring publisher `RETRACTED:` prefix) |
| The year matches (±1 for online-first vs print) | same (published & updated years for arXiv) |
| Text in quotes (`"…"`, `“…”`, `「…」`) appears in the abstract | PubMed / Crossref / OpenAlex (`abstract_inverted_index`) / arXiv abstract, falling back to open-access full text (PubMed Central, Europe PMC) when needed |
| Study type: meta-analysis, RCT, observational, animal… | PubMed publication types and MeSH |
| A web link is alive and contains the quoted text | fetch the page (respecting `robots.txt`), strip HTML, normalise whitespace |
| A reference with no DOI or PMID exists | search the title in Crossref, then PubMed; then check it like any other paper |
| Numbered and footnote citations match the reference list | report body markers like `[5]` or `[^1]` with no entry (with line numbers) and numbered/footnote entries never cited in the text |
| Local XERJ evidence spans (`xerj://`) exist and match verbatim quotes | batch read-only `POST /_mget` (fallback `GET /{index}/_doc/{doc_id}`), exact UTF-8 byte (`#b=`/`#offset=`) or char (`#c=`) span verification, zero SQLite cache writes |

It reads Markdown or plain text (unwrapping soft-wrapped lines and indented reference items) and understands the common
`Author (Year). Title. Journal: "quote". https://doi.org/…` pattern, quotes written in the same sentence after an identifier, plus `doi:…`, bare DOIs,
`PMID: …`, `arXiv:…` and PubMed/arXiv links. Numbered and author-year citations work too: a quote in the text followed by `[1]`, `[^1]`,
`[1, 2]`, `[1-3]` or an unambiguous `(Author, Year)` / `Author (Year)` citation is checked against the matching entry in the reference list, and dangling markers or uncited numbered/footnote entries are flagged as errors.

Most invented citations have no DOI: `Chen, L. (2021). A plausible title. Journal of Something, 14(2), 88–97.`
Entries like that under a reference-list heading (`## References`, `参考文献`, `Sources` …), and `[1] …` or
`[^1]: …` entries anywhere, are searched by title in Crossref and then PubMed. APA, Harvard, MLA, Vancouver and
GB/T 7714 styles are understood. A paper that is found is checked like one with a DOI. One that neither registry
has is a **warning**, not an error: it may not exist, or the title may be worded differently. Books (most are in
neither registry) and Chinese-language references are left *not checked*. Searches run one a second (Crossref's
limit), so 30 such references take about half a minute the first time. `--no-search` turns this off. Code blocks and inline code are skipped. A quote in Chinese is only compared with Chinese text: next to an English abstract or page it is
noted as not comparable word for word, not reported as missing. Other web links are fetched (respecting `robots.txt`) and checked for
availability and for the quoted text. A dead link or a missing quote is a **warning**, never an error —
pages change and some sites block bots. When a page cannot be read the link stays *not checked*.

**What it does not do (yet):** check paraphrases. Only text in quotation marks is compared with the source, word for word; a sentence that restates a source without quotes is not extracted or checked. It also cannot decide whether a source *supports* a claim. That needs a language model and a
labelled test set. See the roadmap. claimcite only confirms that sources exist and match what the text says
about them. It is not medical advice.

## Install and run

Requires Python 3.10+. No API keys needed.

```bash
pipx install claimcite                   # or: uv tool install claimcite
claimcite check article.md               # Markdown report
claimcite check article.md --lang zh     # 中文报告
claimcite check article.md --format json # for scripts and CI
cat article.md | claimcite check -
```

The exit code is 1 when any citation has an error, so it works as a CI gate (`--fail-on warning|never` to tune).
Set `NCBI_API_KEY` to raise PubMed's rate limit. OpenAlex works out of the box with no API key (`OPENALEX_API_KEY` and `OPENALEX_MAILTO` are optional).

### Lookup cache

Successful registry responses are cached on disk for **30 days** (default `~/.cache/claimcite/v1/`,
or `$XDG_CACHE_HOME/claimcite/v1/`), so re-running after a one-line edit does not repeat every
request. Lookup failures are never cached. Use `--no-cache` to bypass the cache for one run.

Web pages are fetched at most once a second per site, so the first run over a document with hundreds of
links takes minutes. Use `--no-web` to check only DOIs and PMIDs. Quotes in PDFs are not checked yet.

### Local XERJ span verification (`xerj://`)

`claimcite` verifies host-free local knowledge-base evidence handles emitted by [`kbwall`](https://github.com/wasabihu/kbwall) against a local [`XERJ`](https://github.com/xerj-org/xerj) node (still zero runtime dependencies, Python 3.10+ stdlib only):

- **Shared URI contract** (strictly no `host:port` inside documents):
  - `xerj://<index>/<doc_id>#b=<byte_start>[-<byte_end>][&f=<field>]`
  - `xerj://<index>/<doc_id>#c=<char_start>[-<char_end>][&f=<field>]`
  - `xerj://<index>/<doc_id>#offset=<byte_start>[&f=<field>]` (alias for `#b=<byte_start>`)
  - When `f=` is omitted, `_source` text fields are probed deterministically in order: `text -> body -> content -> code`.

```bash
# Point to your local loopback XERJ node (never auto-probed when unset)
claimcite check notes.md --xerj-url http://127.0.0.1:9200
XERJ_URL=http://127.0.0.1:9200 claimcite check notes.md --format json
```

- **CLI flags & precedence**: `--xerj-url` > `XERJ_URL`; credentials follow `--xerj-api-key` > `XERJ_AUTH` (preserves `ApiKey`/`Bearer`) > `XERJ_API_KEY` (auto-prefixed `ApiKey `) > `<xerj_data_dir>/admin.key` (`--xerj-data-dir` / `XERJ_DATA_DIR`). Optional guards: `--xerj-allow-remote` (forces `https://` unless `--xerj-allow-insecure-http` is also passed) and `--xerj-strict-whitespace`.
- **Hard security & privacy boundaries**: loopback-only by default (`127.0.0.1`, `::1`, `localhost`), explicit proxy bypass (`ProxyHandler({})`), all `3xx` redirects blocked, 10 MiB hard response cap, read-only batch `POST /_mget` (fallback `GET /{index}/_doc/{doc_id}`), and **never writes local XERJ documents into the 30-day SQLite registry cache**.
- **MCP isolation**: when running `claimcite mcp`, XERJ settings are read strictly from process environment variables (`XERJ_URL`, `XERJ_AUTH`, `XERJ_API_KEY`, `XERJ_DATA_DIR`) and are never exposed as MCP tool arguments.

### In CI: GitHub Action

```yaml
# .github/workflows/citations.yml
on: [push, pull_request]
jobs:
  claimcite:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: wasabihu/claimcite@v0.6.0
        with:
          files: "docs/**/*.md"  # default: every Markdown file
          fail-on: error         # or: warning, never
          web: "true"            # "false" checks DOIs and PMIDs only
          search: "true"         # "false" skips title search for references without a DOI
```

The report also appears in the job summary. Runs on Linux and macOS runners.

### Before each commit: pre-commit

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/wasabihu/claimcite
    rev: v0.6.0
    hooks:
      - id: claimcite
        # args: [--no-web]   # faster: skip ordinary web links
```

Every changed Markdown file is checked; the commit stops when a citation has an error. You can also pass
several files on the command line: `claimcite check docs/*.md`, or a whole folder: `claimcite check docs/`.

### MCP server & Agent Skill

`claimcite` ships with a zero-dependency stdio [Model Context Protocol](https://modelcontextprotocol.io/) server (`claimcite mcp`) exposing `check_citations` and `check_files`:

```json
{
  "mcpServers": {
    "claimcite": {
      "command": "uvx",
      "args": ["claimcite", "mcp"]
    }
  }
}
```

A ready-to-use Agent Skill guide lives in [`skills/claimcite/SKILL.md`](skills/claimcite/SKILL.md).

### Offline benchmark

Run the 26-case fixture-backed benchmark locally (zero network calls):

```bash
uv run python benchmarks/run.py
```

See [`benchmarks/README.md`](benchmarks/README.md) for what each category covers.

## Roadmap

Each item is a self-contained piece of work. Pick one and open an issue to say you're on it.

- [x] **Web links**: check that the page is alive and contains the quoted text
- [x] **References without identifiers**: find them by title (Crossref, PubMed)
- [x] **Retractions**: flag retracted papers via Crossref (`updated-by` / `relation`) and PubMed (`Retracted Publication`)
- [ ] **More registries**: Semantic Scholar, CNKI/Wanfang metadata (`OpenAlex`, `arXiv`, and DataCite `10.48550/arXiv.*` are supported)
- [x] **Full text**: look for quotes in open-access full text (PubMed Central, Europe PMC), not only abstracts
- [ ] **Support check**: does the source support the sentence? Pluggable LLM judge, local models first
- [x] **Benchmark**: offline 26-case fixture benchmark in [`benchmarks/`](benchmarks/README.md) (more public corpora welcome)
- [x] **Numbered citations**: `[1]`, `[1-3]` and footnotes `[^1]` linked to the reference list (including missing-entry & uncited-entry detection)
- [ ] **More input formats**: BibTeX, Word / PDF input
- [x] **Integrations**: GitHub Action, pre-commit hook, stdio MCP server (`claimcite mcp`), Agent Skill
- [ ] Browser extension
- [x] **Caching** between runs (30-day on-disk cache, `--no-cache` to bypass)
- [ ] Faster batch lookups

## Verified XERJ Open Ecosystem

| Stage | Project | Role |
|---|---|---|
| **1. Capture** | **[`wasabihu/sessfork`](https://github.com/wasabihu/sessfork)** | Silent background Chromium auth fork (`sessfork-mcp` / `sessfork-browser`) and secret-redacting authenticated web ingestion into local XERJ. |
| **2. Search Engine & Open Models** | **[`xerj-org/xerj`](https://github.com/xerj-org/xerj)**<br>**[`wasabihu/xerj-bilingual-fin-law-384`](https://huggingface.co/wasabihu/xerj-bilingual-fin-law-384)** *(MIT)*<br>**[`wasabihu/VeriCorpus-Reranker-0.6B`](https://huggingface.co/wasabihu/VeriCorpus-Reranker-0.6B)** *(Apache-2.0)* | Single-binary Rust ES-compatible search engine paired with compact bilingual CJK/EN financial & legal embedding and cross-encoder reranker models. |
| **3. Read-Only Agent Firewall** | **[`wasabihu/kbwall`](https://github.com/wasabihu/kbwall)** | `~1 MB` air-gapped, loopback-only read-only Rust MCP firewall for XERJ that emits canonical `xerj://` byte-span evidence handles. |
| **4. Citation & Corpus Verification** | **[`wasabihu/claimcite`](https://github.com/wasabihu/claimcite)**<br>**[`wasabihu/vericorpus`](https://github.com/wasabihu/vericorpus)** | Zero-dependency citation & `xerj://` UTF-8 span verifier paired with audit-grade statutory, financial, and clinical retrieval corpora. |

## Related project

Looking to build a minimal, audited AI agent runtime in Python? Check out our sister project: **[cairnkit](https://github.com/wasabihu/cairnkit)** (includes a citation-verifying tool example wired cleanly with `claimcite`).

## Contributing

Contributions of every size are welcome, including AI-assisted ones. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
