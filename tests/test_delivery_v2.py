"""Slice 2: v2 batch delivery state machine (deliver_batch).

Covers: exact Rich/HTML requests; message_id capture; the narrow
rich_unsupported -> one HTML fallback; no fallback for generic 4xx/ok=false;
ambiguous outcomes leave durable in_flight and block resend; write-ahead
ordering; resume without duplication; per-item renderer durability; legacy v1
cutover; missing credentials; fail-closed validation; secret absence.

All env values are synthetic and no real network is used.
"""

from __future__ import annotations

import json
import os

import pytest

from pipeline.delivery import telegram
from pipeline.notifications import (
    NotificationBatch,
    render_html,
    render_rich,
)
from tests.fixtures.telegram.build_helpers import (
    load_fixture,
    product_item,
    summary_item,
    synthetic_batch,
)

API = "https://api.telegram.org"


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _env(monkeypatch, *, renderer=None, token="tok", chat="chat"):
    if renderer is None:
        monkeypatch.delenv("TELEGRAM_RENDERER", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_RENDERER", renderer)
    if token is None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    if chat is None:
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_CHAT_ID", chat)


def _install_post(monkeypatch, script, calls=None):
    """Record every post call and serve scripted responses (list, single or
    callable producing the next response/exception).  Pass an existing
    ``calls`` list to keep one recorder across re-installs."""
    if calls is None:
        calls = []

    def post(url, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if callable(script):
            item = script()
        else:
            item = script.pop(0) if isinstance(script, list) else script
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(telegram.requests, "post", post)
    return calls


def _batch(n_products=1, run_id="run-v2", mode="sunday", summary=False) -> NotificationBatch:
    products = [product_item(load_fixture()["products"][i]) for i in range(n_products)]
    if summary:
        return NotificationBatch.build(
            mode=mode,
            run_id=run_id,
            summary=summary_item(load_fixture()["summary"]),
            products=products,
        )
    return NotificationBatch.build(mode=mode, run_id=run_id, products=products)


def _state(state_dir, state_id):
    with open(os.path.join(state_dir, f"{state_id}.json"), encoding="utf-8") as f:
        return json.load(f)


def _expected_fragment(batch, index, renderer):
    return (render_rich if renderer == "rich" else render_html)(batch.items[index])


def _failing_writes(monkeypatch, *, fail_count=None, fail_condition=None):
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


# ── Exact requests ────────────────────────────────────────────────────────


def test_rich_request_exact_url_body_and_timeout(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 11}}))

    report = telegram.deliver_batch(batch, "rich-1", str(tmp_path))

    assert report["delivered"] is True and report["status"] == "success"
    assert len(calls) == 1
    fragment = _expected_fragment(batch, 0, "rich")
    assert calls[0]["url"] == f"{API}/bottok/sendRichMessage"
    assert calls[0]["json"] == {"chat_id": "chat", **fragment["payload"]}
    assert calls[0]["timeout"] == telegram.REQUEST_TIMEOUT


def test_html_request_exact_url_body_and_timeout_default_renderer(monkeypatch, tmp_path):
    _env(monkeypatch)  # no TELEGRAM_RENDERER -> safe default html
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 22}}))

    report = telegram.deliver_batch(batch, "html-1", str(tmp_path))

    assert report["delivered"] is True and report["status"] == "success"
    assert len(calls) == 1
    fragment = _expected_fragment(batch, 0, "html")
    assert calls[0]["url"] == f"{API}/bottok/sendMessage"
    assert calls[0]["json"] == {"chat_id": "chat", **fragment["payload"]}
    assert set(calls[0]["json"]) == {"chat_id", "text", "parse_mode", "link_preview_options"}
    assert calls[0]["timeout"] == telegram.REQUEST_TIMEOUT


def test_renderer_argument_overrides_env(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))

    telegram.deliver_batch(batch, "arg-1", str(tmp_path), renderer="html")

    assert calls[0]["url"].endswith("/sendMessage")


