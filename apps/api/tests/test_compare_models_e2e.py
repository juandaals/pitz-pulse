"""A real (non `-n`) run of `make compare-models` end-to-end, mock provider only.

`make -n` dry-run tests (in `test_model_compare.py`) only check the printed recipe text; they
never execute a classify call or a `model_compare` invocation, so they could not have caught the
mock-model stem bug (mock always writes `model=mock` regardless of `LLM_MODEL`, so the table step
must look up the same literal name, not the `MODELS` value). This test drives the real Makefile
target against a tmp copy of the repo tree (`APP_ROOT` redirected), so nothing lands in the real
repo's `eval/runs`, and asserts the table actually gets built and printed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from compare_models_support import build_compare_tree

from pitz_pulse.model_compare import MOCK_HEADER

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKE = shutil.which("make")


@pytest.mark.skipif(MAKE is None, reason="make is not installed")
def test_compare_models_mock_end_to_end_prints_both_set_tables(tmp_path):
    app_root = build_compare_tree(tmp_path)

    result = subprocess.run(
        ["make", "compare-models", "MODELS=claude-haiku-4-5", "LLM_PROVIDER=mock", "FORCE=1"],
        cwd=REPO_ROOT,
        env={**os.environ, "APP_ROOT": str(app_root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert MOCK_HEADER in result.stdout
    assert "## case set" in result.stdout
    assert "## edge set" in result.stdout
    # The bug this guards against: the table must be keyed by the real `mock` stem, not by the
    # MODELS value the mock provider silently ignores.
    assert "claude-haiku-4-5" not in result.stdout.split(MOCK_HEADER, 1)[1]
    written = list((app_root / "eval" / "runs").glob("*.json"))
    assert written and all("mock" in path.name for path in written)
    # Nothing was written into the real repo's eval/runs (APP_ROOT redirected everything).
    real_runs = REPO_ROOT / "apps" / "api" / "eval" / "runs"
    assert not list(real_runs.glob("*__cmp*"))
