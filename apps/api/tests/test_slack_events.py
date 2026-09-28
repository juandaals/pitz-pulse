"""POST /slack/events: happy path, the failure table, payload-shape safety and mock marking
(Spec 06d). Mounting, signature verification and event filters live in `test_slack_routes.py`;
shared helpers live in `slack_support.py`.
"""

import logging

import httpx2
import pytest
from api_support import HEADERS
from fakes import FakeAdapter, make_call
from service_support import unavailable
from slack_support import RecordingNotifier, message_event, signed_post, slack_client

from pitz_pulse import slack_routes
from pitz_pulse.slack import SlackNotifier

# -- happy path ---------------------------------------------------------------------------------


def test_valid_message_replies_exactly_once_in_the_thread_and_persists_a_row(tmp_path):
    notifier = RecordingNotifier()
    adapter = FakeAdapter([make_call()])
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)
    payload = message_event(
        "Ev-ok", "C1", "222.2", "No puedo acceder a mi cuenta de correo, es urgente"
    )
    response = signed_post(client, payload)

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
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)
    payload = message_event("Ev-retry", "C1", "333.3", "Necesito ayuda urgente con mi acceso")

    signed_post(client, payload)
    signed_post(client, payload)  # Slack resends the identical event

    assert len(notifier.replies) == 1
    assert len(adapter.calls) == 1


def test_source_area_is_looked_up_from_slack_channel_areas(tmp_path):
    adapter = FakeAdapter([make_call()])
    client, _ = slack_client(tmp_path, adapter, SLACK_CHANNEL_AREAS="C1=Comercial MX")
    payload = message_event("Ev-area", "C1", "444.4", "No puedo acceder a mi cuenta, es urgente")
    signed_post(client, payload)
    stored = client.get("/solicitudes/slack-Ev-area", headers=HEADERS).json()
    assert stored["source_area"] == "Comercial MX"


def test_unknown_channel_has_a_null_source_area(tmp_path):
    adapter = FakeAdapter([make_call()])
    client, _ = slack_client(tmp_path, adapter, SLACK_CHANNEL_AREAS="C-known=Soporte")
    payload = message_event(
        "Ev-unknown-ch", "C-unknown", "555.5", "No puedo acceder a mi cuenta, es urgente"
    )
    signed_post(client, payload)
    stored = client.get("/solicitudes/slack-Ev-unknown-ch", headers=HEADERS).json()
    assert stored["source_area"] is None


def test_slack_mailto_markup_is_masked_before_the_llm(tmp_path):
    adapter = FakeAdapter([make_call()])
    client, _ = slack_client(tmp_path, adapter)
    text = (
        "Mi correo es <mailto:persona@empresa.com|persona@empresa.com> "
        "y necesito ayuda urgente con el acceso"
    )
    payload = message_event("Ev-mask", "C1", "666.6", text)
    signed_post(client, payload)

    assert len(adapter.calls) == 1
    user_prompt = adapter.calls[0]["user"]
    assert "persona@empresa.com" not in user_prompt
    assert "[EMAIL]" in user_prompt


def test_reply_is_marked_as_mock_when_the_providers_classifier_is_mock(tmp_path):
    notifier = RecordingNotifier()
    client, _ = slack_client(tmp_path, None, notifier=notifier)  # real MockAdapter, not FakeAdapter
    payload = message_event("Ev-mock", "C1", "777.1", "No puedo acceder a mi cuenta, es urgente")
    signed_post(client, payload)

    assert len(notifier.replies) == 1
    last_line = notifier.replies[0]["text"].splitlines()[-1]
    assert last_line == "(clasificación de prueba — modo mock, no es un modelo)"


# -- failure table --------------------------------------------------------------------------


def test_empty_message_gets_a_limit_reply_and_is_never_classified(tmp_path):
    notifier = RecordingNotifier()
    adapter = FakeAdapter([])
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)
    payload = message_event("Ev-empty", "C1", "777.7", "")
    response = signed_post(client, payload)

    assert response.status_code == 200
    assert len(notifier.replies) == 1
    assert "caracteres" in notifier.replies[0]["text"]
    assert adapter.calls == []
    assert client.get("/solicitudes/slack-Ev-empty", headers=HEADERS).status_code == 404


