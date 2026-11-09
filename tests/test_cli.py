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


def test_cli_checks_several_files_in_one_run(tmp_path, fake_http, monkeypatch, capsys):
    """pre-commit and the GitHub Action pass every changed file at once."""
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    good.write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")
    bad.write_text(
        "Ghost (2021). Never. https://doi.org/10.9999/does-not-exist-claimcite\n", encoding="utf-8"
    )
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=fake_http))

    assert cli.main(["check", str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert out.index(f"## {good}") < out.index("Randomized controlled trial") < out.index(f"## {bad}")

    assert cli.main(["check", str(good), str(bad), "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert [f["path"] for f in data["files"]] == [str(good), str(bad)]
    assert data["summary"]["error"] == 1 and data["files"][0]["summary"]["error"] == 0

    assert cli.main(["check", str(good)]) == 0  # one file: output unchanged, no file header
    assert "## " not in capsys.readouterr().out.split("# Citation check")[0]


def test_a_directory_checks_the_documents_in_it(tmp_path, fake_http, monkeypatch, capsys):
    """`claimcite check docs/` used to stop with IsADirectoryError."""
    (tmp_path / "b.md").write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("ASPREE, PMID: 30221596\n", encoding="utf-8")
    (tmp_path / "cover.png").write_bytes(b"\x89PNG")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "x.md").write_text("PMID: 1\n", encoding="utf-8")
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=fake_http))

    assert cli.main(["check", str(tmp_path), "--format", "json"]) == 0
    paths = [f["path"] for f in json.loads(capsys.readouterr().out)["files"]]
    assert paths == [str(tmp_path / "b.md"), str(tmp_path / "sub" / "a.txt")]


def test_a_missing_file_is_a_short_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_lookup", lambda no_cache=False: Lookup(http=lambda url: ""))
    assert cli.main(["check", str(tmp_path / "nope.md")]) == 2
    err = capsys.readouterr().err
    assert "nope.md" in err and "Traceback" not in err

    assert cli.main(["check", str(tmp_path / "nope.md"), "--lang", "zh"]) == 2
    assert "读不了" in capsys.readouterr().err
