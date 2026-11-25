"""Local XERJ span-verification client and URI parser. Standard library only.

Shared cross-project URI contract (matches ``kbwall`` byte-for-byte, no ``host:port``):
  - ``xerj://<index>/<doc_id>#b=<byte_start>[-<byte_end>][&f=<field>]``
  - ``xerj://<index>/<doc_id>#c=<char_start>[-<char_end>][&f=<field>]``
  - ``xerj://<index>/<doc_id>#offset=<byte_start>[&f=<field>]`` (alias for ``#b=<byte_start>``)
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from . import __version__
from .models import XerjResult

USER_AGENT = f"claimcite/{__version__} (+https://github.com/wasabihu/claimcite)"
DEFAULT_TEXT_FIELDS: tuple[str, ...] = ("text", "body", "content", "code")
DEFAULT_TIMEOUT: float = 10.0
DEFAULT_MAX_RESPONSE_BYTES: int = 10 * 1024 * 1024
_MGET_BATCH_SIZE = 100
_MAX_EXCERPT_CHARS = 500
_CROSS_LINE_PADDING = 128

_OFFSET_RE = re.compile(r"^(\d+)(?:-(\d+))?$")
_ILLEGAL_INDEX_CHARS = frozenset("/\\*?#:")
_CJK_CHAR = r"[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]"
_CJK_SPACE = re.compile(rf"(?<={_CJK_CHAR})\s+|\s+(?={_CJK_CHAR})")

XERJ_STATUS_MAP: dict[str, str] = {
    "verified": "ok",
    "quote_mismatch": "error",
    "offset_out_of_range": "error",
    "doc_not_found": "error",
    "field_missing": "error",
    "invalid_uri": "error",
    "auth_error": "unchecked",
    "xerj_unconfigured": "unchecked",
    "network_error": "unchecked",
}


class XerjConfigError(ValueError):
    """Invalid, missing, or policy-blocked XERJ node configuration."""


class _RedirectBlockedError(urllib.error.URLError):
    """Raised when a XERJ endpoint returns any 3xx redirect."""


class _ResponseTooLargeError(ValueError):
    """Raised when a XERJ HTTP response exceeds the hard byte cap."""


@dataclass(frozen=True)
class ParsedXerjUri:
    index: str
    doc_id: str
    offset_mode: str  # "b" | "c"
    start: int
    end: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class XerjConfig:
    url: str | None = None
    api_key: str | None = None
    data_dir: str | None = None
    allow_remote: bool = False
    allow_insecure_http: bool = False
    strict_whitespace: bool = False
    timeout: float = DEFAULT_TIMEOUT
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES


@dataclass(frozen=True)
class XerjDocOutcome:
    state: str  # "found" | "doc_not_found" | "auth_error" | "network_error" | "xerj_unconfigured"
    source: dict[str, Any] | None = None
    detail: str | None = None


def _has_control_char(s: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F or unicodedata.category(ch) == "Cc" for ch in s)


def _env_bool(environ: Mapping[str, str], name: str) -> bool:
    return environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def xerj_config_from_env(
    *,
    environ: Mapping[str, str] | None = None,
    url: str | None = None,
    api_key: str | None = None,
    data_dir: str | None = None,
    allow_remote: bool | None = None,
    allow_insecure_http: bool | None = None,
    strict_whitespace: bool | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> XerjConfig:
    """Build a XerjConfig from environment variables with optional CLI overrides."""
    env = os.environ if environ is None else environ
    resolved_url = url if url is not None else (env.get("XERJ_URL", "").strip() or None)
    resolved_data_dir = data_dir if data_dir is not None else (env.get("XERJ_DATA_DIR", "").strip() or None)
    return XerjConfig(
        url=resolved_url,
        api_key=api_key,
        data_dir=resolved_data_dir,
        allow_remote=_env_bool(env, "XERJ_ALLOW_REMOTE") if allow_remote is None else allow_remote,
        allow_insecure_http=(
            _env_bool(env, "XERJ_ALLOW_INSECURE_HTTP")
            if allow_insecure_http is None
            else allow_insecure_http
        ),
        strict_whitespace=(
            _env_bool(env, "XERJ_STRICT_WHITESPACE") if strict_whitespace is None else strict_whitespace
        ),
        timeout=timeout,
        max_response_bytes=max_response_bytes,
    )


def _format_auth_header(raw: str | None, *, preserve_scheme: bool) -> str | None:
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    if _has_control_char(cleaned):
        raise XerjConfigError("XERJ credential contains invalid control characters")
    low = cleaned.lower()
    if preserve_scheme and low.startswith(("apikey ", "bearer ")):
        return cleaned
    if low.startswith("apikey "):
        return cleaned
    return f"ApiKey {cleaned}"


def resolve_xerj_auth(config: XerjConfig, environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve XERJ Authorization header with strict precedence:
    --xerj-api-key > XERJ_AUTH > XERJ_API_KEY > <xerj_data_dir>/admin.key.
    """
    env = os.environ if environ is None else environ
    if config.api_key:
        return _format_auth_header(config.api_key, preserve_scheme=True)
    env_auth = env.get("XERJ_AUTH", "").strip()
    if env_auth:
        return _format_auth_header(env_auth, preserve_scheme=True)
    env_key = env.get("XERJ_API_KEY", "").strip()
    if env_key:
        return _format_auth_header(env_key, preserve_scheme=False)
    if config.data_dir:
        key_path = Path(config.data_dir) / "admin.key"
        try:
            if key_path.is_file():
                for line in key_path.read_text(encoding="utf-8").splitlines():
                    candidate = line.strip()
                    if candidate:
                        return _format_auth_header(candidate, preserve_scheme=True)
        except (OSError, UnicodeDecodeError):
            return None
    return None


