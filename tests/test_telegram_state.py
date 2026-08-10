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
    responses = [Response(200, {"ok": True}), Response(400, {"ok": False})]
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


def test_unexpected_transport_exception_is_ambiguous(monkeypatch, tmp_path, caplog):
    """Uma exceção inesperada no transporte é um outcome ambíguo: o Telegram
    pode ter aceite o chunk, por isso o rerun recusa reenviar."""
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

    assert report["status"] == "state_ambiguous"
    assert report["errors"] == ["state_ambiguous"]
    assert "SECRET" not in caplog.text
    assert "api.telegram.org" not in caplog.text
    assert "SECRET" not in json.dumps(_state(str(tmp_path), "run10"))
    # O marcador in_flight ficou durável: o rerun recusa.
    durable = _state(str(tmp_path), "run10")
    assert durable["chunks"][0]["in_flight"] is True
    assert durable["chunks"][0]["sent"] is False


@pytest.mark.parametrize(
    "failure",
    [
        ("timeout", lambda: telegram.requests.Timeout("timeout")),
        ("network_error", lambda: telegram.requests.ConnectionError("con")),
        ("http_5xx", lambda: Response(500, {"ok": False})),
        ("json_invalid", lambda: Response(200, ValueError("não é JSON"))),
    ],
)
def test_ambiguous_outcomes_leave_in_flight_durable_and_rerun_refuses(
    monkeypatch, tmp_path, failure
):
    """Timeout/network/HTTP>=500/JSON inválido no 200 são ambíguos: o marcador
    in_flight fica durável, o report é state_ambiguous e o rerun recusa."""
    kind, maker = failure
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    state_id = f"amb-{kind}"
    if isinstance(maker(), Exception):
        def fail(*args, **kwargs):
            raise maker()
    else:
        response = maker()
        def fail(*args, **kwargs):
            return response
    post = Mock(side_effect=fail)
    monkeypatch.setattr(telegram.requests, "post", post)

    report = telegram.deliver("digest", state_id=state_id, state_dir=state_dir)

    assert report["delivered"] is False
    assert report["status"] == "state_ambiguous"
    assert report["errors"] == ["state_ambiguous"]
    durable = _state(state_dir, state_id)
    assert durable["chunks"][0]["sent"] is False
    assert durable["chunks"][0]["in_flight"] is True
    assert durable["status"] == "state_ambiguous"

    # Rerun: nunca reenvia um chunk em voo sem resolução operacional.
    second = telegram.deliver("digest", state_id=state_id, state_dir=state_dir)
    assert second["status"] == "state_ambiguous"
    assert post.call_count == 1


