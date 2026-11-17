"""Tests for the zero-dependency stdio MCP server and `claimcite mcp` CLI."""

import io
import json

from conftest import FakeHttp

from claimcite import __version__, cli
from claimcite.mcp import TOOLS, MCPServer
from claimcite.sources import Lookup


def _make_server() -> MCPServer:
    http = FakeHttp(
        {
            "api/handles/10.1007/s10654-019-00524-3": "handle_found.json",
            "api/handles/10.9999/does-not-exist-claimcite": "handle_missing.json",
            "term=%2210.1007%2Fs10654-019-00524-3%22%5Bdoi%5D": "esearch_doi_found.json",
            "efetch.fcgi?db=pubmed&id=31055709": "pubmed_efetch.xml",
            "id_list=1706.03762&max_results=1": "arxiv_1706_03762.xml",
        }
    )
    return MCPServer(lookup_factory=lambda no_cache=False: Lookup(http, workers=1))


def test_mcp_initialize_notification_ping_and_tools_list():
    srv = _make_server()
    init = srv.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert init["result"]["serverInfo"] == {"name": "claimcite", "version": __version__}

    assert srv.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert srv.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"}) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {},
    }
    listed = srv.handle_message({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert [t["name"] for t in listed["result"]["tools"]] == [t["name"] for t in TOOLS]


def test_mcp_check_citations_and_check_files(tmp_path):
    srv = _make_server()
    resp = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "check_citations",
                "arguments": {
                    "text": "See Attention Is All You Need (2017, arXiv:1706.03762) and doi:10.9999/does-not-exist-claimcite.",
                    "format": "json",
                    "web": False,
                    "search": False,
                },
            },
        }
    )
    result = resp["result"]
    assert result["isError"] is False
    data = json.loads(result["content"][0]["text"])
    assert data["summary"] == {"ok": 1, "warning": 0, "error": 1, "unchecked": 0}

    md_file = tmp_path / "draft.md"
    md_file.write_text(
        "Coffee consumption and all-cause and cause-specific mortality: a meta-analysis by potential modifiers "
        "(2019, doi:10.1007/s10654-019-00524-3).",
        encoding="utf-8",
    )
    f_resp = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "check_files",
                "arguments": {"paths": [str(md_file)], "lang": "zh", "web": False, "search": False},
            },
        }
    )
    assert f_resp["result"]["isError"] is False
    assert "引用核查" in f_resp["result"]["content"][0]["text"]


def test_mcp_errors_and_validation(tmp_path):
    srv = _make_server()
    assert (
        srv.handle_message({"jsonrpc": "2.0", "id": 1, "method": "unknown/method"})["error"]["code"] == -32601
    )
    assert (
        srv.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
        )["error"]["code"]
        == -32602
    )
    bad_stdin = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "check_files", "arguments": {"paths": ["-"]}},
        }
    )
    assert bad_stdin["result"]["isError"] is True
    missing = srv.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "check_files", "arguments": {"paths": [str(tmp_path / "missing.md")]}},
        }
    )
    assert missing["result"]["isError"] is True


def test_mcp_serve_jsonl_and_content_length_framing():
    srv = _make_server()
    req1 = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
    req2 = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode("utf-8")
    stream_in = io.BytesIO(req1 + b"\n" + f"Content-Length: {len(req2)}\r\n\r\n".encode("ascii") + req2)
    stream_out = io.BytesIO()
    assert srv.serve(stream_in, stream_out) == 0

    raw = stream_out.getvalue()
    first_line, rest = raw.split(b"\n", 1)
    assert json.loads(first_line.decode("utf-8")) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    header, body = rest.split(b"\r\n\r\n", 1)
    assert header.startswith(b"Content-Length:")
    assert json.loads(body.decode("utf-8"))["id"] == 2


def test_cli_mcp_subcommand(monkeypatch):
    stdin = io.BytesIO((json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"}) + "\n").encode("utf-8"))
    stdout = io.BytesIO()

    class _Buf:
        def __init__(self, b):
            self.buffer = b

    monkeypatch.setattr("sys.stdin", _Buf(stdin))
    monkeypatch.setattr("sys.stdout", _Buf(stdout))
    assert cli.main(["mcp", "--no-cache"]) == 0
    assert json.loads(stdout.getvalue().decode("utf-8")) == {"jsonrpc": "2.0", "id": 7, "result": {}}
