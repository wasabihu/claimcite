# claimcite

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
| The DOI / PMID exists | doi.org handle API (every registrar), PubMed |
| The title written in the text matches the identifier | PubMed, then Crossref; fuzzy match |
| The year matches (±1 for online-first vs print) | same |
| Text in quotes (`"…"`, `“…”`, `「…」`) appears in the abstract | PubMed / Crossref abstract |
| Study type: meta-analysis, RCT, observational, animal… | PubMed publication types and MeSH |
| A web link is alive and contains the quoted text | fetch the page (respecting `robots.txt`), strip HTML, normalise whitespace |

It reads Markdown or plain text and understands the common
`Author (Year). Title. Journal: "quote". https://doi.org/…` pattern, plus `doi:…`, bare DOIs,
`PMID: …` and PubMed links. Other web links are fetched (respecting `robots.txt`) and checked for
availability and for the quoted text. A dead link or a missing quote is a **warning**, never an error —
pages change and some sites block bots. When a page cannot be read the link stays *not checked*.

**What it does not do (yet):** decide whether a source *supports* a claim. That needs a language model and a
labelled test set. See the roadmap. claimcite only confirms that sources exist and match what the text says
about them. It is not medical advice.

## Install and run

Requires Python 3.10+. No API keys needed.

```bash
uv tool install git+https://github.com/wasabihu/claimcite   # or: pipx install git+...
claimcite check article.md               # Markdown report
claimcite check article.md --lang zh     # 中文报告
claimcite check article.md --format json # for scripts and CI
cat article.md | claimcite check -
```

The exit code is 1 when any citation has an error, so it works as a CI gate (`--fail-on warning|never` to tune).
Set `NCBI_API_KEY` to raise PubMed's rate limit.

### Lookup cache

Successful registry responses are cached on disk for **30 days** (default `~/.cache/claimcite/v1/`,
or `$XDG_CACHE_HOME/claimcite/v1/`), so re-running after a one-line edit does not repeat every
request. Lookup failures are never cached. Use `--no-cache` to bypass the cache for one run.

## Roadmap

Each item is a self-contained piece of work. Pick one and open an issue to say you're on it.

- [x] **Web links**: check that the page is alive and contains the quoted text
- [ ] **More registries**: OpenAlex, Semantic Scholar, arXiv, CNKI/Wanfang metadata, DataCite
- [ ] **Retractions**: flag retracted papers (Crossref / Retraction Watch data)
- [ ] **Full text**: look for quotes in open-access full text (PubMed Central, Europe PMC), not only abstracts
- [ ] **Support check**: does the source support the sentence? Pluggable LLM judge, local models first
- [ ] **Benchmark**: a public, labelled set of real and fabricated citations to measure every check
- [ ] **Citation styles**: numbered references `[1]`, footnotes `[^1]`, BibTeX, Word / PDF input
- [ ] **Integrations**: GitHub Action, pre-commit hook, browser extension
- [x] **Caching** between runs (30-day on-disk cache, `--no-cache` to bypass)
- [ ] Faster batch lookups

## Contributing

Contributions of every size are welcome, including AI-assisted ones. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
