from claimcite.check import check_text
from claimcite.evidence import evidence_level
from claimcite.net import NotFound
from claimcite.sources import Lookup

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
