import json

import pytest
from promote_support import BASELINE_STEM, baseline_items, build_repo, write_run

from pitz_pulse import batch, promote
from pitz_pulse.config import parse_llm_settings
from pitz_pulse.providers.mock import MOCK_SUMMARY
from pitz_pulse.runs import run_paths, sha256_hex

MOCK_STEM = "case__v1__mock__mock"


def run_promote(app_root, argv):
    return promote.main(argv, env={"APP_ROOT": str(app_root)})


def outputs(app_root):
    root = app_root.parents[1]
    return root / "resultados.json", root / "resultados.meta.json"


@pytest.fixture
def mock_run(tmp_path):
    """A real mock-classifier batch run in the temp tree."""
    app_root = build_repo(tmp_path)
    settings = parse_llm_settings({"LLM_PROVIDER": "mock", "APP_ROOT": str(app_root)})
    assert batch.main(["--set", "case"], settings=settings) == 0
    return app_root


def test_allow_mock_promotes_meta_first_then_run(mock_run, monkeypatch, capsys):
    calls = []
    real_write_pair = promote.write_pair

    def spy(run_path, meta_path, run_bytes, meta):
        calls.append((run_path, meta_path))
        real_write_pair(run_path, meta_path, run_bytes, meta)

    monkeypatch.setattr(promote, "write_pair", spy)

    code = run_promote(mock_run, ["--run", MOCK_STEM, "--allow-mock"])

    assert code == 0, capsys.readouterr().err
    results_path, meta_path = outputs(mock_run)
    assert calls == [(results_path, meta_path)]
    meta = json.loads(meta_path.read_text())
    assert meta["mock"] is True
    assert meta["source_run"] == MOCK_STEM
    assert meta["promoted_at"].endswith("Z") and len(meta["promoted_at"]) == 20
    assert sha256_hex(results_path.read_bytes()) == meta["results_sha256"]
    assert results_path.read_bytes() == run_paths(mock_run, MOCK_STEM)[0].read_bytes()


def test_real_baseline_promotes_with_mock_false(tmp_path, capsys):
    app_root = build_repo(tmp_path)
    write_run(app_root)

    code = run_promote(app_root, ["--run", BASELINE_STEM])

    assert code == 0, capsys.readouterr().err
    results_path, meta_path = outputs(app_root)
    meta = json.loads(meta_path.read_text())
    assert meta["mock"] is False
    assert meta["source_run"] == BASELINE_STEM
    assert meta["temperature"] == 0.0
    assert meta["run_at"] == "2026-09-27T10:00:00Z"
    assert results_path.read_bytes() == run_paths(app_root, BASELINE_STEM)[0].read_bytes()
    assert sha256_hex(results_path.read_bytes()) == meta["results_sha256"]


def _items_with(**changes):
    items = baseline_items()
    items[0] = {**items[0], **changes}
    return items


