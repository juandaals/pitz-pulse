"""POST /slack/events (Spec 06d, X1): verify, filter, classify in the background, reply.

Mounted only when SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN are both set (`api.py`); this route
carries no X-API-Key dependency, the Slack signature is the auth. The handler is `async def`
(documented exception to D5): HMAC verification needs the exact raw body bytes before any
parsing. Classification runs in a FastAPI background task (a plain `def`, so Starlette offloads
it to the thread pool via `anyio.to_thread`) using the app's single `TriageService`, so the event
loop is never blocked and the model-slot semaphore (Spec 02 §2) is shared with `/solicitudes`.
"""

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pitz_pulse.classifier import ClassificationCrash
from pitz_pulse.errors import Busy, ClassificationFailed, DbBusy, IdConflict, InProgress
from pitz_pulse.logs import log_event
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


def _is_processable(event: dict[str, Any]) -> bool:
    """`message`, no subtype (edits/deletes), not a bot, and not a reply inside a thread."""
    thread_ts = event.get("thread_ts")
    return (
        event.get("type") == "message"
        and "subtype" not in event
        and not event.get("bot_id")
        and (thread_ts is None or thread_ts == event.get("ts"))
    )


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
    except ValidationError:
        log_event(logger, "slack_validation_failed", event_id=event_id)
        notifier.reply(channel, thread_ts, _LIMIT_TEXT, event_id)
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


def build_slack_router(
    service: TriageService,
    notifier: SlackNotifier,
    verifier: SlackVerifier,
    channel_areas: dict[str, str],
) -> APIRouter:
    router = APIRouter(tags=["slack"])

    @router.post("/slack/events", include_in_schema=False)
    async def slack_events(request: Request, background_tasks: BackgroundTasks) -> Response:
        raw_body = await request.body()
        try:
            verifier.verify(request.headers, raw_body, time.time())
        except InvalidSignature:
            raise HTTPException(status_code=401, detail="invalid Slack signature") from None
        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        if payload.get("type") == "url_verification":
            return JSONResponse({"challenge": payload.get("challenge")})
        event = payload.get("event") or {}
        event_id = payload.get("event_id")
        if payload.get("type") == "event_callback" and event_id and _is_processable(event):
            background_tasks.add_task(
                _process_message, service, notifier, channel_areas, event_id, event
            )
        return Response(status_code=200)

    return router
