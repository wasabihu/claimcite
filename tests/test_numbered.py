"""Numbered citations ([1], [^1], [1-3]) carry the quotes written next to them in the body to the
reference they point to. Before this, a quote in the body was never checked when the identifier only
appeared in the reference list at the end, which is how most AI-written articles cite."""

from claimcite.extract import extract_references

DOC = """\
# Coffee

Drinkers live longer: "Coffee consumption has been associated with decreased mortality"[1].
Aspirin "prevents disability in healthy older adults"[2] and is cheap. It was “a big trial”[^n].
No quote here [1]. Several studies [1-2] agree.
An earlier sentence says "not about any source". Then a plain claim [2].

## References

[1] Kim Y et al. (2019). Coffee consumption and all-cause mortality. https://doi.org/10.1007/s10654-019-00524-3
2. McNeil JJ (2018). Aspirin prevents disability. NEJM. PMID: 30221596
[^n]: Trial site. https://example.org/trial
"""


def by_id(refs):
    return {r.id: r for r in refs}


def test_body_quotes_go_to_the_numbered_reference():
    refs = by_id(extract_references(DOC))
    assert refs["10.1007/s10654-019-00524-3"].quotes == [
        "Coffee consumption has been associated with decreased mortality"
    ]
    assert refs["30221596"].quotes == ["prevents disability in healthy older adults"]
    assert refs["https://example.org/trial"].quotes == ["a big trial"]


def test_a_quote_in_an_earlier_sentence_is_not_attached():
    refs = by_id(extract_references(DOC))
    assert "not about any source" not in refs["30221596"].quotes


def test_reference_list_lines_still_give_title_and_year():
    refs = by_id(extract_references(DOC))
    assert refs["30221596"].year == 2018
    assert refs["10.1007/s10654-019-00524-3"].line == 10


def test_ranges_and_lists_point_to_every_reference():
    text = 'They agree "on the result"[1-2], and "again"[1, 2].\n\n1. https://doi.org/10.1000/a\n2. https://doi.org/10.1000/b\n'
    refs = by_id(extract_references(text))
    assert refs["10.1000/a"].quotes == ["on the result", "again"]
    assert refs["10.1000/b"].quotes == ["on the result", "again"]


def test_markdown_links_and_unknown_labels_are_left_alone():
    text = 'See [the paper](https://doi.org/10.1000/a) and "a quote"[9].\n'
    refs = extract_references(text)
    assert [r.id for r in refs] == ["10.1000/a"]
    assert refs[0].quotes == []


def test_without_numbered_markers_nothing_changes():
    text = 'Smith (2020). A title. Journal: "inline quote". https://doi.org/10.1000/a\n'
    (ref,) = extract_references(text)
    assert ref.quotes == ["inline quote"]


def test_code_is_not_a_citation():
    text = (
        'Index a string:\n\n```python\nfirst = "hello world"[1]\n```\n\nOr inline `"hello world"[1]`.\n\n'
        "1. Install from https://doi.org/10.1000/a\n"
    )
    (ref,) = extract_references(text)
    assert ref.quotes == []


def test_dangling_marker_in_body_is_reported_with_all_line_numbers(fake_http):
    import json

    from claimcite.check import check_text
    from claimcite.report import to_json, to_markdown
    from claimcite.sources import Lookup

    doc = (
        "# Summary\n\n"
        "Coffee drinkers live longer [1] and a wrapped paragraph\n"
        "also makes an unsupported claim [5].\n"
        "Aspirin helps too, and both sources agree [2, 5].\n\n"
        "## References\n\n"
        "[1] Kim Y et al. (2019). Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers. https://doi.org/10.1007/s10654-019-00524-3\n"
        "2. McNeil JJ (2018). Effect of Aspirin on Disability-free Survival in the Healthy Elderly. "
        "PMID: 30221596\n"
    )
    refs = extract_references(doc)
    markers = [r for r in refs if r.kind == "marker"]
    assert len(markers) == 1
    m = markers[0]
    assert m.id == "5"
    assert m.label == "5"
    assert m.line == 4
    assert m.used_lines == [4, 5]

    by_lbl = {r.label: r for r in refs if r.kind != "marker"}
    assert by_lbl["1"].used_lines == [3]
    assert by_lbl["1"].uncited is False
    assert by_lbl["2"].used_lines == [5]
    assert by_lbl["2"].uncited is False

    findings = check_text(doc, Lookup(http=fake_http, workers=1), web=False, search=False)
    marker_findings = [f for f in findings if f.ref.kind == "marker"]
    assert len(marker_findings) == 1
    mf = marker_findings[0]
    assert mf.status == "error"
    assert mf.exists is None

    md_en = to_markdown(findings, "en")
    assert "`[5]`" in md_en
    assert "lines 4, 5" in md_en

    md_zh = to_markdown(findings, "zh")
    assert "`[5]`" in md_zh
    assert "第 4、5 行" in md_zh

    payload = json.loads(to_json(findings))
    (jm,) = [item for item in payload["findings"] if item["kind"] == "marker"]
    assert jm["label"] == "5"
    assert jm["used_lines"] == [4, 5]
    assert jm["uncited"] is False


