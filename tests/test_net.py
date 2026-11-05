"""net.http_get without the network: urlopen is replaced by a fake."""

from __future__ import annotations

import io

from claimcite import net


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_large_bodies_are_cut_off(monkeypatch):
    big = b"a" * (net.MAX_BYTES + 1000)
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout: FakeResponse(big))
    assert len(net.http_get("https://big.example.org/file")) == net.MAX_BYTES


def test_polite_hosts_are_spaced_out(monkeypatch):
    sleeps = []
    monkeypatch.setattr(net.urllib.request, "urlopen", lambda req, timeout: FakeResponse(b"ok"))
    monkeypatch.setattr(net.time, "sleep", sleeps.append)
    net.polite("slow.example.org")
    net.http_get("https://slow.example.org/a")
    net.http_get("https://slow.example.org/b")
    assert sleeps and sleeps[-1] > 0.5
