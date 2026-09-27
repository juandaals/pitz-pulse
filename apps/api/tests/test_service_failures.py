"""Model slots, locked writes and the outcome log on every error path."""

import json
import logging
import sqlite3
import threading
import time

import pytest
from fakes import FakeAdapter, GateAdapter, make_call
from service_support import FakeClock, make_service, start, unavailable

from pitz_pulse import repository
from pitz_pulse.classifier import ClassificationCrash
from pitz_pulse.errors import Busy, DbBusy, IdConflict, InProgress
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


def locked(self, *args, **kwargs):
    raise sqlite3.OperationalError("database is locked")


def outcomes(caplog):
    return [r.fields for r in caplog.records if r.getMessage() == "request_outcome"]


def test_model_slots_bound_concurrent_classifications(tmp_path):
    adapter = GateAdapter([make_call() for _ in range(6)])
    service = make_service(tmp_path, adapter, concurrency=2, queue_wait_s=5)
    threads = [
        threading.Thread(target=service.create, args=(RequestInput(id=f"c{i}", message="hola"),))
        for i in range(6)
    ]
    for thread in threads:
        thread.start()
    assert adapter.entered.acquire(timeout=5) and adapter.entered.acquire(timeout=5)
    assert adapter.entered.acquire(timeout=0.3) is False  # a third call never starts
    adapter.release.set()
    for thread in threads:
        thread.join()
    assert adapter.peak == 2
    assert len(adapter.calls) == 6


def test_no_slot_in_time_marks_busy_then_retry_succeeds(tmp_path):
    gate = GateAdapter([make_call(), make_call()])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=0.1)
    worker, _ = start(service.create, RequestInput(id="a", message="x"))
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(Busy):
        service.create(REQ)
    assert service.get(REQ.id)[0].error == "busy"
    gate.release.set()
    worker.join()
    assert service.create(REQ)[1] is True


def test_waiters_beyond_twice_the_concurrency_are_busy_at_once(tmp_path):
    gate = GateAdapter([make_call() for _ in range(3)])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=5)
    workers = [start(service.create, RequestInput(id=f"w{i}", message="x"))[0] for i in range(3)]
    assert gate.entered.acquire(timeout=5)
    deadline = time.monotonic() + 5
    while service._waiters < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service._waiters == 2
    began = time.monotonic()
    with pytest.raises(Busy):
        service.create(REQ)
    assert time.monotonic() - began < 1
    gate.release.set()
    for worker in workers:
        worker.join()


def test_new_arrivals_never_take_the_fast_path_while_others_wait(tmp_path):
    service = make_service(tmp_path, FakeAdapter([]), concurrency=1)
    service._waiters = service._max_waiters  # a full queue; the only slot is free
    assert service._acquire_slot() is False


def test_already_classified_requests_never_wait_for_a_slot(tmp_path):
    gate = GateAdapter([make_call(), make_call()])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=0.1)
    gate.release.set()
    service.create(REQ)
    assert gate.entered.acquire(timeout=1)  # drain the first call's signal
    gate.release.clear()
    worker, _ = start(service.create, RequestInput(id="a", message="x"))
    assert gate.entered.acquire(timeout=5)
    assert service.create(REQ)[1] is False  # no Busy although the only slot is taken
    gate.release.set()
    worker.join()


