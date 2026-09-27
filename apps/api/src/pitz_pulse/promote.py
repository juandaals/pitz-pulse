"""CLI `python -m pitz_pulse.promote --run <stem> [--allow-mock] [--force]` (spec Sec 5, D23/D31).

The only writer of /resultados.json (D19). Never builds `LLMSettings`: reads only APP_ROOT and
config constants. Every rule failure exits 2 with a reason, never a traceback. The "no suffix"
rule compares the CLI stem with the stem rebuilt from meta; meta records no suffix, so a
suffixed run file renamed by hand to the plain stem is not caught.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from pitz_pulse.config import ACTIVE_PROMPT_VERSION, DEFAULT_APP_ROOT, DEFAULT_MODEL
from pitz_pulse.evaluate import RunMeta, load_run
from pitz_pulse.models_catalog import ANTHROPIC_API, CLAUDE_AGENT_SDK, MOCK, lookup
from pitz_pulse.prompts import PromptError, load_prompt
from pitz_pulse.runs import (
    SETS,
    RunError,
    canonical_sha256,
    load_requests,
    repo_root,
    run_stem,
    sha256_hex,
    write_pair,
)
from pitz_pulse.schema import Classification, ClassificationShape
from pitz_pulse.tool_schema import build_tool_schema

RESULTS_FILE = "resultados.json"
RESULTS_META_FILE = "resultados.meta.json"


def _check_provider(meta: RunMeta, allow_mock: bool) -> None:
    try:
        caps = lookup(meta.provider, meta.model)
    except KeyError:
        raise RunError(f"({meta.provider}, {meta.model}) is not in the models catalog") from None
    if meta.provider == CLAUDE_AGENT_SDK:
        raise RunError("claude_agent_sdk runs are never promotable")
    if meta.provider == MOCK:
        if not allow_mock:
            raise RunError("mock runs are promoted only with --allow-mock")
    elif meta.provider == ANTHROPIC_API:
        if meta.model != DEFAULT_MODEL:
            raise RunError(f"model must be {DEFAULT_MODEL}, got {meta.model}")
        if meta.temperature is None or meta.temperature != 0:
            raise RunError("temperature must be the number 0")
    else:
        raise RunError(f"provider {meta.provider} is not promotable")
    expected_tool = canonical_sha256(build_tool_schema(strict=caps.supports_strict))
    if meta.tool_schema_sha256 != expected_tool:
        raise RunError("meta.tool_schema_sha256 does not match the current tool schema")


def _check_contents(
    app_root: Path, stem: str, results: list[ClassificationShape], meta: RunMeta
) -> None:
    if meta.set != "case":
        raise RunError(f"only set case is promotable, got set {meta.set}")
    if stem != run_stem(meta.set, meta.prompt_version, meta.provider, meta.model):
        raise RunError("run stem has a suffix or does not match its meta")
    if meta.failures:
        raise RunError(f"meta.failures is not empty ({len(meta.failures)})")
    input_path = repo_root(app_root) / SETS["case"]
    expected_ids = sorted(request.id for request in load_requests(input_path))
    if sorted(result.id for result in results) != expected_ids:
        raise RunError(f"result ids do not match the ids of {SETS['case']}")
    if sha256_hex(input_path.read_bytes()) != meta.input_sha256:
        raise RunError(f"meta.input_sha256 does not match the current {SETS['case']}")
    for result in results:
        try:
            Classification.model_validate(result.model_dump())
        except ValidationError:
            raise RunError(f"item {result.id} violates the contract rules") from None


def _check_prompt(app_root: Path, meta: RunMeta) -> None:
    if meta.prompt_version != ACTIVE_PROMPT_VERSION:
        raise RunError(
            f"prompt_version {meta.prompt_version} is not the active prompt version "
            f"{ACTIVE_PROMPT_VERSION}"
        )
    try:
        prompt = load_prompt(app_root, meta.prompt_version)
    except PromptError as exc:
        raise RunError(str(exc)) from None
    if meta.prompt_sha256 != prompt.sha256:
        raise RunError(f"meta.prompt_sha256 does not match prompts/{meta.prompt_version}.md")


def _existing_is_mock(meta_path: Path) -> bool:
    """Anything but a readable meta with `mock` exactly true counts as a real result."""
    if not meta_path.exists():
        return True
    try:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(existing, dict) and existing.get("mock") is True


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m pitz_pulse.promote")
    parser.add_argument("--run", required=True, dest="stem")
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        app_root = Path(env.get("APP_ROOT") or DEFAULT_APP_ROOT).resolve()
        run_bytes, results, meta = load_run(app_root, args.stem)
        _check_provider(meta, args.allow_mock)
        _check_contents(app_root, args.stem, results, meta)
        _check_prompt(app_root, meta)
        root = repo_root(app_root)
        results_path, meta_path = root / RESULTS_FILE, root / RESULTS_META_FILE
        is_mock = meta.provider == MOCK
        if is_mock and not args.force and not _existing_is_mock(meta_path):
            raise RunError(f"{RESULTS_META_FILE} holds a real result; use --force to replace it")
    except RunError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(f"could not read the run inputs ({type(exc).__name__})")

    promoted = {
        **meta.model_dump(mode="json"),
        "promoted_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source_run": args.stem,
        "mock": is_mock,
    }
    try:
        write_pair(results_path, meta_path, run_bytes, promoted)
    except OSError as exc:
        return _fail(f"could not write {RESULTS_FILE} ({type(exc).__name__})")
    print(f"promoted {args.stem} -> {RESULTS_FILE} (mock: {str(is_mock).lower()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
