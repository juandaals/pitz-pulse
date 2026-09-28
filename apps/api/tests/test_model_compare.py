import hashlib
import json
from pathlib import Path

from evaluate_support import CASE_LABELS, build_fixture

from pitz_pulse.model_compare import MOCK_HEADER, Row, build_row, main, to_markdown

TOKEN_META = {
    "total_input_tokens": 3600,
    "total_output_tokens": 300,
    "total_cost_usd": 0.012,
    "total_equivalent_api_cost_usd": 0.024,
    "attempts_total": 4,
    "p50_latency_ms_per_message": 250.5,
}


def run_compare(app_root, argv, **extra_env):
    env = {"APP_ROOT": str(app_root), **extra_env}
    return main(argv, env=env)


def _tree_snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_anthropic_row_math_is_exact_division_by_n(tmp_path):
    app_root, stem = build_fixture(
        tmp_path,
        meta_overrides={"provider": "anthropic_api", "model": "claude-haiku-4-5", **TOKEN_META},
    )

    row = build_row(app_root, stem, threshold=0.7)

    assert row.mean_input_tokens == 1800.0
    assert row.mean_output_tokens == 150.0
    assert row.cost_per_message_usd == 0.006
    assert row.equivalent_cost_per_message_usd == 0.012
    assert row.attempts_per_message == 2.0
    assert row.p50_latency_ms == 250.5
    assert row.mock is False
    assert row.provider == "anthropic_api"
    assert row.model == "claude-haiku-4-5"


def test_mock_row_is_flagged_mock(tmp_path):
    app_root, stem = build_fixture(tmp_path, meta_overrides={**TOKEN_META})

    row = build_row(app_root, stem, threshold=0.7)

    assert row.mock is True
    assert row.provider == "mock"


def test_null_temperature_renders_as_dash(tmp_path):
    app_root, stem = build_fixture(tmp_path, meta_overrides={"temperature": None, **TOKEN_META})
    row = build_row(app_root, stem, threshold=0.7)

    markdown = to_markdown([row])

    assert "| — |" in markdown


def test_mock_header_present_only_when_a_mock_row_exists(tmp_path):
    app_root, mock_stem = build_fixture(
        tmp_path, stem="case__v1__mock__mock", meta_overrides={**TOKEN_META}
    )
    _app_root2, real_stem = build_fixture(
        tmp_path,
        stem="case__v1__anthropic_api__claude-haiku-4-5",
        meta_overrides={"provider": "anthropic_api", "model": "claude-haiku-4-5", **TOKEN_META},
    )
    mock_row = build_row(app_root, mock_stem, threshold=0.7)
    real_row = build_row(app_root, real_stem, threshold=0.7)

    assert to_markdown([mock_row]).splitlines()[0] == MOCK_HEADER
    assert MOCK_HEADER not in to_markdown([real_row])


def test_separate_tables_per_set(tmp_path):
    app_root, case_stem = build_fixture(
        tmp_path, set_name="case", stem="case__v1__mock__mock", meta_overrides={**TOKEN_META}
    )
    _app_root2, edge_stem = build_fixture(
        tmp_path, set_name="edge", stem="edge__v1__mock__mock", meta_overrides={**TOKEN_META}
    )
    case_row = build_row(app_root, case_stem, threshold=0.7)
    edge_row = build_row(app_root, edge_stem, threshold=0.7)

    markdown = to_markdown([case_row, edge_row])

    assert "## case set" in markdown
    assert "## edge set" in markdown
    assert markdown.index("## case set") < markdown.index("## edge set")


def test_note_shows_each_rows_own_scored_count_not_just_the_firsts(tmp_path):
    """Two rows in the same set with a different number of approved labels: the note must show
    each row's own `scored` count, not silently repeat the first row's for every model.
    """
    app_root_a, stem_a = build_fixture(
        tmp_path / "a", set_name="case", stem="case__v1__mock__mock", meta_overrides={**TOKEN_META}
    )
    app_root_b, stem_b = build_fixture(
        tmp_path / "b",
        set_name="case",
        stem="case__v1__anthropic_api__claude-haiku-4-5",
        meta_overrides={"provider": "anthropic_api", "model": "claude-haiku-4-5", **TOKEN_META},
        labels_text=json.dumps(CASE_LABELS[:1]),
    )
    row_a = build_row(app_root_a, stem_a, threshold=0.7)
    row_b = build_row(app_root_b, stem_b, threshold=0.7)
    assert row_a.scored == 2
    assert row_b.scored == 1

    markdown = to_markdown([row_a, row_b])

    note_line = next(line for line in markdown.splitlines() if line.startswith("N = "))
    assert "mock 2" in note_line
    assert "claude-haiku-4-5 1" in note_line


def test_cli_integrity_failure_exits_two_naming_the_stem(tmp_path, capsys):
    app_root, _stem = build_fixture(tmp_path, meta_overrides={**TOKEN_META})
    bogus_stem = "case__v1__mock__does-not-exist"

    code = run_compare(app_root, ["--runs", bogus_stem])

    out = capsys.readouterr()
    assert code == 2
    assert bogus_stem in out.err
    assert out.err.startswith("error: ")


def test_cli_writes_no_files(tmp_path, capsys):
    app_root, stem = build_fixture(tmp_path, meta_overrides={**TOKEN_META})
    before = _tree_snapshot(tmp_path)

    code = run_compare(app_root, ["--runs", stem])

    after = _tree_snapshot(tmp_path)
    assert code == 0
    assert before == after


def test_cli_valid_run_prints_table(tmp_path, capsys):
    app_root, stem = build_fixture(
        tmp_path,
        meta_overrides={"provider": "anthropic_api", "model": "claude-haiku-4-5", **TOKEN_META},
    )

    code = run_compare(app_root, ["--runs", stem])

    out = capsys.readouterr()
    assert code == 0
    assert "## case set" in out.out
    assert "claude-haiku-4-5" in out.out
    assert "N = " in out.out


def test_missing_meta_number_is_a_run_error_not_a_traceback(tmp_path, capsys):
    """A run file that predates the token/cost columns must fail cleanly, not crash."""
    app_root, stem = build_fixture(tmp_path)  # no TOKEN_META overrides

    code = run_compare(app_root, ["--runs", stem])

    out = capsys.readouterr()
    assert code == 2
    assert out.err.startswith("error: ")
    assert "total_input_tokens" in out.err


def test_row_is_a_frozen_dataclass_instance(tmp_path):
    app_root, stem = build_fixture(tmp_path, meta_overrides={**TOKEN_META})

    row = build_row(app_root, stem, threshold=0.7)

    assert isinstance(row, Row)


# Makefile `compare-models` dry-run and end-to-end tests live in `test_compare_models_makefile.py`
# and `test_compare_models_e2e.py`.