def test_invalid_renderer_fails_closed_without_network_or_file(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="garbage")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))

    report = telegram.deliver_batch(batch, "bad-ren", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "renderer_invalid"
    assert report["errors"] == ["renderer_invalid"]
    assert calls == []
    assert list(os.listdir(tmp_path)) == []


def test_batch_sends_items_in_deterministic_order(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = synthetic_batch(run_id="run-order", mode="sunday")  # summary + 3 products
    calls = _install_post(
        monkeypatch,
        [Response(200, {"ok": True, "result": {"message_id": i}}) for i in range(4)],
    )

    report = telegram.deliver_batch(batch, "order-1", str(tmp_path))

    assert report["items_total"] == 4 and report["items_sent"] == 4
    assert len(calls) == 4
    for index, (item, call) in enumerate(zip(batch.items, calls)):
        fragment = _expected_fragment(batch, index, "rich")
        assert call["url"].endswith("/sendRichMessage")
        assert call["json"]["rich_message"] == fragment["payload"]["rich_message"]
    state = _state(str(tmp_path), "order-1")
    assert [item["order"] for item in state["items"]] == [0, 1, 2, 3]
    assert [item["message_id"] for item in state["items"]] == [0, 1, 2, 3]


# ── Success and message_id ────────────────────────────────────────────────


def test_rich_accepted_persists_message_id_sent_and_hash(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 99}}))

    telegram.deliver_batch(batch, "mid-1", str(tmp_path))

    state = _state(str(tmp_path), "mid-1")
    assert state["schema_version"] == 2
    assert state["run_id"] == batch.run_id
    assert state["batch_id"] == batch.batch_id
    assert state["audit_text"] == batch.audit_text
    assert state["renderer"] == "rich"
    item = state["items"][0]
    assert item["item_id"] == batch.items[0].item_id
    assert item["kind"] == "product"
    assert item["product"] == batch.items[0].product
    assert item["order"] == 0
    assert item["audit_text"] == batch.items[0].audit_text
    assert item["renderer"] == "rich"
    assert item["status"] == "sent"
    assert item["in_flight"] is False
    assert item["sent_at"] is not None
    assert item["message_id"] == 99
    assert item["fallback_attempted"] is False
    assert item["last_error_kind"] is None
    assert item["payload_hash"] == telegram._payload_hash(
        _expected_fragment(batch, 0, "rich")
    )


# ── Fallback: narrow rich rejection -> one HTML attempt ───────────────────


