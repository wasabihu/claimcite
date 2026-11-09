from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .cache import CachedHttp, default_cache_dir
from .check import check_text
from .net import http_get
from .report import to_json, to_markdown
from .sources import Lookup

FAIL_ON = {"error": {"error"}, "warning": {"error", "warning"}, "never": set()}


def make_lookup(no_cache: bool = False) -> Lookup:
    http = http_get if no_cache else CachedHttp(http_get, default_cache_dir())
    return Lookup(http=http)


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
    c.add_argument("--no-web", action="store_true", help="do not fetch web pages; check DOIs and PMIDs only")
    args = p.parse_args(argv)

    lookup = make_lookup(no_cache=args.no_cache)
    several = len(args.paths) > 1
    failed, reports = False, []
    for path in args.paths:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        findings = check_text(text, lookup, web=not args.no_web)
        failed |= any(f.status in FAIL_ON[args.fail_on] for f in findings)
        if args.format == "json":
            reports.append({"path": path, **json.loads(to_json(findings))})
        else:
            print(f"## {path}\n" if several else "", to_markdown(findings, args.lang), sep="")
    if args.format == "json":
        if several:
            total = {k: sum(r["summary"][k] for r in reports) for k in reports[0]["summary"]}
            print(json.dumps({"summary": total, "files": reports}, ensure_ascii=False, indent=2))
        else:
            reports[0].pop("path")
            print(json.dumps(reports[0], ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
