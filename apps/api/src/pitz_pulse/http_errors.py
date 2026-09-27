"""One error body shape for every failure; unhandled exceptions never reach uvicorn (D9)."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pitz_pulse.errors import DomainError

logger = logging.getLogger("pitz_pulse.http")
_HTTP_CODES = {
    401: "unauthorized",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
}


def error_response(
    status: int, code: str, detail: str, headers: dict[str, str] | None = None, **extra: Any
) -> JSONResponse:
    body = {"error": code, "detail": detail}
    body.update({key: value for key, value in extra.items() if value is not None})
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        headers = {"Retry-After": str(exc.retry_after_s)} if exc.retry_after_s else None
        return error_response(
            exc.http_status,
            exc.code,
            exc.detail,
            headers,
            fields=getattr(exc, "fields", None),
            kind=getattr(exc, "kind", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        return error_response(422, "validation_error", "request validation failed", fields=fields)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail), exc.headers)

    @app.middleware("http")
    async def _catch_all(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            # Class name only: messages and chained causes may carry request or model text.
            logger.error(
                "unhandled_error",
                extra={"fields": {"exc_type": type(exc).__name__, "path": request.url.path}},
            )
            return error_response(500, "internal_error", "internal error")
