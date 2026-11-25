"""Shared file discovery and multi-document report formatting for CLI and MCP."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .cache import CachedHttp, default_cache_dir
from .models import Finding
from .net import http_get
from .report import to_json, to_markdown
from .sources import Lookup
from .xerj import XerjConfig

DOCUMENT_SUFFIXES = {".md", ".markdown", ".txt"}
CANNOT_READ = {"en": "claimcite: cannot read {path}: {reason}", "zh": "claimcite：读不了 {path}：{reason}"}
NO_DOCUMENTS = {
    "en": "claimcite: no Markdown or text files in {path}",
    "zh": "claimcite：{path} 里没有 Markdown 或文本文件",
}
UNSUPPORTED_FILE = {
    "en": "claimcite: unsupported file type {path} (expected .md, .markdown or .txt)",
    "zh": "claimcite：不支持的文件类型 {path}（仅支持 .md、.markdown 或 .txt）",
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


def make_default_lookup(
    no_cache: bool = False,
    http_fn: Callable[[str], str] = http_get,
    xerj_config: XerjConfig | None = None,
) -> Lookup:
    http = http_fn if no_cache else CachedHttp(http_fn, default_cache_dir())
    return Lookup(http=http, xerj_config=xerj_config)


def format_documents_report(
    results: dict[str, list[Finding]],
    *,
    fmt: str = "md",
    lang: str = "en",
    several: bool = False,
) -> str:
    if fmt == "json":
        reports = [{"path": path, **json.loads(to_json(findings))} for path, findings in results.items()]
        if several:
            total = {k: sum(r["summary"][k] for r in reports) for k in reports[0]["summary"]}
            return json.dumps({"summary": total, "files": reports}, ensure_ascii=False, indent=2)
        reports[0].pop("path")
        return json.dumps(reports[0], ensure_ascii=False, indent=2)

    sections = []
    for path, findings in results.items():
        body = to_markdown(findings, lang)
        sections.append(f"## {path}\n{body}" if several else body)
    return "\n".join(sections) if several else sections[0]
