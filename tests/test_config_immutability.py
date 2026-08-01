from run import _consume_keywords


def test_sunday_keyword_read_does_not_mutate_config(monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    keywords = config / "keywords.txt"
    original = "# keep this\nmochila\n"
    keywords.write_text(original, encoding="utf-8")
    monkeypatch.setattr("run.REPO_ROOT", str(tmp_path))

    assert _consume_keywords() == ["mochila"]
    assert keywords.read_text(encoding="utf-8") == original
