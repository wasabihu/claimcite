"""Crossref allows one request at a time per client (x-concurrency-limit: 1). Sending eight single-DOI
requests at once got four HTTP 429s on 2026-11-09, and a 215-citation chapter got 6 of 91 rejected.
DOIs are now asked for 20 per request, one request after another."""

import json
import urllib.error

from conftest import FIXTURES

from claimcite import net
from claimcite.cache import CachedHttp
from claimcite.sources import Lookup

BELONG = "10.1037/0033-2909.117.3.497"
SELF_COMPASSION = "10.1080/15298860309032"
NOT_IN_CROSSREF = "10.11735/j.issn.1004-0242.2022.07.a002"  # registered with a Chinese agency
BATCH = (FIXTURES / "crossref_batch.json").read_text()  # recorded 2026-11-09 for these three DOIs


class Recorder:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return self.answer(url)


def test_one_request_for_several_dois():
    http = Recorder(lambda url: BATCH)
    out = Lookup(http=http)._crossref_many([BELONG, SELF_COMPASSION, NOT_IN_CROSSREF])
    filters = f"doi:{BELONG},doi:{SELF_COMPASSION},doi:{NOT_IN_CROSSREF}"
    assert http.calls == [f"https://api.crossref.org/works?filter={filters}&rows=3"]
    assert out[BELONG].title.startswith("The need to belong")
    assert out[BELONG].year == 1995
    assert out[SELF_COMPASSION].source == "crossref"
    # Missing from the answer is what a 404 for a single DOI meant: registered, no Crossref metadata.
    assert (out[NOT_IN_CROSSREF].source, out[NOT_IN_CROSSREF].title) == ("doi.org", None)


def test_batches_of_twenty():
    dois = [f"10.1000/x{i}" for i in range(45)]
    http = Recorder(lambda url: json.dumps({"message": {"items": []}}))
    Lookup(http=http)._crossref_many(dois)
    assert [u.count("doi:") for u in http.calls] == [20, 20, 5]


def test_each_work_is_cached_on_its_own(tmp_path):
    http = Recorder(lambda url: BATCH)
    Lookup(http=CachedHttp(http, tmp_path))._crossref_many([BELONG, SELF_COMPASSION, NOT_IN_CROSSREF])
    assert len(http.calls) == 1

    # Next run, one citation more: only the new DOI goes out.
    http2 = Recorder(lambda url: json.dumps({"message": {"items": []}}))
    out = Lookup(http=CachedHttp(http2, tmp_path))._crossref_many([BELONG, SELF_COMPASSION, "10.1000/new"])
    assert http2.calls == ["https://api.crossref.org/works?filter=doi:10.1000/new&rows=1"]
    assert out[BELONG].title.startswith("The need to belong")


def test_a_rejected_batch_falls_back_to_single_requests():
    def answer(url):
        if "filter=" in url:
            raise urllib.error.HTTPError(url, 400, "Bad Request", None, None)
        if BELONG in url:
            return json.dumps({"message": {"DOI": BELONG, "title": ["The need to belong"]}})
        raise net.NotFound(url)

    http = Recorder(answer)
    out = Lookup(http=http)._crossref_many([BELONG, "10.1000/bad"])
    assert out[BELONG].title == "The need to belong"
    assert out["10.1000/bad"].source == "doi.org"
    assert len(http.calls) == 3


def test_a_failed_batch_is_not_reported_as_missing():
    def answer(url):
        raise urllib.error.HTTPError(url, 503, "Unavailable", None, None)

    out = Lookup(http=Recorder(answer))._crossref_many([BELONG])
    assert type(out[BELONG]).__name__ == "LookupFailed"


def test_crossref_requests_are_spaced_a_second_apart(monkeypatch):
    sleeps = []

    class Ok:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n):
            return b"{}"

    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout: Ok())
    monkeypatch.setattr(net.time, "sleep", sleeps.append)
    net.http_get("https://api.crossref.org/works?filter=doi:10.1/a")
    net.http_get("https://api.crossref.org/works?filter=doi:10.1/b")
    assert sleeps and sleeps[-1] > 0.9
