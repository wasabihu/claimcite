"""References written out without a DOI or PMID. AI-invented citations usually look like this, and
before this change claimcite skipped them silently: a document with three such references reported
"0 ok · 0 warnings · 0 errors · 0 not checked", which reads like a clean pass."""

import json

from conftest import FIXTURES, FakeHttp

from claimcite.check import check_text
from claimcite.extract import extract_references
from claimcite.report import to_json, to_markdown
from claimcite.sources import Lookup, best_match, parse_crossref_search

INVENTED = (
    "Smith, J., & Lee, K. (2019). Daily walking and depression risk: a cohort study. "
    "Journal of Affective Disorders, 245, 112-118."
)
HOLZEL = (
    "Hölzel BK, et al. Mindfulness practice leads to increases in regional brain gray matter density. "
    "Psychiatry Res. 2011;191(1):36-43."
)
DOC = f"""\
# 测试

研究表明，每天走路 30 分钟可以降低抑郁风险。

## 参考文献

1. {INVENTED}
2. {HOLZEL}
3. 张三. 中年女性焦虑的成因分析[J]. 心理学报, 2020, 52(3): 301-310.
"""

# Recorded on 2026-11-09. Only the exact requests these tests make are routed.
ROUTES = {
    "query.bibliographic=Smith%2C%20J.%2C%20%26%20Lee": "crossref_search_invented.json",
    "term=daily%20walking%20and%20depression%20risk%20a%20cohort%20study%5Bti%5D": "esearch_title_invented.json",
    "query.bibliographic=H%C3%B6lzel%20BK": "crossref_search_holzel.json",
    "api/handles/10.1016/j.pscychresns.2010.08.006": "handle_holzel.json",
    "term=10.1016%2Fj.pscychresns.2010.08.006%5Bdoi%5D": "esearch_doi_holzel.json",
    "efetch.fcgi?db=pubmed&id=21071182&": "pubmed_holzel.xml",
    "term=mindfulness%20practice%20leads%20to%20increases%20in%20regional%20brain%20gray%20matter%20density%5Bti%5D": (
        "esearch_title_holzel.json"
    ),
}


def text_refs(doc):
    return [r for r in extract_references(doc) if r.kind == "text"]


def test_reference_list_entries_without_identifiers_are_found():
    refs = text_refs(DOC)
    assert [(r.line, r.year, r.title) for r in refs] == [
        (7, 2019, "Daily walking and depression risk: a cohort study"),
        (8, 2011, "Mindfulness practice leads to increases in regional brain gray matter density"),
        (9, 2020, "中年女性焦虑的成因分析"),
    ]


def test_titles_in_common_styles():
    doc = """\
## References

- Smith, J. (2019) Walking and mood in older adults. Journal of Aging, 3, 1-9.
- Smith, John. "Walking and Mood in Older Adults." Journal of Aging, vol. 3, 2019, pp. 1-9.
- *Kim Y, Lee J.* Coffee and sleep. Sleep Med. 2020;12:4-9.
"""
    assert [r.title for r in text_refs(doc)] == [
        "Walking and mood in older adults",
        "Walking and Mood in Older Adults",
        "Coffee and sleep",
    ]
    mla = text_refs(doc)[1]
    assert mla.quotes == []  # the quoted MLA title is not a quote from the paper


def test_only_reference_lists_and_labelled_entries_count():
    doc = f"""\
# Notes

1. Buy a 2019 calendar and a pen for the kitchen.
- In 2020 we moved house, which took most of the summer.

[1] {INVENTED}

## References

- {INVENTED} https://doi.org/10.1007/s10654-019-00524-3
- Too short 2019

## Next steps

- Read more papers from 2021 about walking and depression.
"""
    refs = extract_references(doc)
    assert [(r.kind, r.line) for r in refs] == [("text", 6), ("doi", 10)]


def test_an_entry_whose_doi_is_on_the_next_line_is_not_searched():
    doc = f"## References\n\n1. {INVENTED}\n   https://doi.org/10.1007/s10654-019-00524-3\n2. {HOLZEL}\n"
    assert [(r.kind, r.line) for r in extract_references(doc)] == [("doi", 4), ("text", 5)]


