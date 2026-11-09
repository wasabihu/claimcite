"""A Chinese paraphrase in quotes cannot be found word for word in an English abstract or page.

Found on 2026-11-09 checking a Chinese psychology article: “走神的人更不快乐”[1] pointed at
Killingsworth & Gilbert (2010), "A wandering mind is an unhappy mind", and was reported as a
warning ("quoted text not found in the abstract"). The citation was right; the check could not apply.
"""

from test_web import URL, WebHttp, web_ref

from claimcite.check import check_text
from claimcite.report import to_json, to_markdown
from claimcite.sources import Lookup
from claimcite.text import quote_found
from claimcite.web import check_page

COFFEE = (
    "Kim Y 等 (2019). Coffee consumption and all-cause and cause-specific mortality: "
    "a meta-analysis by potential modifiers. Eur J Epidemiol：「{q}」. "
    "<https://doi.org/10.1007/s10654-019-00524-3>"
)


def test_chinese_quote_against_english_text_cannot_be_compared():
    assert (
        quote_found("喝咖啡的人死亡率更低", "Coffee consumption has been associated with decreased mortality")
        is None
    )


def test_chinese_quote_against_chinese_text_is_still_checked():
    assert quote_found("喝咖啡的人死亡率更低", "研究发现，喝咖啡的人死亡率更低。") is True
    assert quote_found("喝茶的人死亡率更低", "研究发现，喝咖啡的人死亡率更低。") is False


def test_english_quote_is_unchanged():
    assert (
        quote_found("decreased mortality", "Coffee consumption has been associated with decreased mortality")
        is True
    )
    assert (
        quote_found("cured everyone", "Coffee consumption has been associated with decreased mortality")
        is False
    )


def test_a_chinese_paraphrase_of_an_english_abstract_is_not_a_warning(fake_http):
    (f,) = check_text(COFFEE.format(q="喝咖啡的人死亡率更低"), Lookup(http=fake_http))
    assert f.status == "ok"
    assert f.quotes == [("喝咖啡的人死亡率更低", None)]
    md = to_markdown([f], "zh")
    assert "引文是中文，摘要不是中文，无法逐字核对：「喝咖啡的人死亡率更低」" in md
    assert "没有摘要" not in md
    assert "Quote is in Chinese and the abstract is not" in to_markdown([f], "en")
    assert '"found_in_abstract": null' in to_json([f])


def test_a_chinese_quote_of_an_english_page_is_not_a_warning():
    result = check_page(web_ref(("疫苗对大多数人是安全有效的",)), WebHttp())
    assert result.state == "alive"
    assert result.quotes == [("疫苗对大多数人是安全有效的", None)]
    (f,) = check_text(
        f"WHO (2020). Fact sheet：「疫苗对大多数人是安全有效的」. {URL}\n", Lookup(http=WebHttp())
    )
    assert f.status == "ok"
    md = to_markdown([f], "zh")
    assert "引文是中文，网页正文不是中文，无法逐字核对" in md
    assert "比如 PDF" not in md
