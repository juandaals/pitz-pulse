import json

from evaluate_support import CASE_LABELS, RESULT_FIELDS, build_fixture, run_eval

from pitz_pulse.evaluate import main


def test_valid_run_exits_zero_with_header_and_mock_line(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 0
    assert out.out.splitlines()[0] == "# MOCK RUN — NOT MODEL QUALITY"
    assert "## Eval — set case · run" in out.out
    assert "2 scored" in out.out


def test_anthropic_provider_env_with_empty_key_still_exits_zero(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)

    code = run_eval(app_root, ["--run", stem], LLM_PROVIDER="anthropic_api", ANTHROPIC_API_KEY="")

    out = capsys.readouterr()
    assert code == 0
    assert out.err == ""


def test_all_draft_labels_exit_zero_with_na(tmp_path, capsys):
    draft_labels = [{**label, "label_status": "draft"} for label in CASE_LABELS]
    app_root, stem = build_fixture(tmp_path, labels_text=json.dumps(draft_labels))

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 0
    assert "n/a" in out.out


def test_compare_valid_shows_diff_section(tmp_path, capsys):
    app_root, stem_a = build_fixture(tmp_path, stem="case__v1__mock__mock")
    _app_root2, stem_b = build_fixture(
        tmp_path,
        stem="case__v1__mock__mock-b",
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "prioridad": "baja"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = run_eval(app_root, ["--run", stem_a, "--compare", stem_b])

    out = capsys.readouterr()
    assert code == 0
    assert f"### Diff vs {stem_b}" in out.out
    assert "prioridad" in out.out


def test_compare_diff_section_reports_ids_flipped_per_field(tmp_path, capsys):
    app_root, stem_a = build_fixture(tmp_path, stem="case__v1__mock__mock")
    _app_root2, stem_b = build_fixture(
        tmp_path,
        stem="case__v1__mock__mock-b",
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "prioridad": "baja"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = run_eval(app_root, ["--run", stem_a, "--compare", stem_b])

    out = capsys.readouterr()
    assert code == 0
    assert "#### Noise floor (ids flipped per field)" in out.out
    assert "| prioridad | 1 |" in out.out
    assert "| categoria | 0 |" in out.out


def test_app_root_with_surrounding_whitespace_is_stripped(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)

    code = main(["--run", stem], env={"APP_ROOT": f"  {app_root}  \n"})

    out = capsys.readouterr()
    assert code == 0, out.err