def test_complete_is_retried_when_the_database_is_locked(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    original = repository.Repository.complete
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(repository.Repository, "complete", flaky)
    row, created = service.create(REQ)
    assert (row.status, created, calls["n"]) == ("classified", True, 2)


def test_complete_locked_every_time_is_db_busy(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    monkeypatch.setattr(repository.Repository, "complete", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.attempts == 1
    assert service.get(REQ.id)[0].status == "pending"


def test_unexpected_error_in_complete_fails_the_row(tmp_path, monkeypatch):
    service = make_service(tmp_path, FakeAdapter([make_call()]))

    def broken(self, *args, **kwargs):
        raise sqlite3.IntegrityError("SENTINEL")

    monkeypatch.setattr(repository.Repository, "complete", broken)
    with pytest.raises(sqlite3.IntegrityError):
        service.create(REQ)
    assert service.get(REQ.id)[0].error == "internal_error"


def test_locked_fail_write_is_logged_and_surfaces_db_busy(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([unavailable()]))
    monkeypatch.setattr(repository.Repository, "fail", locked)
    with pytest.raises(DbBusy) as info:
        service.create(REQ)
    assert info.value.attempts == 1
    events = [(r.getMessage(), r.fields) for r in caplog.records]
    assert ("request_fail_write_failed", {"id": REQ.id, "exc_type": "DbBusy"}) in events
    assert service.get(REQ.id)[0].status == "pending"


def test_locked_fail_write_after_a_crash_is_logged(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([RuntimeError("x")]))
    monkeypatch.setattr(repository.Repository, "fail", locked)
    with pytest.raises(ClassificationCrash):
        service.create(REQ)
    events = [r.getMessage() for r in caplog.records]
    assert "request_fail_write_failed" in events


def test_outcome_log_carries_409_and_503_statuses(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    gate = GateAdapter([make_call()])
    service = make_service(tmp_path, gate, concurrency=1, queue_wait_s=0.05)
    worker, _ = start(service.create, REQ)
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(InProgress):
        service.create(REQ)
    with pytest.raises(Busy):
        service.create(RequestInput(id="b1", message="x"))
    gate.release.set()
    worker.join()
    with pytest.raises(IdConflict):
        service.create(RequestInput(id=REQ.id, message="otro"))
    monkeypatch.setattr(repository.Repository, "get", locked)
    with pytest.raises(DbBusy):
        service.create(RequestInput(id="c1", message="x"))
    statuses = [(f["id"], f["http_status"], f["error"]) for f in outcomes(caplog)]
    assert statuses == [
        (REQ.id, 409, "in_progress"),
        ("b1", 503, "busy"),
        (REQ.id, 201, None),
        (REQ.id, 409, "id_conflict"),
        ("c1", 503, "db_busy"),
    ]


def test_no_error_path_logs_the_message_or_exception_text(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "SENTINEL-7F3A"
    req = RequestInput(id="s1", message=f"hola {secret}")
    make_service(tmp_path / "a", FakeAdapter([make_call()])).create(req)
    with pytest.raises(IdConflict):
        make_service(tmp_path / "a", FakeAdapter([])).create(RequestInput(id="s1", message=secret))
    with pytest.raises(ClassificationCrash):
        make_service(tmp_path / "b", FakeAdapter([RuntimeError(secret)])).create(req)
    gate = GateAdapter([make_call()])
    busy = make_service(tmp_path / "c", gate, concurrency=1, queue_wait_s=0.05)
    worker, _ = start(busy.create, RequestInput(id="g1", message="x"))
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(Busy):
        busy.create(req)
    gate.release.set()
    worker.join()
    clock = FakeClock()
    late_gate = GateAdapter([make_call()])
    late, outcome = start(make_service(tmp_path / "d", late_gate, clock=clock).create, req)
    assert late_gate.entered.acquire(timeout=5)
    clock.advance(531)
    make_service(tmp_path / "d", FakeAdapter([make_call()]), clock=clock).create(req)
    late_gate.release.set()
    late.join()
    assert outcome["result"][1] is False
    monkeypatch.setattr(repository.Repository, "complete", locked)
    with pytest.raises(DbBusy):
        make_service(tmp_path / "e", FakeAdapter([make_call()])).create(req)
    text = json.dumps(
        [[r.getMessage(), getattr(r, "fields", None), r.exc_info] for r in caplog.records],
        default=str,
    )
    assert "request_claim_lost" in text and "request_complete_failed" in text
    assert secret not in text
