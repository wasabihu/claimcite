# Changelog

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
