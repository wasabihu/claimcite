"""Tests for OpenAlex metadata lookup, batching, abstract reconstruction, and credential redaction."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
from urllib.parse import quote

from conftest import FIXTURES, FakeHttp

from claimcite.cache import CachedHttp, cache_key
from claimcite.check import check_text
from claimcite.sources import Lookup, parse_openalex, reconstruct_abstract


def _doi_route(doi: str) -> str:
    return "term=" + quote('"' + doi + '"[doi]', safe="")


def test_reconstruct_abstract_handles_positions_gaps_and_markup():
    assert reconstruct_abstract(None) is None
    assert reconstruct_abstract({}) is None
    inv = {"We": [0], "evaluate": [1, 4], "<i>models</i>": [2], "and": [3], "data.": [10]}
    assert reconstruct_abstract(inv) == "We evaluate models and evaluate data."


def test_datacite_doi_missing_from_crossref_resolves_via_openalex():
    doi = "10.7275/jyj1-4868"
    http = FakeHttp(
        {
            f"doi.org/api/handles/{doi}": "handle_datacite_efa.json",
            _doi_route(doi): "esearch_empty.json",
            f"api.crossref.org/works?filter=doi:{doi}&rows=1": "crossref_filter_datacite_efa.json",
            f"api.openalex.org/works?filter=doi:{doi}": "openalex_datacite_efa.json",
        }
    )
    quote_text = "Exploratory factor analysis (EFA) is a complex, multi-step process"
    doc = (
        "Costello AB, Osborne J (2005). Best practices in exploratory factor analysis: "
        "four recommendations for getting the most from your analysis. Practical Assessment, "
        f'Research, and Evaluation: "{quote_text}". doi:{doi}'
    )
    (f,) = check_text(doc, Lookup(http=http, workers=1), web=False, search=False)
    assert f.status == "ok"
    assert f.exists is True
    assert f.record is not None
    assert f.record.source == "openalex"
    assert f.title_match == "match"
    assert f.year_match is True
    assert f.quotes == [(quote_text, True)]

    oa_payload = json.loads((FIXTURES / "openalex_datacite_efa.json").read_text(encoding="utf-8"))["results"][0]
    direct = parse_openalex(json.dumps(oa_payload))
    assert direct.source == "openalex"
    assert direct.doi == doi
    assert direct.year == 2005


def test_crossref_record_without_abstract_is_enriched_by_openalex_for_quote_check():
    doi = "10.1145/3442188.3445922"
    http = FakeHttp(
        {
            f"doi.org/api/handles/{doi}": "handle_acm_parrots.json",
            _doi_route(doi): "esearch_empty.json",
            f"api.crossref.org/works?filter=doi:{doi}&rows=1": "crossref_filter_acm_parrots.json",
            f"api.openalex.org/works?filter=doi:{doi}": "openalex_acm_parrots.json",
        }
    )
    real_quote = (
        "Using these pretrained models and the methodology of fine-tuning them for specific tasks, "
        "researchers have extended the state of the art"
    )
    ok_doc = (
        "Bender EM et al. (2021). On the Dangers of Stochastic Parrots. FAccT: "
        f'"{real_quote}". doi:{doi}'
    )
    (f_ok,) = check_text(ok_doc, Lookup(http=http, workers=1), web=False, search=False)
    assert f_ok.status == "ok"
    assert f_ok.record is not None
    assert f_ok.record.source == "crossref"
    assert f_ok.quotes == [(real_quote, True)]

    fake_quote = "stochastic parrots never exhibit training bias"
    warn_doc = (
        "Bender EM et al. (2021). On the Dangers of Stochastic Parrots. FAccT: "
        f'"{fake_quote}". doi:{doi}'
    )
    (f_warn,) = check_text(warn_doc, Lookup(http=http, workers=1), web=False, search=False)
    assert f_warn.status == "warning"
    assert f_warn.quotes == [(fake_quote, False)]


def test_openalex_batches_fifty_dois_caches_individually_and_redacts_credentials(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OPENALEX_API_KEY", "SECRET_OA_KEY")
    monkeypatch.setenv("OPENALEX_MAILTO", "private@example.org")

    batch_calls: list[str] = []
    acm = json.loads((FIXTURES / "openalex_acm_parrots.json").read_text(encoding="utf-8"))["results"][0]
    efa = json.loads((FIXTURES / "openalex_datacite_efa.json").read_text(encoding="utf-8"))["results"][0]

    def batch_http(url: str) -> str:
        batch_calls.append(url)
        return json.dumps({"results": [acm, efa]})

    dois = ["10.1145/3442188.3445922", "10.7275/jyj1-4868", *[f"10.1000/oax{i}" for i in range(53)]]
    cached = CachedHttp(batch_http, cache_dir=tmp_path)
    out = Lookup(http=cached, workers=1)._openalex_many(dois)

    assert len(batch_calls) == 2
    assert [u.count("10.") for u in batch_calls] == [50, 5]
    assert all("mailto=private%40example.org" in u or "mailto=private@example.org" in u for u in batch_calls)
    assert all("api_key=SECRET_OA_KEY" in u for u in batch_calls)
    assert out["10.1145/3442188.3445922"].year == 2021
    assert out["10.7275/jyj1-4868"].year == 2005
    assert out["10.1000/oax0"] is None

    # Subsequent run adding only one new DOI fetches just that DOI (cached works stay offline).
    followup_calls: list[str] = []

    def next_http(url: str) -> str:
        followup_calls.append(url)
        return json.dumps({"results": []})

    out2 = Lookup(http=CachedHttp(next_http, cache_dir=tmp_path), workers=1)._openalex_many(
        ["10.1145/3442188.3445922", "10.7275/jyj1-4868", "10.1000/oanew"]
    )
    assert len(followup_calls) == 1
    assert "filter=doi:10.1000/oanew" in followup_calls[0]
    assert out2["10.1145/3442188.3445922"].year == 2021

    # Credentials never appear in cache keys or on disk; each hit work has its own cache row.
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        urls = [row[0] for row in db.execute("SELECT url FROM responses").fetchall()]
    assert any("works/https://doi.org/10.1145/3442188.3445922" in u for u in urls)
    assert any("works/https://doi.org/10.7275/jyj1-4868" in u for u in urls)
    assert all(
        "SECRET_OA_KEY" not in u and "private@example.org" not in u and "private%40example.org" not in u
        for u in urls
    )
    assert "SECRET_OA_KEY" not in cache_key(batch_calls[0])
    assert "private" not in cache_key(batch_calls[0])

    # When Crossref already succeeded and OpenAlex fails, warn cleanly without leaking creds or failing check.
    doi = "10.1145/3442188.3445922"
    handle_acm = (FIXTURES / "handle_acm_parrots.json").read_text(encoding="utf-8")
    esearch_empty = (FIXTURES / "esearch_empty.json").read_text(encoding="utf-8")
    cr_acm = (FIXTURES / "crossref_filter_acm_parrots.json").read_text(encoding="utf-8")

    def oa_flaky(url: str) -> str:
        if url == f"https://doi.org/api/handles/{doi}":
            return handle_acm
        if "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in url:
            return esearch_empty
        if url == f"https://api.crossref.org/works?filter=doi:{doi}&rows=1":
            return cr_acm
        if url.startswith("https://api.openalex.org/"):
            raise urllib.error.URLError(f"request failed for {url}")
        raise AssertionError(f"unexpected url: {url}")

    (f_fallback,) = check_text(
        f"Bender EM et al. (2021). On the Dangers of Stochastic Parrots. doi:{doi}",
        Lookup(http=oa_flaky, workers=1),
        web=False,
        search=False,
    )
    assert f_fallback.status == "ok"
    assert f_fallback.record is not None and f_fallback.record.source == "crossref"
    err = capsys.readouterr().err
    assert "claimcite: OpenAlex" in err
    assert "SECRET_OA_KEY" not in err
    assert "private@example.org" not in err and "private%40example.org" not in err
