"""CLI `python -m pitz_pulse.model_compare --runs STEM [STEM ...]` (Spec 06 §06b, D29).

Builds a cost-vs-quality table from run files already on disk; makes no model call and writes
nothing (it never touches `/resultados.json`). Only APP_ROOT and CONFIDENCE_THRESHOLD are read
from env — never builds `LLMSettings`, so a broken LLM_PROVIDER/credential setup never blocks a
comparison. Structural integrity is `evaluate.load_run`'s job; this module only adds the per-set
labels and the per-message arithmetic.
"""

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pitz_pulse.config import resolve_app_root
from pitz_pulse.evaluate import _threshold, load_run
from pitz_pulse.labels import LABEL_FILES, SCORED_FIELDS, LabelError, load_labels
from pitz_pulse.models_catalog import MOCK
from pitz_pulse.runs import RunError, repo_root
from pitz_pulse.scoring import score

MOCK_HEADER = "# MOCK ROWS PRESENT — NOT MODEL QUALITY"
_META_NUMBERS = (
    "total_input_tokens",
    "total_output_tokens",
    "total_cost_usd",
    "total_equivalent_api_cost_usd",
    "attempts_total",
)


@dataclass(frozen=True)
class Row:
    stem: str
    provider: str
    model: str
    temperature: float | None
    set: str
    scored: int
    field_accuracy: dict[str, tuple[int, int]]
    exact_match: tuple[int, int]
    mean_input_tokens: float
    mean_output_tokens: float
    cost_per_message_usd: float
    equivalent_cost_per_message_usd: float
    attempts_per_message: float
    p50_latency_ms: float | None
    mock: bool


def _meta_number(meta, stem: str, name: str) -> float:
    value = getattr(meta, name, None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RunError(f"{stem}: meta.{name} is missing or not a number")
    return value


def _p50(meta, stem: str) -> float | None:
    value = getattr(meta, "p50_latency_ms_per_message", None)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RunError(f"{stem}: meta.p50_latency_ms_per_message is not a number")
    return value


def build_row(app_root: Path, stem: str, threshold: float) -> Row:
    """Load, score and reduce one run to a table row. Raises RunError/LabelError, never crashes."""
    _run_bytes, results, meta = load_run(app_root, stem)
    if meta.set not in LABEL_FILES:
        raise RunError(f"unknown set {meta.set!r} in meta for {stem}")
    if meta.n <= 0:
        raise RunError(f"{stem}: meta.n is not positive")
    label_path = repo_root(app_root) / LABEL_FILES[meta.set]
    labels = load_labels(label_path)
    report = score(labels, results, threshold)
    totals = {name: _meta_number(meta, stem, name) for name in _META_NUMBERS}
    n = meta.n
    return Row(
        stem=stem,
        provider=meta.provider,
        model=meta.model,
        temperature=meta.temperature,
        set=meta.set,
        scored=report.scored,
        field_accuracy=report.field_accuracy,
        exact_match=report.exact_match,
        mean_input_tokens=totals["total_input_tokens"] / n,
        mean_output_tokens=totals["total_output_tokens"] / n,
        cost_per_message_usd=totals["total_cost_usd"] / n,
        equivalent_cost_per_message_usd=totals["total_equivalent_api_cost_usd"] / n,
        attempts_per_message=totals["attempts_total"] / n,
        p50_latency_ms=_p50(meta, stem),
        mock=meta.provider == MOCK,
    )


def _temp(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _pct(pair: tuple[int, int]) -> str:
    correct, total = pair
    return "n/a" if total == 0 else f"{round(100 * correct / total)}%"


def _p50_cell(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


_COLUMNS = (
    "model",
    "provider",
    "temperature",
    *SCORED_FIELDS,
    "exact match",
    "in/out tokens/msg",
    "cost/msg (incl. retries)",
    "equivalent API cost/msg",
    "attempts/msg",
    "p50 ms",
)


def _row_cells(row: Row) -> list[str]:
    return [
        row.model,
        row.provider,
        _temp(row.temperature),
        *(_pct(row.field_accuracy[name]) for name in SCORED_FIELDS),
        _pct(row.exact_match),
        f"{row.mean_input_tokens:.1f}/{row.mean_output_tokens:.1f}",
        f"${row.cost_per_message_usd:.4f}",
        f"${row.equivalent_cost_per_message_usd:.4f}",
        f"{row.attempts_per_message:.2f}",
        _p50_cell(row.p50_latency_ms),
    ]


def to_markdown(rows: list[Row]) -> str:
    """One table per set (case / edge); never recommends routing (Spec 06 §06b)."""
    lines: list[str] = []
    if any(row.mock for row in rows):
        lines += [MOCK_HEADER, ""]
    for set_name in dict.fromkeys(row.set for row in rows):
        set_rows = [row for row in rows if row.set == set_name]
        lines += [
            f"## {set_name} set",
            "",
            "| " + " | ".join(_COLUMNS) + " |",
            "| " + " | ".join("---" for _ in _COLUMNS) + " |",
        ]
        lines += ["| " + " | ".join(_row_cells(row)) + " |" for row in set_rows]
        lines += [
            "",
            f"N = {set_rows[0].scored} per set; differences within the noise floor of two "
            "identical runs (`make compare`) are not meaningful.",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m pitz_pulse.model_compare")
    parser.add_argument("--runs", required=True, nargs="+", dest="stems")
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
        threshold = _threshold(None, env)
        app_root = resolve_app_root(env)
        rows = [build_row(app_root, stem, threshold) for stem in args.stems]
    except (RunError, LabelError) as exc:
        return _fail(str(exc))

    print(to_markdown(rows), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
