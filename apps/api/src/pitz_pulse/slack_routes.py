"""POST /slack/events (Spec 06d, X1): verify, filter, classify in the background, reply.

Mounted only when SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN are both set (`api.py`); this route
carries no X-API-Key dependency, the Slack signature is the auth. The handler is `async def`
(documented exception to D5): HMAC verification needs the exact raw body bytes before any
parsing. The body is read from `request.stream()` with a running 65536-byte cap (the app-wide
`Content-Length`-based cap in `api.py` cannot see a chunked body sent with no, or a lying,
`Content-Length` header). Payload and event shape are validated before dispatch: anything that
is not the object shape Slack sends is logged and ignored, never a 500. Classification runs in a
FastAPI background task (a plain `def`, so Starlette offloads it to the thread pool via
`anyio.to_thread`) using the app's single `TriageService`, so the event loop is never blocked and
the model-slot semaphore (Spec 02 §2) is shared with `/solicitudes`; the task is wrapped so it
never raises into Starlette's background runner.
"""

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pitz_pulse import http_errors
from pitz_pulse.classifier import ClassificationCrash
from pitz_pulse.errors import Busy, ClassificationFailed, DbBusy, IdConflict, InProgress
from pitz_pulse.logs import log_event
from pitz_pulse.models_catalog import MOCK
from pitz_pulse.repository import StoredRequest
from pitz_pulse.schema import MAX_MESSAGE_CHARS, RequestInput
from pitz_pulse.service import TriageService
from pitz_pulse.slack import InvalidSignature, SlackNotifier, SlackVerifier

logger = logging.getLogger("pitz_pulse.slack")

_LIMIT_TEXT = (
    "No pudimos registrar el mensaje: debe tener entre 1 y "
    f"{MAX_MESSAGE_CHARS} caracteres. Reenvíalo ajustando la longitud."
)
_RETRY_TEXT = "No pudimos clasificar la solicitud; un humano la revisará."
# Grouped per the failure table (Spec 06d): all four end in the same logged, retried reply.
_RETRYABLE_ERRORS = (ClassificationFailed, Busy, DbBusy, ClassificationCrash)
_MAX_BODY_BYTES = 65536  # matches api.py's app-wide cap; enforced here too for chunked bodies
_MOCK_DISCLAIMER = "(clasificación de prueba — modo mock, no es un modelo)"
_STRING_EVENT_FIELDS = ("channel", "ts", "text")


class _PayloadTooLarge(Exception):
    pass


async def _read_capped_body(request: Request, cap: int) -> bytes:
    """Reads the raw body off the stream, enforcing `cap` as chunks arrive: unlike a
    `Content-Length` check, this also catches a chunked body that never declares its size."""
    total = 0
    chunks: list[bytes] = []
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise _PayloadTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def _is_processable(event: dict[str, Any]) -> bool:
    """`message`, no subtype (edits/deletes), not a bot, and not a reply inside a thread."""
    thread_ts = event.get("thread_ts")
    return (
        event.get("type") == "message"
        and "subtype" not in event
        and not event.get("bot_id")
        and (thread_ts is None or thread_ts == event.get("ts"))
    )


def _invalid_event_field(event: dict[str, Any]) -> str | None:
    """Name of the first of `channel`/`ts`/`text` that is present but not a string, else None.

    Each is optional (a bot message may carry no `text`, for instance), so only a present,
    wrong-typed value is rejected.
    """
    for field in _STRING_EVENT_FIELDS:
        value = event.get(field)
        if value is not None and not isinstance(value, str):
            return f"{field}_not_string"
    return None


def _reply_text(row: StoredRequest, needs_review: bool) -> str:
    classification = row.classification
    assert classification is not None
    lines = [
        f"categoria: {classification.categoria} · prioridad: {classification.prioridad} · "
        f"area_sugerida: {classification.area_sugerida}",
        classification.resumen,
    ]
    if classification.requiere_info and classification.pregunta_seguimiento:
        lines.append(classification.pregunta_seguimiento)
    if needs_review:
        lines.append("Un humano revisará esto.")
    if row.provider == MOCK:
        lines.append(_MOCK_DISCLAIMER)
    return "\n".join(lines)


