"""Send-only Telegram delivery tests; every request is mocked."""

from __future__ import annotations

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


def test_missing_config_prints_complete_digest_without_request(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post = Mock()
    monkeypatch.setattr(telegram.requests, "post", post)

    assert telegram.send("digest completo") is False
    assert capsys.readouterr().out == "digest completo\n"
    post.assert_not_called()


def test_success_payload_is_plain_send_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    post = Mock(return_value=Response(200, {"ok": True, "result": {}}))
    monkeypatch.setattr(telegram.requests, "post", post)

    assert telegram.send("hello") is True
    post.assert_called_once()
    url, kwargs = post.call_args.args[0], post.call_args.kwargs
    assert url.endswith("/bottoken-for-test/sendMessage")
    assert kwargs["timeout"] == (5, 15)
    assert kwargs["json"] == {
        "chat_id": "chat-1",
        "text": "hello",
        "disable_web_page_preview": True,
    }
    assert "parse_mode" not in kwargs["json"]
    assert "reply_markup" not in kwargs["json"]
    assert "callback" not in kwargs["json"]


def test_long_digest_splits_at_newlines_and_stays_under_limit(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    text = ("line\n" * 1000) + "fim"

    assert telegram.send(text) is True
    chunks = [call.kwargs["json"]["text"] for call in post.call_args_list]
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "\n".join(chunks) == text


@pytest.mark.parametrize(
    "response",
    [
        Response(500, {"ok": False}),
        Response(200, ValueError("invalid json")),
        Response(200, {"ok": False}),
    ],
)
def test_non_success_responses_fallback_to_stdout(monkeypatch, capsys, response):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setattr(telegram.requests, "post", Mock(return_value=response))

    assert telegram.send("fallback digest") is False
    assert capsys.readouterr().out == "fallback digest\n"


def test_timeout_fallback_is_sanitized_and_never_leaks_token(monkeypatch, capsys, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-1")
    monkeypatch.setattr(telegram.requests, "post", Mock(side_effect=telegram.requests.Timeout("secret-token")))

    assert telegram.send("fallback digest") is False
    assert capsys.readouterr().out == "fallback digest\n"
    assert "secret-token" not in caplog.text
