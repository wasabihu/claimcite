"""On-disk lookup cache: between-run reuse, expiry, --no-cache, no failure caching."""

from __future__ import annotations

import sqlite3
import time

import pytest

from claimcite import cli
from claimcite.cache import CachedHttp
from claimcite.net import NotFound


class CountingHttp:
    """Fake network backend that records every request."""

    def __init__(self, body: str = '{"ok": true}', fail_first: bool = False):
        self.body = body
        self.fail_first = fail_first
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.fail_first and len(self.calls) == 1:
            raise NotFound(url)
        return self.body


URL = "https://api.crossref.org/works/10.1007/s10654-019-00524-3"


def test_second_run_is_served_from_disk(tmp_path):
    backend = CountingHttp()
    first_run = CachedHttp(backend, cache_dir=tmp_path)
    assert first_run(URL) == '{"ok": true}'
    assert len(backend.calls) == 1

    # a fresh instance stands in for a fresh process
    second_run = CachedHttp(backend, cache_dir=tmp_path)
    assert second_run(URL) == '{"ok": true}'
    assert len(backend.calls) == 1


def test_expired_entry_is_refetched(tmp_path):
    backend = CountingHttp()
    CachedHttp(backend, cache_dir=tmp_path, ttl_days=30)(URL)
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        db.execute("UPDATE responses SET fetched_at = ?", (time.time() - 31 * 86400,))

    CachedHttp(backend, cache_dir=tmp_path, ttl_days=30)(URL)
    assert len(backend.calls) == 2


def test_lookup_failures_are_never_cached(tmp_path):
    backend = CountingHttp(fail_first=True)
    cached = CachedHttp(backend, cache_dir=tmp_path)
    with pytest.raises(NotFound):
        cached(URL)
    assert cached(URL) == '{"ok": true}'
    assert len(backend.calls) == 2  # the failure was not persisted


def test_distinct_urls_are_cached_separately(tmp_path):
    backend = CountingHttp()
    cached = CachedHttp(backend, cache_dir=tmp_path)
    cached(URL)
    cached(URL + "-other")
    cached(URL)
    assert len(backend.calls) == 2


def test_cli_second_run_reuses_cache(tmp_path, monkeypatch, fake_http, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "http_get", fake_http)
    doc = tmp_path / "doc.md"
    doc.write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")

    assert cli.main(["check", str(doc)]) == 0
    calls_after_first = len(fake_http.calls)
    assert calls_after_first > 0
    capsys.readouterr()

    assert cli.main(["check", str(doc)]) == 0
    assert len(fake_http.calls) == calls_after_first


def test_cli_no_cache_bypasses_disk(tmp_path, monkeypatch, fake_http, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "http_get", fake_http)
    doc = tmp_path / "doc.md"
    doc.write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")

    assert cli.main(["check", str(doc), "--no-cache"]) == 0
    assert "Randomized controlled trial" in capsys.readouterr().out
    assert not (tmp_path / "cache" / "claimcite" / "v1" / "cache.sqlite3").exists()


def test_make_lookup_wires_cache_by_default(tmp_path, monkeypatch, fake_http):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "http_get", fake_http)
    assert isinstance(cli.make_lookup(no_cache=False).http, CachedHttp)
    assert cli.make_lookup(no_cache=True).http is fake_http


def test_api_key_is_not_stored_and_does_not_split_the_cache(tmp_path):
    backend = CountingHttp()
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=1"
    CachedHttp(backend, cache_dir=tmp_path)(base + "&api_key=SECRET123")
    CachedHttp(backend, cache_dir=tmp_path)(base + "&api_key=OTHERKEY")
    CachedHttp(backend, cache_dir=tmp_path)(base)
    assert len(backend.calls) == 1
    assert backend.calls[0].endswith("api_key=SECRET123")  # the network still gets the key
    raw = (tmp_path / "cache.sqlite3").read_bytes()
    assert b"SECRET123" not in raw


def test_unusable_cache_dir_falls_back_to_network(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")  # a file where the cache directory should be
    backend = CountingHttp()
    cached = CachedHttp(backend, cache_dir=blocker / "claimcite")
    assert cached(URL) == '{"ok": true}'
    assert cached(URL) == '{"ok": true}'
    assert len(backend.calls) == 2


def test_bodies_are_stored_compressed(tmp_path):
    page = "<p>" + "Vaccines are safe and effective. " * 20000 + "</p>"
    cached = CachedHttp(CountingHttp(body=page), cache_dir=tmp_path)
    assert cached(URL) == page
    assert (tmp_path / "cache.sqlite3").stat().st_size < len(page) / 5
    assert CachedHttp(CountingHttp(), cache_dir=tmp_path)(URL) == page


def test_entries_written_before_compression_still_read(tmp_path):
    CachedHttp(CountingHttp(), cache_dir=tmp_path)  # creates the table
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        db.execute("INSERT INTO responses VALUES (?, ?, ?)", (URL, '{"old": true}', time.time()))
    backend = CountingHttp()
    assert CachedHttp(backend, cache_dir=tmp_path)(URL) == '{"old": true}'
    assert backend.calls == []


def test_corrupt_cached_body_falls_back_to_network(tmp_path):
    backend = CountingHttp()
    CachedHttp(backend, cache_dir=tmp_path)(URL)
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        db.execute("UPDATE responses SET body = ?", (b"not zlib data",))

    assert CachedHttp(backend, cache_dir=tmp_path)(URL) == '{"ok": true}'
    assert len(backend.calls) == 2
