"""Slack signature verification and reply delivery (Spec 06d, X1).

`SlackVerifier` implements Slack's request-signing scheme: the signature replaces the API key
on `/slack/events` (D5's async exception: it needs the exact raw body bytes before any parsing).
`SlackNotifier` posts the classification reply in the message's thread over `httpx2` (already a
transitive dependency of `anthropic`, now declared directly); it never raises into the caller —
every failure (network, HTTP status, `ok:false`) is logged with the event id and dropped, never
the message text, token or reply text.
"""

import hashlib
import hmac
import logging
import re
from collections.abc import Mapping

import httpx2

from pitz_pulse.logs import log_event

logger = logging.getLogger("pitz_pulse.slack")

_MAX_SKEW_S = 300
_SLACK_API_BASE_URL = "https://slack.com/api"
# Bounds the timestamp to a plain, short non-negative integer: no sign, no huge digit runs that
# would overflow `now - ts`'s float conversion (12 digits reaches year ~33658, ample headroom).
_TIMESTAMP_PATTERN = re.compile(r"^\d{1,12}$")


class InvalidSignature(Exception):
    pass


class SlackVerifier:
    """HMAC-SHA256 over `v0:{timestamp}:{raw_body}`, per Slack's signing-secrets scheme."""

    def __init__(self, signing_secret: str):
        self._secret = signing_secret.encode()

    def verify(self, headers: Mapping[str, str], raw_body: bytes, now: float) -> None:
        timestamp = headers.get("X-Slack-Request-Timestamp")
        signature = headers.get("X-Slack-Signature")
        if timestamp is None or signature is None:
            raise InvalidSignature("missing timestamp or signature header")
        if not _TIMESTAMP_PATTERN.fullmatch(timestamp):
            raise InvalidSignature("timestamp is not a plain integer of a sane length")
        if not signature.isascii():
            raise InvalidSignature("signature is not ASCII")
        ts = int(timestamp)
        if abs(now - ts) > _MAX_SKEW_S:
            raise InvalidSignature("timestamp outside the allowed window")
        base = b"v0:" + str(ts).encode() + b":" + raw_body
        expected = "v0=" + hmac.new(self._secret, base, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected.encode(), signature.encode()):
            raise InvalidSignature("signature mismatch")


class SlackNotifier:
    """Posts one `chat.postMessage` reply; never raises (a failed reply is only logged)."""

    def __init__(self, bot_token: str, client: httpx2.Client | None = None):
        self._token = bot_token
        self._client = client or httpx2.Client(base_url=_SLACK_API_BASE_URL, timeout=10.0)

    def reply(self, channel: str, thread_ts: str, text: str, event_id: str) -> None:
        try:
            response = self._client.post(
                "/chat.postMessage",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"channel": channel, "thread_ts": thread_ts, "text": text},
            )
            body = response.json()
        except Exception as exc:  # transport error, timeout, non-JSON body: never reach the task
            log_event(logger, "slack_reply_failed", event_id=event_id, exc_type=type(exc).__name__)
            return
        if response.status_code != 200 or not (isinstance(body, dict) and body.get("ok")):
            log_event(
                logger,
                "slack_reply_failed",
                event_id=event_id,
                status=response.status_code,
                slack_error=body.get("error") if isinstance(body, dict) else None,
            )
