"""Idempotent intake (Spec 02 §6): reserve, classify outside any transaction, complete or fail.

A row is left `pending` only when the database stays locked (DbBusy); it is then re-claimed
after the stale window. Every other failure path writes `failed` or answers from the row.
"""

import hashlib
import logging
import math
import time
import uuid
from datetime import datetime, timedelta

from pitz_pulse import db
from pitz_pulse.classifier import ClassificationCrash, ClassificationError, ClassifyOutcome
from pitz_pulse.errors import (
    Busy,
    ClassificationFailed,
    DbBusy,
    DomainError,
    IdConflict,
    InProgress,
)
from pitz_pulse.logs import log_event
from pitz_pulse.repository import StoredRequest
from pitz_pulse.schema import RequestInput

logger = logging.getLogger("pitz_pulse.service")
Answer = tuple[StoredRequest, bool, int]  # row, this call classified it, model attempts
# Set on an unexpected error raised after the model answered, so the outcome counts paid calls.
BILLED_ATTEMPTS = "pitz_billed_attempts"


class IntakeMixin:
    """Mixed into TriageService, which provides the connection, clock, slots and settings."""

    def create(self, req: RequestInput) -> tuple[StoredRequest, bool]:
        start = time.monotonic()
        status, row, attempts, error = 500, None, 0, None
        kind, row_status = None, None
        try:
            row, created, attempts = self._create(req)
            status = 201 if created else 200
            return row, created
        except DomainError as exc:
            status, attempts, error = exc.http_status, exc.attempts, exc.code
            kind, row_status = getattr(exc, "kind", None), exc.row_status
            raise
        except ClassificationCrash as exc:
            attempts, error = len(exc.attempts), "internal_error"
            raise
        except Exception as exc:
            error = type(exc).__name__
            attempts = getattr(exc, BILLED_ATTEMPTS, 0)
            raise
        finally:
            log_event(
                logger,
                "request_outcome",
                id=req.id,
                http_status=status,
                status=row.status if row else row_status,
                error=row.error if row else error,
                kind=kind,
                attempts=attempts,
                latency_ms=round((time.monotonic() - start) * 1000, 1),
            )

    def _create(self, req: RequestInput) -> Answer:
        message_hash = hashlib.sha256(req.message.encode("utf-8")).hexdigest()
        token = uuid.uuid4().hex
        with self.connection() as repo:
            with repo.transaction():
                row = repo.get(req.id)
                now = self.clock()
                if row is None:
                    repo.insert_pending(req, message_hash, token, db.format_ts(now))
                elif row.message_hash != message_hash:
                    raise _with_status(IdConflict(), row.status)
                elif row.status == "classified":
                    return row, False, 0
                elif row.status == "pending" and not self._is_stale(row, now):
                    raise _with_status(InProgress(self._retry_after(row, now)), "pending")
                else:
                    repo.reclaim(req.id, token, db.format_ts(now))
                stored = repo.get(req.id)
        # Retries classify what was stored first (same id + message; stored source_area).
        request = RequestInput(id=stored.id, message=stored.message, source_area=stored.source_area)
        try:
            return self._classify(request, token)
        except DbBusy as exc:  # after the reservation: the row stays pending until it is stale
            exc.retry_after_s = self._retry_after(stored, self.clock())
            exc.row_status = "pending"
            raise

    def _is_stale(self, row: StoredRequest, now: datetime) -> bool:
        cutoff = db.format_ts(now - timedelta(seconds=self.pending_stale_s))
        return row.updated_at < cutoff

    def _retry_after(self, row: StoredRequest, now: datetime) -> int:
        age = (now - db.parse_ts(row.updated_at)).total_seconds()
        return max(1, math.ceil(self.pending_stale_s - age))

    def _classify(self, req: RequestInput, token: str) -> Answer:
        if not self._acquire_slot():
            answer = self._fail(req.id, token, "busy", attempts=0)
            if answer is not None:
                return answer
            raise _with_status(Busy(), "failed")
        attempts = 0
        try:
            try:
                outcome = self.classifier.classify(req)
            finally:
                self._release_slot()
            attempts = len(outcome.attempts)
            duplicate_of = self._detect_duplicate(req, outcome.classification.idioma)
            return self._complete(req.id, token, outcome, duplicate_of)
        except ClassificationError as exc:
            attempts = len(exc.attempts)
            answer = self._fail(req.id, token, exc.kind, attempts)
            if answer is not None:
                return answer
            failed = _with_status(ClassificationFailed(exc.kind), "failed")
            failed.attempts = attempts
            raise failed from None
        except DomainError:
            raise  # DbBusy from complete, or the current row's answer after a lost claim
        except Exception as exc:  # a crash in classify or an unexpected error in complete
            if isinstance(exc, ClassificationCrash):
                attempts = len(exc.attempts)
            answer = self._best_effort_fail(req.id, token, attempts)
            if answer is not None:
                return answer
            setattr(exc, BILLED_ATTEMPTS, attempts)
            raise

    def _detect_duplicate(self, req: RequestInput, idioma: str) -> str | None:
        """Read-only, best-effort: never raises, so a check failure never fails the request."""
        try:
            with self.connection() as repo, repo.read_transaction():
                candidates = repo.list_recent_classified(
                    idioma, req.id, self.duplicate_detector.window
                )
            return self.duplicate_detector.find(req.message, candidates)
        except Exception as exc:
            log_event(logger, "duplicate_check_failed", id=req.id, exc_type=type(exc).__name__)
            return None

    def _complete(
        self,
        request_id: str,
        token: str,
        outcome: ClassifyOutcome,
        possible_duplicate_of: str | None,
    ) -> Answer:
        attempts = len(outcome.attempts)
        adapter = self.classifier.adapter
        for delay in (*self.complete_backoff_s, None):
            try:
                with self.connection() as repo:
                    with repo.transaction():
                        done = repo.complete(
                            request_id,
                            token,
                            outcome.classification,
                            adapter.provider,
                            adapter.model,
                            self._now(),
                            possible_duplicate_of=possible_duplicate_of,
                        )
                        row = repo.get(request_id)
                break
            except DbBusy as exc:
                if delay is None:
                    log_event(logger, "request_complete_failed", id=request_id, exc_type="DbBusy")
                    exc.attempts = attempts
                    raise
                time.sleep(delay)
        if done:
            return row, True, attempts
        return self._answer_lost_claim(row, attempts)

    def _record_failure(
        self, request_id: str, token: str, kind: str, attempts: int
    ) -> Answer | None:
        """None if the failure was recorded; else the current row answers (lost claim)."""
        with self.connection() as repo:
            with repo.transaction():
                done = repo.fail(request_id, token, kind, self._now())
                row = repo.get(request_id)
        if done:
            return None
        return self._answer_lost_claim(row, attempts)

    def _fail(self, request_id: str, token: str, kind: str, attempts: int) -> Answer | None:
        """Classification failure or busy: a locked database surfaces as DbBusy (row pending)."""
        try:
            return self._record_failure(request_id, token, kind, attempts)
        except DbBusy as exc:
            log_event(logger, "request_fail_write_failed", id=request_id, exc_type="DbBusy")
            exc.attempts = attempts
            raise

    def _best_effort_fail(self, request_id: str, token: str, attempts: int) -> Answer | None:
        """After an unexpected error: mark the row internal_error; the error itself is reported.

        A lost claim still answers from the current row (InProgress/ClassificationFailed raise).
        """
        try:
            return self._record_failure(request_id, token, "internal_error", attempts)
        except (InProgress, ClassificationFailed):
            raise
        except Exception as exc:  # DbBusy or any other write error: log it, report the original
            log_event(
                logger, "request_fail_write_failed", id=request_id, exc_type=type(exc).__name__
            )
            return None

    def _answer_lost_claim(self, row: StoredRequest, attempts: int) -> Answer:
        log_event(logger, "request_claim_lost", id=row.id, status=row.status)
        if row.status == "classified":
            return row, False, attempts
        if row.status == "pending":
            error: DomainError = InProgress(self._retry_after(row, self.clock()))
        else:
            error = ClassificationFailed(row.error or "internal_error")
        error.attempts = attempts
        error.row_status = row.status
        raise error


def _with_status(error: DomainError, row_status: str) -> DomainError:
    error.row_status = row_status
    return error
