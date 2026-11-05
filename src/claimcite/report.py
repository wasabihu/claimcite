from __future__ import annotations

import json
from collections import Counter

from .models import Finding

STATUSES = ("ok", "warning", "error", "unchecked")

LABELS = {
    "en": {
        "title": "Citation check",
        "summary": "{ok} ok · {warning} warnings · {error} errors · {unchecked} not checked",
        "ok": "OK",
        "warning": "Warning",
        "error": "Error",
        "unchecked": "Not checked",
        "line": "line",
        "missing": "Identifier does not exist",
        "title_mismatch": "Title does not match this identifier. Registry title: {t}",
        "title_partial": "Title only partly matches. Registry title: {t}",
        "year": "Year differs: document {a}, registry {b}",
        "quote_missing": "Quoted text not found in the abstract (it may be in the full text): “{q}”",
        "quote_unknown": "No abstract available to check the quote: “{q}”",
        "web": "Web page: not verified yet",
        "web_alive": "Web page is reachable",
        "web_dead": "Web page could not be opened (HTTP {code})",
        "web_blocked": "Skipped: robots.txt disallows automated fetching",
        "web_failed": "Web page lookup failed ({e}); not checked",
        "web_quote_missing": "Quoted text not found on the page: “{q}”",
        "web_quote_unknown": "Could not read the page text (e.g. a PDF); quoted text not checked: “{q}”",
        "failed": "Lookup failed, try again later ({e})",
        "evidence": "Study type (as tagged by PubMed)",
        "disclaimer": "Automated check. It confirms that sources exist and match; it does not judge "
        "whether a claim is true and is not medical advice.",
    },
    "zh": {
        "title": "引用核查",
        "summary": "通过 {ok} · 警告 {warning} · 错误 {error} · 未核查 {unchecked}",
        "ok": "通过",
        "warning": "警告",
        "error": "错误",
        "unchecked": "未核查",
        "line": "第 {n} 行",
        "missing": "编号不存在",
        "title_mismatch": "标题与该编号对应的文献不符。登记的标题：{t}",
        "title_partial": "标题只部分吻合。登记的标题：{t}",
        "year": "年份不一致：文中 {a}，登记 {b}",
        "quote_missing": "引文在摘要中没有找到（可能在全文里）：「{q}」",
        "quote_unknown": "没有摘要，无法核对引文：「{q}」",
        "web": "网页链接：暂不核查",
        "web_alive": "网页可访问",
        "web_dead": "网页打不开（HTTP {code}）",
        "web_blocked": "已跳过：robots.txt 不允许自动抓取",
        "web_failed": "网页查询失败（{e}），未核查",
        "web_quote_missing": "页面正文中未找到引文：「{q}」",
        "web_quote_unknown": "读不到页面文字（比如 PDF），引文未核对：「{q}」",
        "failed": "查询失败，请稍后重试（{e}）",
        "evidence": "研究类型（PubMed 标注）",
        "disclaimer": "自动核查只确认出处存在且对得上，不判断说法本身是否正确，也不构成医疗建议。",
    },
}


def summary(findings: list[Finding]) -> dict[str, int]:
    c = Counter(f.status for f in findings)
    return {s: c.get(s, 0) for s in STATUSES}


def _problems(f: Finding, L: dict[str, str]) -> list[str]:
    if f.ref.kind == "url":
        w = f.web
        if w is None:
            return [L["web"]]
        if w.state == "blocked":
            return [L["web_blocked"]]
        if w.state == "failed":
            return [L["web_failed"].format(e=w.detail or "error")]
        if w.state == "dead":
            return [L["web_dead"].format(code=w.status_code or "?")]
        out = [L["web_alive"]]
        out += [L["web_quote_missing"].format(q=q) for q, ok in w.quotes if ok is False]
        out += [L["web_quote_unknown"].format(q=q) for q, ok in w.quotes if ok is None]
        return out
    if f.error:
        return [L["failed"].format(e=f.error)]
    if f.exists is False:
        return [L["missing"]]
    out = []
    title = f.record.title if f.record else None
    if f.title_match == "mismatch":
        out.append(L["title_mismatch"].format(t=title))
    elif f.title_match == "partial":
        out.append(L["title_partial"].format(t=title))
    if f.year_match is False:
        out.append(L["year"].format(a=f.ref.year, b=f.record.year))
    for q, ok in f.quotes:
        if ok is False:
            out.append(L["quote_missing"].format(q=q))
        elif ok is None:
            out.append(L["quote_unknown"].format(q=q))
    return out


def to_markdown(findings: list[Finding], lang: str = "en") -> str:
    L = LABELS[lang]
    lines = [f"# {L['title']}", "", L["summary"].format(**summary(findings)), ""]
    for f in findings:
        where = L["line"].format(n=f.ref.line) if lang == "zh" else f"{L['line']} {f.ref.line}"
        lines.append(f"- **{L[f.status]}** · {where} · `{f.ref.kind}:{f.ref.id}`")
        if f.record and f.record.title:
            lines.append(f"  - {f.record.title} ({f.record.year or '?'})")
        if f.evidence and f.evidence.key != "unknown":
            label = f.evidence.label_zh if lang == "zh" else f.evidence.label_en
            lines.append(f"  - {L['evidence']}{'：' if lang == 'zh' else ': '}{label}")
        lines += [f"  - {p}" for p in _problems(f, L)]
    lines += ["", f"_{L['disclaimer']}_", ""]
    return "\n".join(lines)


def to_json(findings: list[Finding]) -> str:
    def one(f: Finding) -> dict:
        r = f.record
        return {
            "kind": f.ref.kind,
            "id": f.ref.id,
            "line": f.ref.line,
            "status": f.status,
            "exists": f.exists,
            "stated": {"title": f.ref.title, "year": f.ref.year, "quotes": f.ref.quotes},
            "registry": None
            if r is None
            else {
                "source": r.source,
                "title": r.title,
                "year": r.year,
                "doi": r.doi,
                "pmid": r.pmid,
                "publication_types": r.pub_types,
            },
            "title_match": f.title_match,
            "year_match": f.year_match,
            "quotes": [{"text": q, "found_in_abstract": ok} for q, ok in f.quotes],
            "evidence": f.evidence.key if f.evidence else None,
            "error": f.error,
            "web": None
            if f.web is None
            else {
                "state": f.web.state,
                "status_code": f.web.status_code,
                "quotes": [{"text": q, "found": ok} for q, ok in f.web.quotes],
            },
        }

    return json.dumps(
        {"summary": summary(findings), "findings": [one(f) for f in findings]}, ensure_ascii=False, indent=2
    )
