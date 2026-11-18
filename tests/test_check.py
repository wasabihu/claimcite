import json
import urllib.error
from urllib.parse import quote

import pytest
from conftest import FIXTURES, FakeHttp

from claimcite.check import check_text
from claimcite.evidence import evidence_level
from claimcite.net import NotFound
from claimcite.sources import Lookup, LookupFailed, parse_pubmed

COFFEE_OK = (
    "Kim Y 等 (2019). Coffee consumption and all-cause and cause-specific mortality: "
    "a meta-analysis by potential modifiers. Eur J Epidemiol：「Coffee consumption has been "
    "associated with decreased mortality in previous studies」. "
    "<https://doi.org/10.1007/s10654-019-00524-3>"
)


def run(text, fake_http):
    return check_text(text, Lookup(http=fake_http))


def test_existing_doi_with_matching_title_quote_and_evidence(fake_http):
    (f,) = run(COFFEE_OK, fake_http)
    assert f.status == "ok"
    assert f.exists is True
    assert f.title_match == "match"
    assert f.year_match is True
    assert f.quotes == [
        ("Coffee consumption has been associated with decreased mortality in previous studies", True)
    ]
    assert f.record.pmid == "31055709"
    assert f.evidence.key == "meta_analysis"


def test_doi_that_does_not_exist_is_an_error(fake_http):
    text = "Ghost A (2021). A paper that was never written. Nature. https://doi.org/10.9999/does-not-exist-claimcite"
    (f,) = run(text, fake_http)
    assert f.exists is False
    assert f.status == "error"


def test_real_doi_but_wrong_title_is_an_error(fake_http):
    # The classic fabricated citation: a real DOI attached to a different paper.
    text = (
        "Lee K (2019). Green tea intake reduces stroke risk in Japanese adults. "
        "Stroke. https://doi.org/10.1007/s10654-019-00524-3"
    )
    (f,) = run(text, fake_http)
    assert f.exists is True
    assert f.title_match == "mismatch"
    assert f.status == "error"


def test_wrong_year_and_missing_quote_are_warnings(fake_http):
    text = (
        "Kim Y (2015). Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers. 「coffee cures cancer」 https://doi.org/10.1007/s10654-019-00524-3"
    )
    (f,) = run(text, fake_http)
    assert f.title_match == "match"
    assert f.year_match is False
    assert f.quotes == [("coffee cures cancer", False)]
    assert f.status == "warning"


def test_pmid_lookup_and_rct_evidence(fake_http):
    (f,) = run("ASPREE trial, PMID: 30221596", fake_http)
    assert f.exists is True
    assert f.record.doi == "10.1056/nejmoa1800722"
    assert f.evidence.key == "rct"
    assert f.title_match == "not_stated"
    assert f.status == "ok"


def test_unknown_pmid(fake_http):
    (f,) = run("PMID: 99999999", fake_http)
    assert f.exists is False
    assert f.status == "error"


def test_web_links_are_reported_as_unchecked(fake_http):
    # Web links are now fetched; an unroutable URL fails gracefully instead of
    # being silently skipped (issue #1).
    (f,) = run("WHO (2023). News. https://www.who.int/news/item/x", fake_http)
    assert f.status == "unchecked"
    assert f.web.state == "failed"
    assert any(call.endswith("/robots.txt") for call in fake_http.calls)


def test_evidence_ranking():
    assert (
        evidence_level(["Journal Article", "Randomized Controlled Trial", "Meta-Analysis"], True, False).key
        == "meta_analysis"
    )
    assert evidence_level(["Journal Article"], False, True).key == "animal"
    assert evidence_level(["Case Reports"], True, False).key == "case_report"
    assert evidence_level(["Journal Article"], True, False).key == "unknown"


def test_one_failed_lookup_does_not_sink_the_report(fake_http):
    def flaky(url):
        if "api/handles/10.5555/" in url:
            raise TimeoutError("doi.org timed out")
        return fake_http(url)

    text = "A, https://doi.org/10.5555/timeout\nB, PMID: 30221596"
    first, second = check_text(text, Lookup(http=flaky))
    assert first.status == "unchecked"
    assert "timed out" in first.error
    assert second.status == "ok"


def test_pubmed_requests_are_batched(fake_http):
    text = "PMID: 30221596 and PMID: 31055709 and PMID: 33237064"
    findings = run(text, fake_http)
    assert [f.status for f in findings] == ["ok", "ok", "ok"]
    assert len([u for u in fake_http.calls if "efetch" in u]) == 1