def test_narrow_rich_rejection_falls_back_to_html_once(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(
        monkeypatch,
        [
            Response(200, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse rich message"}),
            Response(200, {"ok": True, "result": {"message_id": 7}}),
        ],
    )

    report = telegram.deliver_batch(batch, "fb-1", str(tmp_path))

    assert report["delivered"] is True and report["status"] == "success"
    assert report["fallback_used"] is True
    assert len(calls) == 2
    assert calls[0]["url"].endswith("/sendRichMessage")
    assert calls[1]["url"].endswith("/sendMessage")
    state = _state(str(tmp_path), "fb-1")
    item = state["items"][0]
    assert item["fallback_attempted"] is True
    assert item["renderer"] == "html"
    assert item["status"] == "sent"
    assert item["message_id"] == 7
    assert item["payload_hash"] == telegram._payload_hash(
        _expected_fragment(batch, 0, "html")
    )


def test_rich_rejection_http_4xx_falls_back(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(
        monkeypatch,
        [
            Response(400, {"ok": False, "description": "Bad Request: can't parse rich message"}),
            Response(200, {"ok": True, "result": {"message_id": 8}}),
        ],
    )

    report = telegram.deliver_batch(batch, "fb-2", str(tmp_path))

    assert report["delivered"] is True and report["fallback_used"] is True
    assert len(calls) == 2
    assert calls[1]["url"].endswith("/sendMessage")


def test_rich_rejection_description_never_persisted_logged_or_reported(monkeypatch, tmp_path, caplog):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    description = "Bad Request: can't parse rich message"
    calls = _install_post(
        monkeypatch,
        [
            Response(200, {"ok": False, "error_code": 400, "description": description}),
            Response(200, {"ok": True, "result": {"message_id": 3}}),
        ],
    )

    report = telegram.deliver_batch(batch, "fb-3", str(tmp_path))

    assert report["delivered"] is True
    raw_state = open(os.path.join(str(tmp_path), "fb-3.json"), encoding="utf-8").read()
    assert description not in raw_state
    assert description not in caplog.text
    assert description not in json.dumps(report)


def test_generic_4xx_no_fallback(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(400, {"ok": False, "description": "Bad Request: chat not found"}))

    report = telegram.deliver_batch(batch, "no4xx", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "error"
    assert report["errors"] == ["http_error"]
    assert len(calls) == 1  # rich only, never html
    assert calls[0]["url"].endswith("/sendRichMessage")
    state = _state(str(tmp_path), "no4xx")
    item = state["items"][0]
    assert item["status"] == "rejected"
    assert item["in_flight"] is False
    assert item["fallback_attempted"] is False
    assert item["renderer"] == "rich"
    assert item["last_error_kind"] == "http_error"


def test_ok_false_with_non_matching_description_no_fallback(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(
        monkeypatch,
        Response(200, {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}),
    )

    report = telegram.deliver_batch(batch, "no-ok", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "error"
    assert report["errors"] == ["api_not_ok"]
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/sendRichMessage")


def test_ok_false_generic_does_not_fallback_even_without_description(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": False}))

    report = telegram.deliver_batch(batch, "no-ok2", str(tmp_path))

    assert report["delivered"] is False and report["errors"] == ["api_not_ok"]
    assert len(calls) == 1


# ── Ambiguous outcomes: durable in_flight, block resend ───────────────────


@pytest.mark.parametrize(
    "maker",
    [
        lambda: telegram.requests.Timeout("timeout"),
        lambda: telegram.requests.ConnectionError("con"),
        lambda: Response(500, {"ok": False}),
        lambda: Response(503, {"ok": False}),
        lambda: Response(200, ValueError("não é JSON")),
        lambda: Response(200, ["not", "a", "dict"]),
        lambda: Response(200, {"ok": True, "result": {}}),
        lambda: Response(200, {"ok": True, "result": {"message_id": True}}),
        lambda: Response(200, {"ok": True, "result": {"message_id": "42"}}),
    ],
)
def test_ambiguous_outcomes_leave_durable_in_flight_no_fallback_and_rerun_refuses(
    monkeypatch, tmp_path, maker
):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    state_id = "amb-v2"
    item = maker()
    if isinstance(item, Exception):
        def fail(*args, **kwargs):
            raise item
    else:
        def fail(*args, **kwargs):
            return item
    calls = _install_post(monkeypatch, fail)

    report = telegram.deliver_batch(batch, state_id, str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_ambiguous"
    assert report["errors"] == ["state_ambiguous"]
    assert len(calls) == 1  # never a fallback after ambiguity
    assert calls[0]["url"].endswith("/sendRichMessage")
    durable = _state(str(tmp_path), state_id)
    assert durable["items"][0]["in_flight"] is True
    assert durable["items"][0]["status"] != "sent"
    assert durable["status"] == "state_ambiguous"

    second = telegram.deliver_batch(batch, state_id, str(tmp_path))
    assert second["status"] == "state_ambiguous"
    assert len(calls) == 1  # rerun refuses to resend


# ── Write-ahead ordering and persistence failures ─────────────────────────


def test_fresh_state_persist_failure_blocks_network(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))
    _failing_writes(monkeypatch, fail_count=1)  # first write = fresh state

    report = telegram.deliver_batch(batch, "w-fresh", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_persist_failed"
    assert report["errors"] == ["state_persist_failed"]
    assert calls == []


def test_inflight_persist_failure_blocks_network_before_send(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))
    _failing_writes(monkeypatch, fail_count=2)  # second write = item0 in_flight

    report = telegram.deliver_batch(batch, "w-inflight", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_persist_failed"
    assert calls == []


def test_fallback_preflight_persist_failure_blocks_html_network(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(
        monkeypatch,
        Response(200, {"ok": False, "error_code": 400, "description": "Bad Request: can't parse rich message"}),
    )
    # writes: 1 fresh, 2 item0 in_flight, 3 clear(rejected), 4 fallback preflight -> fails
    _failing_writes(monkeypatch, fail_count=4)

    report = telegram.deliver_batch(batch, "w-preflight", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_persist_failed"
    assert len(calls) == 1  # rich rejection observed, html never called
    assert calls[0]["url"].endswith("/sendRichMessage")
    durable = _state(str(tmp_path), "w-preflight")
    assert durable["items"][0]["fallback_attempted"] is False
    assert durable["items"][0]["renderer"] == "rich"
    assert durable["items"][0]["in_flight"] is False
    assert durable["items"][0]["status"] == "rejected"


def test_post_accept_persist_failure_is_ambiguous_and_rerun_refuses(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 5}}))
    # Fail the first write that records a sent item (post-accept persist).
    _failing_writes(
        monkeypatch,
        fail_condition=lambda data: any(
            isinstance(item, dict) and item.get("status") == "sent"
            for item in data.get("items", [])
        ),
    )

    report = telegram.deliver_batch(batch, "w-sent", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_ambiguous"
    assert len(calls) == 1  # item0 accepted; then the sent write failed
    durable = _state(str(tmp_path), "w-sent")
    assert durable["items"][0]["in_flight"] is True
    assert durable["items"][0]["status"] != "sent"

    second = telegram.deliver_batch(batch, "w-sent", str(tmp_path))
    assert second["status"] == "state_ambiguous"
    assert len(calls) == 1


# ── Resume and idempotency ────────────────────────────────────────────────


def test_resume_between_messages_without_duplication(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(
        monkeypatch,
        [
            Response(200, {"ok": True, "result": {"message_id": 1}}),
            Response(400, {"ok": False, "description": "Bad Request: chat not found"}),
        ],
    )

    first = telegram.deliver_batch(batch, "res-1", str(tmp_path))
    assert first["delivered"] is False
    assert first["status"] == "partial"
    assert first["items_sent"] == 1
    assert len(calls) == 2

    # Resume: only the unsent item is retried.
    _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 2}}), calls=calls)
    second = telegram.deliver_batch(batch, "res-1", str(tmp_path))
    assert second["delivered"] is True and second["status"] == "success"
    assert second["items_sent"] == 2
    state = _state(str(tmp_path), "res-1")
    assert [item["message_id"] for item in state["items"]] == [1, 2]
    # item0 was sent exactly once across both invocations.
    item0_calls = [
        c for c in calls
        if c["json"].get("rich_message", {}).get("html", "").startswith("<h1>mochila personalizada</h1>")
    ]
    assert len(item0_calls) == 1


def test_sent_never_resent(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(
        monkeypatch,
        [Response(200, {"ok": True, "result": {"message_id": i}}) for i in range(2)],
    )

    first = telegram.deliver_batch(batch, "sent-1", str(tmp_path))
    assert first["delivered"] is True
    assert len(calls) == 2

    second = telegram.deliver_batch(batch, "sent-1", str(tmp_path))
    assert second["delivered"] is True and second["status"] == "success"
    assert len(calls) == 2  # SENT items are never sent again


def test_resume_follows_durable_item_renderer_not_env(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    calls = _install_post(
        monkeypatch,
        [
            Response(200, {"ok": True, "result": {"message_id": 1}}),
            Response(400, {"ok": False, "description": "Bad Request: chat not found"}),
        ],
    )
    telegram.deliver_batch(batch, "ren-1", str(tmp_path))
    assert len(calls) == 2

    # The operator rolls back to html; the durable per-item renderer still wins.
    _env(monkeypatch, renderer="html")
    _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 2}}), calls=calls)
    second = telegram.deliver_batch(batch, "ren-1", str(tmp_path))

    assert second["delivered"] is True
    assert len(calls) == 3
    assert calls[2]["url"].endswith("/sendRichMessage")  # durable renderer, not env
    state = _state(str(tmp_path), "ren-1")
    assert state["items"][1]["renderer"] == "rich"
    assert state["items"][1]["payload_hash"] == telegram._payload_hash(
        _expected_fragment(batch, 1, "rich")
    )


# ── Fail-closed state validation ──────────────────────────────────────────


def test_corrupt_state_file_fails_closed_and_is_not_overwritten(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))
    state_path = os.path.join(str(tmp_path), "corrupt-1.json")
    with open(state_path, "w", encoding="utf-8") as f:
        f.write("{not json")

    report = telegram.deliver_batch(batch, "corrupt-1", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_corrupt"
    assert report["errors"] == ["state_corrupt"]
    assert calls == []
    assert open(state_path, encoding="utf-8").read() == "{not json"


def test_tampered_v2_state_fails_closed(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))

    telegram.deliver_batch(batch, "tamper-1", str(tmp_path))
    state_path = os.path.join(str(tmp_path), "tamper-1.json")
    state = _state(str(tmp_path), "tamper-1")
    state["items"][0]["payload_hash"] = "deadbeef"
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f)

    report = telegram.deliver_batch(batch, "tamper-1", str(tmp_path))
    assert report["delivered"] is False
    assert report["status"] == "state_corrupt"
    assert len(calls) == 1  # the original send only; nothing new for the tampered state


# ── Legacy v1 cutover ─────────────────────────────────────────────────────


def test_legacy_v1_success_is_historical_noop(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))
    legacy = {"text": "digest", "hash": "x", "status": "success", "chunks": []}
    state_path = os.path.join(str(tmp_path), "legacy-1.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(legacy, f)
    before = open(state_path, encoding="utf-8").read()

    report = telegram.deliver_batch(batch, "legacy-1", str(tmp_path))

    assert report["delivered"] is True
    assert report["status"] == "success"
    assert report["items_total"] == 0 and report["items_sent"] == 0
    assert calls == []
    # Historical completed: never rewritten, never auto-migrated.
    assert open(state_path, encoding="utf-8").read() == before


def test_legacy_v1_partial_fails_closed_and_is_never_rewritten(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))
    for status in ("partial", "error", "missing_config", "pending"):
        state_path = os.path.join(str(tmp_path), f"legacy-{status}.json")
        legacy = {"text": "digest", "hash": "x", "status": status, "chunks": []}
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)
        before = open(state_path, encoding="utf-8").read()

        report = telegram.deliver_batch(batch, f"legacy-{status}", str(tmp_path))

        assert report["delivered"] is False
        assert report["status"] == "state_legacy_migration"
        assert report["errors"] == ["state_legacy_migration"]
        assert calls == []
        assert open(state_path, encoding="utf-8").read() == before


