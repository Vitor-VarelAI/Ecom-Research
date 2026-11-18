from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import pipeline.arbitrage.validation as validation
from pipeline.arbitrage.goto_urls import count_goto_links, resolve_goto_links
from pipeline.arbitrage.validation import SERP_TOOL, validate_offer

from tests.arbitrage.test_validation import _FakeClient, _fixture, _now

GOTO = "https://google.com/goto?url=CAESabc&gsessionid=xyz"
PAGE = "https://www.example.pt/clinica/"


def _serp(url: str) -> dict[str, Any]:
    return {"results": [{"items": [{"type": "organic", "domain": None, "url": url}]}]}


def _resolver(tmp_path: Path) -> Path:
    path = tmp_path / "goto_resolver.py"
    path.write_text("# stand-in; the fake runner never executes it\n")
    return path


def _fake_run(stdout: str):
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[-1] == "--json"
        assert GOTO in kwargs["input"]
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    return run, calls


def test_counts_goto_links_anywhere_in_the_content() -> None:
    assert count_goto_links(_serp(GOTO)) == 1
    assert count_goto_links({"md": f"[x]({GOTO}) and https://www.google.pt/goto?url=Z"}) == 2
    assert count_goto_links(_serp(PAGE)) == 0


def test_content_without_goto_links_skips_the_resolver(tmp_path: Path) -> None:
    run, calls = _fake_run("")

    content, outcome = resolve_goto_links(_serp(PAGE), resolver=_resolver(tmp_path), run=run)

    assert content == _serp(PAGE)
    assert outcome == {"status": "none"}
    assert calls == []


def test_replaces_goto_links_with_the_resolver_output(tmp_path: Path) -> None:
    run, calls = _fake_run(json.dumps(_serp(PAGE)))

    content, outcome = resolve_goto_links(_serp(GOTO), resolver=_resolver(tmp_path), run=run)

    assert content == _serp(PAGE)
    assert outcome == {"status": "resolved", "resolved": 1, "unresolved": 0}
    assert len(calls) == 1


def test_reports_links_the_resolver_left(tmp_path: Path) -> None:
    run, _ = _fake_run(json.dumps(_serp(GOTO)))

    content, outcome = resolve_goto_links(_serp(GOTO), resolver=_resolver(tmp_path), run=run)

    assert content == _serp(GOTO)
    assert outcome == {"status": "partial", "resolved": 0, "unresolved": 1}


def test_missing_resolver_keeps_the_raw_content(tmp_path: Path) -> None:
    content, outcome = resolve_goto_links(_serp(GOTO), resolver=tmp_path / "absent.py")

    assert content == _serp(GOTO)
    assert outcome == {"status": "unavailable", "unresolved": 1}


@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, "resolver"),
        subprocess.TimeoutExpired("resolver", 120),
        OSError("no python"),
    ],
)
def test_resolver_failure_keeps_the_raw_content(tmp_path: Path, error: Exception) -> None:
    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise error

    content, outcome = resolve_goto_links(_serp(GOTO), resolver=_resolver(tmp_path), run=run)

    assert content == _serp(GOTO)
    assert outcome == {"status": "failed", "unresolved": 1}


def test_unreadable_resolver_output_keeps_the_raw_content(tmp_path: Path) -> None:
    run, _ = _fake_run("not json")

    content, outcome = resolve_goto_links(_serp(GOTO), resolver=_resolver(tmp_path), run=run)

    assert content == _serp(GOTO)
    assert outcome["status"] == "failed"


def test_validation_saves_serp_results_with_page_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _fixture(monkeypatch)
    monkeypatch.setattr(
        validation,
        "resolve_goto_links",
        lambda content: (_serp(PAGE), {"status": "resolved", "resolved": 1, "unresolved": 0}),
    )

    artifact = validate_offer(
        "offer-approved",
        config_path=tmp_path / "offers.json",
        data_dir=tmp_path / "data",
        artifact_dir=tmp_path / "validations",
        project_id="project-123",
        client=_FakeClient(responses={SERP_TOOL: _serp(GOTO)}),
        as_of=date(2026, 9, 7),
        now=_now,
    )

    serp = artifact["tools"][SERP_TOOL]
    assert serp["structured_content"] == _serp(PAGE)
    assert serp["goto_links"] == {"status": "resolved", "resolved": 1, "unresolved": 0}
    assert GOTO not in validation.validation_to_json(artifact)