def test_doi_org_404_means_the_doi_does_not_exist(fake_http):
    # doi.org answers HTTP 404 (not just responseCode 100) for unregistered DOIs.
    fake_http.routes = {"api/handles/10.1001/jama.2022.99999": NotFound, **fake_http.routes}
    (f,) = run("Smith A (2022). Vitamin D. JAMA. https://doi.org/10.1001/jama.2022.99999", fake_http)
    assert f.exists is False
    assert f.status == "error"


def test_pubmed_book_article_pmid_is_parsed_not_reported_missing():
    http = FakeHttp({"efetch.fcgi?db=pubmed&id=28230935": "pubmed_book.xml"})
    text = (
        "National Academies (2017). Countering Violent Extremism Through Public Health Practice: "
        'Proceedings of a Workshop: "increase the resilience of communities and individuals". '
        "PMID: 28230935"
    )
    (f,) = check_text(text, Lookup(http=http, workers=1), web=False, search=False)
    assert f.status == "ok"
    assert f.exists is True
    assert f.title_match == "match"
    assert f.year_match is True
    assert f.quotes == [("increase the resilience of communities and individuals", True)]
    assert f.record.doi == "10.17226/24638"
    assert f.evidence.key == "narrative_review"

    chapter_xml = """<PubmedArticleSet><PubmedBookArticle><BookDocument>
      <PMID>30020604</PMID>
      <Book><BookTitle>StatPearls</BookTitle><PubDate><Year>2018</Year></PubDate></Book>
      <ArticleTitle>Essential Hypertension</ArticleTitle>
      <DateRevised><Year>2024</Year></DateRevised>
    </BookDocument></PubmedBookArticle></PubmedArticleSet>"""
    rec = parse_pubmed(chapter_xml)["30020604"]
    assert rec.title == "Essential Hypertension"
    assert set(rec.years) == {2018, 2024}


def test_parenthesized_sici_doi_quotes_esearch_term_and_hits_pubmed():
    doi = "10.1016/s0140-6736(97)11096-0"
    quoted_term = quote('"' + doi + '"[doi]', safe="")
    http = FakeHttp(
        {
            f"api/handles/{doi}": "handle_retracted.json",
            "api/handles/10.1016/s0140-6736%2897%2911096-0": "handle_retracted.json",
            f"term={quoted_term}": "esearch_wakefield.json",
            "efetch.fcgi?db=pubmed&id=9500320": "pubmed_retracted.xml",
        }
    )
    (f,) = check_text(f"Wakefield (1998). doi:{doi}", Lookup(http=http, workers=1), web=False, search=False)
    assert f.retracted is True
    assert f.status == "error"
    assert f.record.source == "pubmed"
    assert f.record.pmid == "9500320"


def test_pubmed_efetch_chunk_failure_preserves_succeeded_chunks():
    efetch_ok = (FIXTURES / "pubmed_efetch.xml").read_text(encoding="utf-8")
    pmids = ["30221596", *[str(1000000 + i) for i in range(155)]]

    def chunk_flaky(url):
        if "efetch.fcgi" in url:
            if "1000150" in url:
                raise TimeoutError("second chunk timed out")
            return efetch_ok
        raise AssertionError(f"unexpected url: {url}")

    lookup = Lookup(http=chunk_flaky, workers=1)
    lookup.prefetch(pmids=pmids)
    assert lookup.pmid("30221596") is not None
    assert lookup.pmid("1000001") is None
    with pytest.raises(LookupFailed):
        lookup.pmid("1000150")


def test_pubmed_esearch_failure_warns_on_stderr_without_leaking_api_key_and_falls_back_to_crossref(
    monkeypatch, capsys
):
    monkeypatch.setenv("NCBI_API_KEY", "SECRET_NCBI_KEY")
    crossref_item = json.loads((FIXTURES / "crossref_work.json").read_text(encoding="utf-8"))["message"]
    crossref_filter = json.dumps({"status": "ok", "message": {"items": [crossref_item]}})
    handle_ok = (FIXTURES / "handle_found.json").read_text(encoding="utf-8")

    def http(url):
        if url == "https://doi.org/api/handles/10.1007/s10654-019-00524-3":
            return handle_ok
        if "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in url:
            raise urllib.error.URLError(f"request failed for {url}")
        if url == "https://api.crossref.org/works?filter=doi:10.1007/s10654-019-00524-3&rows=1":
            return crossref_filter
        raise AssertionError(f"unexpected url: {url}")

    (f,) = check_text(
        "Kim Y (2019). Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers. doi:10.1007/s10654-019-00524-3",
        Lookup(http=http, workers=1),
        web=False,
        search=False,
    )
    assert f.status == "ok"
    assert f.record.source == "crossref"
    err = capsys.readouterr().err
    assert "claimcite: PubMed" in err
    assert "SECRET_NCBI_KEY" not in err