def test_an_invented_reference_is_a_warning_and_a_real_one_is_found_by_title():
    http = FakeHttp(ROUTES)
    findings = check_text(DOC, Lookup(http=http), web=False)
    invented, real, chinese = findings

    assert invented.status == "warning"
    assert invented.note == "not_found"
    assert invented.exists is None  # not "does not exist": it may be a book or a reworded title

    assert real.status == "ok"
    assert real.found_via == "crossref"
    assert real.record.doi == "10.1016/j.pscychresns.2010.08.006"
    assert real.record.source == "pubmed"  # the full record is fetched, so the study type is shown
    assert real.evidence.key != "unknown"

    assert chinese.status == "unchecked"
    assert chinese.note == "cjk"
    assert not any("%E4%B8%AD" in url for url in http.calls)  # no request for the Chinese title


def test_the_report_no_longer_looks_like_a_clean_pass():
    findings = check_text(DOC, Lookup(http=FakeHttp(ROUTES)), web=False)
    md = to_markdown(findings, "zh")
    assert "通过 1 · 警告 1 · 错误 0 · 未核查 1" in md
    assert "在 Crossref 和 PubMed 都没找到" in md
    assert "按标题在 Crossref 找到：doi:10.1016/j.pscychresns.2010.08.006" in md
    data = json.loads(to_json(findings))
    assert [f["note"] for f in data["findings"]] == ["not_found", None, "cjk"]
    assert data["findings"][1]["found_via"] == "crossref"


def test_a_document_without_citations_says_so():
    assert "No citations found." in to_markdown([], "en")
    assert "没有找到任何引用。" in to_markdown([], "zh")


def test_search_off_makes_no_requests():
    http = FakeHttp({})
    findings = check_text(DOC, Lookup(http=http), web=False, search=False)
    assert [f.note for f in findings] == ["search_off", "search_off", "cjk"]
    assert http.calls == []


def test_a_failed_search_is_not_reported_as_missing():
    def down(url):
        raise OSError("network down")

    finding = check_text(DOC, Lookup(http=down), web=False)[0]
    assert finding.status == "unchecked"
    assert finding.note is None and finding.error


def test_pubmed_title_search_is_used_when_crossref_has_no_match():
    # Crossref answers with unrelated papers (the recorded results for another reference).
    routes = {**ROUTES, "query.bibliographic=H%C3%B6lzel%20BK": "crossref_search_invented.json"}
    lookup = Lookup(http=FakeHttp(routes))
    ref = text_refs(DOC)[1]
    via, record = lookup.find(ref)
    assert (via, record.pmid) == ("pubmed", "21071182")


def test_best_match_rejects_unrelated_search_results():
    ref = text_refs(DOC)[0]
    candidates = parse_crossref_search((FIXTURES / "crossref_search_invented.json").read_text())
    assert len(candidates) == 5
    assert best_match(ref, candidates) is None


BOOK = "Kahneman, D. (2011). Thinking, fast and slow. Farrar, Straus and Giroux."
BOOK_ROUTES = {
    "query.bibliographic=Kahneman%2C%20D.": "crossref_search_kahneman.json",
    "term=thinking%20fast%20and%20slow%5Bti%5D": "esearch_title_kahneman.json",
    "efetch.fcgi?db=pubmed&id=42679605,": "pubmed_title_kahneman.xml",
}


def test_book_detection():
    doc = f"""\
## References

- {BOOK}
- Walker M. Why we sleep. New York: Scribner; 2017.
- 王五. 心理学与生活[M]. 北京: 人民邮电出版社, 2003.
- {INVENTED}
- {HOLZEL}
"""
    assert [r.book for r in text_refs(doc)] == [True, True, True, False, False]


def test_a_book_matched_only_by_reviews_of_it_is_not_checked():
    # Crossref's top hits for the book are reviews, e.g. "D. Kahneman. (2011). Thinking, Fast and
    # Slow. New York, NY: Farrar, Straus and Giroux. 499 pages." in a journal. Not the book.
    candidates = parse_crossref_search((FIXTURES / "crossref_search_kahneman.json").read_text())
    assert any("499 pages" in (c.title or "") for c in candidates)
    finding = check_text(f"## References\n\n1. {BOOK}\n", Lookup(http=FakeHttp(BOOK_ROUTES)), web=False)[0]
    assert (finding.status, finding.note) == ("unchecked", "book")