# ── Credentials and secrets ───────────────────────────────────────────────


def test_missing_credentials_zero_network_and_missing_config(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich", token=None, chat=None)
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))

    report = telegram.deliver_batch(batch, "nocfg-1", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "missing_config"
    assert report["errors"] == ["missing_config"]
    assert calls == []
    state = _state(str(tmp_path), "nocfg-1")
    assert state["status"] == "missing_config"
    assert state["schema_version"] == 2


def test_secrets_absent_from_state_log_and_report(monkeypatch, tmp_path, caplog):
    _env(monkeypatch, renderer="rich", token="bot-SUPERSECRET-token", chat="chat-SUPERSECRET-id")
    batch = _batch(n_products=1)
    _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 4}}))

    report = telegram.deliver_batch(batch, "sec-1", str(tmp_path))

    raw = open(os.path.join(str(tmp_path), "sec-1.json"), encoding="utf-8").read()
    for needle in (
        "bot-SUPERSECRET-token",
        "chat-SUPERSECRET-id",
        "api.telegram.org",
        "/bot",
        "rich_message",
        "parse_mode",
        "<h1>",
    ):
        assert needle not in raw, f"segredo/payload no estado: {needle!r}"
    for needle in ("bot-SUPERSECRET-token", "chat-SUPERSECRET-id", "api.telegram.org"):
        assert needle not in caplog.text, f"segredo em logs: {needle!r}"
        assert needle not in json.dumps(report), f"segredo no report: {needle!r}"


