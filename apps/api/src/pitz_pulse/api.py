"""HTTP API (Spec 02 §4). `uvicorn --factory pitz_pulse.api:create_app` calls create_app()."""

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Response, Security
from fastapi.security import APIKeyHeader

from pitz_pulse import db, http_errors
from pitz_pulse.api_models import ErrorBody, Item, ItemDetail, ListQuery, Page, PatchBody
from pitz_pulse.classifier import build_classifier
from pitz_pulse.config import disable_tracing
from pitz_pulse.logs import configure_logging
from pitz_pulse.models_catalog import MOCK
from pitz_pulse.providers.base import ProviderAdapter
from pitz_pulse.repository import StoredRequest
from pitz_pulse.schema import ID_PATTERN, RequestInput
from pitz_pulse.service import TriageService
from pitz_pulse.settings_api import ApiSettings, parse_api_settings

MOCK_HEADER = "X-Pitz-Provider"
RequestId = Annotated[str, Path(pattern=ID_PATTERN)]
MAX_BODY_BYTES = 65536


def _errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries for the error responses a route can return (all use ErrorBody)."""
    return {status: {"model": ErrorBody} for status in statuses}


def create_app(
    settings: ApiSettings | None = None, adapter: ProviderAdapter | None = None
) -> FastAPI:
    if settings is None:
        configure_logging("INFO")  # before parsing: the auto-selection log line must not be lost
        disable_tracing(os.environ)
        settings = parse_api_settings(os.environ)  # ConfigError aborts startup
        configure_logging(settings.llm.log_level)
    classifier = build_classifier(settings.llm, adapter)
    conn = db.connect(settings.db_path)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    service = TriageService(
        settings.db_path,
        classifier,
        threshold=settings.llm.confidence_threshold,
        pending_stale_s=settings.pending_stale_s,
    )
    running_mock = classifier.adapter.provider == MOCK
    # Every waiter holds a worker thread: slots + 2x waiters + headroom for reads (Spec 02 §2).
    worker_threads = 3 * settings.llm.concurrency + 16

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = max(limiter.total_tokens, worker_threads)
        yield

    app = FastAPI(title="Pitz Pulse", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    http_errors.install(app)

    @app.middleware("http")
    async def _limit_body_size(request, call_next):
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_BODY_BYTES:
            detail = f"request body larger than {MAX_BODY_BYTES} bytes"
            return http_errors.error_response(413, "payload_too_large", detail)
        return await call_next(request)

    @app.middleware("http")
    async def _mark_mock_mode(request, call_next):
        response = await call_next(request)
        if running_mock:
            response.headers[MOCK_HEADER] = "mock"
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:  # async: no DB, answers even when the pool is busy
        return {
            "status": "ok",
            "provider": classifier.adapter.provider,
            "model": classifier.adapter.model,
            "prompt_version": classifier.prompt.version,
        }

    app.include_router(_router(service, settings.api_key))
    return app


def _mark_stored_mock_rows(response: Response, rows: list[StoredRequest]) -> None:
    if any(row.provider == MOCK for row in rows):
        response.headers[MOCK_HEADER] = "mock"


def _router(service: TriageService, api_key: str) -> APIRouter:
    header = APIKeyHeader(name="X-API-Key", auto_error=False)
    expected = api_key.encode()

    async def require_api_key(provided: Annotated[str | None, Security(header)]) -> None:
        if provided is None or not hmac.compare_digest(provided.encode(), expected):
            raise HTTPException(status_code=401, detail="missing or invalid API key")

    router = APIRouter(
        prefix="/solicitudes",
        dependencies=[Depends(require_api_key)],
        responses=_errors(401, 422, 500, 503),
    )

    @router.post(
        "",
        status_code=201,
        response_model=Item,
        responses={200: {"model": Item}, **_errors(409, 502)},
    )
    def create(body: RequestInput, response: Response) -> Item:
        row, created = service.create(body)
        response.status_code = 201 if created else 200
        _mark_stored_mock_rows(response, [row])
        return Item.from_stored(row, service.needs_review(row))

    @router.get("", response_model=Page)
    def list_requests(query: Annotated[ListQuery, Query()], response: Response) -> Page:
        rows, total = service.list_requests(query.filters(), query.limit, query.offset)
        _mark_stored_mock_rows(response, rows)
        items = [Item.from_stored(row, service.needs_review(row)) for row in rows]
        return Page(items=items, total=total, limit=query.limit, offset=query.offset)

    @router.get("/{request_id}", response_model=ItemDetail, responses=_errors(404))
    def get_request(request_id: RequestId, response: Response) -> ItemDetail:
        row, corrections = service.get(request_id)
        _mark_stored_mock_rows(response, [row])
        return ItemDetail.from_stored(row, service.needs_review(row), corrections)

    @router.patch("/{request_id}", response_model=Item, responses=_errors(404, 409))
    def correct(request_id: RequestId, body: PatchBody, response: Response) -> Item:
        row = service.correct(request_id, body.changes(), body.author, body.reason)
        _mark_stored_mock_rows(response, [row])
        return Item.from_stored(row, service.needs_review(row))

    return router
