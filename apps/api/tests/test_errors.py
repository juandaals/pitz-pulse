import pytest
from pydantic import ValidationError

from pitz_pulse.errors import (
    Busy,
    ClassificationFailed,
    ContractViolation,
    DbBusy,
    DomainError,
    IdConflict,
    InProgress,
    NotClassified,
    NotFound,
    field_errors,
)
from pitz_pulse.schema import RequestInput


@pytest.mark.parametrize(
    ("error", "status", "code", "retry_after"),
    [
        (IdConflict(), 409, "id_conflict", None),
        (InProgress(12), 409, "in_progress", 12),
        (NotFound(), 404, "not_found", None),
        (NotClassified(), 409, "not_classified", None),
        (ContractViolation([]), 422, "validation_error", None),
        (ClassificationFailed("llm_rejected"), 502, "classification_failed", None),
        (Busy(), 503, "busy", 30),
        (DbBusy(), 503, "db_busy", 1),
    ],
)
def test_each_domain_error_carries_its_http_mapping(error, status, code, retry_after):
    assert isinstance(error, DomainError)
    assert (error.http_status, error.code, error.retry_after_s) == (status, code, retry_after)
    assert error.detail and error.attempts == 0


def test_classification_failed_exposes_the_kind():
    assert ClassificationFailed("invalid_output").kind == "invalid_output"


def test_field_errors_prefix_locations_and_never_echo_input():
    sentinel = "SENTINEL-TEXT " * 700  # too long: the error is on the message field itself
    with pytest.raises(ValidationError) as info:
        RequestInput.model_validate({"id": "a", "message": sentinel})
    assert "SENTINEL-TEXT" in str(info.value)  # pydantic itself echoes the input
    fields = field_errors(info.value)
    assert [error["loc"] for error in fields] == [["body", "message"]]
    assert "SENTINEL-TEXT" not in str(fields)
