"""Zero-dependency Model Context Protocol (MCP) server over stdio."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from . import __version__
from .check import check_documents, check_text
from .report import to_json, to_markdown
from .sources import Lookup
from .workspace import (
    CANNOT_READ,
    DOCUMENT_SUFFIXES,
    NO_DOCUMENTS,
    UNSUPPORTED_FILE,
    documents,
    format_documents_report,
    make_default_lookup,
)

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2024-11-05"

_COMMON_OPTIONS = {
    "format": {"type": "string", "enum": ["md", "json"], "default": "md"},
    "lang": {"type": "string", "enum": ["en", "zh"], "default": "en"},
    "web": {
        "type": "boolean",
        "default": True,
        "description": "Fetch ordinary web links and check for quoted text.",
    },
    "search": {
        "type": "boolean",
        "default": True,
        "description": "Search Crossref and PubMed by title for references without identifiers.",
    },
    "no_cache": {
        "type": "boolean",
        "default": False,
        "description": "Bypass the on-disk lookup cache for this check.",
    },
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "check_citations",
        "description": (
            "Verify every citation in Markdown or plain text: check whether each DOI, PMID, "
            "arXiv ID, web link, or bibliographic reference exists, whether the paper is "
            "retracted, whether titles and years match, whether quoted text appears in "
            "the abstract or web page, and whether numbered/footnote markers or entries are missing or uncited."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Markdown or plain text to verify."},
                **_COMMON_OPTIONS,
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Verify citations in text",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "check_files",
        "description": (
            "Verify citations in one or more local Markdown/text files or folders. Returns a "
            "Markdown or JSON report covering existence, retractions, title/year match, quotes, "
            "and numbered/footnote reference consistency."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "File paths or directories containing .md, .markdown, or .txt files.",
                },
                **_COMMON_OPTIONS,
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Verify citations in files",
            "readOnlyHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
]


class _FrameParseError(Exception):
    """Malformed JSON or framing on stdin."""


class MCPServer:
    """In-process and stdio JSON-RPC 2.0 handler for claimcite MCP tools."""

    def __init__(
        self,
        lookup_factory: Callable[..., Lookup] = make_default_lookup,
        *,
        default_no_cache: bool = False,
    ) -> None:
        self.lookup_factory = lookup_factory
        self.default_no_cache = default_no_cache

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") not in (None, "2.0"):
            return _jsonrpc_error(None, -32600, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            return _jsonrpc_error(message.get("id"), -32600, "Invalid Request")
        # Notifications omit "id" and expect no response frame.
        if "id" not in message:
            return None
        req_id = message["id"]
        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _jsonrpc_error(req_id, -32602, "params must be an object")

        if method == "initialize":
            client_proto = params.get("protocolVersion")
            proto = client_proto if client_proto in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
            return _jsonrpc_ok(
                req_id,
                {
                    "protocolVersion": proto,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "claimcite", "version": __version__},
                },
            )
        if method == "ping":
            return _jsonrpc_ok(req_id, {})
        if method == "tools/list":
            return _jsonrpc_ok(req_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call_tool(req_id, params)
        return _jsonrpc_error(req_id, -32601, f"Method not found: {method}")

    def _call_tool(self, req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _jsonrpc_error(req_id, -32602, "Invalid tools/call parameters")
        if name not in {"check_citations", "check_files"}:
            return _jsonrpc_error(req_id, -32602, f"Unknown tool: {name}")

        fmt = arguments.get("format", "md")
        lang = arguments.get("lang", "en")
        if fmt not in ("md", "json"):
            return _tool_error("Invalid 'format': expected 'md' or 'json'.", req_id)
        if lang not in ("en", "zh"):
            return _tool_error("Invalid 'lang': expected 'en' or 'zh'.", req_id)

        web = bool(arguments.get("web", True))
        search = bool(arguments.get("search", True))
        no_cache = bool(arguments.get("no_cache", self.default_no_cache))
        lookup = self.lookup_factory(no_cache=no_cache)

        if name == "check_citations":
            text = arguments.get("text")
            if not isinstance(text, str):
                return _tool_error("Invalid 'text': expected a string.", req_id)
            findings = check_text(text, lookup, web=web, search=search)
            out = to_json(findings) if fmt == "json" else to_markdown(findings, lang)
            return _tool_ok(out, req_id)

        raw_paths = arguments.get("paths")
        if (
            not isinstance(raw_paths, list)
            or not raw_paths
            or not all(isinstance(p, str) and p for p in raw_paths)
        ):
            return _tool_error("Invalid 'paths': expected a non-empty list of path strings.", req_id)

        texts: dict[str, str] = {}
        for given in raw_paths:
            if given == "-":
                return _tool_error("Path '-' (stdin) is reserved for the MCP stdio transport.", req_id)
            given_path = Path(given)
            if not given_path.is_dir() and given_path.suffix.lower() not in DOCUMENT_SUFFIXES:
                return _tool_error(UNSUPPORTED_FILE[lang].format(path=given), req_id)
            paths = documents(given)
            if not paths:
                return _tool_error(NO_DOCUMENTS[lang].format(path=given), req_id)
            for path in paths:
                try:
                    texts[path] = Path(path).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else type(exc).__name__
                    return _tool_error(CANNOT_READ[lang].format(path=path, reason=reason), req_id)

        several = len(texts) > 1 or any(Path(g).is_dir() for g in raw_paths)
        results = check_documents(texts, lookup, web=web, search=search)
        return _tool_ok(format_documents_report(results, fmt=fmt, lang=lang, several=several), req_id)

    def serve(self, stdin: BinaryIO, stdout: BinaryIO) -> int:
        while True:
            try:
                message, framing = _read_frame(stdin)
            except _FrameParseError:
                _write_frame(stdout, _jsonrpc_error(None, -32700, "Parse error"), "jsonl")
                continue
            if message is None:
                return 0
            response = self.handle_message(message)
            if response is not None:
                _write_frame(stdout, response, framing)


def _jsonrpc_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_ok(text: str, req_id: Any) -> dict[str, Any]:
    return _jsonrpc_ok(req_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _tool_error(text: str, req_id: Any) -> dict[str, Any]:
    return _jsonrpc_ok(req_id, {"content": [{"type": "text", "text": text}], "isError": True})


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(stream: BinaryIO) -> tuple[Any | None, str]:
    while True:
        line = stream.readline()
        if not line:
            return None, "jsonl"
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith((b"{", b"[")):
            try:
                return json.loads(stripped.decode("utf-8")), "jsonl"
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _FrameParseError from exc
        headers: dict[str, str] = {}
        current: bytes | None = line
        while current is not None:
            text = current.decode("ascii", errors="replace").strip()
            if not text:
                break
            if ":" in text:
                k, v = text.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            current = stream.readline()
            if not current:
                break
        try:
            length = int(headers["content-length"])
        except (KeyError, ValueError) as exc:
            raise _FrameParseError from exc
        if length < 0:
            raise _FrameParseError
        payload = _read_exact(stream, length)
        if len(payload) != length:
            return None, "lsp"
        try:
            return json.loads(payload.decode("utf-8")), "lsp"
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _FrameParseError from exc


def _write_frame(stream: BinaryIO, payload: dict[str, Any], framing: str) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if framing == "lsp":
        stream.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
        stream.write(data)
    else:
        stream.write(data)
        stream.write(b"\n")
    stream.flush()


def run_mcp_server(*, no_cache: bool = False) -> int:
    server = MCPServer(default_no_cache=no_cache)
    return server.serve(sys.stdin.buffer, sys.stdout.buffer)