def _process_message(
    service: TriageService,
    notifier: SlackNotifier,
    channel_areas: dict[str, str],
    event_id: str,
    event: dict[str, Any],
) -> None:
    channel, thread_ts = event.get("channel"), event.get("ts")
    try:
        req = RequestInput(
            id=f"slack-{event_id}",
            message=event.get("text") or "",
            source_area=channel_areas.get(channel) if channel else None,
        )
    except ValidationError as exc:
        field = str(exc.errors()[0]["loc"][0])
        if field == "message":
            log_event(logger, "slack_validation_failed", event_id=event_id)
            notifier.reply(channel, thread_ts, _LIMIT_TEXT, event_id)
        else:
            # e.g. a malformed event id or an out-of-contract source_area: no reply, since there
            # is nothing the sender in Slack can do about either.
            log_event(logger, "slack_event_rejected", event_id=event_id, field=field)
        return
    try:
        row, created = service.create(req)
    except IdConflict:
        log_event(logger, "slack_id_conflict", event_id=event_id)
        return
    except InProgress:
        return  # the worker already classifying this event replies
    except _RETRYABLE_ERRORS as exc:
        log_event(
            logger, "slack_classification_failed", event_id=event_id, exc_type=type(exc).__name__
        )
        notifier.reply(channel, thread_ts, _RETRY_TEXT, event_id)
        return
    except Exception as exc:  # any other DomainError or crash: never re-raise from the task
        log_event(logger, "slack_error", event_id=event_id, exc_type=type(exc).__name__)
        return
    if created:
        notifier.reply(channel, thread_ts, _reply_text(row, service.needs_review(row)), event_id)


def _run_process_message(
    service: TriageService,
    notifier: SlackNotifier,
    channel_areas: dict[str, str],
    event_id: str,
    event: dict[str, Any],
) -> None:
    """Background-task entry point: a catch-all so any unexpected internal error that
    `_process_message` does not already handle cannot raise into Starlette's background runner."""
    try:
        _process_message(service, notifier, channel_areas, event_id, event)
    except Exception as exc:
        log_event(logger, "slack_task_failed", event_id=event_id, exc_type=type(exc).__name__)


def build_slack_router(
    service: TriageService,
    notifier: SlackNotifier,
    verifier: SlackVerifier,
    channel_areas: dict[str, str],
) -> APIRouter:
    router = APIRouter(tags=["slack"])

    @router.post("/slack/events", include_in_schema=False)
    async def slack_events(request: Request, background_tasks: BackgroundTasks) -> Response:
        try:
            raw_body = await _read_capped_body(request, _MAX_BODY_BYTES)
        except _PayloadTooLarge:
            return http_errors.error_response(
                413, "payload_too_large", f"request body larger than {_MAX_BODY_BYTES} bytes"
            )
        try:
            verifier.verify(request.headers, raw_body, time.time())
        except InvalidSignature:
            raise HTTPException(status_code=401, detail="invalid Slack signature") from None
        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        if not isinstance(payload, dict):
            log_event(logger, "slack_event_ignored", reason="payload_not_object")
            return Response(status_code=200)
        if payload.get("type") == "url_verification":
            return JSONResponse({"challenge": payload.get("challenge")})
        if payload.get("type") != "event_callback":
            return Response(status_code=200)
        event = payload.get("event")
        event_id = payload.get("event_id")
        if not isinstance(event, dict):
            log_event(logger, "slack_event_ignored", reason="event_not_object")
            return Response(status_code=200)
        invalid_field = _invalid_event_field(event)
        if invalid_field:
            log_event(logger, "slack_event_ignored", reason=invalid_field)
            return Response(status_code=200)
        if event_id and _is_processable(event):
            background_tasks.add_task(
                _run_process_message, service, notifier, channel_areas, event_id, event
            )
        return Response(status_code=200)

    return router
