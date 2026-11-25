"""Tests for local XERJ span verification (xerj:// URI contract, HTTP safety, CLI, MCP, zero disk cache).

Guards against:
- Invalid or SSRF-bearing xerj:// URIs (host:port, system indices, traversal, control chars)
- UTF-8 mid-codepoint splits and off-by-one boundaries on CJK text
- Proxy leaks (HTTP_PROXY/ALL_PROXY), 3xx redirect Authorization leaks, and unbounded responses
- Writing private local XERJ documents into the 30-day SQLite registry cache
- Prompt-injected MCP tool arguments overriding process-configured XERJ endpoints or keys
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote

import pytest

from claimcite import cli
from claimcite.cache import CachedHttp
from claimcite.check import check_documents, check_text
from claimcite.extract import extract_references
from claimcite.mcp import TOOLS, MCPServer
from claimcite.report import STATUSES, to_json, to_markdown
from claimcite.sources import Lookup
from claimcite.xerj import (
    XerjConfig,
    XerjConfigError,
    fetch_xerj_docs,
    parse_xerj_uri,
    resolve_xerj_auth,
    validate_xerj_config,
    verify_xerj_span,
)


class _StubXerjState:
    def __init__(self, docs: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = dict(docs or {})
        self.item_errors: dict[tuple[str, str], dict[str, Any]] = {}
        self.mget_status: int = 200
        self.doc_status_override: dict[tuple[str, str], int] = {}
        self.redirect_to: str | None = None
        self.oversized_bytes: int | None = None
        self.requests: list[dict[str, Any]] = []
        self.lock = threading.Lock()


@contextmanager
def _run_stub_xerj(state: _StubXerjState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _record(self, method: str, body: bytes = b"") -> None:
            with state.lock:
                state.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": body.decode("utf-8", errors="replace") if body else "",
                    }
                )

        def _send_json(self, code: int, payload: Any) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _maybe_intercept(self) -> bool:
            if state.redirect_to:
                self.send_response(302)
                self.send_header("Location", state.redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True
            if state.oversized_bytes is not None:
                blob = b" " * state.oversized_bytes
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return True
            return False

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b""
            self._record("POST", body)
            if self._maybe_intercept():
                return
            if self.path != "/_mget":
                self._send_json(404, {"error": "not_found"})
                return
            if state.mget_status != 200:
                self._send_json(state.mget_status, {"error": {"type": "forced_error"}})
                return
            req_data = json.loads(body.decode("utf-8"))
            out_docs = []
            for spec in req_data.get("docs", []):
                key = (spec["_index"], spec["_id"])
                if key in state.item_errors:
                    out_docs.append({"_index": key[0], "_id": key[1], **state.item_errors[key]})
                elif key in state.docs:
                    out_docs.append({"_index": key[0], "_id": key[1], "found": True, "_source": state.docs[key]})
                else:
                    out_docs.append({"_index": key[0], "_id": key[1], "found": False})
            self._send_json(200, {"docs": out_docs})

        def do_GET(self) -> None:
            self._record("GET")
            if self._maybe_intercept():
                return
            parts = self.path.lstrip("/").split("/")
            if len(parts) == 3 and parts[1] == "_doc":
                key = (unquote(parts[0]), unquote(parts[2]))
                override = state.doc_status_override.get(key)
                if override is not None:
                    self._send_json(override, {"error": {"type": "forced_error"}})
                    return
                if key in state.docs:
                    self._send_json(
                        200, {"_index": key[0], "_id": key[1], "found": True, "_source": state.docs[key]}
                    )
                    return
                self._send_json(404, {"_index": key[0], "_id": key[1], "found": False})
                return
            self._send_json(404, {"error": "unexpected_route", "path": self.path})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# --- 1. URI contract parsing & security validation ---


def test_parse_xerj_uri_valid_variants():
    p1, err1 = parse_xerj_uri("xerj://aikb-docs/abc123#b=10-42&f=body")
    assert err1 is None and p1 is not None
    assert (p1.index, p1.doc_id, p1.offset_mode, p1.start, p1.end, p1.field) == (
        "aikb-docs",
        "abc123",
        "b",
        10,
        42,
        "body",
    )

    p2, err2 = parse_xerj_uri("xerj://my.idx-1/docs%2Fai%2Frust%20arsenal.md#offset=24&f=text")
    assert err2 is None and p2 is not None
    assert (p2.index, p2.doc_id, p2.offset_mode, p2.start, p2.end, p2.field) == (
        "my.idx-1",
        "docs/ai/rust arsenal.md",
        "b",
        24,
        None,
        "text",
    )

    p3, err3 = parse_xerj_uri("xerj://notes/doc-9#f=code&c=3-12")
    assert err3 is None and p3 is not None
    assert (p3.index, p3.doc_id, p3.offset_mode, p3.start, p3.end, p3.field) == (
        "notes",
        "doc-9",
        "c",
        3,
        12,
        "code",
    )


@pytest.mark.parametrize(
    "bad_uri",
    [
        "xerj://127.0.0.1:6379/keys#b=0",
        "xerj://127.0.0.1%3A6379/keys#b=0",
        "xerj://[::1]:9200/doc#b=0",
        "xerj://_security/users#b=0",
        "xerj://%5Finternal/doc#b=0",
        "xerj://foo%2Fbar/doc#b=0",
        "xerj://foo%5Cbar/doc#b=0",
        "xerj://a..b/doc#b=0",
        "xerj://../doc#b=0",
        "xerj://./doc#b=0",
        "xerj://idx*/doc#b=0",
        "xerj://idx%3F/doc#b=0",
        "xerj://idx%23/doc#b=0",
        "xerj://idx%20name/doc#b=0",
        "xerj://idx%00/doc#b=0",
        "xerj://aikb-docs/#b=0",
        "xerj://aikb-docs/a%00b#b=0",
        "xerj://aikb-docs/a%0Ab#b=0",
        "xerj://aikb-docs/doc#b=0#extra",
        "xerj://aikb-docs/doc",
        "xerj://aikb-docs/doc#f=body",
        "xerj://aikb-docs/doc#b=0&c=0",
        "xerj://aikb-docs/doc#b=-1",
        "xerj://aikb-docs/doc#b=0&unknown=1",
        "xerj://aikb-docs/doc#b=0&f=",
        "xerj://aikb-docs/doc?q=1#b=0",
        "xerj://aikb-docs/%FF#b=0",
    ],
)
def test_parse_xerj_uri_rejects_illegal_and_ssrf_uris(bad_uri: str):
    parsed, err = parse_xerj_uri(bad_uri)
    assert parsed is None and err is not None


# --- 2. UTF-8 byte & char span verification ---


def test_verify_xerj_span_cjk_utf8_boundaries_and_mid_codepoint_splits():
    text = "第一行：Rust 能力武器库\n第二行：内存 9.9 MB，支持全量\n语义检索。\n"
    raw = text.encode("utf-8")

    phrase = "Rust 能力武器库"
    b_start = raw.index(phrase.encode("utf-8"))
    b_end = b_start + len(phrase.encode("utf-8"))
    ok = verify_xerj_span(text, offset_mode="b", start=b_start, end=b_end, quotes=[phrase])
    assert ok.state == "verified"
    assert ok.quotes == [(phrase, True)]
    assert ok.matched_excerpt == phrase

    # Tampered character / number must fail with quote_mismatch.
    tampered = verify_xerj_span(text, offset_mode="b", start=b_start, end=b_end, quotes=["Rust 能利武器库"])
    assert tampered.state == "quote_mismatch"
    assert tampered.quotes == [("Rust 能利武器库", False)]

    # Mid-codepoint byte split on range start, range end, empty mid-codepoint, and single-point.
    cjk_start = raw.index("能".encode())
    assert verify_xerj_span(text, offset_mode="b", start=cjk_start + 1, end=b_end).state == "offset_out_of_range"
    assert verify_xerj_span(text, offset_mode="b", start=cjk_start, end=cjk_start + 2).state == "offset_out_of_range"
    assert verify_xerj_span(text, offset_mode="b", start=cjk_start + 1, end=cjk_start + 1).state == "offset_out_of_range"
    assert verify_xerj_span(text, offset_mode="b", start=cjk_start + 1, end=None).state == "offset_out_of_range"

    # Boundary start == len(raw) on single-point and end > len(raw) / end < start on range.
    assert verify_xerj_span(text, offset_mode="b", start=len(raw), end=None).state == "offset_out_of_range"
    assert verify_xerj_span(text, offset_mode="b", start=-1, end=None).state == "offset_out_of_range"
    assert verify_xerj_span(text, offset_mode="b", start=0, end=len(raw) + 1).state == "offset_out_of_range"
    assert verify_xerj_span(text, offset_mode="b", start=10, end=5).state == "offset_out_of_range"

    # Single-point #b= extracts full trimmed line and supports wrapped cross-line forward window.
    line2_start = raw.index("第二行".encode())
    wrapped_ok = verify_xerj_span(
        text, offset_mode="b", start=line2_start, end=None, quotes=["支持全量语义检索"]
    )
    assert wrapped_ok.state == "verified"

    # strict_whitespace=True rejects CJK soft-newline collapsing.
    wrapped_strict = verify_xerj_span(
        text,
        offset_mode="b",
        start=line2_start,
        end=None,
        quotes=["支持全量语义检索"],
        strict_whitespace=True,
    )
    assert wrapped_strict.state == "quote_mismatch"

    # No inline quotes passes valid span as verified.
    no_quote = verify_xerj_span(text, offset_mode="b", start=b_start, end=b_end, quotes=[])
    assert no_quote.state == "verified"


def test_verify_xerj_span_char_mode_and_multiple_quotes():
    text = "甲乙丙丁\n第二行：alpha   beta"
    assert verify_xerj_span(text, offset_mode="c", start=0, end=4, quotes=["甲乙丙丁"]).state == "verified"
    assert verify_xerj_span(text, offset_mode="c", start=len(text), end=None).state == "offset_out_of_range"

    # Whitespace collapse vs strict_whitespace.
    assert verify_xerj_span(text, offset_mode="c", start=5, end=len(text), quotes=["alpha beta"]).state == "verified"
    assert (
        verify_xerj_span(
            text, offset_mode="c", start=5, end=len(text), quotes=["alpha beta"], strict_whitespace=True
        ).state
        == "quote_mismatch"
    )

    # Every quote must match: one valid + one tampered -> quote_mismatch.
    multi = verify_xerj_span(text, offset_mode="c", start=5, end=len(text), quotes=["alpha", "gamma"])
    assert multi.state == "quote_mismatch"
    assert multi.quotes == [("alpha", True), ("gamma", False)]


# --- 3. Config validation, credential precedence & HTTP safety ---


def test_validate_xerj_config_loopback_and_remote_tls_policy():
    assert validate_xerj_config(XerjConfig(url="http://127.0.0.1:9200/")) == "http://127.0.0.1:9200"
    assert validate_xerj_config(XerjConfig(url="http://[::1]:9200")) == "http://[::1]:9200"
    assert validate_xerj_config(XerjConfig(url="http://localhost:9200")) == "http://localhost:9200"

    with pytest.raises(XerjConfigError):
        validate_xerj_config(XerjConfig(url=None))
    with pytest.raises(XerjConfigError):
        validate_xerj_config(XerjConfig(url="http://169.254.169.254/"))
    with pytest.raises(XerjConfigError):
        validate_xerj_config(XerjConfig(url="https://xerj.example.com"))
    with pytest.raises(XerjConfigError):
        validate_xerj_config(XerjConfig(url="http://xerj.example.com", allow_remote=True))
    assert (
        validate_xerj_config(XerjConfig(url="https://xerj.example.com/", allow_remote=True))
        == "https://xerj.example.com"
    )
    assert (
        validate_xerj_config(
            XerjConfig(url="http://xerj.example.com:9200", allow_remote=True, allow_insecure_http=True)
        )
        == "http://xerj.example.com:9200"
    )
    with pytest.raises(XerjConfigError):
        validate_xerj_config(XerjConfig(url="http://user:pass@127.0.0.1:9200"))


def test_credential_precedence_and_live_header_delivery(tmp_path, monkeypatch):
    data_dir = tmp_path / "xerj_data"
    data_dir.mkdir()
    (data_dir / "admin.key").write_text("file-admin-secret\n", encoding="utf-8")

    # 4th: admin.key fallback
    assert resolve_xerj_auth(XerjConfig(url="http://127.0.0.1:9200", data_dir=str(data_dir)), environ={}) == (
        "ApiKey file-admin-secret"
    )
    # 3rd: XERJ_API_KEY auto-prefixed
    assert resolve_xerj_auth(
        XerjConfig(url="http://127.0.0.1:9200", data_dir=str(data_dir)),
        environ={"XERJ_API_KEY": "env-raw-key"},
    ) == "ApiKey env-raw-key"
    # 2nd: XERJ_AUTH preserves Bearer / ApiKey prefix
    assert resolve_xerj_auth(
        XerjConfig(url="http://127.0.0.1:9200", data_dir=str(data_dir)),
        environ={"XERJ_AUTH": "Bearer env-bearer-tok", "XERJ_API_KEY": "env-raw-key"},
    ) == "Bearer env-bearer-tok"
    # 1st: explicit --xerj-api-key wins over everything
    assert resolve_xerj_auth(
        XerjConfig(url="http://127.0.0.1:9200", api_key="cli-top-key", data_dir=str(data_dir)),
        environ={"XERJ_AUTH": "Bearer env-bearer-tok", "XERJ_API_KEY": "env-raw-key"},
    ) == "ApiKey cli-top-key"

    # Verify the actual HTTP request carries the exact Authorization header.
    state = _StubXerjState({("idx", "d1"): {"body": "hello world"}})
    monkeypatch.setenv("XERJ_AUTH", "Bearer live-token")
    with _run_stub_xerj(state) as url:
        out = fetch_xerj_docs([("idx", "d1")], XerjConfig(url=url))
    assert out[("idx", "d1")].state == "found"
    assert state.requests[0]["auth"] == "Bearer live-token"


def test_proxy_bypass_redirect_blocking_and_response_size_cap(monkeypatch):
    # 1. ProxyHandler({}) bypasses HTTP_PROXY / ALL_PROXY so local requests & keys never leak to proxy.
    spy_proxy = _StubXerjState()
    xerj_state = _StubXerjState({("idx", "d1"): {"text": "secret local note"}})
    with _run_stub_xerj(spy_proxy) as proxy_url, _run_stub_xerj(xerj_state) as xerj_url:
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("http_proxy", proxy_url)
        monkeypatch.setenv("ALL_PROXY", proxy_url)
        out = fetch_xerj_docs([("idx", "d1")], XerjConfig(url=xerj_url, api_key="super-secret"))
    assert out[("idx", "d1")].state == "found"
    assert spy_proxy.requests == []
    assert xerj_state.requests[0]["auth"] == "ApiKey super-secret"

    # 2. 3xx redirects are blocked so Authorization never leaks to redirect target.
    redirect_spy = _StubXerjState()
    redir_src = _StubXerjState()
    with _run_stub_xerj(redirect_spy) as target_url:
        redir_src.redirect_to = f"{target_url}/steal"
        with _run_stub_xerj(redir_src) as src_url:
            out_r = fetch_xerj_docs([("idx", "d1")], XerjConfig(url=src_url, api_key="do-not-leak"))
    assert out_r[("idx", "d1")].state == "network_error"
    assert "redirect" in (out_r[("idx", "d1")].detail or "").lower()
    assert redirect_spy.requests == []

    # 3. Hard response byte cap aborts oversized responses.
    big_state = _StubXerjState()
    big_state.oversized_bytes = 2048
    with _run_stub_xerj(big_state) as big_url:
        out_b = fetch_xerj_docs([("idx", "d1")], XerjConfig(url=big_url, max_response_bytes=512))
    assert out_b[("idx", "d1")].state == "network_error"
    assert "512" in (out_b[("idx", "d1")].detail or "")


# --- 4. Batched _mget isolation, 405/403 -> _doc fallback, 401 short-circuit ---


def test_mget_partial_error_isolation_and_405_doc_fallback_and_401_short_circuit():
    # A. Partial isolation in a single _mget batch: found + not_found + security_exception.
    state = _StubXerjState({("idx", "ok1"): {"body": "healthy doc"}})
    state.item_errors[("idx", "sec1")] = {
        "status": 403,
        "error": {"type": "security_exception", "reason": "action denied"},
    }
    with _run_stub_xerj(state) as url:
        out = fetch_xerj_docs([("idx", "ok1"), ("idx", "missing1"), ("idx", "sec1")], XerjConfig(url=url))
    assert out[("idx", "ok1")].state == "found"
    assert out[("idx", "missing1")].state == "doc_not_found"
    assert out[("idx", "sec1")].state == "auth_error"
    assert len(state.requests) == 1 and state.requests[0]["path"] == "/_mget"

    # B. Batch 405 -> falls back to GET /{index}/_doc/{doc_id} (NEVER GET /_source), isolating 403 & 404.
    fb = _StubXerjState({("idx", "path/doc1"): {"content": "via _doc fallback"}})
    fb.mget_status = 405
    fb.doc_status_override[("idx", "denied")] = 403
    with _run_stub_xerj(fb) as url:
        out_fb = fetch_xerj_docs(
            [("idx", "path/doc1"), ("idx", "denied"), ("idx", "absent")], XerjConfig(url=url)
        )
    assert out_fb[("idx", "path/doc1")].state == "found"
    assert out_fb[("idx", "denied")].state == "auth_error"
    assert out_fb[("idx", "absent")].state == "doc_not_found"
    paths = [r["path"] for r in fb.requests]
    assert paths[0] == "/_mget"
    assert "/idx/_doc/path%2Fdoc1" in paths
    assert not any("/_source" in p for p in paths)

    # C. Batch 403 also falls back to GET /{index}/_doc/{doc_id}.
    fb_403 = _StubXerjState({("idx", "ok"): {"code": "fn main() {}"}})
    fb_403.mget_status = 403
    with _run_stub_xerj(fb_403) as url:
        out_403 = fetch_xerj_docs([("idx", "ok")], XerjConfig(url=url))
    assert out_403[("idx", "ok")].state == "found"
    assert [r["path"] for r in fb_403.requests] == ["/_mget", "/idx/_doc/ok"]

    # D. >100 healthy docs chunk cleanly into batches of 100.
    many_docs = {("idx", f"d{i}"): {"text": f"doc {i}"} for i in range(105)}
    many_state = _StubXerjState(many_docs)
    with _run_stub_xerj(many_state) as url:
        out_many = fetch_xerj_docs(list(many_docs), XerjConfig(url=url))
    assert len(many_state.requests) == 2
    assert len(out_many) == 105 and all(v.state == "found" for v in out_many.values())

    # E. Batch 401 short-circuits across >100 items (only 1 HTTP request sent!).
    auth_state = _StubXerjState()
    auth_state.mget_status = 401
    pairs = [("idx", f"d{i}") for i in range(105)]
    with _run_stub_xerj(auth_state) as url:
        out_401 = fetch_xerj_docs(pairs, XerjConfig(url=url))
    assert len(auth_state.requests) == 1
    assert all(v.state == "auth_error" for v in out_401.values())


def test_default_field_probe_order_and_explicit_field_override():
    state = _StubXerjState(
        {
            ("idx", "d1"): {"text": "from-text", "body": "from-body", "content": "from-content", "code": "from-code"},
            ("idx", "d2"): {"body": "from-body", "content": "from-content", "code": "from-code"},
            ("idx", "d3"): {"content": "from-content", "code": "from-code"},
            ("idx", "d4"): {"code": "from-code"},
        }
    )
    with _run_stub_xerj(state) as url:
        lookup = Lookup(http=lambda u: "", xerj_config=XerjConfig(url=url))
        doc = (
            'Check "from-text" (xerj://idx/d1#b=0-9), '
            '"from-code" (xerj://idx/d1#b=0-9&f=code), '
            '"from-body" (xerj://idx/d2#b=0-9), '
            '"from-content" (xerj://idx/d3#b=0-12), '
            '"from-code" (xerj://idx/d4#b=0-9).\n'
        )
        findings = check_text(doc, lookup, web=False, search=False)
    assert [(f.status, f.xerj.field) for f in findings] == [
        ("ok", "text"),
        ("ok", "code"),
        ("ok", "body"),
        ("ok", "content"),
        ("ok", "code"),
    ]


# --- 5. Extraction, mixed DOI + xerj:// zero SQLite cache writes, field probe, reports ---


def test_extract_xerj_inline_bare_numbered_and_footnote_with_quotes():
    doc = (
        "文档指出“第一句中文引文”。[来源](xerj://aikb-docs/d1#b=0-30&f=body)\n\n"
        "另见 xerj://aikb-docs/d2#offset=0：「第二句引文」。\n\n"
        'A claim says "English verbatim quote"[1], and “脚注引文”[^note].\n'
        "Inline code `xerj://aikb-docs/ignored#b=0` is skipped.\n\n"
        "## References\n\n"
        "[1]: xerj://papers/10.1000%2Fxyz#c=0-40\n"
        "[^note]: xerj://aikb-docs/d3#b=0-30\n"
    )
    refs = extract_references(doc)
    assert [r.kind for r in refs] == ["xerj", "xerj", "xerj", "xerj"]
    by_id = {r.id: r for r in refs}
    assert by_id["xerj://aikb-docs/d1#b=0-30&f=body"].quotes == ["第一句中文引文"]
    assert by_id["xerj://aikb-docs/d2#offset=0"].quotes == ["第二句引文"]
    assert by_id["xerj://papers/10.1000%2Fxyz#c=0-40"].quotes == ["English verbatim quote"]
    assert by_id["xerj://aikb-docs/d3#b=0-30"].quotes == ["脚注引文"]


def test_mixed_doi_and_xerj_never_writes_xerj_to_sqlite_cache(tmp_path, fake_http):
    cached = CachedHttp(fake_http, tmp_path / "cache")
    body = "XERJ 实测常驻内存 9.9 MB，支持原生混合检索。"
    raw = body.encode("utf-8")
    q = "常驻内存 9.9 MB"
    s = raw.index(q.encode("utf-8"))
    e = s + len(q.encode("utf-8"))

    state = _StubXerjState(
        {
            ("aikb-docs", "doc-a"): {"body": body},
            ("aikb-docs", "doc-no-text"): {"title": 12345},
        }
    )
    with _run_stub_xerj(state) as url:
        lookup = Lookup(http=cached, workers=2, xerj_config=XerjConfig(url=url))
        md = (
            "Kim Y et al. (2019). Coffee consumption and all-cause and cause-specific mortality: "
            "a meta-analysis by potential modifiers. https://doi.org/10.1007/s10654-019-00524-3\n\n"
            f"本地实测显示“{q}”[证据](xerj://aikb-docs/doc-a#b={s}-{e})。\n"
            f"同一文档再次引用“XERJ 实测”[证据2](xerj://aikb-docs/doc-a#offset=0)。\n"
            "缺字段测试 xerj://aikb-docs/doc-no-text#b=0\n"
            "非法端口测试 xerj://127.0.0.1:6379/keys#b=0\n"
        )
        findings = check_documents({"draft.md": md}, lookup, web=False, search=False)["draft.md"]

    # Two citations to the same (aikb-docs, doc-a) are deduplicated into one _mget request.
    assert len(state.requests) == 1
    assert all(f.status in STATUSES for f in findings)

    by_id = {f.ref.id: f for f in findings}
    assert by_id["10.1007/s10654-019-00524-3"].status == "ok"
    assert by_id[f"xerj://aikb-docs/doc-a#b={s}-{e}"].status == "ok"
    assert by_id[f"xerj://aikb-docs/doc-a#b={s}-{e}"].xerj.field == "body"
    assert by_id["xerj://aikb-docs/doc-a#offset=0"].status == "ok"
    assert by_id["xerj://aikb-docs/doc-no-text#b=0"].status == "error"
    assert by_id["xerj://aikb-docs/doc-no-text#b=0"].xerj.state == "field_missing"
    assert by_id["xerj://127.0.0.1:6379/keys#b=0"].status == "error"
    assert by_id["xerj://127.0.0.1:6379/keys#b=0"].xerj.state == "invalid_uri"

    # Assert SQLite cache has DOI responses and ZERO traces of XERJ URLs or payloads.
    db = sqlite3.connect(cached.db_path)
    try:
        rows = db.execute("SELECT url, body FROM responses").fetchall()
    finally:
        db.close()
    assert rows, "expected DOI registry responses in SQLite cache"
    for row_url, row_blob in rows:
        assert "127.0.0.1" not in row_url
        assert "_mget" not in row_url
        assert "_doc" not in row_url
        assert "xerj" not in row_url.lower()
        assert b"aikb-docs" not in (row_blob if isinstance(row_blob, bytes) else row_blob.encode())

    # Bilingual markdown and JSON rendering sanity check.
    md_en = to_markdown(findings, "en")
    md_zh = to_markdown(findings, "zh")
    assert "XERJ span verified" in md_en and "Invalid xerj:// URI" in md_en
    assert "XERJ 证据片段核验通过" in md_zh and "无效的 xerj:// 引用地址" in md_zh
    parsed_json = json.loads(to_json(findings))
    assert parsed_json["findings"][0]["xerj"] is None
    xerj_json = [item for item in parsed_json["findings"] if item["kind"] == "xerj"]
    assert xerj_json[0]["xerj"]["state"] == "verified"


def test_xerj_unconfigured_never_probes_default_port(monkeypatch):
    monkeypatch.delenv("XERJ_URL", raising=False)
    (f,) = check_text("See xerj://aikb-docs/doc1#b=0-5\n", Lookup(http=lambda u: ""))
    assert f.status == "unchecked"
    assert f.error is not None
    assert f.xerj is not None and f.xerj.state == "xerj_unconfigured"


# --- 6. CLI flags & MCP env-only isolation ---


def test_cli_xerj_flags_exit_codes_and_strict_whitespace(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("XERJ_URL", raising=False)
    body = "本地检索  内存占用 9.9 MB"
    state = _StubXerjState({("aikb-docs", "d1"): {"body": body}})
    ok_doc = tmp_path / "ok.md"
    ok_doc.write_text('报告称“本地检索 内存占用 9.9 MB”[出处](xerj://aikb-docs/d1#b=0)\n', encoding="utf-8")
    bad_doc = tmp_path / "bad.md"
    bad_doc.write_text('报告称“本地检索 内存占用 8.8 MB”[出处](xerj://aikb-docs/d1#b=0)\n', encoding="utf-8")

    with _run_stub_xerj(state) as url:
        assert cli.main(["check", str(ok_doc), "--no-cache", "--xerj-url", url, "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"] == {"ok": 1, "warning": 0, "error": 0, "unchecked": 0}
        assert payload["findings"][0]["xerj"]["state"] == "verified"

        # --xerj-strict-whitespace turns double-space difference into quote_mismatch error.
        assert (
            cli.main(
                ["check", str(ok_doc), "--no-cache", "--xerj-url", url, "--xerj-strict-whitespace", "--format", "json"]
            )
            == 1
        )
        strict_payload = json.loads(capsys.readouterr().out)
        assert strict_payload["findings"][0]["xerj"]["state"] == "quote_mismatch"

        # Tampered number fails with exit code 1.
        assert cli.main(["check", str(bad_doc), "--no-cache", "--xerj-url", url, "--lang", "zh"]) == 1
        assert "未找到引文" in capsys.readouterr().out


def test_mcp_env_only_isolation_blocks_prompt_injected_xerj_params(monkeypatch):
    # Tool schemas MUST NOT expose xerj_url or xerj_api_key (prevents prompt-injection SSRF).
    for tool in TOOLS:
        props = set(tool["inputSchema"]["properties"])
        assert not any(k.startswith("xerj") for k in props)
        assert tool["inputSchema"]["additionalProperties"] is False

    spy_state = _StubXerjState()
    real_state = _StubXerjState({("aikb-docs", "mcp1"): {"text": "MCP env verified phrase"}})
    with _run_stub_xerj(spy_state) as spy_url, _run_stub_xerj(real_state) as real_url:
        monkeypatch.setenv("XERJ_URL", real_url)
        srv = MCPServer(default_no_cache=True)

        # Prompt-injected xerj_url / xerj_api_key must be rejected with isError=True and zero spy hits.
        inj = srv.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "check_citations",
                    "arguments": {
                        "text": 'Check "MCP env verified phrase" (xerj://aikb-docs/mcp1#b=0-23).',
                        "xerj_url": spy_url,
                        "xerj_api_key": "injected-secret",
                    },
                },
            }
        )
        assert inj["result"]["isError"] is True
        assert spy_state.requests == []

        # Legitimate call uses XERJ_URL from process env and keeps backward-compatible JSON schema.
        ok = srv.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "check_citations",
                    "arguments": {
                        "text": 'Check "MCP env verified phrase" (xerj://aikb-docs/mcp1#b=0-23).',
                        "format": "json",
                        "web": False,
                        "search": False,
                    },
                },
            }
        )
        assert ok["result"]["isError"] is False
        data = json.loads(ok["result"]["content"][0]["text"])
        assert data["summary"] == {"ok": 1, "warning": 0, "error": 0, "unchecked": 0}
        assert data["findings"][0]["xerj"]["state"] == "verified"


def test_cli_xerj_data_dir_and_mcp_check_files_env_only(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("XERJ_URL", raising=False)
    monkeypatch.delenv("XERJ_AUTH", raising=False)
    monkeypatch.delenv("XERJ_API_KEY", raising=False)
    data_dir = tmp_path / "xerj-data"
    data_dir.mkdir()
    (data_dir / "admin.key").write_text("disk-admin-key\n", encoding="utf-8")

    md = tmp_path / "note.md"
    md.write_text('引用“真实原文” [证据][1]。\n\n[1]: xerj://aikb-docs/f1#b=0-12&f=body\n', encoding="utf-8")

    state = _StubXerjState({("aikb-docs", "f1"): {"body": "真实原文在这里"}})
    with _run_stub_xerj(state) as url:
        assert (
            cli.main(
                ["check", str(md), "--no-cache", "--xerj-url", url, "--xerj-data-dir", str(data_dir), "--format", "json"]
            )
            == 0
        )
        assert state.requests[-1]["auth"] == "ApiKey disk-admin-key"
        out = json.loads(capsys.readouterr().out)
        assert out["summary"] == {"ok": 1, "warning": 0, "error": 0, "unchecked": 0}

        monkeypatch.setenv("XERJ_URL", url)
        monkeypatch.setenv("XERJ_DATA_DIR", str(data_dir))
        srv = MCPServer(default_no_cache=True)
        bad_files = srv.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "check_files",
                    "arguments": {"paths": [str(md)], "xerj_url": "http://127.0.0.1:1"},
                },
            }
        )
        assert bad_files["result"]["isError"] is True

        ok_files = srv.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "check_files",
                    "arguments": {"paths": [str(md)], "format": "json", "web": False, "search": False},
                },
            }
        )
        assert ok_files["result"]["isError"] is False
        assert json.loads(ok_files["result"]["content"][0]["text"])["summary"]["ok"] == 1
