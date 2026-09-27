"""Batch CLI: classify a golden set into eval/runs/ (never writes resultados.json, D19)."""

import argparse
import logging
import os
import statistics
import sys
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pitz_pulse.classifier import (
    ClassificationCrash,
    ClassificationError,
    ClassifyOutcome,
    build_classifier,
)
from pitz_pulse.config import ConfigError, LLMSettings, load_llm_settings
from pitz_pulse.graph import AttemptRecord
from pitz_pulse.logs import configure_logging
from pitz_pulse.prompts import PromptError
from pitz_pulse.providers.base import CREDENTIAL_ERROR_TYPES
from pitz_pulse.runs import (
    SETS,
    RunError,
    ensure_writable,
    load_requests,
    repo_root,
    run_paths,
    run_stem,
    serialize_run,
    sha256_hex,
    write_pair,
)

logger = logging.getLogger(__name__)
META_KEYS = (
    "set",
    "input_file",
    "input_sha256",
    "provider",
    "model",
    "billing",
    "prompt_version",
    "prompt_sha256",
    "temperature",
    "invalid_output_retries",
    "llm_max_retries",
    "timeout_s",
    "concurrency",
    "n",
    "n_input",
    "failures",
    "total_input_tokens",
    "total_output_tokens",
    "total_cost_usd",
    "total_equivalent_api_cost_usd",
    "attempts_total",
    "invalid_output_attempts",
    "transport_retries_total",
    "p50_latency_ms_per_message",
    "run_at",
)


@dataclass(frozen=True)
class Failure:
    id: str
    kind: str  # llm_unavailable | llm_rejected | invalid_output | unexpected | cancelled
    attempts: list[AttemptRecord] = field(default_factory=list)


class BatchInterrupted(Exception):
    def __init__(self, in_flight: int):
        super().__init__(in_flight)
        self.in_flight = in_flight


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


def build_meta(
    settings: LLMSettings, set_name: str, input_bytes: bytes, prompt, requests, outcomes, failures
) -> dict:
    attempts = [a for o in outcomes.values() for a in o.attempts]
    attempts += [a for f in failures for a in f.attempts]
    per_message = [sum(a.latency_ms for a in o.attempts) for o in outcomes.values()]
    return {
        "set": set_name,
        "input_file": SETS[set_name],
        "input_sha256": sha256_hex(input_bytes),
        "provider": settings.provider,
        "model": settings.model,
        "billing": settings.caps.billing,
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.sha256,
        "temperature": settings.temperature,
        "invalid_output_retries": settings.invalid_output_retries,
        "llm_max_retries": settings.max_retries,
        "timeout_s": settings.timeout_s,
        "concurrency": settings.concurrency,
        "n": len(outcomes),
        "n_input": len(requests),
        "failures": sorted(({"id": f.id, "kind": f.kind} for f in failures), key=lambda f: f["id"]),
        "total_input_tokens": sum(a.input_tokens for a in attempts),
        "total_output_tokens": sum(a.output_tokens for a in attempts),
        "total_cost_usd": round(sum(a.cost_usd for a in attempts), 6),
        "total_equivalent_api_cost_usd": round(sum(a.equivalent_api_cost_usd for a in attempts), 6),
        "attempts_total": len(attempts),
        "invalid_output_attempts": sum(a.outcome == "invalid_output" for a in attempts),
        "transport_retries_total": sum(a.transport_retries for a in attempts),
        "p50_latency_ms_per_message": (
            round(statistics.median(per_message), 1) if per_message else None
        ),
        "run_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def _parse(argv):
    parser = argparse.ArgumentParser(prog="python -m pitz_pulse.batch")
    parser.add_argument("--set", required=True, dest="set_name")
    parser.add_argument("--suffix")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv=None, settings: LLMSettings | None = None, classifier=None) -> int:
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    except ValueError:
        configure_logging("INFO")
    try:
        settings = settings or load_llm_settings()
        stem = run_stem(
            args.set_name, settings.prompt_version, settings.provider, settings.model, args.suffix
        )
        input_path = repo_root(settings.app_root) / SETS[args.set_name]
        input_bytes = input_path.read_bytes()
        requests = load_requests(input_path)
        run_path, meta_path = run_paths(settings.app_root, stem)
        if run_path.exists() and not args.force:
            return _fail(f"{run_path.name} exists; use --force to overwrite")
        ensure_writable(run_path.parent)
        classifier = classifier or build_classifier(settings)
    except (ConfigError, PromptError, RunError, OSError) as exc:
        return _fail(str(exc))
    worst_case = len(requests) * (1 + settings.invalid_output_retries) * (1 + settings.max_retries)
    print(
        f"classify {args.set_name}: provider={settings.provider} model={settings.model} "
        f"temperature={settings.temperature} calls<={worst_case}"
    )
    try:
        outcomes, failures = run_batch(classifier, requests, settings.concurrency)
    except BatchInterrupted as exc:
        print(
            f"interrupted: nothing written; {exc.in_flight} in-flight call(s) abandoned "
            "(may still be billed)",
            file=sys.stderr,
        )
        return 130
    if not outcomes and all(f.kind in ("llm_rejected", "cancelled") for f in failures):
        print(
            "error: every item was rejected; nothing written (check the credential)",
            file=sys.stderr,
        )
        return 1
    items = [
        outcomes[r.id].classification
        for r in sorted(requests, key=lambda r: r.id)
        if r.id in outcomes
    ]
    meta = build_meta(
        settings, args.set_name, input_bytes, classifier.prompt, requests, outcomes, failures
    )
    run_bytes = serialize_run(items)
    try:
        write_pair(run_path, meta_path, run_bytes, meta)
    except OSError as exc:
        print(
            f"error: could not write run files ({type(exc).__name__}); run follows on stdout",
            file=sys.stderr,
        )
        sys.stdout.write(run_bytes.decode("utf-8"))
        return 1
    print(f"wrote {run_path.name} ({len(items)} ok, {len(failures)} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    if exit_code == 130:
        os._exit(exit_code)  # abandon in-flight worker threads instead of joining them
    sys.exit(exit_code)