# ── Report shape and callback ─────────────────────────────────────────────


def test_invalid_state_id_fails_closed_without_file_or_network(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=1)
    calls = _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))

    report = telegram.deliver_batch(batch, "../escape", str(tmp_path))

    assert report["delivered"] is False
    assert report["status"] == "state_invalid"
    assert report["errors"] == ["state_invalid"]
    assert calls == []
    assert list(os.listdir(tmp_path)) == []  # nenhum ficheiro criado


def test_report_shape_is_sanitized(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="html")
    batch = _batch(n_products=1)
    _install_post(monkeypatch, Response(200, {"ok": True, "result": {"message_id": 1}}))

    report = telegram.deliver_batch(batch, "shape-1", str(tmp_path))

    assert set(report) == {
        "delivered", "status", "schema_version", "items_total", "items_sent",
        "errors", "renderer", "fallback_used",
    }
    assert report["schema_version"] == 2
    assert report["renderer"] == "html"
    assert report["fallback_used"] is False
    assert report["items_total"] == 1 and report["items_sent"] == 1


def test_on_item_sent_callback_fires_after_each_persisted_item(monkeypatch, tmp_path):
    _env(monkeypatch, renderer="rich")
    batch = _batch(n_products=2)
    _install_post(monkeypatch, [Response(200, {"ok": True, "result": {"message_id": i}}) for i in range(2)])
    seen = []
    report = telegram.deliver_batch(
        batch, "cb-1", str(tmp_path), on_item_sent=lambda item_state: seen.append(item_state["item_id"])
    )
    assert report["delivered"] is True
    assert seen == [batch.items[0].item_id, batch.items[1].item_id]
    state = _state(str(tmp_path), "cb-1")
    assert all(item["status"] == "sent" for item in state["items"])
