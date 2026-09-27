"""Domain errors. Each knows its HTTP mapping; messages never carry request or model text."""

from typing import Any

from pydantic import ValidationError


class DomainError(Exception):
    http_status = 500
    code = "internal_error"
    detail = "internal error"
    retry_after_s: int | None = None

    def __init__(self) -> None:
        super().__init__(self.code)
        self.attempts = 0  # model attempts spent on this request, for the outcome log


class IdConflict(DomainError):
    http_status, code = 409, "id_conflict"
    detail = "this id was already used with a different message"


class InProgress(DomainError):
    http_status, code = 409, "in_progress"
    detail = "this request is being classified; retry later"

    def __init__(self, retry_after_s: int):
        super().__init__()
        self.retry_after_s = retry_after_s


class NotFound(DomainError):
    http_status, code, detail = 404, "not_found", "request not found"


class NotClassified(DomainError):
    http_status, code = 409, "not_classified"
    detail = "only classified requests can be corrected"


class ContractViolation(DomainError):
    http_status, code = 422, "validation_error"
    detail = "the corrected values break the contract"

    def __init__(self, fields: list[dict[str, Any]]):
        super().__init__()
        self.fields = fields


class ClassificationFailed(DomainError):
    http_status, code = 502, "classification_failed"
    detail = "the request could not be classified; retry later"

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind


class Busy(DomainError):
    http_status, code, retry_after_s = 503, "busy", 30
    detail = "too many classifications in progress; retry later"


class DbBusy(DomainError):
    http_status, code, retry_after_s = 503, "db_busy", 1
    detail = "the database is busy; retry"


def field_errors(exc: ValidationError, prefix: tuple[str, ...] = ("body",)) -> list[dict[str, Any]]:
    """Locations and messages only; pydantic's `input` is dropped (it may echo user text)."""
    return [
        {"loc": [*prefix, *(str(part) for part in error["loc"])], "msg": error["msg"]}
        for error in exc.errors(include_url=False)
    ]
