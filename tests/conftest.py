"""Offline test harness: every network call is answered from tests/fixtures."""

from pathlib import Path

import pytest

from claimcite.net import NotFound

FIXTURES = Path(__file__).parent / "fixtures"


class FakeHttp:
    """Maps URL substrings to fixture files. Unknown URLs fail loudly."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for needle, target in self.routes.items():
            if needle in url:
                if target is NotFound:
                    raise NotFound(url)
                return (FIXTURES / target).read_text(encoding="utf-8")
        raise AssertionError(f"unexpected request: {url}")


@pytest.fixture
def fake_http():
    return FakeHttp(
        {
            "api/handles/10.1016": "handle_found.json",
            "10.1016/s0140-6736(10)60175-4": "handle_found.json",
            "term=10.1016%2Fs0140-6736(97)11096-0%5Bdoi%5D": "esearch_wakefield.json",
            "term=10.1016%2Fs0140-6736%2897%2911096-0%5Bdoi%5D": "esearch_wakefield.json",
            "11096-0%5Bdoi%5D": "esearch_wakefield.json",
            "efetch.fcgi?db=pubmed&id=9500320": "pubmed_retracted.xml",
            "60175-4%5Bdoi%5D": "esearch_empty.json",
            "api.crossref.org/works/10.1016/s0140-6736(97)11096-0": "crossref_retracted.json",
            "api.crossref.org/works/10.1016/s0140-6736%2897%2911096-0": "crossref_retracted.json",
            "api.crossref.org/works/10.1016/s0140-6736(10)60175-4": "crossref_retraction_notice.json",
            "api.crossref.org/works/10.1016/s0140-6736%2810%2960175-4": "crossref_retraction_notice.json",
            "api/handles/10.1007/s10654-019-00524-3": "handle_found.json",
            "api/handles/10.9999/": "handle_missing.json",
            "term=10.1007%2Fs10654-019-00524-3%5Bdoi%5D": "esearch_doi_found.json",
            "term=10.9999%2Fdoes-not-exist-claimcite%5Bdoi%5D": "esearch_doi_missing.json",
            "efetch.fcgi?db=pubmed&id=99999999": "pubmed_missing.xml",
            "efetch.fcgi?db=pubmed&id=": "pubmed_efetch.xml",
            "api.crossref.org/works/10.1007/s10654-019-00524-3": "crossref_work.json",
            "api.crossref.org/works/10.9999/": NotFound,
        }
    )
