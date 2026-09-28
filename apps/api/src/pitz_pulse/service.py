"""TriageService: connections, model slots and reads (Spec 02 §2, §4); intake lives in intake.py."""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pitz_pulse import db
from pitz_pulse.classifier import Classifier
from pitz_pulse.corrections import apply_correction
from pitz_pulse.duplicates import DuplicateDetector
from pitz_pulse.errors import DbBusy, NotFound
from pitz_pulse.intake import IntakeMixin
from pitz_pulse.repository import ListFilters, Repository, StoredRequest
from pitz_pulse.review import needs_review
from pitz_pulse.settings_api import DEFAULT_DUPLICATE_THRESHOLD, QUEUE_WAIT_S


def utc_now() -> datetime:
    return datetime.now(UTC)


class TriageService(IntakeMixin):
    def __init__(
        self,
        db_path: Path,
        classifier: Classifier,
        *,
        threshold: float,
        pending_stale_s: int,
        queue_wait_s: float = QUEUE_WAIT_S,
        clock: Callable[[], datetime] = utc_now,
        complete_backoff_s: tuple[float, ...] = (0.2, 0.4, 0.8),
        duplicate_threshold: float = DEFAULT_DUPLICATE_THRESHOLD,
    ):
        self.db_path = db_path
        self.classifier = classifier
        self.threshold = threshold
        self.pending_stale_s = pending_stale_s
        self.queue_wait_s = queue_wait_s
        self.clock = clock
        self.complete_backoff_s = complete_backoff_s
        self.duplicate_detector = DuplicateDetector(threshold=duplicate_threshold)
        concurrency = classifier.settings.concurrency
        self._slots = threading.BoundedSemaphore(concurrency)
        self._max_waiters = 2 * concurrency
        self._waiters = 0
        self._waiters_lock = threading.Lock()

    # -- connections -------------------------------------------------------------------

    @contextmanager
    def connection(self) -> Iterator[Repository]:
        try:
            conn = db.connect(self.db_path)  # a lock while connecting is DbBusy too
            try:
                yield Repository(conn)
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc):
                raise DbBusy() from None
            raise

    def _now(self) -> str:
        return db.format_ts(self.clock())

    # -- model slots -------------------------------------------------------------------

    def _acquire_slot(self) -> bool:
        """Take a model slot; False when the queue is full or the wait times out (busy)."""
        with self._waiters_lock:
            # The fast path only when nobody queues: new arrivals never overtake waiters.
            if self._waiters == 0 and self._slots.acquire(blocking=False):
                return True
            if self._waiters >= self._max_waiters:
                return False
            self._waiters += 1
        try:
            return self._slots.acquire(timeout=self.queue_wait_s)
        finally:
            with self._waiters_lock:
                self._waiters -= 1

    def _release_slot(self) -> None:
        self._slots.release()

    # -- reads -------------------------------------------------------------------------

    def get(self, request_id: str) -> tuple[StoredRequest, list[dict[str, Any]]]:
        with self.connection() as repo, repo.read_transaction():
            row = repo.get(request_id)
            if row is None:
                raise NotFound()
            return row, repo.list_corrections(request_id)

    def list_requests(
        self, filters: ListFilters, limit: int, offset: int
    ) -> tuple[list[StoredRequest], int]:
        with self.connection() as repo:
            return repo.list_requests(filters, self.threshold, limit, offset)

    def needs_review(self, row: StoredRequest) -> bool:
        return (
            row.status == "classified"
            and not row.reviewed
            and needs_review(row.classification.confianza, self.threshold)
        )

    # -- corrections -------------------------------------------------------------------

    def correct(
        self, request_id: str, changes: dict[str, Any], author: str, reason: str | None
    ) -> StoredRequest:
        with self.connection() as repo:
            return apply_correction(repo, request_id, changes, author, reason, self._now())
