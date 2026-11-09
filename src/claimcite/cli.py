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
DOCUMENT_SUFFIXES = {".md", ".markdown", ".txt"}
CANNOT_READ = {"en": "claimcite: cannot read {path}: {reason}", "zh": "claimcite：读不了 {path}：{reason}"}
NO_DOCUMENTS = {
    "en": "claimcite: no Markdown or text files in {path}",
    "zh": "claimcite：{path} 里没有 Markdown 或文本文件",
}


def documents(path: str) -> list[str]:
    """A directory stands for the Markdown and text files in it, hidden folders left out."""
    root = Path(path)
    if path == "-" or not root.is_dir():
        return [path]
    found = (
        f
        for f in root.rglob("*")
        if f.suffix.lower() in DOCUMENT_SUFFIXES
        and f.is_file()
        and not any(part.startswith(".") for part in f.relative_to(root).parts)
    )
    return sorted(str(f) for f in found)


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
    c.add_argument(
        "--no-search",
        action="store_true",
        help="do not look up references that have no DOI or PMID by title (they stay not checked)",
    )
    args = p.parse_args(argv)

    texts = {}
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

    lookup = make_lookup(no_cache=args.no_cache)
    several = len(texts) > 1 or any(Path(g).is_dir() for g in args.paths if g != "-")
    failed, reports = False, []
    for path, text in texts.items():
        findings = check_text(text, lookup, web=not args.no_web, search=not args.no_search)
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
