import json

from pitz_pulse.evaluate import main
from pitz_pulse.runs import run_paths, sha256_hex, write_pair

CASE_MESSAGES = [
    {"id": "EX-1", "message": "Mensaje de prueba uno."},
    {"id": "EX-2", "message": "Mensaje de prueba dos."},
]
CASE_LABELS = [
    {
        "id": "EX-1",
        "categoria": "bug",
        "prioridad": "alta",
        "area_sugerida": "backend",
        "idioma": "es",
        "requiere_info": False,
        "label_status": "approved",
    },
    {
        "id": "EX-2",
        "categoria": "consulta",
        "prioridad": "baja",
        "area_sugerida": "producto",
        "idioma": "es",
        "requiere_info": False,
        "label_status": "approved",
    },
]
RESULT_FIELDS = {
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
DROP = object()


def _build(
    tmp_path,
    *,
    set_name="case",
    stem=None,
    items=None,
    run_bytes=None,
    meta_overrides=None,
    labels_text=None,
    input_text=None,
):
    """A shared valid fixture builder: each failing test applies ONE mutation to it."""
    app_root = tmp_path / "apps" / "api"
    (app_root / "eval" / "runs").mkdir(parents=True, exist_ok=True)

    if set_name == "case":
        input_path = tmp_path / "mensajes.json"
        default_labels_path = tmp_path / "etiquetas_esperadas.json"
    else:
        (app_root / "eval" / "golden").mkdir(parents=True, exist_ok=True)
        input_path = app_root / "eval" / "golden" / "edge_cases.messages.json"
        default_labels_path = app_root / "eval" / "golden" / "edge_cases.labels.json"

    input_path.write_text(
        input_text if input_text is not None else json.dumps(CASE_MESSAGES), encoding="utf-8"
    )
    default_labels_path.write_text(
        labels_text if labels_text is not None else json.dumps(CASE_LABELS), encoding="utf-8"
    )

    result_items = (
        items
        if items is not None
        else [
            {"id": "EX-1", **RESULT_FIELDS},
            {"id": "EX-2", **RESULT_FIELDS},
        ]
    )
    final_run_bytes = (
        run_bytes if run_bytes is not None else (json.dumps(result_items) + "\n").encode("utf-8")
    )

    meta = {
        "set": set_name,
        "provider": "mock",
        "model": "mock",
        "prompt_version": "v1",
        "prompt_sha256": "a" * 64,
        "tool_schema_sha256": "b" * 64,
        "temperature": 0.5,
        "n": len(result_items),
        "failures": [],
        "input_sha256": sha256_hex(input_path.read_bytes()),
    }
    if meta_overrides:
        for key, value in meta_overrides.items():
            if value is DROP:
                meta.pop(key, None)
            else:
                meta[key] = value

    stem = stem or f"{set_name}__v1__mock__mock"
    run_path, meta_path = run_paths(app_root, stem)
    write_pair(run_path, meta_path, final_run_bytes, meta)
    return app_root, stem


def _env(app_root, **extra):
    return {"APP_ROOT": str(app_root), **extra}


def _run(app_root, argv, **extra_env):
    return main(argv, env=_env(app_root, **extra_env))


def test_valid_run_exits_zero_with_header_and_mock_line(tmp_path, capsys):
    app_root, stem = _build(tmp_path)

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 0
    assert out.out.splitlines()[0] == "# MOCK RUN — NOT MODEL QUALITY"
    assert "## Eval — set case · run" in out.out
    assert "2 scored" in out.out


def test_stem_invalid_exits_two(tmp_path, capsys):
    app_root = tmp_path / "apps" / "api"
    app_root.mkdir(parents=True)

    code = _run(app_root, ["--run", "../evil"])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "error:" in out.err


def test_run_file_missing_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)
    run_path, _meta_path = run_paths(app_root, stem)
    run_path.unlink()

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "does not exist" in out.err


def test_meta_file_missing_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)
    _run_path, meta_path = run_paths(app_root, stem)
    meta_path.unlink()

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "does not exist" in out.err