REFUSALS = {
    "temperature_none": ({"meta_overrides": {"temperature": None}}, [], "temperature"),
    "temperature_half": ({"meta_overrides": {"temperature": 0.5}}, [], "temperature"),
    "temperature_false": ({"meta_overrides": {"temperature": False}}, [], "temperature"),
    "other_model": ({"meta_overrides": {"model": "claude-sonnet-4-6"}}, [], "claude-haiku-4-5"),
    "set_edge": ({"meta_overrides": {"set": "edge"}}, [], "only set case"),
    "ids_differ": ({"items": _items_with(id="EX-9")}, [], "mensajes.json"),
    "failures": (
        {"meta_overrides": {"failures": [{"id": "EX-1", "kind": "invalid_output"}]}},
        [],
        "failures",
    ),
    "input_sha": ({"meta_overrides": {"input_sha256": "0" * 64}}, [], "input_sha256"),
    "prompt_sha": ({"meta_overrides": {"prompt_sha256": "0" * 64}}, [], "prompt_sha256"),
    "tool_sha": ({"meta_overrides": {"tool_schema_sha256": "0" * 64}}, [], "tool_schema_sha256"),
    "prompt_v2": (
        {
            "stem": "case__v2__anthropic_api__claude-haiku-4-5",
            "meta_overrides": {"prompt_version": "v2"},
            "items": [{**item, "version_prompt": "v2"} for item in baseline_items()],
        },
        [],
        "active prompt version",
    ),
    "long_resumen": (
        {"items": _items_with(resumen=" ".join(["palabra"] * 21))},
        [],
        "contract rules",
    ),
    "agent_sdk": (
        {"meta_overrides": {"provider": "claude_agent_sdk"}},
        ["--allow-mock"],
        "never promotable",
    ),
    "unknown_model": ({"meta_overrides": {"model": "claude-unknown"}}, [], "catalog"),
    "model_int": ({"meta_overrides": {"model": 7}}, [], "model"),
    "suffixed_stem": ({"stem": BASELINE_STEM + "__b"}, [], "suffix"),
    "mock_without_flag": (
        {"meta_overrides": {"provider": "mock", "model": "mock"}},
        [],
        "--allow-mock",
    ),
    "mock_billing": ({"meta_overrides": {"billing": "none"}}, [], "mock output"),
    "mock_zero_tokens": ({"meta_overrides": {"total_input_tokens": 0}}, [], "mock output"),
    "mock_resumen": ({"items": _items_with(resumen=MOCK_SUMMARY)}, [], "mock output"),
    "results_sha_mismatch": ({"corrupt_bytes": True}, [], "results_sha256"),
    "length_mismatch": ({"meta_overrides": {"n": 1}}, [], "meta.n"),
}


@pytest.mark.parametrize("case", REFUSALS.values(), ids=REFUSALS.keys())
def test_refusals(tmp_path, capsys, case):
    mutation, extra_argv, reason = case
    app_root = build_repo(tmp_path)
    stem = write_run(app_root, **mutation)

    code = run_promote(app_root, ["--run", stem, *extra_argv])

    err = capsys.readouterr().err
    assert code == 2
    assert reason in err
    assert not any(path.exists() for path in outputs(app_root))


def test_missing_run_is_refused(tmp_path, capsys):
    app_root = build_repo(tmp_path)

    assert run_promote(app_root, ["--run", BASELINE_STEM]) == 2
    assert "does not exist" in capsys.readouterr().err


EXISTING_REAL = {
    "mock_false": json.dumps({"mock": False}),
    "corrupt": "{not json",
    "no_mock_key": json.dumps({"source_run": "x"}),
    "mock_string": json.dumps({"mock": "true"}),
}


@pytest.mark.parametrize("text", EXISTING_REAL.values(), ids=EXISTING_REAL.keys())
def test_mock_over_real_result_needs_force(mock_run, capsys, text):
    results_path, meta_path = outputs(mock_run)
    meta_path.write_text(text, encoding="utf-8")

    code = run_promote(mock_run, ["--run", MOCK_STEM, "--allow-mock"])

    assert code == 2
    assert "--force" in capsys.readouterr().err
    assert meta_path.read_text(encoding="utf-8") == text
    assert not results_path.exists()

    assert run_promote(mock_run, ["--run", MOCK_STEM, "--allow-mock", "--force"]) == 0
    assert json.loads(meta_path.read_text())["mock"] is True


def test_mock_over_mock_result_needs_no_force(mock_run):
    outputs(mock_run)[1].write_text(json.dumps({"mock": True}), encoding="utf-8")

    assert run_promote(mock_run, ["--run", MOCK_STEM, "--allow-mock"]) == 0


def test_real_over_real_result_needs_no_force(tmp_path):
    app_root = build_repo(tmp_path)
    write_run(app_root)
    outputs(app_root)[1].write_text(json.dumps({"mock": False}), encoding="utf-8")

    assert run_promote(app_root, ["--run", BASELINE_STEM]) == 0


def test_missing_meta_with_existing_results_needs_force(mock_run, capsys):
    assert run_promote(mock_run, ["--run", MOCK_STEM, "--allow-mock"]) == 0
    results_path, meta_path = outputs(mock_run)
    meta_path.unlink()
    assert results_path.exists()

    code = run_promote(mock_run, ["--run", MOCK_STEM, "--allow-mock"])

    assert code == 2
    assert "--force" in capsys.readouterr().err
    assert results_path.exists()  # the orphaned real result is left untouched
