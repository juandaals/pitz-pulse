"""Pure scoring for the golden-set evaluation (spec Sec 5-7). No I/O here.

`evaluate.py` (a separate task) owns files, printing and exit codes; this module only compares
already-parsed labels and results and builds the report data.
"""

from dataclasses import dataclass

from pydantic import ValidationError

from pitz_pulse.labels import SCORED_FIELDS, Label
from pitz_pulse.review import needs_review
from pitz_pulse.schema import Classification, ClassificationShape

MISSING = "<missing>"
THRESHOLDS = tuple(round(0.50 + 0.05 * i, 2) for i in range(9))


@dataclass(frozen=True)
class Failure:
    id: str
    field: str
    expected: str
    got: str
    confianza: float | None


@dataclass(frozen=True)
class SweepRow:
    threshold: float
    routed: int
    wrong_caught: int
    wrong_total: int


@dataclass(frozen=True)
class Diff:
    id: str
    field: str
    a: str
    b: str


def _ratio(correct: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{correct}/{total} ({round(100 * correct / total)}%)"


def _fmt(value: object) -> str:
    """JSON-style `true`/`false` for booleans; plain `str()` otherwise (enums render as-is)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@dataclass(frozen=True)
class EvalReport:
    scored: int
    not_scored_draft: list[str]
    field_accuracy: dict[str, tuple[int, int]]
    exact_match: tuple[int, int]
    failures: list[Failure]
    missing_ids: list[str]
    extra_ids: list[str]
    rule_violations: list[tuple[str, str]]
    per_message_confidence: list[tuple[str, float, bool]]
    routed_at_threshold: int
    threshold: float
    sweep: list[SweepRow]

    def to_markdown(self, header: str) -> str:
        lines = [f"## Eval — {header}", "", "| field | accuracy |", "| --- | --- |"]
        for name in SCORED_FIELDS:
            lines.append(f"| {name} | {_ratio(*self.field_accuracy[name])} |")
        lines.append(f"| all 5 fields | {_ratio(*self.exact_match)} |")

        lines += [
            "",
            f"### Not scored (draft): {', '.join(self.not_scored_draft) or 'none'}",
            f"### Missing: {', '.join(self.missing_ids) or 'none'}",
            f"### Extra ids: {', '.join(self.extra_ids) or 'none'}",
        ]

        lines += [
            "",
            "### Failures",
            "| id | field | expected | got | confianza |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in self.failures:
            conf = "n/a" if f.confianza is None else f"{f.confianza:.2f}"
            lines.append(f"| {f.id} | {f.field} | {f.expected} | {f.got} | {conf} |")

        lines += ["", "### Rule violations", "| id | rule |", "| --- | --- |"]
        for rule_id, msg in self.rule_violations:
            lines.append(f"| {rule_id} | {msg} |")

        lines += [
            "",
            "### Confidence",
            "| id | confianza | all-fields correct |",
            "| --- | --- | --- |",
        ]
        for msg_id, conf, ok in self.per_message_confidence:
            lines.append(f"| {msg_id} | {conf:.2f} | {'yes' if ok else 'no'} |")

        lines += [
            "",
            f"### Threshold sweep (routed at {self.threshold}: {self.routed_at_threshold})",
            "| threshold | routed to review | wrong caught | catch rate |",
            "| --- | --- | --- | --- |",
        ]
        for row in self.sweep:
            caught = f"{row.wrong_caught}/{row.wrong_total}" if row.wrong_total else "n/a"
            rate = _ratio(row.wrong_caught, row.wrong_total)
            lines.append(f"| {row.threshold:.2f} | {row.routed} | {caught} | {rate} |")

        confidences = [conf for _, conf, _ in self.per_message_confidence]
        if confidences and all(c == confidences[0] for c in confidences):
            lines += [
                "",
                "Degenerate: all confianza values are equal; the sweep carries no "
                "calibration signal.",
            ]

        return "\n".join(lines) + "\n"


def score(labels: list[Label], results: list[ClassificationShape], threshold: float) -> EvalReport:
    results_by_id = {r.id: r for r in results}
    label_ids = {label.id for label in labels}
    not_scored_draft = sorted(label.id for label in labels if label.label_status == "draft")
    approved = [label for label in labels if label.label_status == "approved"]
    extra_ids = sorted(rid for rid in results_by_id if rid not in label_ids)
    scored_total = len(approved)

    field_correct = dict.fromkeys(SCORED_FIELDS, 0)
    exact_correct = 0
    failures: list[Failure] = []
    missing_ids: list[str] = []
    per_message_confidence: list[tuple[str, float, bool]] = []

    for label in approved:
        result = results_by_id.get(label.id)
        if result is None:
            missing_ids.append(label.id)
            for name in SCORED_FIELDS:
                failures.append(Failure(label.id, name, _fmt(getattr(label, name)), MISSING, None))
            continue
        all_correct = True
        for name in SCORED_FIELDS:
            expected, got = getattr(label, name), getattr(result, name)
            if expected == got:
                field_correct[name] += 1
            else:
                all_correct = False
                failures.append(
                    Failure(label.id, name, _fmt(expected), _fmt(got), result.confianza)
                )
        if all_correct:
            exact_correct += 1
        per_message_confidence.append((label.id, result.confianza, all_correct))

    rule_violations: list[tuple[str, str]] = []
    for result in results:
        try:
            Classification.model_validate(result.model_dump())
        except ValidationError as exc:
            rule_violations.append((result.id, exc.errors(include_input=False)[0]["msg"]))

    routed_at_threshold = sum(
        1 for _, conf, _ in per_message_confidence if needs_review(conf, threshold)
    )
    sweep = threshold_sweep([(conf, not ok) for _, conf, ok in per_message_confidence])

    return EvalReport(
        scored=scored_total,
        not_scored_draft=not_scored_draft,
        field_accuracy={name: (field_correct[name], scored_total) for name in SCORED_FIELDS},
        exact_match=(exact_correct, scored_total),
        failures=failures,
        missing_ids=sorted(missing_ids),
        extra_ids=extra_ids,
        rule_violations=rule_violations,
        per_message_confidence=per_message_confidence,
        routed_at_threshold=routed_at_threshold,
        threshold=threshold,
        sweep=sweep,
    )


def threshold_sweep(pairs: list[tuple[float, bool]]) -> list[SweepRow]:
    wrong_total = sum(1 for _, wrong in pairs if wrong)
    rows = []
    for t in THRESHOLDS:
        routed = sum(1 for conf, _ in pairs if needs_review(conf, t))
        wrong_caught = sum(1 for conf, wrong in pairs if wrong and needs_review(conf, t))
        rows.append(SweepRow(t, routed, wrong_caught, wrong_total))
    return rows


def compare_runs(a: list[ClassificationShape], b: list[ClassificationShape]) -> list[Diff]:
    a_by_id = {r.id: r for r in a}
    b_by_id = {r.id: r for r in b}
    diffs: list[Diff] = []
    for rid in set(a_by_id) | set(b_by_id):
        ra, rb = a_by_id.get(rid), b_by_id.get(rid)
        if ra is None or rb is None:
            diffs.append(Diff(rid, "<present>", "yes" if ra else "no", "yes" if rb else "no"))
            continue
        for name in SCORED_FIELDS:
            va, vb = getattr(ra, name), getattr(rb, name)
            if va != vb:
                diffs.append(Diff(rid, name, str(va), str(vb)))
        if round(abs(ra.confianza - rb.confianza), 6) > 0.05:
            diffs.append(Diff(rid, "confianza", f"{ra.confianza:.2f}", f"{rb.confianza:.2f}"))
    return sorted(diffs, key=lambda d: (d.id, d.field))
