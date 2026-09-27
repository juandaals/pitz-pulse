from pitz_pulse.labels import SCORED_FIELDS, Label
from pitz_pulse.schema import ClassificationShape
from pitz_pulse.scoring import Diff, Failure, compare_runs, score, threshold_sweep

LABEL_DEFAULTS = {
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "requiere_info": False,
    "label_status": "approved",
}
RESULT_DEFAULTS = {
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "resumen": "Resumen corto de prueba",
    "requiere_info": False,
    "pregunta_seguimiento": None,
    "confianza": 0.9,
    "version_prompt": "v1",
}


def make_label(id, **overrides):
    return Label.model_validate({"id": id, **LABEL_DEFAULTS, **overrides})


def make_result(id, **overrides):
    return ClassificationShape.model_validate({"id": id, **RESULT_DEFAULTS, **overrides})


def test_perfect_match_scores_all_fields_correct():
    labels = [make_label("EX-1"), make_label("EX-2")]
    results = [make_result("EX-1"), make_result("EX-2")]

    report = score(labels, results, threshold=0.7)

    assert report.scored == 2
    assert report.exact_match == (2, 2)
    for name in SCORED_FIELDS:
        assert report.field_accuracy[name] == (2, 2)
    assert report.failures == []
    assert report.missing_ids == []
    assert report.extra_ids == []
    assert report.not_scored_draft == []


def test_one_wrong_field_recorded_as_failure():
    labels = [make_label("EX-1")]
    results = [make_result("EX-1", prioridad="baja")]

    report = score(labels, results, threshold=0.7)

    assert report.field_accuracy["prioridad"] == (0, 1)
    assert report.field_accuracy["categoria"] == (1, 1)
    assert report.exact_match == (0, 1)
    assert report.failures == [Failure("EX-1", "prioridad", "alta", "baja", 0.9)]


def test_missing_result_fails_all_five_fields_and_is_excluded_from_confidence():
    labels = [make_label("EX-1")]

    report = score(labels, [], threshold=0.7)

    assert report.missing_ids == ["EX-1"]
    assert len(report.failures) == len(SCORED_FIELDS)
    assert all(f.got == "<missing>" and f.confianza is None for f in report.failures)
    assert report.field_accuracy["categoria"] == (0, 1)
    assert report.exact_match == (0, 1)
    assert report.per_message_confidence == []
    assert report.sweep[0].wrong_total == 0


def test_extra_result_id_not_scored():
    labels = [make_label("EX-1")]
    results = [make_result("EX-1"), make_result("EX-99")]

    report = score(labels, results, threshold=0.7)

    assert report.extra_ids == ["EX-99"]
    assert report.scored == 1
    assert report.exact_match == (1, 1)


def test_draft_label_with_result_is_not_scored():
    labels = [make_label("EX-1"), make_label("EX-2", label_status="draft")]
    results = [make_result("EX-1"), make_result("EX-2")]

    report = score(labels, results, threshold=0.7)

    assert report.not_scored_draft == ["EX-2"]
    assert report.scored == 1
    assert report.exact_match == (1, 1)
    assert report.extra_ids == []


def test_draft_label_without_result_is_also_listed():
    labels = [make_label("EX-1", label_status="draft")]

    report = score(labels, [], threshold=0.7)

    assert report.not_scored_draft == ["EX-1"]
    assert report.missing_ids == []
    assert report.scored == 0


def test_all_draft_labels_yield_na_percentages_without_division_error():
    labels = [make_label("EX-1", label_status="draft"), make_label("EX-2", label_status="draft")]

    report = score(labels, [], threshold=0.7)

    assert report.scored == 0
    for name in SCORED_FIELDS:
        assert report.field_accuracy[name] == (0, 0)
    assert report.exact_match == (0, 0)
    md = report.to_markdown("set case · 0 scored")
    assert "| categoria | n/a |" in md
    assert "| all 5 fields | n/a |" in md


