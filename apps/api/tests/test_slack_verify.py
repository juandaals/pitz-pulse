"""SlackVerifier: HMAC-SHA256 request signing (Spec 06d, X1)."""

import hashlib
import hmac

import pytest

from pitz_pulse.slack import InvalidSignature, SlackVerifier

SECRET = "shhh-signing-secret"
BODY = b'{"type":"event_callback","event_id":"Ev1"}'


def _sign(ts: int, body: bytes = BODY, secret: str = SECRET) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    now = 1_700_000_000
    headers = {"X-Slack-Request-Timestamp": str(now), "X-Slack-Signature": _sign(now)}
    SlackVerifier(SECRET).verify(headers, BODY, now)  # must not raise


def test_wrong_secret_is_invalid():
    now = 1_700_000_000
    headers = {
        "X-Slack-Request-Timestamp": str(now),
        "X-Slack-Signature": _sign(now, secret="other-secret"),
    }
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY, now)


def test_tampered_body_is_invalid():
    now = 1_700_000_000
    headers = {"X-Slack-Request-Timestamp": str(now), "X-Slack-Signature": _sign(now)}
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY + b"tampered", now)


def test_expired_timestamp_is_invalid():
    now = 1_700_000_000
    old = now - 301
    headers = {"X-Slack-Request-Timestamp": str(old), "X-Slack-Signature": _sign(old)}
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY, now)


def test_boundary_of_300_seconds_is_still_valid():
    now = 1_700_000_000
    old = now - 300
    headers = {"X-Slack-Request-Timestamp": str(old), "X-Slack-Signature": _sign(old)}
    SlackVerifier(SECRET).verify(headers, BODY, now)  # must not raise


def test_future_timestamp_beyond_the_window_is_invalid():
    now = 1_700_000_000
    future = now + 301
    headers = {"X-Slack-Request-Timestamp": str(future), "X-Slack-Signature": _sign(future)}
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY, now)


def test_missing_timestamp_header_is_invalid():
    headers = {"X-Slack-Signature": _sign(1_700_000_000)}
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY, 1_700_000_000)


def test_missing_signature_header_is_invalid():
    headers = {"X-Slack-Request-Timestamp": "1700000000"}
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY, 1_700_000_000)


def test_non_integer_timestamp_is_invalid():
    headers = {"X-Slack-Request-Timestamp": "soon", "X-Slack-Signature": "v0=" + "0" * 64}
    with pytest.raises(InvalidSignature):
        SlackVerifier(SECRET).verify(headers, BODY, 1_700_000_000)
