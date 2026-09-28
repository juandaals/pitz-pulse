"""G16/G24: a worker whose row was re-claimed never overwrites it and never answers 201."""

import logging

from fakes import VALID_OUTPUT, FakeAdapter, GateAdapter, make_call
from service_support import FakeClock, make_service, start, unavailable

from pitz_pulse.errors import ClassificationFailed, InProgress
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")
DATOS = {**VALID_OUTPUT, "categoria": "datos"}


def hold_first_worker(tmp_path, response):
    """A starts and blocks inside the model call; then its row becomes stale."""
    clock = FakeClock()
    gate = GateAdapter([response])
    worker, outcome = start(make_service(tmp_path, gate, clock=clock).create, REQ)
    assert gate.entered.acquire(timeout=5)
    clock.advance(531)
    return clock, gate, worker, outcome


def test_lost_claim_on_a_classified_row_answers_200_never_201(tmp_path, caplog):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, make_call())
    make_service(tmp_path, FakeAdapter([make_call()]), clock=clock).create(REQ)  # B wins
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    gate.release.set()
    worker.join()
    row, created = outcome["result"]
    assert (row.status, created) == ("classified", False)
    assert any(r.getMessage() == "request_claim_lost" for r in caplog.records)


def test_late_worker_never_completes_a_row_re_claimed_in_flight(tmp_path):
    clock, gate_a, worker_a, outcome_a = hold_first_worker(tmp_path, make_call())
    gate_b = GateAdapter([make_call(DATOS)])
    service_b = make_service(tmp_path, gate_b, clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    assert gate_b.entered.acquire(timeout=5)  # B re-claimed and is in flight

    gate_a.release.set()
    worker_a.join()
    assert isinstance(outcome_a["error"], InProgress)
    row = service_b.get(REQ.id)[0]
    assert row.status == "pending" and row.claim_token is not None

    gate_b.release.set()
    worker_b.join()
    row, created = outcome_b["result"]
    assert (row.status, row.classification.categoria, created) == ("classified", "datos", True)


def test_lost_claim_on_a_failed_row_is_502_with_that_kind(tmp_path):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, make_call())
    service_b = make_service(tmp_path, FakeAdapter([unavailable()]), clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    worker_b.join()
    assert isinstance(outcome_b["error"], ClassificationFailed)

    gate.release.set()
    worker.join()
    error = outcome["error"]
    assert isinstance(error, ClassificationFailed)
    assert (error.kind, error.attempts) == ("llm_unavailable", 1)
    assert service_b.get(REQ.id)[0].status == "failed"


def test_late_failure_after_a_re_claim_does_not_fail_the_row(tmp_path):
    clock, gate_a, worker_a, outcome_a = hold_first_worker(tmp_path, unavailable())
    gate_b = GateAdapter([make_call()])
    service_b = make_service(tmp_path, gate_b, clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    assert gate_b.entered.acquire(timeout=5)

    gate_a.release.set()
    worker_a.join()
    assert isinstance(outcome_a["error"], InProgress)
    row = service_b.get(REQ.id)[0]
    assert (row.status, row.error) == ("pending", None)

    gate_b.release.set()
    worker_b.join()
    assert outcome_b["result"][1] is True


def test_crash_after_a_lost_claim_answers_from_the_current_row(tmp_path):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, RuntimeError("SENTINEL"))
    make_service(tmp_path, FakeAdapter([make_call()]), clock=clock).create(REQ)  # B wins
    gate.release.set()
    worker.join()
    row, created = outcome["result"]
    assert (row.status, row.error, created) == ("classified", None, False)


def test_crash_while_a_re_claim_is_in_flight_is_in_progress(tmp_path):
    clock, gate_a, worker_a, outcome_a = hold_first_worker(tmp_path, RuntimeError("SENTINEL"))
    gate_b = GateAdapter([make_call()])
    service_b = make_service(tmp_path, gate_b, clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    assert gate_b.entered.acquire(timeout=5)  # B re-claimed and is in flight

    gate_a.release.set()
    worker_a.join()
    assert isinstance(outcome_a["error"], InProgress)
    assert service_b.get(REQ.id)[0].status == "pending"

    gate_b.release.set()
    worker_b.join()
    assert outcome_b["result"][1] is True


def test_crash_after_a_re_claim_failed_the_row_is_classification_failed(tmp_path):
    clock, gate, worker, outcome = hold_first_worker(tmp_path, RuntimeError("SENTINEL"))
    service_b = make_service(tmp_path, FakeAdapter([unavailable()]), clock=clock)
    worker_b, outcome_b = start(service_b.create, REQ)
    worker_b.join()
    assert isinstance(outcome_b["error"], ClassificationFailed)

    gate.release.set()
    worker.join()
    error = outcome["error"]
    assert isinstance(error, ClassificationFailed)
    assert error.kind == "llm_unavailable"
    assert service_b.get(REQ.id)[0].error == "llm_unavailable"
