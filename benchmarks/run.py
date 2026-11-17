"""Offline replayable benchmark for claimcite verification accuracy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from claimcite.check import check_text
from claimcite.net import NotFound
from claimcite.sources import Lookup

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
DEFAULT_CASES = Path(__file__).resolve().parent / "cases.jsonl"


def _doi_route(doi: str) -> str:
    return "term=" + quote('"' + doi + '"[doi]', safe="")


_ROUTES: dict[str, str | type[NotFound]] = {
    "doi.org/api/handles/10.1007/s10654-019-00524-3": "handle_found.json",
    "doi.org/api/handles/10.1016/j.pscychresns.2010.08.006": "handle_holzel.json",
    "doi.org/api/handles/10.48550/arxiv.1706.03762": "handle_arxiv_1706_03762.json",
    "doi.org/api/handles/10.1016/s0140-6736(97)11096-0": "handle_retracted.json",
    "doi.org/api/handles/10.1016/s0140-6736%2897%2911096-0": "handle_retracted.json",
    "doi.org/api/handles/10.1016/s0140-6736(10)60175-4": "handle_retraction_notice.json",
    "doi.org/api/handles/10.1016/s0140-6736%2810%2960175-4": "handle_retraction_notice.json",
    "doi.org/api/handles/10.9999/does-not-exist-claimcite": "handle_missing.json",
    _doi_route("10.1007/s10654-019-00524-3"): "esearch_doi_found.json",
    _doi_route("10.1016/j.pscychresns.2010.08.006"): "esearch_doi_holzel.json",
    _doi_route("10.9999/does-not-exist-claimcite"): "esearch_doi_missing.json",
    _doi_route("10.1016/s0140-6736(97)11096-0"): "esearch_empty.json",
    _doi_route("10.1016/s0140-6736(10)60175-4"): "esearch_empty.json",
    "efetch.fcgi?db=pubmed&id=31055709": "pubmed_efetch.xml",
    "efetch.fcgi?db=pubmed&id=33237064": "pubmed_efetch.xml",
    "efetch.fcgi?db=pubmed&id=30221596": "pubmed_efetch.xml",
    "efetch.fcgi?db=pubmed&id=21071182": "pubmed_holzel.xml",
    "efetch.fcgi?db=pubmed&id=9500320": "pubmed_retracted.xml",
    "efetch.fcgi?db=pubmed&id=99999999": "pubmed_missing.xml",
    "api.crossref.org/works?filter=doi:10.1016/s0140-6736(97)11096-0&rows=1": "crossref_retracted_filter.json",
    "api.crossref.org/works?filter=doi:10.1016/s0140-6736(10)60175-4&rows=1": "crossref_retraction_notice_filter.json",
    "api.crossref.org/works/10.1007/s10654-019-00524-3": "crossref_work.json",
    "api.crossref.org/works/10.9999/does-not-exist-claimcite": NotFound,
    "export.arxiv.org/api/query?id_list=1706.03762&max_results=1": "arxiv_1706_03762.xml",
    "export.arxiv.org/api/query?id_list=9912.99999&max_results=1": "arxiv_missing.xml",
    "https://example.org/robots.txt": "web_robots_allow.txt",
    "https://example.org/who-facts": "web_page.html",
    "https://example.org/missing-page": NotFound,
}


def benchmark_http(url: str) -> str:
    """Resolve benchmark HTTP calls strictly against recorded fixture files."""
    for needle, target in _ROUTES.items():
        if needle in url:
            if target is NotFound:
                raise NotFound(url)
            return (FIXTURES / target).read_text(encoding="utf-8")
    if "api.crossref.org/works?" in url and "query.bibliographic=" in url:
        if "Mindfulness" in url:
            return (FIXTURES / "crossref_search_holzel.json").read_text(encoding="utf-8")
        if "Thinking" in url:
            return (FIXTURES / "crossref_search_kahneman.json").read_text(encoding="utf-8")
        if "Daily" in url or "walking" in url.lower():
            return (FIXTURES / "crossref_search_invented.json").read_text(encoding="utf-8")
    if "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi" in url and "retmax=5" in url:
        if "mindfulness" in url.lower():
            return (FIXTURES / "esearch_title_holzel.json").read_text(encoding="utf-8")
        if "thinking" in url.lower():
            return (FIXTURES / "esearch_title_kahneman.json").read_text(encoding="utf-8")
        if "daily" in url.lower() or "walking" in url.lower():
            return (FIXTURES / "esearch_title_invented.json").read_text(encoding="utf-8")
    raise AssertionError(f"Unexpected benchmark URL: {url}")


def load_cases(path: Path = DEFAULT_CASES) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(cases_path: Path = DEFAULT_CASES) -> dict:
    cases = load_cases(cases_path)
    results = []
    by_expected: Counter[str] = Counter()
    passed_by_expected: Counter[str] = Counter()
    false_errors = 0

    for case in cases:
        lookup = Lookup(http=benchmark_http, workers=1)
        findings = check_text(case["text"], lookup=lookup, web=True, search=True)
        if len(findings) != 1:
            actual = f"count:{len(findings)}"
        else:
            raw_status = findings[0].status
            actual = "not_checked" if raw_status == "unchecked" else raw_status
        expected = case["expected_status"]
        ok = actual == expected
        by_expected[expected] += 1
        if ok:
            passed_by_expected[expected] += 1
        elif actual == "error" and expected in ("ok", "not_checked"):
            false_errors += 1
        results.append(
            {
                "id": case["id"],
                "category": case["category"],
                "expected": expected,
                "actual": actual,
                "ok": ok,
            }
        )

    total = len(cases)
    passed = sum(1 for r in results if r["ok"])
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "false_errors": false_errors,
        "by_expected": {
            status: {"passed": passed_by_expected[status], "total": count}
            for status, count in sorted(by_expected.items())
        },
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline claimcite benchmark.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    args = parser.parse_args(argv)

    summary = evaluate(args.cases)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"claimcite offline benchmark: {summary['passed']}/{summary['total']} "
            f"({summary['accuracy'] * 100:.1f}%), false_errors={summary['false_errors']}"
        )
        for status, stats in summary["by_expected"].items():
            print(f"  {status:12s}: {stats['passed']}/{stats['total']}")
        for item in summary["results"]:
            if not item["ok"]:
                print(f"  FAIL {item['id']}: expected={item['expected']} actual={item['actual']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