def test_results_sha256_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)
    run_path, _meta_path = run_paths(app_root, stem)
    run_path.write_bytes(run_path.read_bytes().rstrip(b"\n") + b" \n")

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "results_sha256" in out.err


def test_run_file_not_a_json_list_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path, run_bytes=b'{"not": "a list"}')

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "list" in out.err


def test_length_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path, meta_overrides={"n": 3})

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "meta.n" in out.err


def test_duplicate_result_id_exits_two(tmp_path, capsys):
    app_root, stem = _build(
        tmp_path,
        items=[{"id": "EX-1", **RESULT_FIELDS}, {"id": "EX-1", **RESULT_FIELDS}],
    )

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "duplicate" in out.err


def test_item_failing_classification_shape_exits_two(tmp_path, capsys):
    app_root, stem = _build(
        tmp_path,
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "categoria": "not-a-real-category"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "invalid result item EX-1" in out.err


def test_version_prompt_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = _build(
        tmp_path,
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "version_prompt": "v2"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "version_prompt" in out.err


def test_unknown_set_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path, meta_overrides={"set": "unknown"})

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "set" in out.err


def test_input_sha256_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path, meta_overrides={"input_sha256": "f" * 64})

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "input_sha256" in out.err


def test_label_file_missing_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)
    (tmp_path / "etiquetas_esperadas.json").unlink()

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "error:" in out.err


def test_label_file_invalid_json_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)
    (tmp_path / "etiquetas_esperadas.json").write_text("{not json", encoding="utf-8")

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "error:" in out.err


def test_meta_not_json_exits_two_without_traceback(tmp_path, capsys):
    app_root, stem = _build(tmp_path)
    _run_path, meta_path = run_paths(app_root, stem)
    meta_path.write_text("{not valid json", encoding="utf-8")

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "JSON" in out.err


def test_meta_missing_n_exits_two_without_traceback(tmp_path, capsys):
    app_root, stem = _build(tmp_path, meta_overrides={"n": DROP})

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "n" in out.err


def test_meta_model_as_number_exits_two_without_traceback(tmp_path, capsys):
    app_root, stem = _build(tmp_path, meta_overrides={"model": 123})

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "model" in out.err


def test_threshold_nan_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)

    code = _run(app_root, ["--run", stem, "--threshold", "nan"])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "threshold" in out.err


def test_threshold_above_one_exits_two(tmp_path, capsys):
    app_root, stem = _build(tmp_path)

    code = _run(app_root, ["--run", stem, "--threshold", "1.5"])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "threshold" in out.err


def test_anthropic_provider_env_with_empty_key_still_exits_zero(tmp_path, capsys):
    app_root, stem = _build(tmp_path)

    code = _run(app_root, ["--run", stem], LLM_PROVIDER="anthropic_api", ANTHROPIC_API_KEY="")

    out = capsys.readouterr()
    assert code == 0
    assert out.err == ""


def test_all_draft_labels_exit_zero_with_na(tmp_path, capsys):
    draft_labels = [{**label, "label_status": "draft"} for label in CASE_LABELS]
    app_root, stem = _build(tmp_path, labels_text=json.dumps(draft_labels))

    code = _run(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 0
    assert "n/a" in out.out


def test_compare_across_different_sets_exits_two(tmp_path, capsys):
    app_root, case_stem = _build(tmp_path, set_name="case")
    _app_root2, edge_stem = _build(
        tmp_path,
        set_name="edge",
        items=[
            {"id": "EDGE-1", **RESULT_FIELDS},
            {"id": "EDGE-2", **RESULT_FIELDS},
        ],
    )

    code = _run(app_root, ["--run", case_stem, "--compare", edge_stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "set" in out.err or "input_sha256" in out.err


def test_compare_valid_shows_diff_section(tmp_path, capsys):
    app_root, stem_a = _build(tmp_path, stem="case__v1__mock__mock")
    _app_root2, stem_b = _build(
        tmp_path,
        stem="case__v1__mock__mock-b",
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "prioridad": "baja"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = _run(app_root, ["--run", stem_a, "--compare", stem_b])

    out = capsys.readouterr()
    assert code == 0
    assert f"### Diff vs {stem_b}" in out.out
    assert "prioridad" in out.out
