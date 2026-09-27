import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from evaluate_support import build_fixture

from pitz_pulse.model_compare import MOCK_HEADER, Row, build_row, main, to_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKE = shutil.which("make")

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


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_makefile_dry_run_shows_two_classify_calls_per_model_with_temperature_override():
    """LLM_PROVIDER is always passed explicitly on the command line: it overrides whatever a
    developer's own root `.env` sets (never read here), so the scenario is deterministic.
    """
    result = subprocess.run(
        [
            "make",
            "-n",
            "compare-models",
            "MODELS=claude-haiku-4-5 claude-sonnet-5",
            "LLM_PROVIDER=mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    # The recursive `$(MAKE) classify ...` calls all live on one printed recipe line (joined by
    # `&&`); isolate it before splitting, since a later line also mentions the model names.
    loop_line = next(line for line in result.stdout.splitlines() if "SET=case SUFFIX=cmp" in line)
    segments = loop_line.split("&&")
    haiku_segments = [s for s in segments if "claude-haiku-4-5" in s]
    sonnet_segments = [s for s in segments if "claude-sonnet-5" in s]
    assert len(haiku_segments) == 2  # one classify call per set (case, edge)
    assert len(sonnet_segments) == 2
    assert all("LLM_TEMPERATURE=none" not in s for s in haiku_segments)
    assert all("LLM_TEMPERATURE=none" in s for s in sonnet_segments)
    assert loop_line.count("SET=case SUFFIX=cmp") == 2
    assert loop_line.count("SET=edge SUFFIX=cmp") == 2


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_makefile_non_mock_provider_without_confirm_stops():
    result = subprocess.run(
        ["make", "-n", "compare-models", "MODELS=claude-haiku-4-5", "LLM_PROVIDER=anthropic_api"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "CONFIRM=1" in result.stderr


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_makefile_mock_needs_no_confirm():
    result = subprocess.run(
        ["make", "-n", "compare-models", "MODELS=claude-haiku-4-5", "LLM_PROVIDER=mock"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.returncode == 0
