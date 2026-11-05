from __future__ import annotations

import argparse
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
    c = sub.add_parser("check", help="check a Markdown or text file ('-' reads stdin)")
    c.add_argument("path")
    c.add_argument("--format", choices=["md", "json"], default="md")
    c.add_argument("--lang", choices=["en", "zh"], default="en")
    c.add_argument(
        "--fail-on",
        choices=list(FAIL_ON),
        default="error",
        help="exit with 1 when any finding has this status (default: error)",
    )
    c.add_argument("--no-cache", action="store_true", help="bypass the on-disk lookup cache for this run")
    args = p.parse_args(argv)

    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    findings = check_text(text, make_lookup(no_cache=args.no_cache))
    print(to_json(findings) if args.format == "json" else to_markdown(findings, args.lang))
    return 1 if any(f.status in FAIL_ON[args.fail_on] for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
