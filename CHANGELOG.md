# Changelog

## Unreleased

- A quote in Chinese next to an English abstract or web page is no longer a warning ("quoted text not found").
  It is a translation or paraphrase, so the report notes that it cannot be compared word for word.

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
