"""CLI `python -m pitz_pulse.evaluate --run <stem> [--compare <stem>] [--threshold T]`.

Never builds `LLMSettings` (spec Sec 5): only APP_ROOT and CONFIDENCE_THRESHOLD are read from
env, so a broken LLM_PROVIDER/credential setup never blocks an eval. `load_run` and `RunMeta`
are shared with `promote` (Task 4) so neither can crash with a traceback on a malformed meta.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from pitz_pulse.config import DEFAULT_APP_ROOT
from pitz_pulse.labels import LABEL_FILES, Label, LabelError, load_labels
from pitz_pulse.runs import SETS, RunError, repo_root, run_paths, sha256_hex
from pitz_pulse.schema import ClassificationShape
from pitz_pulse.scoring import EvalReport, compare_runs, score

MOCK_HEADER = "# MOCK RUN — NOT MODEL QUALITY"
DEFAULT_THRESHOLD = 0.7


class RunMeta(BaseModel):
    """Only the keys the checks read; everything else a run meta carries is passed through."""

    model_config = ConfigDict(extra="allow")

    set: str
    provider: str
    model: str
    prompt_version: str
    prompt_sha256: str
    tool_schema_sha256: str | None
    temperature: Annotated[float, Field(strict=True)] | None
    n: StrictInt
    failures: list
    input_sha256: str
    results_sha256: str


def _ref(item: object, index: int) -> object:
    if isinstance(item, dict) and isinstance(item.get("id"), str):
        return item["id"]
    return index


def _validation_reason(exc: ValidationError) -> str:
    return ", ".join(".".join(map(str, e["loc"])) for e in exc.errors(include_input=False))


def _load_meta(meta_path: Path) -> RunMeta:
    try:
        text = meta_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RunError(f"{meta_path.name} is not valid UTF-8") from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        raise RunError(f"{meta_path.name} is not valid JSON") from None
    if not isinstance(raw, dict):
        raise RunError(f"{meta_path.name} must be a JSON object")
    try:
        return RunMeta.model_validate(raw)
    except ValidationError as exc:
        raise RunError(f"invalid run meta ({meta_path.name}): {_validation_reason(exc)}") from None


def _load_results(run_path: Path, run_bytes: bytes, meta: RunMeta) -> list[ClassificationShape]:
    try:
        items = json.loads(run_bytes.decode("utf-8"))
    except UnicodeDecodeError:
        raise RunError(f"{run_path.name} is not valid UTF-8") from None
    except json.JSONDecodeError:
        raise RunError(f"{run_path.name} is not valid JSON") from None
    if not isinstance(items, list):
        raise RunError(f"{run_path.name} must be a JSON list of results")
    if len(items) != meta.n:
        raise RunError(f"{run_path.name} has {len(items)} item(s); meta.n is {meta.n}")
    results: list[ClassificationShape] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        try:
            result = ClassificationShape.model_validate(item)
        except ValidationError as exc:
            raise RunError(
                f"invalid result item {_ref(item, index)}: {_validation_reason(exc)}"
            ) from None
        if result.id in seen:
            raise RunError(f"duplicate id {result.id}")
        seen.add(result.id)
        if result.version_prompt != meta.prompt_version:
            raise RunError(f"item {result.id} version_prompt does not match meta.prompt_version")
        results.append(result)
    return results


def load_run(app_root: Path, stem: str) -> tuple[bytes, list[ClassificationShape], RunMeta]:
    """Structural integrity only: file presence, meta shape, hash, length, ids, version_prompt."""
    run_path, meta_path = run_paths(app_root, stem)
    if not meta_path.is_file():
        raise RunError(f"{meta_path.name} does not exist")
    if not run_path.is_file():
        raise RunError(f"{run_path.name} does not exist")
    meta = _load_meta(meta_path)
    run_bytes = run_path.read_bytes()
    if sha256_hex(run_bytes) != meta.results_sha256:
        raise RunError(f"{run_path.name} does not match meta.results_sha256")
    results = _load_results(run_path, run_bytes, meta)
    return run_bytes, results, meta


def _resolve(
    app_root: Path, stem: str
) -> tuple[list[ClassificationShape], RunMeta, list[Label], Path]:
    """load_run plus the set-dependent checks promote does not share (SETS/labels lookup)."""
    _run_bytes, results, meta = load_run(app_root, stem)
    if meta.set not in SETS:
        raise RunError(f"unknown set {meta.set!r} in meta for {stem}")
    root = repo_root(app_root)
    input_path = root / SETS[meta.set]
    try:
        input_bytes = input_path.read_bytes()
    except OSError:
        raise RunError(f"could not read {SETS[meta.set]}") from None
    if sha256_hex(input_bytes) != meta.input_sha256:
        raise RunError(f"meta.input_sha256 does not match the current {SETS[meta.set]}")
    label_path = root / LABEL_FILES[meta.set]
    labels = load_labels(label_path)
    return results, meta, labels, label_path


def _threshold(raw: str | None, env: Mapping[str, str]) -> float:
    text = raw if raw is not None else env.get("CONFIDENCE_THRESHOLD")
    if text is None or not text.strip():
        return DEFAULT_THRESHOLD
    try:
        value = float(text)
    except ValueError:
        raise RunError("threshold must be a number") from None
    if not 0 <= value <= 1:
        raise RunError("threshold must be between 0 and 1")
    return value


def _sha_prefix(value: str | None) -> str:
    return "none" if value is None else value[:12]


def _diff_section(compare_stem, meta, other, results, other_results) -> str:
    prompt_a, prompt_b = _sha_prefix(meta.prompt_sha256), _sha_prefix(other.prompt_sha256)
    tool_a, tool_b = _sha_prefix(meta.tool_schema_sha256), _sha_prefix(other.tool_schema_sha256)
    lines = [
        f"### Diff vs {compare_stem}",
        "| field | this run | other run |",
        "| --- | --- | --- |",
        f"| model | {meta.model} | {other.model} |",
        f"| prompt_sha256 | {prompt_a} | {prompt_b} |",
        f"| tool_schema_sha256 | {tool_a} | {tool_b} |",
        "",
        "| id | field | this run | other run |",
        "| --- | --- | --- | --- |",
    ]
    for diff in compare_runs(results, other_results):
        lines.append(f"| {diff.id} | {diff.field} | {diff.a} | {diff.b} |")
    return "\n".join(lines)


def _header(stem: str, meta: RunMeta, label_path: Path, report: EvalReport) -> str:
    temperature = "none" if meta.temperature is None else meta.temperature
    label_sha = sha256_hex(label_path.read_bytes())[:12]
    return (
        f"set {meta.set} · run {stem} · provider {meta.provider} · model {meta.model} · "
        f"temperature {temperature} · labels {label_sha} · {report.scored} scored"
    )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m pitz_pulse.evaluate")
    parser.add_argument("--run", required=True, dest="stem")
    parser.add_argument("--compare", dest="compare_stem")
    parser.add_argument("--threshold", dest="threshold")
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
        threshold = _threshold(args.threshold, env)
        app_root = Path(env.get("APP_ROOT") or DEFAULT_APP_ROOT).resolve()
        results, meta, labels, label_path = _resolve(app_root, args.stem)
        report = score(labels, results, threshold)
        diff_section = None
        if args.compare_stem:
            other_results, other_meta, _other_labels, _other_label_path = _resolve(
                app_root, args.compare_stem
            )
            if meta.set != other_meta.set or meta.input_sha256 != other_meta.input_sha256:
                raise RunError("compare run has a different set or input_sha256")
            diff_section = _diff_section(
                args.compare_stem, meta, other_meta, results, other_results
            )
    except (RunError, LabelError) as exc:
        return _fail(str(exc))

    output = []
    if meta.provider == "mock":
        output.append(MOCK_HEADER)
    output.append(report.to_markdown(_header(args.stem, meta, label_path, report)).rstrip("\n"))
    if diff_section:
        output.append(diff_section)
    print("\n\n".join(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
