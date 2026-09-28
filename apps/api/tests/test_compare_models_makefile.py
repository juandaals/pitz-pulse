"""`make -n compare-models` dry-run tests: only the printed recipe text, no real execution.

Split out of `test_model_compare.py` (the Python module's own unit/CLI tests) to keep both files
well under the 300-line guideline. A real (non `-n`) end-to-end run lives in
`test_compare_models_e2e.py`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKE = shutil.which("make")


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_makefile_dry_run_shows_two_classify_calls_per_model_with_temperature_override():
    """LLM_PROVIDER is always passed explicitly on the command line: it overrides whatever a
    developer's own root `.env` sets (never read here), so the scenario is deterministic.

    Two models need a non-mock provider: mock ignores `LLM_MODEL` and always writes
    `model=mock`, so `compare-models` refuses more than one MODELS entry under mock (see
    `test_makefile_mock_refuses_more_than_one_model` below); `CONFIRM=1` is required here only
    because the provider is non-mock, not because any real call is made (`-n` never executes
    the recipe).
    """
    result = subprocess.run(
        [
            "make",
            "-n",
            "compare-models",
            "MODELS=claude-haiku-4-5 claude-sonnet-5",
            "LLM_PROVIDER=anthropic_api",
            "CONFIRM=1",
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


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_makefile_mock_refuses_more_than_one_model():
    """Mock ignores `LLM_MODEL` and always writes `model=mock`: a second model would silently
    collide with (and overwrite) the first model's run files under the same stem.
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
    )

    assert result.returncode == 2
    assert "mock" in result.stderr
    assert "one MODELS entry" in result.stderr


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_makefile_preflight_runs_before_the_first_classify_call():
    result = subprocess.run(
        [
            "make",
            "-n",
            "compare-models",
            "MODELS=claude-haiku-4-5 claude-sonnet-5",
            "LLM_PROVIDER=anthropic_api",
            "CONFIRM=1",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.splitlines()
    preflight_index = next(i for i, line in enumerate(lines) if "pitz_pulse.preflight" in line)
    classify_index = next(i for i, line in enumerate(lines) if "SET=case SUFFIX=cmp" in line)
    assert preflight_index < classify_index
