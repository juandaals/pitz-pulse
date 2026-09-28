import json
import logging

import pytest
from fakes import FakeAdapter, GateAdapter, make_call
from service_support import FakeClock, make_service, start, unavailable

from pitz_pulse.classifier import ClassificationCrash
from pitz_pulse.errors import ClassificationFailed, IdConflict, InProgress
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


def test_same_id_and_text_calls_the_model_once_then_replays(tmp_path):
    adapter = FakeAdapter([make_call()])
    service = make_service(tmp_path, adapter)
    row, created = service.create(REQ)
    assert (row.status, created) == ("classified", True)
    row, created = service.create(REQ)
    assert (row.status, created) == ("classified", False)
    assert len(adapter.calls) == 1


def test_unicode_message_round_trips_and_replays(tmp_path):
    adapter = FakeAdapter([make_call()])
    service = make_service(tmp_path, adapter)
    req = RequestInput(id="u1", message="Não consigo acessar 🚀 ação")
    service.create(req)
    row, created = service.create(req)
    assert created is False
    assert service.get("u1")[0].message == "Não consigo acessar 🚀 ação"


def test_same_id_with_different_text_is_a_conflict(tmp_path):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    service.create(REQ)
    with pytest.raises(IdConflict):
        service.create(RequestInput(id="MSG-01", message="otro texto"))


def test_whitespace_difference_counts_as_different_text(tmp_path):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    service.create(REQ)
    with pytest.raises(IdConflict):
        service.create(RequestInput(id="MSG-01", message=REQ.message + " "))


def test_two_threads_same_id_make_exactly_one_model_call(tmp_path):
    adapter = GateAdapter([make_call(), make_call()])
    service = make_service(tmp_path, adapter)
    first, outcome = start(service.create, REQ)
    assert adapter.entered.acquire(timeout=5)  # first call is in flight
    with pytest.raises(InProgress) as info:
        service.create(REQ)
    assert info.value.retry_after_s > 0
    adapter.release.set()
    first.join()
    assert len(adapter.calls) == 1
    assert outcome["result"][1] is True


def test_pending_row_survives_restart_as_in_progress(tmp_path):
    adapter = GateAdapter([make_call()])
    clock = FakeClock()
    service = make_service(tmp_path, adapter, clock=clock)
    worker, _ = start(service.create, REQ)
    assert adapter.entered.acquire(timeout=5)
    restarted = make_service(tmp_path, FakeAdapter([]), clock=clock)  # same DB file
    with pytest.raises(InProgress) as info:
        restarted.create(REQ)
    assert 0 < info.value.retry_after_s <= 530
    adapter.release.set()
    worker.join()


def test_stale_pending_row_is_reclaimed(tmp_path):
    clock = FakeClock()
    gate = GateAdapter([make_call()])
    service = make_service(tmp_path, gate, clock=clock)
    worker, _ = start(service.create, REQ)
    assert gate.entered.acquire(timeout=5)
    clock.advance(531)
    fresh = make_service(tmp_path, FakeAdapter([make_call()]), clock=clock)
    row, created = fresh.create(REQ)
    assert (row.status, created) == ("classified", True)
    gate.release.set()
    worker.join()


def test_failed_classification_is_recorded_then_retried(tmp_path):
    adapter = FakeAdapter([unavailable(), make_call()])
    service = make_service(tmp_path, adapter)
    with pytest.raises(ClassificationFailed) as info:
        service.create(REQ)
    assert (info.value.kind, info.value.attempts) == ("llm_unavailable", 1)
    row, _ = service.get(REQ.id)
    assert (row.status, row.error) == ("failed", "llm_unavailable")
    row, created = service.create(REQ)
    assert (row.status, row.error, created) == ("classified", None, True)


def test_failing_twice_stays_failed(tmp_path):
    service = make_service(tmp_path, FakeAdapter([unavailable(), unavailable()]))
    for _ in range(2):
        with pytest.raises(ClassificationFailed):
            service.create(REQ)
    assert service.get(REQ.id)[0].status == "failed"


def test_failed_retry_refreshes_updated_at_so_a_concurrent_post_waits(tmp_path):
    clock = FakeClock()
    gate = GateAdapter([make_call()])
    service = make_service(tmp_path, FakeAdapter([unavailable()]), clock=clock)
    with pytest.raises(ClassificationFailed):
        service.create(REQ)
    clock.advance(10_000)  # the failed row is old
    retrying = make_service(tmp_path, gate, clock=clock)
    worker, _ = start(retrying.create, REQ)
    assert gate.entered.acquire(timeout=5)
    with pytest.raises(InProgress):
        make_service(tmp_path, FakeAdapter([]), clock=clock).create(REQ)
    gate.release.set()
    worker.join()


def test_retry_classifies_the_stored_source_area(tmp_path):
    adapter = FakeAdapter([unavailable(), make_call()])
    service = make_service(tmp_path, adapter)
    with pytest.raises(ClassificationFailed):
        service.create(RequestInput(id="s1", message="hola", source_area="Ventas"))
    service.create(RequestInput(id="s1", message="hola", source_area="Soporte"))
    assert "<source_area>Ventas</source_area>" in adapter.calls[1]["user"]
    assert service.get("s1")[0].source_area == "Ventas"


def test_crash_marks_the_row_internal_error_and_allows_retry(tmp_path):
    service = make_service(tmp_path, FakeAdapter([RuntimeError("SENTINEL"), make_call()]))
    with pytest.raises(ClassificationCrash):
        service.create(REQ)
    assert service.get(REQ.id)[0].error == "internal_error"
    assert service.create(REQ)[1] is True


def test_each_create_logs_one_outcome_without_message_text(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    req = RequestInput(id="s1", message="SENTINEL-MESSAGE-TEXT")
    service.create(req)
    service.create(req)
    outcomes = [r for r in caplog.records if r.getMessage() == "request_outcome"]
    assert [r.fields["http_status"] for r in outcomes] == [201, 200]
    assert outcomes[0].fields["attempts"] == 1
    assert "SENTINEL-MESSAGE-TEXT" not in json.dumps([r.fields for r in caplog.records])


def test_needs_review_follows_the_threshold_without_rewriting_rows(tmp_path):
    service = make_service(tmp_path, FakeAdapter([make_call()]))
    row, _ = service.create(REQ)  # confianza 0.86
    assert service.needs_review(row) is False
    strict = make_service(tmp_path, FakeAdapter([]), threshold=0.9)
    assert strict.needs_review(strict.get(REQ.id)[0]) is True
