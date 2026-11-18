# claimcite Offline Benchmark

A small, deterministic 26-case benchmark built exclusively from real recorded responses in [`tests/fixtures/`](../tests/fixtures/) (no synthetic API payloads and no network calls required at run time).

## Running it

```bash
uv run python benchmarks/run.py
uv run python benchmarks/run.py --json
```

It exits `0` only when all 26 cases match their expected verification status (`ok`, `warning`, `error`, `not_checked`).

## Case coverage (`cases.jsonl`)

| Status | Count | Covered failure & verification modes |
|---|---:|---|
| `ok` | 11 | Valid DOI, valid PMID, bare `arXiv:` ID, DataCite `10.48550/arXiv.*` DOI, OpenAlex DataCite DOI, Crossref-without-abstract enriched via OpenAlex `abstract_inverted_index`, unlinked title search, live web quote, retraction notice itself (not flagged), CJK summary next to an English paper, body-only quote verified in PMC open-access full text |
| `not_checked` | 1 | Book citation with publisher and ISBN |
| `warning` | 6 | Wrong stated year, missing abstract quote on arXiv, invented unlinked reference, dead 404 web link, quote inside `<script>` tag, fabricated quote missing from both abstract and open-access PMC full text |
| `error` | 8 | Fake DOI, fake PMID, fake arXiv ID, swapped/mismatched title, retracted paper via PubMed, retracted paper via Crossref, dangling body citation marker (`[2]`), uncited footnote entry (`[^1]`) |

Contributions adding additional recorded real-world fixtures and labelled edge cases are welcome!
