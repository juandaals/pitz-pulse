"""SlackNotifier: `chat.postMessage` over httpx2, offline (Spec 06d, X1). Never raises."""

import json
import logging

import httpx2

from pitz_pulse.slack import SlackNotifier


def _notifier(handler):
    client = httpx2.Client(
        transport=httpx2.MockTransport(handler), base_url="https://slack.com/api"
    )
    return SlackNotifier("xoxb-test-token", client=client)


def test_reply_posts_the_expected_body_and_bearer_auth():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    _notifier(handler).reply("C1", "123.456", "hola", "Ev1")

    assert len(seen) == 1
    request = seen[0]
    assert request.url.path.endswith("/chat.postMessage")
    assert request.headers["authorization"] == "Bearer xoxb-test-token"
    assert json.loads(request.content) == {"channel": "C1", "thread_ts": "123.456", "text": "hola"}


def test_success_logs_nothing(caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")

    def handler(request):
        return httpx2.Response(200, json={"ok": True})

    _notifier(handler).reply("C1", "123.456", "hola", "Ev2")

    assert not [r for r in caplog.records if r.getMessage() == "slack_reply_failed"]


def test_ok_false_is_logged_with_event_id_and_never_raises(caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")

    def handler(request):
        return httpx2.Response(200, json={"ok": False, "error": "channel_not_found"})

    _notifier(handler).reply("C1", "123.456", "hola", "Ev3")  # must not raise

    events = [r for r in caplog.records if r.getMessage() == "slack_reply_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev3"


def test_http_429_is_logged_and_never_raises(caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")

    def handler(request):
        return httpx2.Response(429, json={"ok": False, "error": "rate_limited"})

    _notifier(handler).reply("C1", "123.456", "hola", "Ev4")

    events = [r for r in caplog.records if r.getMessage() == "slack_reply_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev4"


def test_transport_failure_is_logged_and_never_raises(caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")

    def handler(request):
        raise httpx2.ConnectError("SENTINEL-CONNECT-ERROR", request=request)

    _notifier(handler).reply("C1", "123.456", "hola", "Ev5")  # must not raise

    events = [r for r in caplog.records if r.getMessage() == "slack_reply_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev5"
    assert "SENTINEL-CONNECT-ERROR" not in str(events[0].fields)
