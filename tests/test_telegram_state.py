"""Telegram delivery state: persistence, no-resend, resume, sanitization."""

from __future__ import annotations

import json
import os
from unittest.mock import Mock

import pytest

from pipeline.delivery import telegram


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _state(state_dir, state_id):
    with open(os.path.join(state_dir, f"{state_id}.json"), encoding="utf-8") as f:
        return json.load(f)


def test_success_state_persisted_and_never_resent(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    state_dir = str(tmp_path)

    report = telegram.deliver("hello", state_id="run1", state_dir=state_dir)
    assert report["delivered"] is True
    assert report["status"] == "success"
    assert post.call_count == 1

    state = _state(state_dir, "run1")
    assert state["text"] == "hello"
    assert state["chunks"][0]["sent"] is True
    assert state["status"] == "success"

    telegram.deliver("hello", state_id="run1", state_dir=state_dir)
    assert post.call_count == 1  # never re-sends delivered chunks


def test_partial_progress_resumes_unsent_chunks_only(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"  # > 4000 chars -> multiple chunks
    responses = [Response(200, {"ok": True}), Response(500, {"ok": False})]
    post = Mock(side_effect=lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(telegram.requests, "post", post)

    first = telegram.deliver(text, state_id="run2", state_dir=state_dir)
    assert first["status"] == "partial"
    assert first["chunks_sent"] == 1
    assert first["chunks_total"] > 1
    assert first["errors"] == ["http_error"]

    # Resume: only the unsent chunk is retried.
    post.side_effect = lambda *a, **k: Response(200, {"ok": True})
    second = telegram.deliver(text, state_id="run2", state_dir=state_dir)
    assert second["delivered"] is True
    assert second["status"] == "success"
    assert post.call_count == 3  # 2 in the first attempt + 1 resumed chunk

    sent = [c["text"] for c in _state(state_dir, "run2")["chunks"] if c["sent"]]
    assert "\n".join(sent) == text


def test_missing_config_persists_state_without_request(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post = Mock()
    monkeypatch.setattr(telegram.requests, "post", post)
    state_dir = str(tmp_path)

    report = telegram.deliver("digest", state_id="run3", state_dir=state_dir)
    assert report["delivered"] is False
    assert report["status"] == "missing_config"
    post.assert_not_called()
    assert _state(state_dir, "run3")["status"] == "missing_config"


def test_state_never_contains_token_or_token_url(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "supersecret-token-xyz")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(telegram.requests, "post", Mock(return_value=Response(200, {"ok": True})))
    state_dir = str(tmp_path)

    telegram.deliver("oi", state_id="run4", state_dir=state_dir)
    raw = open(os.path.join(state_dir, "run4.json"), encoding="utf-8").read()
    assert "supersecret-token-xyz" not in raw
    assert "/bottoken" not in raw


def test_on_chunk_sent_callback_fires_after_each_persisted_chunk(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(telegram.requests, "post", Mock(return_value=Response(200, {"ok": True})))
    seen = []
    telegram.deliver(
        "multi\n" * 900,
        state_id="run5",
        state_dir=str(tmp_path),
        on_chunk_sent=lambda chunk, report: seen.append(chunk["index"]),
    )
    assert len(seen) > 1
    assert seen == list(range(len(seen)))


def test_different_text_fails_closed_without_overwriting_state(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    state_dir = str(tmp_path)

    first = telegram.deliver("texto A", state_id="run6", state_dir=state_dir)
    assert first["delivered"] is True
    assert post.call_count == 1
    # Same state_id with different content is existing-corrupt: send nothing,
    # report a sanitized state_corrupt failure, and never overwrite the state.
    second = telegram.deliver("texto B", state_id="run6", state_dir=state_dir)
    assert second["delivered"] is False
    assert second["status"] == "state_corrupt"
    assert second["chunks_sent"] == 0 and second["errors"] == ["state_corrupt"]
    assert post.call_count == 1  # nothing new was sent
    state = _state(state_dir, "run6")
    assert state["text"] == "texto A"  # the suspect state was NOT overwritten
    assert state["chunks"][0]["sent"] is True


def test_corrupt_state_file_fails_closed_and_is_not_overwritten(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    state_dir = str(tmp_path)
    state_path = os.path.join(state_dir, "run7.json")
    with open(state_path, "w", encoding="utf-8") as f:
        f.write("{not json")

    report = telegram.deliver("digest", state_id="run7", state_dir=state_dir)
    assert report["delivered"] is False
    assert report["status"] == "state_corrupt"
    post.assert_not_called()
    # The unreadable suspect state is left exactly as it was.
    assert open(state_path, encoding="utf-8").read() == "{not json"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: state.update({"hash": "deadbeef"}),
        lambda state: state.update({"text": "other text"}),
        lambda state: state["chunks"].append(
            {"index": 1, "text": "extra", "sent": False, "sent_at": None}),
        lambda state: state["chunks"][0].update({"index": 5}),
        lambda state: state["chunks"][0].update({"text": "tampered"}),
        lambda state: state["chunks"][0].update({"sent": "yes"}),
        lambda state: state["chunks"][0].update({"sent": True, "sent_at": None}),
        lambda state: state["chunks"][0].update({"sent_at": 123}),
    ],
)
def test_tampered_state_fails_closed(monkeypatch, tmp_path, mutate):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    state_dir = str(tmp_path)

    telegram.deliver("texto A", state_id="run8", state_dir=state_dir)
    state_path = os.path.join(state_dir, "run8.json")
    state = _state(state_dir, "run8")
    mutate(state)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

    report = telegram.deliver("texto A", state_id="run8", state_dir=state_dir)
    assert report["delivered"] is False
    assert report["status"] == "state_corrupt"
    assert post.call_count == 1  # nothing new was sent for the tampered state


def test_corrupt_state_makes_caller_print_stdout_fallback(monkeypatch, tmp_path, capsys):
    """The run-level fallback prints the digest when delivery reports corrupt."""
    import run as run_module

    state_dir = str(tmp_path)
    state_path = os.path.join(state_dir, "run9.json")
    with open(state_path, "w", encoding="utf-8") as f:
        f.write('{"hash": "different"}')
    monkeypatch.setattr(run_module, "_digests_dir", lambda: state_dir)

    report = run_module._deliver_digest("digest completo", "run9", send=True)
    assert report["status"] == "state_corrupt"
    assert report["delivered"] is False
    assert capsys.readouterr().out == "digest completo\n"


def test_unexpected_transport_exception_is_sanitized(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.setattr(
        telegram.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("https://api.telegram.org/botSECRET/sendMessage")
        ),
    )

    report = telegram.deliver("digest", state_id="run10", state_dir=str(tmp_path))

    assert report["status"] == "error"
    assert report["errors"] == ["unexpected_error"]
    assert "SECRET" not in caplog.text
    assert "api.telegram.org" not in caplog.text
    assert "SECRET" not in json.dumps(_state(str(tmp_path), "run10"))


def test_send_compat_wrapper_keeps_stdout_fallback(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post = Mock()
    monkeypatch.setattr(telegram.requests, "post", post)
    assert telegram.send("digest completo") is False
    assert capsys.readouterr().out == "digest completo\n"
    post.assert_not_called()
