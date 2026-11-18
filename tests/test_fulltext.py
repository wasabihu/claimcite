"""Tests for optional open-access full-text quote verification (PMC & Europe PMC)."""

from __future__ import annotations

import json

from conftest import FIXTURES

from claimcite.check import check_text
from claimcite.models import Record
from claimcite.report import to_json, to_markdown
from claimcite.sources import Lookup, parse_jats_fulltext, parse_pubmed
from claimcite.text import quote_found


def test_parse_pubmed_extracts_article_pmcid_and_ignores_reference_list():
    xml = (FIXTURES / "pubmed_oa_40132810.xml").read_text(encoding="utf-8")
    rec = parse_pubmed(xml)["40132810"]
    assert rec.pmcid == "PMC11938218"


def test_parse_jats_fulltext_strips_xref_separates_blocks_and_returns_none_without_body():
    oa_xml = (FIXTURES / "epmc_fulltext_PMC11938218.xml").read_text(encoding="utf-8")
    body = parse_jats_fulltext(oa_xml)
    assert body is not None
    # <xref rid="R1" ref-type="bibr"><sup>1</sup></xref> sits between "2024." and "While".
    assert quote_found(
        "68.3 million internally displaced people (IDP) alone in 2024. While trends in conflicts",
        body,
    )
    # <back>/<ref-list> must stay excluded so reference titles cannot satisfy body quotes.
    assert "Excess mortality in refugees" not in body

    closed_xml = (FIXTURES / "pmc_closed_PMC3233762.xml").read_text(encoding="utf-8")
    assert parse_jats_fulltext(closed_xml) is None


def test_pubmed_quote_absent_from_abstract_falls_back_to_pmc_fulltext():
    pubmed_xml = (FIXTURES / "pubmed_oa_38555664.xml").read_text(encoding="utf-8")
    pmc_xml = (FIXTURES / "pmc_fulltext_PMC11098530.xml").read_text(encoding="utf-8")
    calls = []

    def http(url):
        calls.append(url)
        if url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=38555664&retmode=xml":
            return pubmed_xml
        if url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id=11098530&retmode=xml":
            return pmc_xml
        raise AssertionError(f"unexpected url: {url}")

    body_only_quote = (
        "The Risk Of Bias In Non-randomized Studies - of Interventions (ROBINS-I) tool was "
        "developed to evaluate estimates of the effects of deliberate intervention"
    )
    text = (
        "Higgins (2024). A tool to assess risk of bias in non-randomized follow-up studies of "
        f'exposure effects (ROBINS-E): "{body_only_quote}". PMID: 38555664'
    )
    (f,) = check_text(text, Lookup(http=http, workers=1), web=False, search=False)
    assert f.status == "ok"
    assert f.fulltext_checked is True
    assert f.record.pmcid == "PMC11098530"
    assert f.quotes == [(body_only_quote, True)]
    assert any("db=pmc&id=11098530" in c for c in calls)


def test_crossref_record_without_abstract_falls_back_to_europepmc_oa_fulltext():
    handle = (FIXTURES / "handle_bmjgh.json").read_text(encoding="utf-8")
    esearch_empty = (FIXTURES / "esearch_empty.json").read_text(encoding="utf-8")
    cr_filter = (FIXTURES / "crossref_filter_bmjgh.json").read_text(encoding="utf-8")
    epmc_search = (FIXTURES / "epmc_search_bmjgh.json").read_text(encoding="utf-8")
    epmc_ft = (FIXTURES / "epmc_fulltext_PMC11938218.xml").read_text(encoding="utf-8")
    calls = []

    def http(url):
        calls.append(url)
        if url == "https://doi.org/api/handles/10.1136/bmjgh-2024-018012":
            return handle
        if "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in url:
            return esearch_empty
        if url == "https://api.crossref.org/works?filter=doi:10.1136/bmjgh-2024-018012&rows=1":
            return cr_filter
        if url.startswith("https://api.openalex.org/works?filter=doi:10.1136/bmjgh-2024-018012"):
            return '{"results": []}'
        if url.startswith("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"):
            return epmc_search
        if url == "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC11938218/fullTextXML":
            return epmc_ft
        raise AssertionError(f"unexpected url: {url}")

    quote_text = "68.3 million internally displaced people (IDP) alone in 2024. While trends in conflicts"
    text = (
        "Dodd PJ et al. (2025). Thinking fast and slow: the urgency of crisis response must not "
        f'lead us to overlook chronic needs such as tuberculosis: "{quote_text}". '
        "https://doi.org/10.1136/bmjgh-2024-018012"
    )
    (f,) = check_text(text, Lookup(http=http, workers=1), web=False, search=False)
    assert f.status == "ok"
    assert f.record.source == "crossref"
    assert f.record.pmcid == "PMC11938218"
    assert f.fulltext_checked is True
    assert f.quotes == [(quote_text, True)]
    assert "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC11938218/fullTextXML" in calls


def test_fabricated_quote_checked_against_fulltext_warns_in_english_and_chinese():
    pubmed_xml = (FIXTURES / "pubmed_oa_40132810.xml").read_text(encoding="utf-8")
    pmc_xml = (FIXTURES / "pmc_fulltext_PMC11938218.xml").read_text(encoding="utf-8")

    def http(url):
        if url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=40132810&retmode=xml":
            return pubmed_xml
        if url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id=11938218&retmode=xml":
            return pmc_xml
        raise AssertionError(f"unexpected url: {url}")

    fake_quote = "tuberculosis was eradicated globally in 2020"
    (f,) = check_text(
        f'Dodd PJ (2025). Thinking fast and slow: "{fake_quote}". PMID: 40132810',
        Lookup(http=http, workers=1),
        web=False,
        search=False,
    )
    assert f.status == "warning"
    assert f.fulltext_checked is True
    assert f.quotes == [(fake_quote, False)]

    en_md = to_markdown([f], lang="en")
    zh_md = to_markdown([f], lang="zh")
    assert f"Quoted text not found in the abstract or open-access full text: “{fake_quote}”" in en_md
    assert f"引文在摘要和开放获取全文中均未找到：「{fake_quote}」" in zh_md

    payload = json.loads(to_json([f]))
    finding = payload["findings"][0]
    assert finding["fulltext_checked"] is True
    assert finding["registry"]["pmcid"] == "PMC11938218"
    assert finding["quotes"] == [{"text": fake_quote, "found_in_abstract": False}]


def test_non_open_access_pmcid_does_not_fetch_europepmc_fulltext_xml():
    pmc_closed = (FIXTURES / "pmc_closed_PMC3233762.xml").read_text(encoding="utf-8")
    epmc_closed = (FIXTURES / "epmc_search_closed.json").read_text(encoding="utf-8")
    calls = []

    def http(url):
        calls.append(url)
        if url == "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id=3233762&retmode=xml":
            return pmc_closed
        if url == "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=PMCID:PMC3233762&format=json&pageSize=1":
            return epmc_closed
        raise AssertionError(f"unexpected url: {url}")

    rec = Record(source="pubmed", pmid="14512209", pmcid="PMC3233762", title="Sample")
    assert Lookup(http=http, workers=1).fulltext(rec) is None
    assert not any("fullTextXML" in c for c in calls)
