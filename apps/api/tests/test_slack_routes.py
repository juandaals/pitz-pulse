"""POST /slack/events end to end: signature, filters, background classify, reply (Spec 06d)."""

import hashlib
import hmac
import json
import logging
import time

import httpx2
from api_support import HEADERS, make_client
from fakes import FakeAdapter, make_call
from service_support import unavailable

from pitz_pulse.slack import SlackNotifier

SECRET = "test-signing-secret"
TOKEN = "xoxb-test-token"


class RecordingNotifier:
    def __init__(self):
        self.replies: list[dict] = []

    def reply(self, channel, thread_ts, text, event_id):
        self.replies.append(
            {"channel": channel, "thread_ts": thread_ts, "text": text, "event_id": event_id}
        )


def _slack_client(tmp_path, adapter, *, notifier=None, **env):
    return make_client(
        tmp_path,
        adapter,
        slack_notifier=notifier if notifier is not None else RecordingNotifier(),
        SLACK_SIGNING_SECRET=SECRET,
        SLACK_BOT_TOKEN=TOKEN,
        **env,
    )


def _sign(ts: int, body: bytes, secret: str = SECRET) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def _signed_post(client, payload: dict, *, secret: str = SECRET, ts: int | None = None):
    body = json.dumps(payload).encode()
    timestamp = ts if ts is not None else int(time.time())
    headers = {
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": _sign(timestamp, body, secret),
        "Content-Type": "application/json",
    }
    return client.post("/slack/events", content=body, headers=headers)


def _message_event(event_id: str, channel: str, ts: str, text: str, **overrides) -> dict:
    event = {"type": "message", "channel": channel, "ts": ts, "text": text, **overrides}
    return {"type": "event_callback", "event_id": event_id, "event": event}


# -- mounting -------------------------------------------------------------------------------


def test_endpoint_absent_when_secrets_are_empty(tmp_path):
    client, _ = make_client(tmp_path, FakeAdapter([]))
    response = client.post("/slack/events", json={"type": "url_verification", "challenge": "x"})
    assert response.status_code == 404


def test_slack_disabled_is_logged_at_startup_when_secrets_are_empty(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    make_client(tmp_path, FakeAdapter([]))
    assert any(r.getMessage() == "slack_disabled" for r in caplog.records)


# -- signature verification -------------------------------------------------------------------


def test_url_verification_returns_the_challenge(tmp_path):
    client, _ = _slack_client(tmp_path, FakeAdapter([]))
    response = _signed_post(client, {"type": "url_verification", "challenge": "abc123"})
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc123"}


def test_no_api_key_header_is_needed(tmp_path):
    client, _ = _slack_client(tmp_path, FakeAdapter([]))
    response = _signed_post(client, {"type": "url_verification", "challenge": "y"})
    assert response.status_code == 200  # sent with no X-API-Key header at all


def test_invalid_signature_is_401(tmp_path):
    client, _ = _slack_client(tmp_path, FakeAdapter([]))
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = {
        "X-Slack-Request-Timestamp": str(int(time.time())),
        "X-Slack-Signature": "v0=" + "0" * 64,
        "Content-Type": "application/json",
    }
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 401


def test_missing_timestamp_is_401(tmp_path):
    client, _ = _slack_client(tmp_path, FakeAdapter([]))
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = {"X-Slack-Signature": "v0=" + "0" * 64, "Content-Type": "application/json"}
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 401


def test_expired_timestamp_is_401(tmp_path):
    client, _ = _slack_client(tmp_path, FakeAdapter([]))
    old_ts = int(time.time()) - 400
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    response = client.post(
        "/slack/events",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": str(old_ts),
            "X-Slack-Signature": _sign(old_ts, body),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


# -- filters ----------------------------------------------------------------------------------


def test_bot_message_is_ignored(tmp_path):
    notifier = RecordingNotifier()
    client, _ = _slack_client(tmp_path, FakeAdapter([]), notifier=notifier)
    payload = _message_event("Ev-bot", "C1", "111.1", "hola", bot_id="B1")
    response = _signed_post(client, payload)
    assert response.status_code == 200
    assert notifier.replies == []
    assert client.get("/solicitudes/slack-Ev-bot", headers=HEADERS).status_code == 404


def test_message_edit_subtype_is_ignored(tmp_path):
    notifier = RecordingNotifier()
    client, _ = _slack_client(tmp_path, FakeAdapter([]), notifier=notifier)
    payload = _message_event("Ev-edit", "C1", "112.1", "hola editado", subtype="message_changed")
    response = _signed_post(client, payload)
    assert response.status_code == 200
    assert notifier.replies == []
    assert client.get("/solicitudes/slack-Ev-edit", headers=HEADERS).status_code == 404


def test_thread_reply_is_ignored(tmp_path):
    notifier = RecordingNotifier()
    client, _ = _slack_client(tmp_path, FakeAdapter([]), notifier=notifier)
    payload = _message_event("Ev-thread", "C1", "113.1", "hola", thread_ts="100.0")
    response = _signed_post(client, payload)
    assert response.status_code == 200
    assert notifier.replies == []
    assert client.get("/solicitudes/slack-Ev-thread", headers=HEADERS).status_code == 404


# -- happy path ---------------------------------------------------------------------------------


def test_valid_message_replies_exactly_once_in_the_thread_and_persists_a_row(tmp_path):
    notifier = RecordingNotifier()
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter, notifier=notifier)
    payload = _message_event(
        "Ev-ok", "C1", "222.2", "No puedo acceder a mi cuenta de correo, es urgente"
    )
    response = _signed_post(client, payload)

    assert response.status_code == 200
    assert len(notifier.replies) == 1
    assert notifier.replies[0]["channel"] == "C1"
    assert notifier.replies[0]["thread_ts"] == "222.2"
    assert notifier.replies[0]["event_id"] == "Ev-ok"

    stored = client.get("/solicitudes/slack-Ev-ok", headers=HEADERS).json()
    assert stored["status"] == "classified"
    assert stored["categoria"] == "bug"


def test_slack_retry_of_the_same_event_id_does_not_reply_twice(tmp_path):
    notifier = RecordingNotifier()
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter, notifier=notifier)
    payload = _message_event("Ev-retry", "C1", "333.3", "Necesito ayuda urgente con mi acceso")

    _signed_post(client, payload)
    _signed_post(client, payload)  # Slack resends the identical event

    assert len(notifier.replies) == 1
    assert len(adapter.calls) == 1


