"""Batch run loop: sliding concurrency window, per-item failure records, credential stop rule."""

import logging
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from pitz_pulse.classifier import ClassificationCrash, ClassificationError, ClassifyOutcome
from pitz_pulse.graph import AttemptRecord
from pitz_pulse.providers.base import CREDENTIAL_ERROR_TYPES

logger = logging.getLogger("pitz_pulse.batch")


@dataclass(frozen=True)
class Failure:
    id: str
    kind: str  # llm_unavailable | llm_rejected | invalid_output | unexpected | cancelled
    attempts: list[AttemptRecord] = field(default_factory=list)


class BatchInterrupted(Exception):
    def __init__(self, in_flight: int):
        super().__init__(in_flight)
        self.in_flight = in_flight


def rejection_summary(failures: list[Failure]) -> str:
    """'APIStatusError:400 ×12' per distinct error type; names the credential only if relevant."""
    types = Counter(
        (f.attempts[-1].error_type if f.attempts else None) or "unknown"
        for f in failures
        if f.kind == "llm_rejected"
    )
    listed = ", ".join(f"{name} ×{count}" for name, count in sorted(types.items()))
    hint = "; check the credential" if CREDENTIAL_ERROR_TYPES & set(types) else ""
    return f"every item was rejected ({listed}); nothing written{hint}"


def _is_credential_rejection(exc: ClassificationError) -> bool:
    last = exc.attempts[-1] if exc.attempts else None
    return (
        exc.kind == "llm_rejected"
        and last is not None
        and (last.error_type in CREDENTIAL_ERROR_TYPES)
    )


def _unexpected(request_id: str, error_type: str, attempts=()) -> Failure:
    logger.error(
        "batch_item_failed",
        extra={"fields": {"message_id": request_id, "error_type": error_type}},
    )
    return Failure(request_id, "unexpected", list(attempts))


def _record(future, request_id, outcomes, failures) -> bool:
    """Store the outcome; return False once a credential rejection means we should stop."""
    try:
        outcomes[request_id] = future.result()
    except CancelledError:
        failures.append(Failure(request_id, "cancelled"))
    except ClassificationError as exc:
        failures.append(Failure(request_id, exc.kind, exc.attempts))
        if _is_credential_rejection(exc):  # every further call would fail the same way
            return False
    except ClassificationCrash as exc:
        failures.append(_unexpected(request_id, exc.error_type, exc.attempts))
    except Exception as exc:  # a classifier bug must not lose the other items
        failures.append(_unexpected(request_id, type(exc).__name__))
    return True


def run_batch(classifier, requests, concurrency: int):
    """Keep at most `concurrency` requests in flight and refill each slot as soon as it
    frees up: a credential rejection (or Ctrl-C) then bounds further spend to whatever the
    pool already holds, and one slow item never stalls the other slots.
    """
    outcomes: dict[str, ClassifyOutcome] = {}
    failures: list[Failure] = []
    pool = ThreadPoolExecutor(max_workers=concurrency)
    pending = iter(requests)
    window: dict = {}
    stopped = interrupted = False

    def fill() -> None:
        while len(window) < concurrency:
            request = next(pending, None)
            if request is None:
                return
            window[pool.submit(classifier.classify, request)] = request.id

    try:
        fill()
        while window:
            done, _ = wait(window, return_when=FIRST_COMPLETED)
            for future in done:
                if not _record(future, window.pop(future), outcomes, failures):
                    stopped = True
            if not stopped:
                fill()
    except KeyboardInterrupt:
        interrupted = True
        in_flight = sum(1 for future in window if future.running())
        raise BatchInterrupted(in_flight) from None
    finally:
        pool.shutdown(wait=not interrupted, cancel_futures=True)
    if stopped:  # requests never submitted: record them as cancelled
        failures.extend(Failure(r.id, "cancelled") for r in pending)
    return outcomes, failures