def test_uncited_numbered_and_footnote_references_are_errors(fake_http):
    import json

    from claimcite.check import check_text
    from claimcite.report import to_json, to_markdown
    from claimcite.sources import Lookup

    doc = (
        "# Draft\n\n"
        "Coffee consumption reduces mortality [1].\n\n"
        "## References\n\n"
        "[1] Kim Y et al. (2019). Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers. https://doi.org/10.1007/s10654-019-00524-3\n"
        "2. McNeil JJ (2018). Effect of Aspirin on Disability-free Survival in the Healthy Elderly. "
        "PMID: 30221596\n"
        "[^unused]: WHO (2023). Fact sheet. https://example.org/unused\n"
    )
    findings = check_text(doc, Lookup(http=fake_http, workers=1), web=False, search=False)
    by_lbl = {f.ref.label: f for f in findings}

    assert by_lbl["1"].status == "ok"
    assert by_lbl["1"].uncited is False
    assert by_lbl["1"].ref.used_lines == [3]

    assert by_lbl["2"].exists is True
    assert by_lbl["2"].uncited is True
    assert by_lbl["2"].status == "error"

    assert by_lbl["^unused"].uncited is True
    assert by_lbl["^unused"].status == "error"

    md_en = to_markdown(findings, "en")
    assert "Reference [2] is never cited in the text" in md_en
    assert "Reference [^unused] is never cited in the text" in md_en

    md_zh = to_markdown(findings, "zh")
    assert "参考文献 [2] 在正文中从未被引用" in md_zh
    assert "参考文献 [^unused] 在正文中从未被引用" in md_zh

    data = json.loads(to_json(findings))
    by_j = {item["label"]: item for item in data["findings"]}
    assert by_j["2"]["uncited"] is True
    assert by_j["2"]["used_lines"] == []
    assert by_j["^unused"]["uncited"] is True
    assert by_j["^unused"]["used_lines"] == []


def test_dangling_footnote_marker_without_reference_section_is_an_error():
    from claimcite.check import check_text
    from claimcite.sources import Lookup

    (f,) = check_text("Claim [^missing].", Lookup(http=lambda u: "", workers=1), web=False, search=False)
    assert f.ref.kind == "marker"
    assert f.ref.id == "^missing"
    assert f.status == "error"
    assert f.ref.used_lines == [1]


def test_unreferenced_numbered_bibliography_without_body_markers_is_not_flagged(fake_http):
    from claimcite.check import check_text
    from claimcite.sources import Lookup

    doc_dots = (
        "# Review\n\n"
        "Earlier work (Kim, 2019) examined mortality.\n\n"
        "## References\n\n"
        "1. Kim Y et al. (2019). Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers. https://doi.org/10.1007/s10654-019-00524-3\n"
        "2. McNeil JJ (2018). Effect of Aspirin on Disability-free Survival in the Healthy Elderly. "
        "PMID: 30221596\n"
    )
    findings = check_text(doc_dots, Lookup(http=fake_http, workers=1), web=False, search=False)
    assert all(f.uncited is False for f in findings)
    assert [f.status for f in findings] == ["ok", "ok"]

    doc_brackets = doc_dots.replace("1. Kim", "[1] Kim").replace("2. McNeil", "[2] McNeil")
    findings_b = check_text(doc_brackets, Lookup(http=fake_http, workers=1), web=False, search=False)
    assert all(f.uncited is False for f in findings_b)
    assert [f.status for f in findings_b] == ["ok", "ok"]


def test_adjacent_markers_intervals_quotes_links_and_body_lists_do_not_false_alarm(fake_http):
    from claimcite.check import check_text
    from claimcite.sources import Lookup

    doc = (
        "# Clinical Notes\n\n"
        "1. Drink water regularly (https://example.org/water).\n\n"
        "Two cohorts agree [1][2] and again [1]\u2060[2].\n"
        "Among 1405 women (median [IQR] age, 60 [52-68] years), efficacy was 75% [65-83], "
        "normalized to [0, 1] on a [1-5] scale across pages [120-185].\n"
        'A paper noted "prior cohorts [12, 15] disagreed"[1].\n'
        "Read [the study][3] and [other][9].\n"
        "However, incidence dropped by 25% [5]. Earlier studies [4-6] agree.\n\n"
        "[3]: https://example.org/study\n\n"
        "📄 参考文献\n\n"
        "1. Dr. Sue Johnson official memorial page\n"
        "2. Kim Y et al. (2019). Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers. https://doi.org/10.1007/s10654-019-00524-3\n"
    )
    refs = extract_references(doc)
    by_lbl = {r.label: r for r in refs if r.kind != "marker"}
    assert by_lbl["2"].used_lines == [5]
    assert "3" in by_lbl and by_lbl["3"].uncited is False
    # Body ordered list "1. Drink water" must not become label "1" or steal "1." in references
    water_refs = [r for r in refs if r.id == "https://example.org/water"]
    assert len(water_refs) == 1
    assert water_refs[0].label is None
    assert water_refs[0].uncited is False

    # Missing markers: 4, 5 (on line 9), 6 — NOT 1, 2, 3, 9, 12, 15, 0, 52-68, 65-83, 1-5, 120-185
    markers = {r.id: r.used_lines for r in refs if r.kind == "marker"}
    assert set(markers) == {"4", "5", "6"}
    assert markers["5"] == [9]

    # Check headings like "参考文献与出处" with a short entry without a year are also respected
    doc_zh_heading = (
        "支持该观点[1]。\n\n"
        "参考文献与出处\n\n"
        "1. 官方网站纪念页\n"
    )
    assert [r for r in extract_references(doc_zh_heading) if r.kind == "marker"] == []

    # Check end-to-end check_text on the complex doc does not error on water link or [1]
    findings = check_text(doc, Lookup(http=fake_http, workers=1), web=False, search=False)
    err_ids = {f.ref.id for f in findings if f.status == "error"}
    assert err_ids == {"4", "5", "6"}
