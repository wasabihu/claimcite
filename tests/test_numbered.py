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
