"""Shared helpers for the Slack route test files (Spec 06c/06d): signing, event payloads, and a
recording notifier double, so `test_slack_routes.py` and `test_slack_events.py` do not duplicate
them (and both stay under the file size limit)."""

import hashlib
import hmac
import json
import time

from api_support import make_client

SECRET = "test-signing-secret"
TOKEN = "xoxb-test-token"


class RecordingNotifier:
    def __init__(self):
        self.replies: list[dict] = []

    def reply(self, channel, thread_ts, text, event_id):
        self.replies.append(
            {"channel": channel, "thread_ts": thread_ts, "text": text, "event_id": event_id}
        )


def slack_client(tmp_path, adapter, *, notifier=None, **env):
    return make_client(
        tmp_path,
        adapter,
        slack_notifier=notifier if notifier is not None else RecordingNotifier(),
        SLACK_SIGNING_SECRET=SECRET,
        SLACK_BOT_TOKEN=TOKEN,
        **env,
    )


def sign(ts: int, body: bytes, secret: str = SECRET) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def signed_post(client, payload, *, secret: str = SECRET, ts: int | None = None):
    body = json.dumps(payload).encode()
    timestamp = ts if ts is not None else int(time.time())
    headers = {
        "X-Slack-Request-Timestamp": str(timestamp),
        "X-Slack-Signature": sign(timestamp, body, secret),
        "Content-Type": "application/json",
    }
    return client.post("/slack/events", content=body, headers=headers)


def message_event(event_id: str, channel: str, ts: str, text: str, **overrides) -> dict:
    event = {"type": "message", "channel": channel, "ts": ts, "text": text, **overrides}
    return {"type": "event_callback", "event_id": event_id, "event": event}
