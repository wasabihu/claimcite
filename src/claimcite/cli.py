from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .check import check_documents
from .net import http_get
from .sources import Lookup
from .workspace import (
    CANNOT_READ,
    DOCUMENT_SUFFIXES,
    NO_DOCUMENTS,
    documents,
    format_documents_report,
    make_default_lookup,
)
from .xerj import XerjConfig, xerj_config_from_env

__all__ = ["CANNOT_READ", "DOCUMENT_SUFFIXES", "NO_DOCUMENTS", "documents", "main", "make_lookup"]

FAIL_ON = {"error": {"error"}, "warning": {"error", "warning"}, "never": set()}


def make_lookup(no_cache: bool = False, xerj_config: XerjConfig | None = None) -> Lookup:
    return make_default_lookup(no_cache=no_cache, http_fn=http_get, xerj_config=xerj_config)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="claimcite", description="Check the citations in a document.")
    p.add_argument("--version", action="version", version=f"claimcite {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check Markdown or text files ('-' reads stdin)")
    c.add_argument("paths", nargs="+", metavar="path")
    c.add_argument("--format", choices=["md", "json"], default="md")
    c.add_argument("--lang", choices=["en", "zh"], default="en")
    c.add_argument(
        "--fail-on",
        choices=list(FAIL_ON),
        default="error",
        help="exit with 1 when any finding has this status (default: error)",
    )
    c.add_argument("--no-cache", action="store_true", help="bypass the on-disk lookup cache for this run")
    c.add_argument(
        "--no-web",
        action="store_true",
        help="do not fetch web pages; check DOIs, PMIDs and arXiv IDs only",
    )
    c.add_argument(
        "--no-search",
        action="store_true",
        help="do not look up references that have no DOI or PMID by title (they stay not checked)",
    )
    c.add_argument(
        "--xerj-url",
        default=None,
        help="local XERJ node base URL for xerj:// citations (overrides XERJ_URL; loopback-only by default)",
    )
    c.add_argument(
        "--xerj-api-key",
        default=None,
        help="XERJ API key or Authorization value (overrides XERJ_AUTH, XERJ_API_KEY, and admin.key)",
    )
    c.add_argument(
        "--xerj-data-dir",
        default=None,
        help="XERJ data directory containing admin.key (overrides XERJ_DATA_DIR)",
    )
    c.add_argument(
        "--xerj-allow-remote",
        action="store_true",
        help="allow connecting to a non-loopback XERJ host (requires https:// unless --xerj-allow-insecure-http)",
    )
    c.add_argument(
        "--xerj-allow-insecure-http",
        action="store_true",
        help="allow plain http:// for a remote XERJ host (requires --xerj-allow-remote)",
    )
    c.add_argument(
        "--xerj-strict-whitespace",
        action="store_true",
        help="require exact whitespace matches for quotes against XERJ spans",
    )
    m = sub.add_parser("mcp", help="run as a Model Context Protocol (MCP) server over stdio")
    m.add_argument("--no-cache", action="store_true", help="bypass the on-disk lookup cache")
    args = p.parse_args(argv)
    if args.command == "mcp":
        from .mcp import run_mcp_server

        return run_mcp_server(no_cache=args.no_cache)

    texts: dict[str, str] = {}
    for given in args.paths:
        paths = documents(given)
        if not paths:
            print(NO_DOCUMENTS[args.lang].format(path=given), file=sys.stderr)
            return 2
        for path in paths:
            try:
                texts[path] = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                reason = e.strerror if isinstance(e, OSError) and e.strerror else type(e).__name__
                print(CANNOT_READ[args.lang].format(path=path, reason=reason), file=sys.stderr)
                return 2

    has_xerj_flags = any(
        (
            args.xerj_url is not None,
            args.xerj_api_key is not None,
            args.xerj_data_dir is not None,
            args.xerj_allow_remote,
            args.xerj_allow_insecure_http,
            args.xerj_strict_whitespace,
        )
    )
    if has_xerj_flags:
        xerj_cfg = xerj_config_from_env(
            url=args.xerj_url,
            api_key=args.xerj_api_key,
            data_dir=args.xerj_data_dir,
            allow_remote=True if args.xerj_allow_remote else None,
            allow_insecure_http=True if args.xerj_allow_insecure_http else None,
            strict_whitespace=True if args.xerj_strict_whitespace else None,
        )
        lookup = make_lookup(no_cache=args.no_cache, xerj_config=xerj_cfg)
    else:
        lookup = make_lookup(no_cache=args.no_cache)
    several = len(texts) > 1 or any(Path(g).is_dir() for g in args.paths if g != "-")
    results = check_documents(texts, lookup, web=not args.no_web, search=not args.no_search)
    failed = any(f.status in FAIL_ON[args.fail_on] for findings in results.values() for f in findings)
    if args.format == "json":
        print(format_documents_report(results, fmt="json", lang=args.lang, several=several))
    else:
        for path, findings in results.items():
            single = {path: findings}
            print(format_documents_report(single, fmt="md", lang=args.lang, several=several))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
