import json

from claimcite import cli
from claimcite.sources import Lookup


def test_cli_exit_code_and_formats(tmp_path, fake_http, monkeypatch, capsys):
    doc = tmp_path / "doc.md"
    doc.write_text(
        "Ghost A (2021). Never written. https://doi.org/10.9999/does-not-exist-claimcite\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=fake_http))

    assert cli.main(["check", str(doc), "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["summary"] == {"ok": 0, "warning": 0, "error": 1, "unchecked": 0}
    assert data["findings"][0]["id"] == "10.9999/does-not-exist-claimcite"

    assert cli.main(["check", str(doc), "--lang", "zh"]) == 1
    out = capsys.readouterr().out
    assert "编号不存在" in out


def test_cli_ok_document_exits_zero(tmp_path, fake_http, monkeypatch, capsys):
    doc = tmp_path / "doc.md"
    doc.write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=fake_http))
    assert cli.main(["check", str(doc)]) == 0
    assert "Randomized controlled trial" in capsys.readouterr().out


def test_no_web_skips_web_pages(tmp_path, monkeypatch, capsys):
    def no_network(url):
        raise AssertionError(f"unexpected request: {url}")

    doc = tmp_path / "doc.md"
    doc.write_text("WHO (2020). Fact sheet. https://www.who.int/fact-sheet\n", encoding="utf-8")
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=no_network))
    assert cli.main(["check", str(doc), "--no-web", "--format", "json"]) == 0
    (f,) = json.loads(capsys.readouterr().out)["findings"]
    assert f["status"] == "unchecked" and f["web"] is None
