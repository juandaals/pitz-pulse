"""request_outcome and failure events stay observable; Retry-After after a reservation."""

import logging
import sqlite3

import pytest
from api_support import HEADERS, make_client
from fakes import FakeAdapter, make_call
from service_support import FakeClock, make_service

from pitz_pulse import db, repository
from pitz_pulse.errors import ClassificationFailed, DbBusy
from pitz_pulse.logs import configure_logging
from pitz_pulse.providers.base import LLMError
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


def locked(self, *args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


def outcomes(caplog):
    return [r.fields for r in caplog.records if r.getMessage() == "request_outcome"]


class SlowAdapter(FakeAdapter):
    """Each model call takes `seconds` on the fake clock."""

    def __init__(self, responses, clock, seconds):
        super().__init__(responses)
        self.clock, self.seconds = clock, seconds

    def invoke(self, *args, **kwargs):
        self.clock.advance(self.seconds)
        return super().invoke(*args, **kwargs)


@pytest.fixture
def warning_level():
    configure_logging("WARNING")
    yield
    configure_logging("INFO")


def test_failure_events_survive_warning_level(tmp_path, monkeypatch, caplog, warning_level):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    monkeypatch.setattr(repository.Repository, "complete", locked)
    with pytest.raises(DbBusy):
        service.create(REQ)
    events = [r.getMessage() for r in caplog.records]
    assert "request_complete_failed" in events
    assert "request_outcome" in events


def test_service_logger_follows_debug_like_llm():
    configure_logging("DEBUG")
    assert logging.getLogger("pitz_pulse.service").isEnabledFor(logging.DEBUG)
    configure_logging("INFO")


def test_rejected_outcome_carries_kind_and_row_status(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    rejected = LLMError("rejected", "APIStatusError:401")
    service = make_service(tmp_path, FakeAdapter([rejected]))
    with pytest.raises(ClassificationFailed):
        service.create(REQ)
    outcome = outcomes(caplog)[-1]
    assert (outcome["http_status"], outcome["kind"], outcome["status"]) == (
        502,
        "llm_rejected",
        "failed",
    )


def test_success_outcome_has_no_kind(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    make_service(tmp_path, FakeAdapter([make_call()])).create(REQ)
    outcome = outcomes(caplog)[-1]
    assert (outcome["kind"], outcome["status"]) == (None, "classified")


def test_unexpected_complete_error_logs_the_billed_attempt(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([make_call()]))

    def broken(self, *args, **kwargs):
        raise sqlite3.IntegrityError("SENTINEL")

    monkeypatch.setattr(repository.Repository, "complete", broken)
    with pytest.raises(sqlite3.IntegrityError):
        service.create(REQ)
    outcome = outcomes(caplog)[-1]
    assert (outcome["attempts"], outcome["error"]) == (1, "IntegrityError")


def test_locked_complete_retry_after_is_the_time_until_stale(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    clock = FakeClock()
    service = make_service(tmp_path, SlowAdapter([make_call()], clock, 100), clock=clock)
    monkeypatch.setattr(repository.Repository, "complete", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.retry_after_s == 430  # 530 s window, reserved 100 s ago
    assert outcomes(caplog)[-1]["status"] == "pending"


def test_locked_fail_write_retry_after_is_the_time_until_stale(tmp_path, monkeypatch):
    clock = FakeClock()
    adapter = SlowAdapter([LLMError("unavailable", "APIConnectionError")], clock, 30)
    service = make_service(tmp_path, adapter, clock=clock)
    monkeypatch.setattr(repository.Repository, "fail", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.retry_after_s == 500


def test_db_busy_before_any_reservation_keeps_one_second(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([]))
    monkeypatch.setattr(repository.Repository, "get", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.retry_after_s == 1


def test_locked_complete_over_http_sends_retry_after_until_stale(tmp_path, monkeypatch):
    client, _ = make_client(tmp_path, FakeAdapter([make_call()]))
    monkeypatch.setattr(repository.Repository, "complete", locked)
    response = client.post("/solicitudes", json=REQ.model_dump(), headers=HEADERS)
    assert (response.status_code, response.json()["error"]) == (503, "db_busy")
    assert 500 <= int(response.headers["Retry-After"]) <= 530


def test_lock_while_connecting_is_db_busy(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([]))

    def locked_connect(path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db, "connect", locked_connect)
    with pytest.raises(DbBusy):
        service.get(REQ.id)