def test_classification_failure_replies_could_not_classify_and_logs(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    notifier = RecordingNotifier()
    adapter = FakeAdapter([unavailable()])
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)
    payload = message_event("Ev-fail", "C1", "888.8", "No puedo acceder a mi cuenta, es urgente")
    signed_post(client, payload)

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
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)

    first = message_event(
        "Ev-conflict", "C1", "999.9", "Mensaje original bastante largo para clasificar bien"
    )
    second = message_event("Ev-conflict", "C1", "999.99", "Mensaje completamente distinto aqui")
    signed_post(client, first)
    signed_post(client, second)

    assert len(notifier.replies) == 1  # only the first
    events = [r for r in caplog.records if r.getMessage() == "slack_id_conflict"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev-conflict"


def test_notifier_failure_is_logged_end_to_end(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")

    def handler(request):
        return httpx2.Response(200, json={"ok": False, "error": "channel_not_found"})

    notifier = SlackNotifier(
        "xoxb-test-token", client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    adapter = FakeAdapter([make_call()])
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)
    payload = message_event(
        "Ev-notify-fail", "C1", "111.11", "No puedo acceder a mi cuenta, es urgente"
    )
    signed_post(client, payload)

    events = [r for r in caplog.records if r.getMessage() == "slack_reply_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev-notify-fail"


def test_bad_event_id_is_rejected_and_logged_without_a_reply(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    notifier = RecordingNotifier()
    adapter = FakeAdapter([])
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)
    payload = message_event("Ev bad id!", "C1", "444.1", "No puedo acceder a mi cuenta, es urgente")
    response = signed_post(client, payload)

    assert response.status_code == 200
    assert notifier.replies == []
    assert adapter.calls == []
    events = [r for r in caplog.records if r.getMessage() == "slack_event_rejected"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev bad id!"
    assert events[0].fields["field"] == "id"


def test_background_task_never_raises_on_an_unexpected_internal_error(
    tmp_path, caplog, monkeypatch
):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    notifier = RecordingNotifier()
    adapter = FakeAdapter([make_call()])
    client, _ = slack_client(tmp_path, adapter, notifier=notifier)

    def boom(row, needs_review):
        raise RuntimeError("SENTINEL-UNEXPECTED")

    monkeypatch.setattr(slack_routes, "_reply_text", boom)

    payload = message_event("Ev-crash", "C1", "321.1", "No puedo acceder a mi cuenta, urgente")
    response = signed_post(client, payload)

    assert response.status_code == 200  # Slack was already acked; the task fails in the background
    events = [r for r in caplog.records if r.getMessage() == "slack_task_failed"]
    assert len(events) == 1
    assert events[0].fields["event_id"] == "Ev-crash"
    assert events[0].fields["exc_type"] == "RuntimeError"
    assert "SENTINEL-UNEXPECTED" not in str(events[0].fields)


# -- payload / event shape ------------------------------------------------------------------


def test_payload_that_is_not_an_object_is_ignored_not_500(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    response = signed_post(client, ["not", "an", "object"])

    assert response.status_code == 200
    events = [r for r in caplog.records if r.getMessage() == "slack_event_ignored"]
    assert len(events) == 1
    assert events[0].fields["reason"] == "payload_not_object"


def test_event_that_is_not_an_object_is_ignored_not_500(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    payload = {"type": "event_callback", "event_id": "Ev-shape", "event": "not-an-object"}
    response = signed_post(client, payload)

    assert response.status_code == 200
    events = [r for r in caplog.records if r.getMessage() == "slack_event_ignored"]
    assert len(events) == 1
    assert events[0].fields["reason"] == "event_not_object"


@pytest.mark.parametrize(
    ("field", "value"), [("channel", 123), ("ts", 456), ("text", ["not", "a", "string"])]
)
def test_event_with_a_non_string_field_is_ignored_not_500(tmp_path, caplog, field, value):
    caplog.set_level(logging.INFO, logger="pitz_pulse.slack")
    client, _ = slack_client(tmp_path, FakeAdapter([]))
    event = {"type": "message", "channel": "C1", "ts": "1.1", "text": "hola", field: value}
    payload = {"type": "event_callback", "event_id": "Ev-badfield", "event": event}
    response = signed_post(client, payload)

    assert response.status_code == 200
    events = [r for r in caplog.records if r.getMessage() == "slack_event_ignored"]
    assert len(events) == 1
    assert events[0].fields["reason"] == f"{field}_not_string"
