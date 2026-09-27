import threading

import pytest
from fakes import FakeAdapter, make_call
from service_support import make_service, unavailable

from pitz_pulse import corrections
from pitz_pulse.errors import ClassificationFailed, ContractViolation, NotClassified, NotFound
from pitz_pulse.repository import Repository
from pitz_pulse.schema import RequestInput

REQ = RequestInput(id="MSG-01", message="Error 500 al subir catálogo")


@pytest.fixture
def service(tmp_path):
    svc = make_service(tmp_path, FakeAdapter([make_call()]))  # requiere_info True, confianza .86
    svc.create(REQ)
    return svc


def test_patching_one_field_on_a_bool_row_works_and_history_is_typed(service):
    row = service.correct(REQ.id, {"categoria": "datos"}, "ana", None)
    assert row.classification.categoria == "datos"
    detail, history = service.get(REQ.id)
    assert history[0]["previous_values"] == {"categoria": "bug"}
    assert history[0]["new_values"] == {"categoria": "datos"}


def test_original_stays_immutable_after_two_patches(service):
    service.correct(REQ.id, {"categoria": "datos"}, "ana", None)
    service.correct(REQ.id, {"prioridad": "baja"}, "luis", "menor impacto")
    row, history = service.get(REQ.id)
    assert row.original_classification["categoria"] == "bug"
    assert row.original_classification["prioridad"] == "alta"
    assert [h["author"] for h in history] == ["ana", "luis"]
    assert row.corrected is True


def test_empty_diff_is_a_confirmation(service):
    row = service.correct(REQ.id, {"categoria": "bug"}, "ana", None)
    assert (row.reviewed, row.corrected) == (True, False)
    assert service.get(REQ.id)[1][0]["new_values"] == {}


def test_confirmation_keeps_an_earlier_correction_flag(service):
    service.correct(REQ.id, {"categoria": "datos"}, "ana", None)
    row = service.correct(REQ.id, {}, "luis", None)
    assert row.corrected is True


def test_turning_requiere_info_off_needs_an_explicit_null_question(service):
    with pytest.raises(ContractViolation) as info:
        service.correct(REQ.id, {"requiere_info": False}, "ana", None)
    assert info.value.fields[0]["loc"] == ["body"]
    row = service.correct(
        REQ.id, {"requiere_info": False, "pregunta_seguimiento": None}, "ana", None
    )
    assert row.classification.requiere_info is False
    assert service.get(REQ.id)[1][0]["new_values"] == {
        "requiere_info": False,
        "pregunta_seguimiento": None,
    }


def test_history_json_stores_booleans_not_integers(service):
    service.correct(REQ.id, {"requiere_info": False, "pregunta_seguimiento": None}, "ana", None)
    with service.connection() as repo:
        previous, new = repo.conn.execute(
            "SELECT previous_values, new_values FROM corrections"
        ).fetchone()
    assert '"requiere_info": true' in previous and '"requiere_info": false' in new


def test_contract_rule_violation_is_rolled_back(service):
    too_long = " ".join(["palabra"] * 25)
    with pytest.raises(ContractViolation):
        service.correct(REQ.id, {"resumen": too_long}, "ana", None)
    row, history = service.get(REQ.id)
    assert history == [] and row.reviewed is False


def test_unknown_id_is_not_found(service):
    with pytest.raises(NotFound):
        service.correct("nope", {"categoria": "datos"}, "ana", None)


def test_failed_rows_cannot_be_corrected(tmp_path):
    svc = make_service(tmp_path, FakeAdapter([unavailable()]))
    with pytest.raises(ClassificationFailed):
        svc.create(REQ)
    with pytest.raises(NotClassified):
        svc.correct(REQ.id, {"categoria": "datos"}, "ana", None)


def test_pending_rows_cannot_be_corrected(service):
    with service.connection() as repo, repo.transaction():
        repo.insert_pending(RequestInput(id="p1", message="x"), "h", "tok", service._now())
    with pytest.raises(NotClassified):
        service.correct("p1", {"categoria": "datos"}, "ana", None)


def test_non_correctable_fields_are_rejected(service):
    with pytest.raises(ValueError, match="not correctable"):
        service.correct(REQ.id, {"confianza": 0.1}, "ana", None)


def test_concurrent_patches_serialize(service, monkeypatch):
    first_inside, release, second_at_begin = (threading.Event() for _ in range(3))
    validate = corrections.Classification.model_validate
    begin = Repository.transaction

    def gated_validate(data, *args, **kwargs):
        if not first_inside.is_set():
            first_inside.set()
            assert release.wait(timeout=5)
        return validate(data, *args, **kwargs)

    def watched_begin(self):
        if first_inside.is_set():
            second_at_begin.set()
        return begin(self)

    monkeypatch.setattr(corrections.Classification, "model_validate", gated_validate)
    monkeypatch.setattr(Repository, "transaction", watched_begin)

    def patch(value):
        service.correct(REQ.id, {"categoria": value}, "ana", None)

    first = threading.Thread(target=patch, args=("datos",))
    first.start()
    assert first_inside.wait(timeout=5)  # the first PATCH is inside its transaction
    second = threading.Thread(target=patch, args=("acceso",))
    second.start()
    assert second_at_begin.wait(timeout=5)  # the second one is about to BEGIN
    release.set()
    first.join()
    second.join()
    older, newer = service.get(REQ.id)[1]
    assert older["new_values"] == {"categoria": "datos"}
    assert newer["previous_values"] == {"categoria": "datos"}
