"""Real repo tree, read-only: pins the committed deliverable and confirms it still validates
and evaluates cleanly against the current prompt, tool schema and golden files (Spec 03
implementation-gate final fix, item 4). Never writes; never touches env-based provider settings.
"""

import json

from pitz_pulse import evaluate, promote
from pitz_pulse.config import DEFAULT_APP_ROOT
from pitz_pulse.runs import repo_root, run_paths, sha256_hex

APP_ROOT = DEFAULT_APP_ROOT
REPO_ROOT = repo_root(APP_ROOT)


def _committed_meta() -> dict:
    return json.loads((REPO_ROOT / "resultados.meta.json").read_text(encoding="utf-8"))


def test_resultados_bytes_match_its_source_run():
    meta = _committed_meta()
    run_path, _meta_path = run_paths(APP_ROOT, meta["source_run"])
    assert (REPO_ROOT / "resultados.json").read_bytes() == run_path.read_bytes()


def test_resultados_sha256_matches_meta():
    meta = _committed_meta()
    results_bytes = (REPO_ROOT / "resultados.json").read_bytes()
    assert sha256_hex(results_bytes) == meta["results_sha256"]


def test_meta_mock_flag_matches_provider():
    meta = _committed_meta()
    assert meta["mock"] == (meta["provider"] == "mock")


def test_committed_run_still_validates_against_current_prompt_and_input():
    meta = _committed_meta()
    validated = promote.verify(APP_ROOT, meta["source_run"], allow_mock=meta["mock"])
    assert validated.provider == meta["provider"]


def test_evaluate_case_mock_run_exits_zero_with_mock_header(capsys):
    code = evaluate.main(["--run", "case__v1__mock__mock"], env={"APP_ROOT": str(APP_ROOT)})

    out = capsys.readouterr()
    assert code == 0, out.err
    assert out.out.splitlines()[0] == evaluate.MOCK_HEADER


def test_evaluate_edge_mock_run_exits_zero_with_zero_scored(capsys):
    code = evaluate.main(["--run", "edge__v1__mock__mock"], env={"APP_ROOT": str(APP_ROOT)})

    out = capsys.readouterr()
    assert code == 0, out.err
    assert out.out.splitlines()[0] == evaluate.MOCK_HEADER
    assert "0 scored" in out.out
