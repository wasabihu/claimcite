"""Tests for retraction detection in PubMed and Crossref."""

import json
from urllib.parse import quote

from conftest import FakeHttp

from claimcite.check import check_text
from claimcite.net import NotFound
from claimcite.report import to_json, to_markdown
from claimcite.sources import Lookup


def _doi_route(doi: str) -> str:
    return "term=" + quote('"' + doi + '"[doi]', safe="")


def test_pubmed_retracted_pmid_is_error_with_notice_doi():
    http = FakeHttp({"efetch.fcgi?db=pubmed&id=9500320": "pubmed_retracted.xml"})
    text = (
        "Wakefield AJ et al. (1998). Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
        "and pervasive developmental disorder in children. Lancet. PMID: 9500320"
    )
    findings = check_text(text, Lookup(http, workers=1), web=False, search=False)
    assert len(findings) == 1
    f = findings[0]
    assert f.exists is True
    assert f.retracted is True
    assert f.status == "error"
    assert f.title_match == "match"
    assert f.year_match is True
    assert f.record is not None
    assert f.record.retraction_doi == "10.1016/s0140-6736(10)60175-4"

    md_en = to_markdown(findings, lang="en")
    assert "This paper has been retracted (notice: doi:10.1016/s0140-6736(10)60175-4)" in md_en
    md_zh = to_markdown(findings, lang="zh")
    assert "该论文已被撤稿（撤稿声明：doi:10.1016/s0140-6736(10)60175-4）" in md_zh

    payload = json.loads(to_json(findings))
    assert payload["summary"]["error"] == 1
    assert payload["findings"][0]["retracted"] is True
    assert payload["findings"][0]["registry"]["retraction_doi"] == "10.1016/s0140-6736(10)60175-4"


def test_crossref_retracted_doi_strips_retracted_title_prefix():
    doi = "10.1016/s0140-6736(97)11096-0"
    http = FakeHttp(
        {
            "api/handles/10.1016/s0140-6736(97)11096-0": "handle_retracted.json",
            "api/handles/10.1016/s0140-6736%2897%2911096-0": "handle_retracted.json",
            _doi_route(doi): "esearch_empty.json",
            "api.crossref.org/works?filter=doi:10.1016/s0140-6736(97)11096-0&rows=1": "crossref_retracted_filter.json",
            "api.openalex.org/works?filter=doi:10.1016/s0140-6736(97)11096-0": "openalex_retracted_filter.json",
        }
    )
    text = (
        "Wakefield AJ et al. (1998). Ileal-lymphoid-nodular hyperplasia, non-specific colitis, "
        f"and pervasive developmental disorder in children. Lancet. doi:{doi}"
    )
    findings = check_text(text, Lookup(http, workers=1), web=False, search=False)
    assert len(findings) == 1
    f = findings[0]
    assert f.exists is True
    assert f.retracted is True
    assert f.status == "error"
    assert f.title_match == "match"
    assert f.record is not None
    assert f.record.retraction_doi == "10.1016/s0140-6736(10)60175-4"


def test_retraction_notice_itself_is_not_flagged_retracted():
    doi = "10.1016/s0140-6736(10)60175-4"
    http = FakeHttp(
        {
            "api/handles/10.1016/s0140-6736(10)60175-4": "handle_retraction_notice.json",
            "api/handles/10.1016/s0140-6736%2810%2960175-4": "handle_retraction_notice.json",
            _doi_route(doi): "esearch_empty.json",
            "api.crossref.org/works?filter=doi:10.1016/s0140-6736(10)60175-4&rows=1": "crossref_retraction_notice_filter.json",
            "api.openalex.org/works?filter=doi:10.1016/s0140-6736(10)60175-4": "openalex_retraction_notice_filter.json",
            "api.crossref.org/works/10.9999/": NotFound,
        }
    )
    text = (
        "Lancet Editors (2010). Retraction—Ileal-lymphoid-nodular hyperplasia, non-specific "
        "colitis, and pervasive developmental disorder in children. Lancet. "
        f"doi:{doi}"
    )
    findings = check_text(text, Lookup(http, workers=1), web=False, search=False)
    assert len(findings) == 1
    f = findings[0]
    assert f.exists is True
    assert f.retracted is False
    assert f.status == "ok"
    assert f.title_match == "match"