def test_rule_violations_report_resumen_and_pregunta_length_without_leaking_values():
    labels = [make_label("EX-1"), make_label("EX-2", requiere_info=True)]
    long_resumen = " ".join(["palabra"] * 21)
    long_question = "x" * 301
    results = [
        make_result("EX-1", resumen=long_resumen),
        make_result("EX-2", requiere_info=True, pregunta_seguimiento=long_question),
    ]

    report = score(labels, results, threshold=0.7)

    violated_ids = {rid for rid, _ in report.rule_violations}
    assert violated_ids == {"EX-1", "EX-2"}
    for _, msg in report.rule_violations:
        assert long_resumen not in msg
        assert long_question not in msg


def test_sweep_math_with_two_wrong_of_four_known_confidences():
    pairs = [(0.95, False), (0.60, True), (0.55, False), (0.80, True)]

    rows = threshold_sweep(pairs)

    assert [row.threshold for row in rows] == [
        0.5,
        0.55,
        0.6,
        0.65,
        0.7,
        0.75,
        0.8,
        0.85,
        0.9,
    ]
    row_70 = next(row for row in rows if row.threshold == 0.7)
    assert row_70.routed == 2  # 0.60 and 0.55 are < 0.7
    assert row_70.wrong_total == 2  # 0.60 and 0.80 are wrong
    assert row_70.wrong_caught == 1  # only 0.60 is both routed and wrong


def test_sweep_zero_wrong_has_na_catch_rate_in_markdown():
    labels = [make_label("EX-1")]
    results = [make_result("EX-1")]

    report = score(labels, results, threshold=0.7)

    assert all(row.wrong_total == 0 for row in report.sweep)
    md = report.to_markdown("set case · 1 scored")
    assert "| 0.70 | 0 | n/a | n/a |" in md


def test_boolean_failure_values_render_json_style():
    labels = [make_label("EX-1", requiere_info=True)]
    results = [make_result("EX-1", requiere_info=False)]

    report = score(labels, results, threshold=0.7)

    failure = next(f for f in report.failures if f.field == "requiere_info")
    assert (failure.expected, failure.got) == ("true", "false")


def test_degenerate_confidence_prints_warning_line():
    labels = [make_label("EX-1"), make_label("EX-2")]
    results = [make_result("EX-1", confianza=0.5), make_result("EX-2", confianza=0.5)]

    report = score(labels, results, threshold=0.7)
    md = report.to_markdown("set case · 2 scored")

    assert (
        "Degenerate: all confianza values are equal; the sweep carries no calibration signal." in md
    )


def test_varied_confidence_has_no_degenerate_line():
    labels = [make_label("EX-1"), make_label("EX-2")]
    results = [make_result("EX-1", confianza=0.5), make_result("EX-2", confianza=0.9)]

    report = score(labels, results, threshold=0.7)
    md = report.to_markdown("set case · 2 scored")

    assert "Degenerate" not in md


def test_compare_runs_reports_changed_field_and_significant_confidence_delta():
    a = [make_result("EX-1", prioridad="alta", confianza=0.90)]
    b = [make_result("EX-1", prioridad="baja", confianza=0.80)]

    diffs = compare_runs(a, b)

    assert Diff("EX-1", "prioridad", "alta", "baja") in diffs
    assert any(d.field == "confianza" for d in diffs)


def test_compare_runs_ignores_small_and_boundary_confidence_deltas():
    a = [make_result("EX-1", confianza=0.90)]
    b = [make_result("EX-1", confianza=0.87)]  # delta 0.03
    assert compare_runs(a, b) == []

    a2 = [make_result("EX-2", confianza=0.90)]
    b2 = [make_result("EX-2", confianza=0.85)]  # delta exactly 0.05
    assert compare_runs(a2, b2) == []


def test_compare_runs_reports_one_sided_id():
    a = [make_result("EX-1"), make_result("EX-2")]
    b = [make_result("EX-1")]

    diffs = compare_runs(a, b)

    assert Diff("EX-2", "<present>", "yes", "no") in diffs


def test_to_markdown_contains_header_field_table_and_na():
    report = score([], [], threshold=0.7)

    md = report.to_markdown("set case · run EX · 0 scored")

    assert "## Eval — set case · run EX · 0 scored" in md
    assert "| categoria | n/a |" in md
    assert "n/a" in md
