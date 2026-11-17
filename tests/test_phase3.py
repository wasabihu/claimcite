"""Phase 3 extraction tests: wrapped blocks, right-hand quotes/years, and APA author-year linking."""

from __future__ import annotations

from conftest import FakeHttp

from claimcite.check import check_text
from claimcite.extract import extract_references
from claimcite.sources import Lookup


def test_multiline_wrapped_reference_with_doi_preserves_doi_line_and_captures_title():
    scheme = "https://"
    doc = (
        "## References\n\n"
        "1. Kim Y, Je Y, Giovannucci E (2019). Coffee consumption and all-cause and\n"
        "   cause-specific mortality: a meta-analysis by potential modifiers.\n"
        "   Eur J Epidemiol.\n"
        f"   {scheme}doi.org/10.1007/s10654-019-00524-3\n"
    )
    refs = extract_references(doc)
    assert len(refs) == 1
    ref = refs[0]
    assert ref.kind == "doi"
    assert ref.id == "10.1007/s10654-019-00524-3"
    assert ref.line == 6
    assert ref.year == 2019
    assert ref.title == (
        "Coffee consumption and all-cause and cause-specific mortality: "
        "a meta-analysis by potential modifiers"
    )


def test_multiline_wrapped_unlinked_reference_is_extracted_as_one_entry():
    doc = (
        "## References\n\n"
        "- Hölzel BK, Carmody J, Vangel M, et al. (2011). Mindfulness practice leads to\n"
        "  increases in regional brain gray matter density. Psychiatry Research:\n"
        "  Neuroimaging, 191(1), 36-43.\n"
    )
    refs = extract_references(doc)
    assert len(refs) == 1
    assert refs[0].kind == "text"
    assert refs[0].line == 3
    assert refs[0].year == 2011
    assert refs[0].title == ("Mindfulness practice leads to increases in regional brain gray matter density")


def test_soft_wrapped_prose_quote_and_numbered_marker_on_multiline_ref():
    scheme = "https://"
    doc = (
        'Drinkers live longer: "Coffee consumption has been associated\n'
        'with decreased mortality in previous studies"[1].\n\n'
        "## References\n\n"
        "[1] Kim Y, Je Y, Giovannucci E (2019). Coffee consumption and all-cause and\n"
        "    cause-specific mortality: a meta-analysis by potential modifiers.\n"
        f"    {scheme}doi.org/10.1007/s10654-019-00524-3\n"
    )
    refs = extract_references(doc)
    assert len(refs) == 1
    assert refs[0].quotes == [
        "Coffee consumption has been associated with decreased mortality in previous studies"
    ]


def test_right_hand_same_sentence_year_and_quote_are_captured_without_bleeding():
    doc = (
        'As Vaswani et al. (arXiv:1706.03762, 2017) showed, "The dominant sequence '
        'transduction models are based on complex recurrent or convolutional neural networks". '
        "Unrelated sentence after period.\n"
    )
    (ref,) = extract_references(doc)
    assert ref.kind == "arxiv"
    assert ref.id == "1706.03762"
    assert ref.year == 2017
    assert ref.quotes == [
        "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks"
    ]


def test_apa_author_year_parenthetical_and_narrative_quotes_link_to_references():
    http = FakeHttp({"id_list=1706.03762&max_results=1": "arxiv_1706_03762.xml"})
    doc = (
        'Earlier models struggled because "The dominant sequence transduction models are based on '
        'complex recurrent or convolutional neural networks in an encoder-decoder configuration" '
        "(Vaswani et al., 2017).\n"
        'However, Vaswani et al. (2017) never wrote "quantum annealing replaces attention".\n\n'
        "## References\n\n"
        "- Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). Attention Is All You Need. "
        "arXiv:1706.03762\n"
    )
    (f,) = check_text(doc, Lookup(http=http, workers=1), web=False, search=False)
    assert f.status == "warning"
    expected_quote = (
        "The dominant sequence transduction models are based on complex recurrent or "
        "convolutional neural networks in an encoder-decoder configuration"
    )
    assert f.quotes == [(expected_quote, True), ("quantum annealing replaces attention", False)]


def test_ambiguous_same_author_same_year_does_not_guess():
    scheme = "https://"
    doc = (
        'Someone claimed "ambiguous quote" (Smith, 2020) and "unambiguous quote" (Lee, 2021).\n\n'
        "## References\n\n"
        f"- Smith, J. (2020). First paper. {scheme}doi.org/10.1007/s10654-019-00524-3\n"
        f"- Smith, A. (2020). Second paper. {scheme}doi.org/10.1039/d0fo00787k\n"
        f"- Lee, K. (2021). Unique author paper. {scheme}doi.org/10.1056/NEJMsa1211128\n"
    )
    refs = extract_references(doc)
    assert [r.quotes for r in refs] == [[], [], ["unambiguous quote"]]
