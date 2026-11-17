"""Phase 2 architecture tests: multi-file global prefetch, cache isolation/pruning, MCP decoupling."""

from __future__ import annotations

import sqlite3
import time

from conftest import FakeHttp

from claimcite import cli
from claimcite.cache import CachedHttp
from claimcite.mcp import MCPServer
from claimcite.sources import Lookup


def test_multi_file_cli_batches_pubmed_across_all_documents(tmp_path, fake_http, monkeypatch, capsys):
    """Checking a folder with two files citing different PMIDs issues ONE batched efetch, not two."""
    (tmp_path / "one.md").write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")
    (tmp_path / "two.md").write_text("Coffee, PMID: 31055709\n", encoding="utf-8")
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=fake_http))

    assert cli.main(["check", str(tmp_path), "--format", "json", "--no-web", "--no-search"]) == 0
    capsys.readouterr()
    efetch_calls = [u for u in fake_http.calls if "efetch.fcgi" in u]
    assert len(efetch_calls) == 1
    assert "30221596" in efetch_calls[0] and "31055709" in efetch_calls[0]


def test_web_pages_do_not_pollute_on_disk_registry_cache_but_dedup_within_run(tmp_path):
    calls: list[str] = []
    scheme = "https://"
    page_url = f"{scheme}example.org/who-facts"

    def backend(url: str) -> str:
        calls.append(url)
        if url.endswith("/robots.txt"):
            return "User-agent: *\nAllow: /\n"
        return "<html><body>Vaccines are safe and effective.</body></html>"

    cached = CachedHttp(backend, cache_dir=tmp_path)
    lookup = Lookup(http=cached, workers=1)
    from claimcite.check import check_documents

    results = check_documents(
        {
            "a.md": f'First doc: "Vaccines are safe and effective" ({page_url}).\n',
            "b.md": f'Second doc: "Vaccines are safe and effective" ({page_url}).\n',
        },
        lookup=lookup,
        web=True,
        search=False,
    )
    assert results["a.md"][0].status == "ok"
    assert results["b.md"][0].status == "ok"
    # Fetched once in memory across both files in the same run:
    assert calls.count(page_url) == 1
    # Never written into the 30-day SQLite registry cache:
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        rows = db.execute("SELECT url FROM responses").fetchall()
    assert rows == []


def test_cache_prunes_expired_rows_on_startup(tmp_path):
    url = "https://api.crossref.org/works/10.1007/s10654-019-00524-3"
    CachedHttp(lambda _: '{"ok": true}', cache_dir=tmp_path, ttl_days=30)(url)
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        db.execute("UPDATE responses SET fetched_at = ?", (time.time() - 40 * 86400,))

    # Opening a new CachedHttp cleans up expired rows automatically.
    CachedHttp(lambda _: '{"ok": true}', cache_dir=tmp_path, ttl_days=30)
    with sqlite3.connect(tmp_path / "cache.sqlite3") as db:
        count = db.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
    assert count == 0


def test_mcp_check_files_rejects_non_document_secret_files(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text('API_SECRET="super_secret_token_value"\n', encoding="utf-8")
    srv = MCPServer(lookup_factory=lambda no_cache=False: Lookup(FakeHttp({}), workers=1))
    resp = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "check_files", "arguments": {"paths": [str(secret)]}},
        }
    )
    assert resp["result"]["isError"] is True
    assert "super_secret_token_value" not in resp["result"]["content"][0]["text"]
