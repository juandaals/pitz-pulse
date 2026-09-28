"""POST /slack/events: mounting, signature verification and event filters (Spec 06d).

Happy-path classification and the failure table live in `test_slack_events.py` (kept under the
300-line file limit); shared helpers live in `slack_support.py`.
"""

import json
import logging
import time

from api_support import HEADERS, make_client
from fakes import FakeAdapter
from slack_support import RecordingNotifier, message_event, sign, signed_post, slack_client

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
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    response = signed_post(client, {"type": "url_verification", "challenge": "abc123"})
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc123"}


def test_no_api_key_header_is_needed(tmp_path):
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    response = signed_post(client, {"type": "url_verification", "challenge": "y"})
    assert response.status_code == 200  # sent with no X-API-Key header at all


def test_invalid_signature_is_401(tmp_path):
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = {
        "X-Slack-Request-Timestamp": str(int(time.time())),
        "X-Slack-Signature": "v0=" + "0" * 64,
        "Content-Type": "application/json",
    }
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 401


def test_missing_timestamp_is_401(tmp_path):
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = {"X-Slack-Signature": "v0=" + "0" * 64, "Content-Type": "application/json"}
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 401


def test_expired_timestamp_is_401(tmp_path):
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    old_ts = int(time.time()) - 400
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    response = client.post(
        "/slack/events",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": str(old_ts),
            "X-Slack-Signature": sign(old_ts, body),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401


def test_non_ascii_signature_header_is_401_not_500(tmp_path):
    """A Latin-1-only byte string in the header (never valid ASCII) must not crash the verifier
    (`hmac.compare_digest` raises `TypeError` on non-ASCII `str` operands)."""
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = {
        "X-Slack-Request-Timestamp": str(int(time.time())),
        "X-Slack-Signature": ("v0=" + "\xf1" * 64).encode("latin-1"),
        "Content-Type": "application/json",
    }
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 401


def test_absurd_timestamp_header_is_401_not_500(tmp_path):
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    body = json.dumps({"type": "url_verification", "challenge": "x"}).encode()
    headers = {
        "X-Slack-Request-Timestamp": "9" * 400,
        "X-Slack-Signature": "v0=" + "0" * 64,
        "Content-Type": "application/json",
    }
    response = client.post("/slack/events", content=body, headers=headers)
    assert response.status_code == 401


def test_chunked_body_over_the_cap_is_413_before_verification(tmp_path):
    """No `Content-Length` header (a generator body forces a streamed/chunked transfer over the
    ASGI transport): the app-wide `Content-Length`-based cap does not see this, so the route
    itself must enforce the cap while reading `request.stream()`, before verifying the signature
    (an obviously-wrong signature is used here to prove the cap wins first)."""
    client, _ = slack_client(tmp_path, FakeAdapter([]))

    def body_stream():
        yield b"x" * 40000
        yield b"y" * 40000

    response = client.post(
        "/slack/events",
        content=body_stream(),
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=" + "0" * 64,
        },
    )
    assert response.status_code == 413
    assert response.json()["error"] == "payload_too_large"


# -- filters ----------------------------------------------------------------------------------


def test_bot_message_is_ignored(tmp_path):
    notifier = RecordingNotifier()
    client, _ = slack_client(tmp_path, FakeAdapter([]), notifier=notifier)
    payload = message_event("Ev-bot", "C1", "111.1", "hola", bot_id="B1")
    response = signed_post(client, payload)
    assert response.status_code == 200
    assert notifier.replies == []
    assert client.get("/solicitudes/slack-Ev-bot", headers=HEADERS).status_code == 404


def test_message_edit_subtype_is_ignored(tmp_path):
    notifier = RecordingNotifier()
    client, _ = slack_client(tmp_path, FakeAdapter([]), notifier=notifier)
    payload = message_event("Ev-edit", "C1", "112.1", "hola editado", subtype="message_changed")
    response = signed_post(client, payload)
    assert response.status_code == 200
    assert notifier.replies == []
    assert client.get("/solicitudes/slack-Ev-edit", headers=HEADERS).status_code == 404


def test_thread_reply_is_ignored(tmp_path):
    notifier = RecordingNotifier()
    client, _ = slack_client(tmp_path, FakeAdapter([]), notifier=notifier)
    payload = message_event("Ev-thread", "C1", "113.1", "hola", thread_ts="100.0")
    response = signed_post(client, payload)
    assert response.status_code == 200
    assert notifier.replies == []
    assert client.get("/solicitudes/slack-Ev-thread", headers=HEADERS).status_code == 404
