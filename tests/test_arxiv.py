"""Tests for arXiv extraction, Atom lookup, DataCite DOIs, and caching."""

from pathlib import Path

from conftest import FakeHttp

from claimcite.cache import CachedHttp
from claimcite.check import check_text
from claimcite.extract import extract_references
from claimcite.sources import Lookup, parse_arxiv


def test_extract_arxiv_forms():
    text = (
        "See arXiv:1706.03762v7, https://arxiv.org/abs/1706.03762, "
        "https://arxiv.org/pdf/2301.00001.pdf, https://arxiv.org/html/2401.12345v2, "
        "and arXiv:hep-th/9901001v2."
    )
    refs = extract_references(text)
    arxiv_ids = [(r.kind, r.id) for r in refs]
    assert arxiv_ids == [
        ("arxiv", "1706.03762"),
        ("arxiv", "1706.03762"),
        ("arxiv", "2301.00001"),
        ("arxiv", "2401.12345"),
        ("arxiv", "hep-th/9901001"),
    ]


def test_arxiv_valid_updated_year_and_missing():
    http = FakeHttp({"id_list=1706.03762,9912.99999&max_results=2": "arxiv_batch.xml"})
    text = (
        'Vaswani A et al. (2023). Attention Is All You Need. arXiv: "The dominant sequence '
        "transduction models are based on complex recurrent or convolutional neural networks in "
        'an encoder-decoder configuration". arXiv:1706.03762\n'
        "Meanwhile arXiv:9912.99999 does not exist."
    )
    findings = check_text(text, Lookup(http, workers=1), web=False, search=False)
    assert len(findings) == 2

    ok_f, bad_f = findings
    assert ok_f.status == "ok"
    assert ok_f.exists is True
    assert ok_f.title_match == "match"
    assert ok_f.year_match is True
    assert ok_f.quotes[0][1] is True
    assert ok_f.record is not None and ok_f.record.arxiv == "1706.03762"

    assert bad_f.status == "error"
    assert bad_f.exists is False


def test_arxiv_datacite_doi_routes_to_arxiv_without_crossref():
    http = FakeHttp(
        {
            "doi.org/api/handles/10.48550/arxiv.1706.03762": "handle_arxiv_1706_03762.json",
            "id_list=1706.03762&max_results=1": "arxiv_1706_03762.xml",
        }
    )
    text = "Vaswani A et al. (2017). Attention Is All You Need. arXiv. doi:10.48550/arXiv.1706.03762"
    findings = check_text(text, Lookup(http, workers=1), web=False, search=False)
    assert len(findings) == 1
    f = findings[0]
    assert f.status == "ok"
    assert f.exists is True
    assert f.title_match == "match"
    assert f.year_match is True
    assert f.record is not None
    assert f.record.source == "arxiv"
    assert f.record.doi == "10.48550/arxiv.1706.03762"
    assert not any("api.crossref.org" in u for u in http.calls)
    assert not any("eutils.ncbi.nlm.nih.gov" in u for u in http.calls)


def test_arxiv_batch_prefetch_and_disk_cache(tmp_path):
    raw = FakeHttp({"id_list=1706.03762,9912.99999&max_results=2": "arxiv_batch.xml"})
    cached = CachedHttp(raw, cache_dir=tmp_path)
    text = (
        "Vaswani A et al. (2017). Attention Is All You Need. arXiv. arXiv:1706.03762\n"
        "Missing arXiv:9912.99999"
    )
    first = check_text(text, Lookup(cached, workers=1), web=False, search=False)
    assert [f.status for f in first] == ["ok", "error"]
    assert len(raw.calls) == 1

    # Second run finds 1706.03762 in the per-ID disk cache and only fetches missing 9912.99999.
    raw2 = FakeHttp({"id_list=9912.99999&max_results=1": "arxiv_missing.xml"})
    cached2 = CachedHttp(raw2, cache_dir=tmp_path)
    second = check_text(text, Lookup(cached2, workers=1), web=False, search=False)
    assert [f.status for f in second] == ["ok", "error"]
    assert len(raw2.calls) == 1


def test_parse_arxiv_handles_missing():
    missing_xml = Path(__file__).parent / "fixtures" / "arxiv_missing.xml"
    assert parse_arxiv(missing_xml.read_text(encoding="utf-8")) == {}
