from evaluate_support import DROP, RESULT_FIELDS, build_fixture, run_eval

from pitz_pulse.runs import run_paths


def test_stem_invalid_exits_two(tmp_path, capsys):
    app_root = tmp_path / "apps" / "api"
    app_root.mkdir(parents=True)

    code = run_eval(app_root, ["--run", "../evil"])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "error:" in out.err


def test_run_file_missing_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)
    run_path, _meta_path = run_paths(app_root, stem)
    run_path.unlink()

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "does not exist" in out.err


def test_meta_file_missing_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)
    _run_path, meta_path = run_paths(app_root, stem)
    meta_path.unlink()

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "does not exist" in out.err


def test_results_sha256_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)
    run_path, _meta_path = run_paths(app_root, stem)
    run_path.write_bytes(run_path.read_bytes().rstrip(b"\n") + b" \n")

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "results_sha256" in out.err


def test_run_file_not_a_json_list_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, run_bytes=b'{"not": "a list"}')

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "list" in out.err


def test_length_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, meta_overrides={"n": 3})

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "meta.n" in out.err


def test_duplicate_result_id_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(
        tmp_path,
        items=[{"id": "EX-1", **RESULT_FIELDS}, {"id": "EX-1", **RESULT_FIELDS}],
    )

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "duplicate" in out.err


def test_item_failing_classification_shape_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(
        tmp_path,
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "categoria": "not-a-real-category"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "invalid result item EX-1" in out.err


def test_version_prompt_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(
        tmp_path,
        items=[
            {"id": "EX-1", **{**RESULT_FIELDS, "version_prompt": "v2"}},
            {"id": "EX-2", **RESULT_FIELDS},
        ],
    )

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "version_prompt" in out.err


def test_unknown_set_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, meta_overrides={"set": "unknown"})

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "set" in out.err


def test_input_sha256_mismatch_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, meta_overrides={"input_sha256": "f" * 64})

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "input_sha256" in out.err


def test_label_file_missing_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)
    (tmp_path / "etiquetas_esperadas.json").unlink()

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "error:" in out.err


def test_label_file_invalid_json_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)
    (tmp_path / "etiquetas_esperadas.json").write_text("{not json", encoding="utf-8")

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "error:" in out.err


def test_meta_not_json_exits_two_without_traceback(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)
    _run_path, meta_path = run_paths(app_root, stem)
    meta_path.write_text("{not valid json", encoding="utf-8")

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "JSON" in out.err


def test_meta_missing_n_exits_two_without_traceback(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, meta_overrides={"n": DROP})

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "n" in out.err


def test_meta_model_as_number_exits_two_without_traceback(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, meta_overrides={"model": 123})

    code = run_eval(app_root, ["--run", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "Traceback" not in out.err
    assert "model" in out.err


def test_threshold_nan_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)

    code = run_eval(app_root, ["--run", stem, "--threshold", "nan"])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "threshold" in out.err


def test_threshold_above_one_exits_two(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path)

    code = run_eval(app_root, ["--run", stem, "--threshold", "1.5"])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "threshold" in out.err


def test_compare_across_different_sets_exits_two(tmp_path, capsys):
    app_root, case_stem = build_fixture(tmp_path, set_name="case")
    _app_root2, edge_stem = build_fixture(
        tmp_path,
        set_name="edge",
        items=[
            {"id": "EDGE-1", **RESULT_FIELDS},
            {"id": "EDGE-2", **RESULT_FIELDS},
        ],
    )

    code = run_eval(app_root, ["--run", case_stem, "--compare", edge_stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "set" in out.err or "input_sha256" in out.err
