"""Batch CLI: classify a golden set into eval/runs/ (never writes resultados.json, D19)."""

import argparse
import os
import statistics
import sys
from datetime import UTC, datetime

from pitz_pulse.batch_run import BatchInterrupted, rejection_summary, run_batch
from pitz_pulse.classifier import build_classifier
from pitz_pulse.config import ConfigError, LLMSettings, load_llm_settings
from pitz_pulse.logs import configure_logging
from pitz_pulse.prompts import PromptError
from pitz_pulse.runs import (
    SETS,
    RunError,
    canonical_sha256,
    ensure_writable,
    load_requests,
    repo_root,
    run_paths,
    run_stem,
    serialize_run,
    sha256_hex,
    write_pair,
)

META_KEYS = (
    "set",
    "input_file",
    "input_sha256",
    "provider",
    "model",
    "billing",
    "prompt_version",
    "prompt_sha256",
    "tool_schema_sha256",
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


def build_meta(
    settings: LLMSettings,
    set_name: str,
    input_bytes: bytes,
    classifier,
    requests,
    outcomes,
    failures,
) -> dict:
    prompt = classifier.prompt
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
        # Tool descriptions carry rubric text outside the prompt file (G14).
        "tool_schema_sha256": canonical_sha256(classifier.tool),
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
        print(f"error: {rejection_summary(failures)}", file=sys.stderr)
        return 1
    items = [
        outcomes[r.id].classification
        for r in sorted(requests, key=lambda r: r.id)
        if r.id in outcomes
    ]
    meta = build_meta(
        settings, args.set_name, input_bytes, classifier, requests, outcomes, failures
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
