"""Intake wiring for near-duplicate detection (Spec 06c): TriageService.create end to end."""

import logging

from fakes import FakeAdapter, make_call
from service_support import make_service

from pitz_pulse import repository
from pitz_pulse.schema import RequestInput

LONG_MESSAGE = "Necesito acceso urgente a mi cuenta de correo corporativo para continuar"


def test_second_near_identical_post_flags_the_first_as_duplicate(tmp_path):
    adapter = FakeAdapter([make_call(), make_call()])
    service = make_service(tmp_path, adapter)

    first, _ = service.create(RequestInput(id="d1", message=LONG_MESSAGE))
    assert first.possible_duplicate_of is None

    second, _ = service.create(RequestInput(id="d2", message=LONG_MESSAGE + " porfavor"))
    assert second.possible_duplicate_of == "d1"


def test_different_wording_is_not_flagged_as_duplicate(tmp_path):
    adapter = FakeAdapter([make_call(), make_call()])
    service = make_service(tmp_path, adapter)
    service.create(RequestInput(id="d1", message=LONG_MESSAGE))

    other, _ = service.create(
        RequestInput(id="d2", message="El reporte mensual de ventas no llega a tiempo nunca")
    )
    assert other.possible_duplicate_of is None


def test_duplicate_check_failure_is_logged_and_never_fails_the_request(
    tmp_path, caplog, monkeypatch
):
    caplog.set_level(logging.INFO, logger="pitz_pulse.service")

    def boom(self, idioma, exclude_id, limit):
        raise RuntimeError("SENTINEL-DB-ERROR")

    monkeypatch.setattr(repository.Repository, "list_recent_classified", boom)
    service = make_service(tmp_path, FakeAdapter([make_call()]))

    row, created = service.create(RequestInput(id="d1", message=LONG_MESSAGE))

    assert (row.status, created) == ("classified", True)
    assert row.possible_duplicate_of is None
    events = [r for r in caplog.records if r.getMessage() == "duplicate_check_failed"]
    assert len(events) == 1
    assert events[0].fields["exc_type"] == "RuntimeError"
    assert "SENTINEL-DB-ERROR" not in str(events[0].fields)