def _is_loopback_host(host: str) -> bool:
    cleaned = host.strip().lower().rstrip(".")
    if cleaned == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(cleaned.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_loopback


def validate_xerj_config(config: XerjConfig | None) -> str:
    """Validate XerjConfig and return the normalized node base URL (no trailing slash)."""
    if config is None or not (config.url or "").strip():
        raise XerjConfigError("XERJ node URL is not configured (set --xerj-url or XERJ_URL)")
    raw_url = config.url.strip()
    if _has_control_char(raw_url) or any(ch.isspace() for ch in raw_url):
        raise XerjConfigError("XERJ node URL contains whitespace or control characters")
    parsed = urlsplit(raw_url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise XerjConfigError(f"unsupported XERJ URL scheme: {parsed.scheme or 'none'}")
    if parsed.username is not None or parsed.password is not None:
        raise XerjConfigError("XERJ node URL must not contain embedded credentials")
    if parsed.query or parsed.fragment:
        raise XerjConfigError("XERJ node URL must not contain query parameters or a fragment")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise XerjConfigError("XERJ node URL is missing a host")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise XerjConfigError(f"invalid XERJ URL port: {exc}") from exc

    if not _is_loopback_host(host):
        if not config.allow_remote:
            raise XerjConfigError(
                f"remote XERJ host blocked by default ({host}); pass --xerj-allow-remote to allow"
            )
        if scheme != "https" and not config.allow_insecure_http:
            raise XerjConfigError(
                f"remote XERJ URL must use https:// ({host}); pass --xerj-allow-insecure-http to override"
            )
    if config.max_response_bytes <= 0:
        raise XerjConfigError("max_response_bytes must be positive")
    return raw_url.rstrip("/")


def parse_xerj_uri(uri: str) -> tuple[ParsedXerjUri | None, str | None]:
    """Parse and strictly validate a ``xerj://<index>/<doc_id>#...`` evidence URI."""
    if not isinstance(uri, str) or len(uri) < 8 or uri[:7].lower() != "xerj://":
        return None, "URI must start with xerj://"
    rest = uri[7:]
    if "#" not in rest:
        return None, "missing span fragment (#b=, #c=, or #offset=)"
    authority_path, fragment = rest.split("#", 1)
    if "#" in fragment:
        return None, "unexpected '#' inside fragment"
    if "?" in authority_path:
        return None, "unexpected '?' in xerj:// URI path"
    if "/" not in authority_path:
        return None, "missing <doc_id> segment after <index>"

    raw_index, raw_doc_id = authority_path.split("/", 1)
    try:
        index = unquote(raw_index, errors="strict")
        doc_id = unquote(raw_doc_id, errors="strict")
    except UnicodeDecodeError:
        return None, "invalid UTF-8 percent-encoding in index or doc_id"

    if not index:
        return None, "empty index name"
    if index.startswith("_"):
        return None, "system index starting with '_' is not allowed"
    if index == "." or ".." in index:
        return None, "path traversal segment in index is not allowed"
    if any(ch in _ILLEGAL_INDEX_CHARS or ch.isspace() for ch in index) or _has_control_char(index):
        return None, "illegal character in index name"

    if not doc_id:
        return None, "empty doc_id"
    if _has_control_char(doc_id):
        return None, "control character in doc_id is not allowed"

    if not fragment:
        return None, "empty fragment"
    params: dict[str, str] = {}
    for part in fragment.split("&"):
        if not part or "=" not in part:
            return None, f"malformed fragment parameter: {part!r}"
        key, val = part.split("=", 1)
        if key not in ("b", "c", "offset", "f"):
            return None, f"unknown fragment parameter: {key!r}"
        if key in params:
            return None, f"duplicate fragment parameter: {key!r}"
        params[key] = val

    offset_keys = [k for k in ("b", "c", "offset") if k in params]
    if len(offset_keys) != 1:
        return None, "fragment must contain exactly one of b=, c=, or offset="

    raw_mode = offset_keys[0]
    offset_mode = "c" if raw_mode == "c" else "b"
    m = _OFFSET_RE.fullmatch(params[raw_mode])
    if not m:
        return None, f"invalid {raw_mode}= span syntax"
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) is not None else None

    field_name: str | None = None
    if "f" in params:
        try:
            field_name = unquote(params["f"], errors="strict")
        except UnicodeDecodeError:
            return None, "invalid UTF-8 percent-encoding in field parameter"
        if not field_name or _has_control_char(field_name) or any(ch.isspace() for ch in field_name):
            return None, "invalid field parameter"

    return ParsedXerjUri(
        index=index,
        doc_id=doc_id,
        offset_mode=offset_mode,
        start=start,
        end=end,
        field=field_name,
    ), None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block all 3xx redirects so Authorization headers and loopback targets never leak."""

    def _reject(self, code: int) -> Any:
        raise _RedirectBlockedError(f"HTTP {code} redirect blocked")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return self._reject(code)

    def http_error_301(self, req, fp, code, msg, headers):
        return self._reject(code)

    def http_error_302(self, req, fp, code, msg, headers):
        return self._reject(code)

    def http_error_303(self, req, fp, code, msg, headers):
        return self._reject(code)

    def http_error_307(self, req, fp, code, msg, headers):
        return self._reject(code)

    def http_error_308(self, req, fp, code, msg, headers):
        return self._reject(code)


def _build_xerj_opener() -> urllib.request.OpenerDirector:
    # Empty ProxyHandler bypasses HTTP_PROXY / ALL_PROXY and OS proxy settings so local
    # requests and Authorization keys never leak to proxy daemons.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())


def _read_bounded_json(resp: Any, max_bytes: int) -> Any:
    raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise _ResponseTooLargeError(f"XERJ response exceeded {max_bytes} bytes")
    return json.loads(raw.decode("utf-8"))


def _classify_mget_item_error(item: dict[str, Any]) -> XerjDocOutcome:
    err = item.get("error")
    status = item.get("status")
    err_type = ""
    reason = ""
    if isinstance(err, dict):
        err_type = str(err.get("type") or "")
        reason = str(err.get("reason") or "")
        if not err_type and isinstance(err.get("root_cause"), list) and err["root_cause"]:
            first = err["root_cause"][0]
            if isinstance(first, dict):
                err_type = str(first.get("type") or "")
                reason = reason or str(first.get("reason") or "")
    elif err is not None:
        reason = str(err)

    low_type = err_type.lower()
    low_all = f"{err_type} {reason}".lower()
    detail = f"{err_type}: {reason}".strip(": ") if (err_type or reason) else f"HTTP {status or 'error'}"
    if status in (401, 403) or "security_exception" in low_all or low_type in ("unauthorized", "forbidden"):
        return XerjDocOutcome(state="auth_error", detail=detail)
    if status == 404 or low_type in ("index_not_found_exception", "document_missing_exception"):
        return XerjDocOutcome(state="doc_not_found", detail=detail)
    return XerjDocOutcome(state="network_error", detail=detail)


def _fetch_single_doc(
    opener: urllib.request.OpenerDirector,
    base_url: str,
    index: str,
    doc_id: str,
    headers: dict[str, str],
    config: XerjConfig,
) -> tuple[XerjDocOutcome, bool]:
    """Read-only fallback via GET /{index}/_doc/{doc_id} (never GET /_source).

    Returns ``(outcome, short_circuit_401)``.
    """
    url = f"{base_url}/{quote(index, safe='')}/_doc/{quote(doc_id, safe='')}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(req, timeout=config.timeout) as resp:
            data = _read_bounded_json(resp, config.max_response_bytes)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return XerjDocOutcome(state="doc_not_found", detail="HTTP 404"), False
        if exc.code == 401:
            return XerjDocOutcome(state="auth_error", detail="HTTP 401 Unauthorized"), True
        if exc.code == 403:
            return XerjDocOutcome(state="auth_error", detail="HTTP 403 Forbidden"), False
        return XerjDocOutcome(state="network_error", detail=f"HTTP {exc.code}"), False
    except (_RedirectBlockedError, _ResponseTooLargeError, urllib.error.URLError, TimeoutError, OSError) as exc:
        return XerjDocOutcome(state="network_error", detail=f"{type(exc).__name__}: {exc}"), False
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return XerjDocOutcome(state="network_error", detail=f"invalid JSON response ({type(exc).__name__})"), False

    if not isinstance(data, dict):
        return XerjDocOutcome(state="network_error", detail="unexpected _doc response type"), False
    if data.get("found") is True:
        src = data.get("_source")
        return XerjDocOutcome(state="found", source=src if isinstance(src, dict) else {}), False
    return XerjDocOutcome(state="doc_not_found", detail="document not found"), False


def fetch_xerj_docs(
    pairs: Sequence[tuple[str, str]],
    config: XerjConfig | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[tuple[str, str], XerjDocOutcome]:
    """Batch-fetch XERJ documents in read-only mode without touching any on-disk cache."""
    unique_pairs = list(dict.fromkeys(pairs))
    if not unique_pairs:
        return {}
    try:
        base_url = validate_xerj_config(config)
        assert config is not None
        auth_header = resolve_xerj_auth(config, environ=environ)
    except XerjConfigError as exc:
        outcome = XerjDocOutcome(state="xerj_unconfigured", detail=str(exc))
        return dict.fromkeys(unique_pairs, outcome)

    opener = _build_xerj_opener()
    base_headers: dict[str, str] = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if auth_header:
        base_headers["Authorization"] = auth_header

    results: dict[tuple[str, str], XerjDocOutcome] = {}
    for start_idx in range(0, len(unique_pairs), _MGET_BATCH_SIZE):
        chunk = unique_pairs[start_idx : start_idx + _MGET_BATCH_SIZE]
        body = json.dumps(
            {"docs": [{"_index": idx, "_id": doc_id} for idx, doc_id in chunk]},
            ensure_ascii=False,
        ).encode("utf-8")
        mget_headers = {**base_headers, "Content-Type": "application/json"}
        req = urllib.request.Request(f"{base_url}/_mget", data=body, headers=mget_headers, method="POST")

        try:
            with opener.open(req, timeout=config.timeout) as resp:
                payload = _read_bounded_json(resp, config.max_response_bytes)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                auth_fail = XerjDocOutcome(state="auth_error", detail="HTTP 401 Unauthorized")
                for remaining in unique_pairs[start_idx:]:
                    results[remaining] = auth_fail
                break
            if exc.code in (403, 405):
                aborted_401 = False
                for pos, pair in enumerate(chunk):
                    outcome, is_401 = _fetch_single_doc(
                        opener, base_url, pair[0], pair[1], base_headers, config
                    )
                    results[pair] = outcome
                    if is_401:
                        for rest_pair in chunk[pos + 1 :]:
                            results[rest_pair] = outcome
                        for rest_pair in unique_pairs[start_idx + len(chunk) :]:
                            results[rest_pair] = outcome
                        aborted_401 = True
                        break
                if aborted_401:
                    break
                continue
            net_fail = XerjDocOutcome(state="network_error", detail=f"HTTP {exc.code}")
            for pair in chunk:
                results[pair] = net_fail
            continue
        except (
            _RedirectBlockedError,
            _ResponseTooLargeError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ) as exc:
            net_fail = XerjDocOutcome(state="network_error", detail=f"{type(exc).__name__}: {exc}")
            for pair in chunk:
                results[pair] = net_fail
            continue
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            net_fail = XerjDocOutcome(
                state="network_error", detail=f"invalid JSON response ({type(exc).__name__})"
            )
            for pair in chunk:
                results[pair] = net_fail
            continue

        docs = payload.get("docs") if isinstance(payload, dict) else None
        if not isinstance(docs, list):
            net_fail = XerjDocOutcome(state="network_error", detail="invalid _mget payload: missing 'docs' list")
            for pair in chunk:
                results[pair] = net_fail
            continue

        chunk_set = set(chunk)
        for pos, item in enumerate(docs):
            if not isinstance(item, dict):
                continue
            item_pair = (item.get("_index"), item.get("_id"))
            if item_pair in chunk_set:
                key = (str(item_pair[0]), str(item_pair[1]))
            elif pos < len(chunk):
                key = chunk[pos]
            else:
                continue

            if item.get("error") is not None:
                results[key] = _classify_mget_item_error(item)
            elif item.get("found") is True:
                src = item.get("_source")
                results[key] = XerjDocOutcome(state="found", source=src if isinstance(src, dict) else {})
            else:
                results[key] = XerjDocOutcome(state="doc_not_found", detail="document not found")

        for pair in chunk:
            results.setdefault(pair, XerjDocOutcome(state="doc_not_found", detail="omitted from _mget"))

    return results


def extract_source_field(source: Mapping[str, Any] | None, field: str | None) -> tuple[str | None, str | None]:
    """Select the target text field from ``_source`` (explicit ``f=`` or ``text->body->content->code``)."""
    if not isinstance(source, Mapping):
        return field, None
    if field is not None:
        if field in source:
            val = source[field]
            return field, val if isinstance(val, str) else None
        if "." in field:
            cur: Any = source
            for part in field.split("."):
                if isinstance(cur, Mapping) and part in cur:
                    cur = cur[part]
                else:
                    return field, None
            return field, cur if isinstance(cur, str) else None
        return field, None

    first_empty: str | None = None
    for cand in DEFAULT_TEXT_FIELDS:
        val = source.get(cand)
        if isinstance(val, str):
            if val != "":
                return cand, val
            if first_empty is None:
                first_empty = cand
    if first_empty is not None:
        return first_empty, ""
    return None, None


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _norm_cjk_ws(s: str) -> str:
    return _CJK_SPACE.sub("", _norm_ws(s))


def _quote_in_text(quote_text: str, candidate: str, *, strict_whitespace: bool) -> bool:
    if not quote_text:
        return False
    if strict_whitespace:
        return quote_text in candidate
    q_ws = _norm_ws(quote_text)
    if not q_ws:
        return False
    if q_ws in _norm_ws(candidate):
        return True
    q_cjk = _norm_cjk_ws(quote_text)
    return bool(q_cjk) and q_cjk in _norm_cjk_ws(candidate)


def _snap_utf8_end(raw: bytes, pos: int) -> int:
    end = min(len(raw), pos)
    while end < len(raw) and (raw[end] & 0xC0) == 0x80:
        end += 1
    return end


def _trim_excerpt(text: str) -> str:
    if len(text) <= _MAX_EXCERPT_CHARS:
        return text
    return text[: _MAX_EXCERPT_CHARS - 1] + "…"


def verify_xerj_span(
    full_text: str,
    *,
    offset_mode: str,
    start: int,
    end: int | None = None,
    quotes: Sequence[str] = (),
    strict_whitespace: bool = False,
    index: str | None = None,
    doc_id: str | None = None,
    field: str | None = None,
) -> XerjResult:
    """Verify byte (``b``) or char (``c``) span boundaries and optional inline quotes."""
    raw: bytes | None = None
    if offset_mode == "b":
        raw = full_text.encode("utf-8")
        if end is None:
            if start < 0 or start >= len(raw):
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="b",
                    start=start,
                    end=None,
                    detail=f"byte offset {start} out of range (len={len(raw)})",
                )
            if (raw[start] & 0xC0) == 0x80:
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="b",
                    start=start,
                    end=None,
                    detail=f"byte offset {start} splits a UTF-8 codepoint",
                )
            line_start_idx = raw.rfind(b"\n", 0, start)
            line_start = 0 if line_start_idx == -1 else line_start_idx + 1
            line_end_idx = raw.find(b"\n", start)
            line_end = len(raw) if line_end_idx == -1 else line_end_idx
            span_text = raw[line_start:line_end].decode("utf-8", errors="strict").strip()
        else:
            if start < 0 or end < start or end > len(raw):
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="b",
                    start=start,
                    end=end,
                    detail=f"byte range {start}-{end} out of range (len={len(raw)})",
                )
            if start < len(raw) and (raw[start] & 0xC0) == 0x80:
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="b",
                    start=start,
                    end=end,
                    detail=f"byte range {start}-{end} starts inside a UTF-8 codepoint",
                )
            try:
                span_text = raw[start:end].decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="b",
                    start=start,
                    end=end,
                    detail=f"byte range {start}-{end} splits a UTF-8 codepoint",
                )
    elif offset_mode == "c":
        if end is None:
            if start < 0 or start >= len(full_text):
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="c",
                    start=start,
                    end=None,
                    detail=f"char offset {start} out of range (len={len(full_text)})",
                )
            line_start_idx = full_text.rfind("\n", 0, start)
            line_start = 0 if line_start_idx == -1 else line_start_idx + 1
            line_end_idx = full_text.find("\n", start)
            line_end = len(full_text) if line_end_idx == -1 else line_end_idx
            span_text = full_text[line_start:line_end].strip()
        else:
            if start < 0 or end < start or end > len(full_text):
                return XerjResult(
                    state="offset_out_of_range",
                    index=index,
                    doc_id=doc_id,
                    field=field,
                    offset_mode="c",
                    start=start,
                    end=end,
                    detail=f"char range {start}-{end} out of range (len={len(full_text)})",
                )
            span_text = full_text[start:end]
    else:
        return XerjResult(
            state="invalid_uri",
            index=index,
            doc_id=doc_id,
            field=field,
            offset_mode=offset_mode,
            start=start,
            end=end,
            detail=f"unsupported offset mode: {offset_mode!r}",
        )

    quote_results: list[tuple[str, bool | None]] = []
    for q in quotes:
        matched = _quote_in_text(q, span_text, strict_whitespace=strict_whitespace)
        if not matched and end is None:
            # Allow cross-line forward window for wrapped paragraphs on single-point anchors.
            if offset_mode == "b":
                assert raw is not None
                q_bytes = q.encode("utf-8")
                win_end = _snap_utf8_end(raw, start + len(q_bytes) + _CROSS_LINE_PADDING)
                window_text = raw[start:win_end].decode("utf-8", errors="strict")
            else:
                window_text = full_text[start : min(len(full_text), start + len(q) + _CROSS_LINE_PADDING)]
            matched = _quote_in_text(q, window_text, strict_whitespace=strict_whitespace)
        quote_results.append((q, matched))

    if any(ok is False for _, ok in quote_results):
        return XerjResult(
            state="quote_mismatch",
            index=index,
            doc_id=doc_id,
            field=field,
            offset_mode=offset_mode,
            start=start,
            end=end,
            matched_excerpt=_trim_excerpt(span_text),
            detail="quoted text does not match XERJ span",
            quotes=quote_results,
        )

    return XerjResult(
        state="verified",
        index=index,
        doc_id=doc_id,
        field=field,
        offset_mode=offset_mode,
        start=start,
        end=end,
        matched_excerpt=_trim_excerpt(span_text),
        quotes=quote_results,
    )
