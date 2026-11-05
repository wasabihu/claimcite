"""Web-page citation checks (issue #1). Every test stays offline.

Issue requirements: fetch the page respecting robots.txt, report dead links and
whether each quoted text appears in the page body, and never raise an *error*
for a web source (pages change and some sites block bots).
"""

from __future__ import annotations

import json
from pathlib import Path

from claimcite.check import check_reference, check_text
from claimcite.models import Reference
from claimcite.net import NotFound
from claimcite.report import to_json, to_markdown
from claimcite.sources import Lookup
from claimcite.web import check_page

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.who.int/fact-sheet"
PAGE = (FIXTURES / "web_page.html").read_text(encoding="utf-8")
ALLOW = (FIXTURES / "web_robots_allow.txt").read_text(encoding="utf-8")
DENY = (FIXTURES / "web_robots_deny.txt").read_text(encoding="utf-8")
GOOD_QUOTE = "Vaccines are safe and effective for most people"


class WebHttp:
    """Answers robots.txt and the page itself from fixtures; no real network."""

    def __init__(self, robots: str = ALLOW, page: str = PAGE, page_error=None):
        self.robots = robots
        self.page = page
        self.page_error = page_error
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        if self.page_error is not None:
            raise self.page_error(url)
        return self.page


def web_ref(quotes=(GOOD_QUOTE,)) -> Reference:
    return Reference(kind="url", id=URL, line=1, quotes=list(quotes))


def doc(quote: str = GOOD_QUOTE) -> str:
    return f"WHO (2020). Fact sheet：「{quote}」. {URL}\n"


def findings_for(quote: str = GOOD_QUOTE, http=None):
    http = http or WebHttp()
    return check_text(doc(quote), Lookup(http=http))


def test_quoted_text_in_page_body_is_found():
    result = check_page(web_ref(), WebHttp())
    assert result.state == "alive"
    assert result.quotes == [(GOOD_QUOTE, True)]
    assert findings_for()[0].status == "ok"


def test_html_markup_and_whitespace_are_normalised():
    # the fixture has three spaces between words and the quote lives outside tags
    assert check_page(web_ref(), WebHttp()).quotes[0][1] is True


def test_script_and_noscript_text_is_ignored():
    result = check_page(web_ref(("THIS TEXT LIVES ONLY IN A SCRIPT TAG",)), WebHttp())
    assert result.quotes[0][1] is False
    assert findings_for("THIS TEXT LIVES ONLY IN A SCRIPT TAG")[0].status == "warning"


def test_missing_quote_is_warning_never_error():
    f = findings_for("this sentence is nowhere on the page")[0]
    assert f.status == "warning"
    assert f.web.state == "alive"
    assert "页面正文中未找到引文" in to_markdown([f], lang="zh")


def test_dead_link_is_warning_never_error():
    http = WebHttp(page_error=NotFound)
    f = findings_for(http=http)[0]
    assert f.status == "warning"
    assert f.web.state == "dead"
    assert f.web.status_code == 404
    assert "网页打不开" in to_markdown([f], lang="zh")


def test_network_failure_stays_unchecked():
    http = WebHttp(page_error=lambda url: OSError("network down"))
    f = findings_for(http=http)[0]
    assert f.status == "unchecked"
    assert f.web.state == "failed"


def test_robots_disallow_skips_the_page_request():
    http = WebHttp(robots=DENY)
    f = findings_for(http=http)[0]
    assert f.status == "unchecked"
    assert f.web.state == "blocked"
    assert all(call.endswith("/robots.txt") for call in http.calls)


def test_robots_unreachable_does_not_block_the_check():
    http = WebHttp(robots="")
    assert findings_for(http=http)[0].status == "ok"


def test_alive_page_without_quotes_is_ok():
    http = WebHttp()
    f = check_reference(web_ref(()), Lookup(http=http))
    assert f.web.state == "alive"
    assert f.status == "ok"


def test_reports_expose_web_result_in_both_languages_and_json():
    f = findings_for()[0]
    assert "网页可访问" in to_markdown([f], lang="zh")
    assert "Web page is reachable" in to_markdown([f])
    data = json.loads(to_json([f]))
    assert data["findings"][0]["web"]["state"] == "alive"