def test_source_area_is_looked_up_from_slack_channel_areas(tmp_path):
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter, SLACK_CHANNEL_AREAS="C1=Comercial MX")
    payload = _message_event("Ev-area", "C1", "444.4", "No puedo acceder a mi cuenta, es urgente")
    _signed_post(client, payload)
    stored = client.get("/solicitudes/slack-Ev-area", headers=HEADERS).json()
    assert stored["source_area"] == "Comercial MX"


def test_unknown_channel_has_a_null_source_area(tmp_path):
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter, SLACK_CHANNEL_AREAS="C-known=Soporte")
    payload = _message_event(
        "Ev-unknown-ch", "C-unknown", "555.5", "No puedo acceder a mi cuenta, es urgente"
    )
    _signed_post(client, payload)
    stored = client.get("/solicitudes/slack-Ev-unknown-ch", headers=HEADERS).json()
    assert stored["source_area"] is None


def test_slack_mailto_markup_is_masked_before_the_llm(tmp_path):
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter)
    text = (
        "Mi correo es <mailto:persona@empresa.com|persona@empresa.com> "
        "y necesito ayuda urgente con el acceso"
    )
    payload = _message_event("Ev-mask", "C1", "666.6", text)
    _signed_post(client, payload)

    assert len(adapter.calls) == 1
    user_prompt = adapter.calls[0]["user"]
    assert "persona@empresa.com" not in user_prompt
    assert "[EMAIL]" in user_prompt


# -- failure table --------------------------------------------------------------------------


def test_empty_message_gets_a_limit_reply_and_is_never_classified(tmp_path):
    notifier = RecordingNotifier()
    adapter = FakeAdapter([])
    client, _ = _slack_client(tmp_path, adapter, notifier=notifier)
    payload = _message_event("Ev-empty", "C1", "777.7", "")
    response = _signed_post(client, payload)

    assert response.status_code == 200
    assert len(notifier.replies) == 1
    assert "caracteres" in notifier.replies[0]["text"]
    assert adapter.calls == []
    assert client.get("/solicitudes/slack-Ev-empty", headers=HEADERS).status_code == 404


def test_classification_failure_replies_could_not_classify_and_logs(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    notifier = RecordingNotifier()
    adapter = FakeAdapter([unavailable()])
    client, _ = _slack_client(tmp_path, adapter, notifier=notifier)
    payload = _message_event("Ev-fail", "C1", "888.8", "No puedo acceder a mi cuenta, es urgente")
    _signed_post(client, payload)

    assert len(notifier.replies) == 1
    assert "clasificar" in notifier.replies[0]["text"]
    events = [r for r in caplog.records if r.getMessage() == "slack_classification_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev-fail"


def test_id_conflict_same_event_id_different_text_is_logged_without_a_second_reply(
    tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    notifier = RecordingNotifier()
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter, notifier=notifier)

    first = _message_event(
        "Ev-conflict", "C1", "999.9", "Mensaje original bastante largo para clasificar bien"
    )
    second = _message_event("Ev-conflict", "C1", "999.99", "Mensaje completamente distinto aqui")
    _signed_post(client, first)
    _signed_post(client, second)

    assert len(notifier.replies) == 1  # only the first
    events = [r for r in caplog.records if r.getMessage() == "slack_id_conflict"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev-conflict"


def test_notifier_failure_is_logged_end_to_end(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")

    def handler(request):
        return httpx2.Response(200, json={"ok": False, "error": "channel_not_found"})

    notifier = SlackNotifier(TOKEN, client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    adapter = FakeAdapter([make_call()])
    client, _ = _slack_client(tmp_path, adapter, notifier=notifier)
    payload = _message_event(
        "Ev-notify-fail", "C1", "111.11", "No puedo acceder a mi cuenta, es urgente"
    )
    _signed_post(client, payload)

    events = [r for r in caplog.records if r.getMessage() == "slack_reply_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev-notify-fail"