@pytest.mark.parametrize(
    "response",
    [Response(400, {"ok": False}), Response(200, {"ok": False})],
)
def test_definite_rejections_clear_in_flight_and_permit_retry(
    monkeypatch, tmp_path, response
):
    """HTTP 4xx e JSON válido com ok != true são rejeições definitivas: o
    in_flight é limpo duravelmente e um rerun pode tentar de novo."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    state_id = "def-rej"
    post = Mock(return_value=response)
    monkeypatch.setattr(telegram.requests, "post", post)

    report = telegram.deliver("digest", state_id=state_id, state_dir=state_dir)

    assert report["delivered"] is False
    assert report["status"] == "error"
    assert report["errors"] == ["http_error" if response.status_code == 400 else "api_not_ok"]
    durable = _state(state_dir, state_id)
    assert durable["chunks"][0]["sent"] is False
    assert durable["chunks"][0]["in_flight"] is False

    # A limpeza durável permite o retry: um rerun tenta de novo.
    post.side_effect = lambda *a, **k: Response(200, {"ok": True})
    second = telegram.deliver("digest", state_id=state_id, state_dir=state_dir)
    assert second["delivered"] is True
    assert second["status"] == "success"
    assert post.call_count == 2


@pytest.mark.parametrize(
    "state_id",
    ["../escape", "a/b", "a\\b", "a b", "..", ".", "a.b", "", "a;b"],
)
def test_invalid_state_id_fails_closed_without_file_or_network(
    monkeypatch, tmp_path, state_id
):
    """state_id com separadores/traversal falha fechado: sem ficheiro, sem
    chamada de rede, report sanitizado state_invalid."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock()
    monkeypatch.setattr(telegram.requests, "post", post)

    report = telegram.deliver("digest", state_id=state_id, state_dir=str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_invalid"
    assert report["errors"] == ["state_invalid"]
    post.assert_not_called()
    assert list(os.listdir(tmp_path)) == []  # nenhum ficheiro criado


@pytest.mark.parametrize(
    "state_id",
    ["a" * 64, "activation-final-20260810", "run-2026-08-10", "abc_123", "A1_b2"],
)
def test_valid_state_ids_are_accepted(monkeypatch, tmp_path, state_id):
    """SHA-256 IDs e ids documentados (activation-final-20260810) são válidos."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)

    report = telegram.deliver("digest", state_id=state_id, state_dir=str(tmp_path))

    assert report["delivered"] is True
    assert post.call_count == 1
    assert os.path.exists(os.path.join(str(tmp_path), f"{state_id}.json"))


def test_send_compat_wrapper_keeps_stdout_fallback(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    post = Mock()
    monkeypatch.setattr(telegram.requests, "post", post)
    assert telegram.send("digest completo") is False
    assert capsys.readouterr().out == "digest completo\n"
    post.assert_not_called()


# ── In-flight write-ahead: stateful delivery nunca envia sem persistir ──────
#
# Protocolo: persistir estado pending fresco antes de qualquer envio; antes de
# cada chunk persistir um marcador in_flight; após sucesso persistir sent e
# limpar in_flight; em falha de rede persistir a limpeza ANTES de permitir
# retry. Qualquer falha de persistência fecha-falha: state_persist_failed se
# nada foi enviado, state_ambiguous se o resultado do envio é indeterminado.


def _failing_writes(monkeypatch, *, fail_count=None, fail_condition=None):
    """Faz atomic_write_json falhar (OSError) em writes seletivos.

    ``fail_count`` falha o enésimo write da chamada; ``fail_condition``
    falha qualquer write cujo payload satisfaça o predicado. Usa a função
    real para os writes que não devem falhar.
    """
    real = telegram.atomic_write_json
    counter = {"n": 0}

    def wrapper(path, data):
        counter["n"] += 1
        if fail_count is not None and counter["n"] == fail_count:
            raise OSError("disco cheio (teste)")
        if fail_condition is not None and fail_condition(data):
            raise OSError("disco cheio (teste)")
        return real(path, data)

    monkeypatch.setattr(telegram, "atomic_write_json", wrapper)
    return counter


def test_fresh_state_persist_failure_blocks_network(monkeypatch, tmp_path):
    """Estado pending fresco tem de ser durável antes de qualquer envio."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    _failing_writes(monkeypatch, fail_count=1)  # a primeira escrita é o estado fresco

    report = telegram.deliver("hello", state_id="w1", state_dir=str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_persist_failed"
    assert report["errors"] == ["state_persist_failed"]
    post.assert_not_called()  # nenhuma chamada de rede antes da persistência inicial


def test_inflight_persist_failure_blocks_network_before_send(monkeypatch, tmp_path):
    """O marcador in_flight tem de ser durável antes do envio do chunk."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"  # 2 chunks
    responses = [Response(200, {"ok": True}), Response(400, {"ok": False})]
    post = Mock(side_effect=lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(telegram.requests, "post", post)

    first = telegram.deliver(text, state_id="w2", state_dir=state_dir)
    assert first["status"] == "partial"
    assert first["chunks_sent"] == 1
    posts_before = post.call_count

    # Resume: a primeira escrita da nova tentativa é o marcador in_flight.
    _failing_writes(monkeypatch, fail_count=1)
    second = telegram.deliver(text, state_id="w2", state_dir=state_dir)
    assert second["delivered"] is False
    assert second["status"] == "state_persist_failed"
    assert second["errors"] == ["state_persist_failed"]
    assert post.call_count == posts_before  # nada foi enviado sem persistir in_flight


def test_post_accept_persist_failure_is_ambiguous_and_rerun_refuses(monkeypatch, tmp_path):
    """Falha ao persistir sent após o Telegram aceitar → ambíguo; rerun recusa."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"  # 2 chunks
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)
    # Falha apenas a escrita que marca o chunk como sent (após aceite remoto).
    _failing_writes(
        monkeypatch,
        fail_condition=lambda data: any(c.get("sent") is True for c in data.get("chunks", [])),
    )

    report = telegram.deliver(text, state_id="w3", state_dir=state_dir)
    assert report["delivered"] is False
    assert report["status"] == "state_ambiguous"
    assert report["errors"] == ["state_ambiguous"]
    assert post.call_count == 1  # o chunk 0 foi aceite; depois a escrita falhou
    # O ficheiro durável continua a mostrar o chunk em voo, não-sent.
    durable = _state(state_dir, "w3")
    assert durable["chunks"][0]["in_flight"] is True
    assert durable["chunks"][0]["sent"] is False

    # Rerun: nunca reenvia um chunk em voo sem resolução operacional.
    second = telegram.deliver(text, state_id="w3", state_dir=state_dir)
    assert second["delivered"] is False
    assert second["status"] == "state_ambiguous"
    assert second["errors"] == ["state_ambiguous"]
    assert post.call_count == 1


def test_network_failure_retries_only_after_durable_clear(monkeypatch, tmp_path):
    """Retry só é permitido depois de a limpeza do in_flight ser durável."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"
    responses = [Response(200, {"ok": True}), Response(400, {"ok": False})]
    post = Mock(side_effect=lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(telegram.requests, "post", post)

    first = telegram.deliver(text, state_id="w4", state_dir=state_dir)
    assert first["status"] == "partial"
    assert first["errors"] == ["http_error"]
    # O estado durável tem o marcador limpo para o chunk falhado.
    durable = _state(state_dir, "w4")
    failed_chunk = durable["chunks"][1]
    assert failed_chunk["sent"] is False and failed_chunk["in_flight"] is False

    # A limpeza durável torna o retry inequívoco.
    post.side_effect = lambda *a, **k: Response(200, {"ok": True})
    second = telegram.deliver(text, state_id="w4", state_dir=state_dir)
    assert second["delivered"] is True
    assert second["status"] == "success"
    assert post.call_count == 3  # 1 aceite + 1 falhou + 1 retry


def test_network_failure_with_unpersisted_clear_fails_closed_ambiguous(monkeypatch, tmp_path):
    """Falha de rede + limpeza não persistida → ambíguo; rerun recusa."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"
    responses = [Response(200, {"ok": True}), Response(400, {"ok": False})]
    post = Mock(side_effect=lambda *a, **k: responses.pop(0))
    monkeypatch.setattr(telegram.requests, "post", post)
    # Escritas: 1 fresco, 2 in_flight(chunk0), 3 sent(chunk0), 4 in_flight(chunk1),
    # 5 limpeza(chunk1) → falha a limpeza após a rejeição definitiva (400).
    _failing_writes(monkeypatch, fail_count=5)

    report = telegram.deliver(text, state_id="w5", state_dir=state_dir)
    assert report["delivered"] is False
    assert report["status"] == "state_ambiguous"
    assert report["errors"] == ["state_ambiguous"]
    assert post.call_count == 2
    durable = _state(state_dir, "w5")
    assert durable["chunks"][1]["sent"] is False
    assert durable["chunks"][1]["in_flight"] is True  # fica em voo

    second = telegram.deliver(text, state_id="w5", state_dir=state_dir)
    assert second["status"] == "state_ambiguous"
    assert post.call_count == 2  # recusou reenviar


def test_happy_resume_stays_idempotent(monkeypatch, tmp_path):
    """Resume feliz: estado sucesso nunca reenvia e mantém o formato."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)

    first = telegram.deliver(text, state_id="w6", state_dir=state_dir)
    assert first["delivered"] is True and first["status"] == "success"
    posts = post.call_count
    assert posts == 2

    second = telegram.deliver(text, state_id="w6", state_dir=state_dir)
    assert second["delivered"] is True and second["status"] == "success"
    assert post.call_count == posts  # idempotente
    durable = _state(state_dir, "w6")
    assert all(c["sent"] is True and c["in_flight"] is False for c in durable["chunks"])
    assert durable["status"] == "success"


def test_legacy_state_without_in_flight_field_is_compatible(monkeypatch, tmp_path):
    """Ficheiros de estado pré-protocolo (sem in_flight) continuam válidos."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    state_dir = str(tmp_path)
    text = ("linha\n" * 900) + "fim"
    chunks = telegram._chunks(text)
    legacy = {
        "text": text,
        "hash": telegram.sha256_hex(text),
        "chunks": [
            {"index": 0, "text": chunks[0], "sent": True, "sent_at": "2026-01-01T00:00:00+00:00"},
            {"index": 1, "text": chunks[1], "sent": False, "sent_at": None},
        ],
        "status": "partial",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    with open(os.path.join(state_dir, "w8.json"), "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    post = Mock(return_value=Response(200, {"ok": True}))
    monkeypatch.setattr(telegram.requests, "post", post)

    report = telegram.deliver(text, state_id="w8", state_dir=state_dir)
    assert report["delivered"] is True and report["status"] == "success"
    assert post.call_count == 1  # só o chunk por enviar
    durable = _state(state_dir, "w8")
    assert durable["chunks"][0]["sent"] is True
    assert durable["chunks"][1]["sent"] is True
    assert durable["chunks"][1].get("in_flight") is False


def test_state_never_contains_token_chat_id_or_url(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot-SUPERSECRET-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-SUPERSECRET-id")
    monkeypatch.setattr(telegram.requests, "post", Mock(return_value=Response(200, {"ok": True})))
    state_dir = str(tmp_path)

    telegram.deliver("oi", state_id="w7", state_dir=state_dir)
    raw = open(os.path.join(state_dir, "w7.json"), encoding="utf-8").read()
    assert "bot-SUPERSECRET-token" not in raw
    assert "chat-SUPERSECRET-id" not in raw
    assert "api.telegram.org" not in raw
    assert "/bot" not in raw
