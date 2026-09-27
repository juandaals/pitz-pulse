import dataclasses
import hashlib
import json
import threading

import pytest
from fakes import FakeAdapter, make_call

from pitz_pulse import batch as batch_module
from pitz_pulse.batch import META_KEYS, main, run_batch
from pitz_pulse.classifier import build_classifier
from pitz_pulse.config import DEFAULT_APP_ROOT, parse_llm_settings
from pitz_pulse.providers.base import LLMError
from pitz_pulse.runs import run_paths, sha256_hex
from pitz_pulse.schema import RequestInput

STEM = "case__v1__mock__mock"


@pytest.fixture
def app_root(tmp_path):
    """Temp repo layout: <tmp>/repo/apps/api with the real prompt copied in."""
    root = tmp_path / "repo" / "apps" / "api"
    (root / "prompts").mkdir(parents=True)
    (root / "prompts" / "v1.md").write_bytes((DEFAULT_APP_ROOT / "prompts" / "v1.md").read_bytes())
    items = [{"id": f"MSG-{n:02d}", "message": f"mensaje {n}"} for n in range(1, 6)]
    (tmp_path / "repo" / "mensajes.json").write_text(json.dumps(items), encoding="utf-8")
    return root


def settings_for(app_root, **changes):
    return dataclasses.replace(parse_llm_settings({"APP_ROOT": str(app_root)}), **changes)


def run_main(app_root, responses, argv=("--set", "case"), **changes):
    settings = settings_for(app_root, **changes)
    adapter = FakeAdapter(responses)
    code = main(list(argv), settings=settings, classifier=build_classifier(settings, adapter))
    return code, adapter


def read_meta(app_root):
    return json.loads(run_paths(app_root, STEM)[1].read_text())


def test_success_writes_sorted_run_and_exact_meta(app_root):
    code, _ = run_main(app_root, [make_call()] * 5)
    run, _ = run_paths(app_root, STEM)
    assert code == 0
    assert [r["id"] for r in json.loads(run.read_text())] == [f"MSG-{n:02d}" for n in range(1, 6)]
    meta = read_meta(app_root)
    assert set(meta) == set(META_KEYS) | {"results_sha256"}
    assert meta["results_sha256"] == sha256_hex(run.read_bytes())
    mensajes = (app_root.parents[1] / "mensajes.json").read_bytes()
    assert meta["input_sha256"] == hashlib.sha256(mensajes).hexdigest()
    assert (meta["set"], meta["n"], meta["n_input"], meta["failures"]) == ("case", 5, 5, [])
    assert meta["input_file"] == "mensajes.json" and meta["billing"] == "none"
    assert meta["total_input_tokens"] == 500 and meta["temperature"] is None
    assert meta["run_at"].endswith("Z")


def test_never_writes_resultados_json(app_root):
    run_main(app_root, [make_call()] * 5)
    assert not list(app_root.parents[1].rglob("resultados*.json"))


def test_meta_totals_include_retry_attempts(app_root):
    run_main(app_root, [make_call(None, input_tokens=70)] + [make_call()] * 5, concurrency=1)
    meta = read_meta(app_root)
    assert meta["attempts_total"] == 6 and meta["invalid_output_attempts"] == 1
    assert meta["total_input_tokens"] == 570


def test_crash_keeps_billed_tokens(app_root):
    responses = [make_call(None, input_tokens=70), KeyError("x")] + [make_call()] * 4
    code, _ = run_main(app_root, responses, concurrency=1)
    meta = read_meta(app_root)
    assert code == 1 and meta["failures"] == [{"id": "MSG-01", "kind": "unexpected"}]
    assert meta["total_input_tokens"] == 70 + 4 * 100


def test_partial_failure_writes_files_and_exits_1(app_root):
    responses = [make_call()] * 4 + [LLMError("unavailable", "X")]
    code, _ = run_main(app_root, responses, concurrency=1)
    assert code == 1
    assert read_meta(app_root)["failures"] == [{"id": "MSG-05", "kind": "llm_unavailable"}]


def test_all_rejected_writes_nothing(app_root):
    code, _ = run_main(app_root, [LLMError("rejected", "401")] * 5, concurrency=1)
    run, meta = run_paths(app_root, STEM)
    assert code == 1 and not run.exists() and not meta.exists()


def test_existing_run_without_force_exits_2_and_keeps_files(app_root):
    run_main(app_root, [make_call()] * 5)
    run, meta = run_paths(app_root, STEM)
    before = (run.read_bytes(), meta.read_bytes())
    code, adapter = run_main(app_root, [make_call()] * 5)
    assert code == 2 and adapter.calls == [] and (run.read_bytes(), meta.read_bytes()) == before
    assert run_main(app_root, [make_call()] * 5, argv=("--set", "case", "--force"))[0] == 0


@pytest.mark.parametrize("argv", [("--set", "case", "--suffix", "../x"), ("--set", "nope")])
def test_bad_arguments_exit_2_without_calls(app_root, argv):
    code, adapter = run_main(app_root, [make_call()] * 5, argv=argv)
    assert code == 2 and adapter.calls == []


def test_help_exits_0():
    assert main(["--help"]) == 0


def test_invalid_input_exits_2_without_text(app_root, capsys):
    (app_root.parents[1] / "mensajes.json").write_text(
        json.dumps([{"id": "A", "message": "SECRETTEXT" * 900}]), encoding="utf-8"
    )
    code, adapter = run_main(app_root, [make_call()])
    assert code == 2 and adapter.calls == []
    assert "SECRETTEXT" not in capsys.readouterr().err


def test_config_error_exits_2_with_message(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert main(["--set", "case"]) == 2
    assert "LLM_PROVIDER" in capsys.readouterr().err


class GatedAdapter(FakeAdapter):
    """Blocks each call until `expected` calls are in flight together (deterministic peak)."""

    def __init__(self, count, expected):
        super().__init__([make_call()] * count)
        self.barrier = threading.Barrier(expected, timeout=5)
        self.lock = threading.Lock()
        self.active = self.peak = 0

    def invoke(self, *args):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.barrier.wait()
        with self.lock:
            self.active -= 1
            return self.responses.pop()


@pytest.mark.parametrize("concurrency,n,expected", [(2, 6, 2), (4, 3, 3), (1, 3, 1)])
def test_peak_concurrency(app_root, concurrency, n, expected):
    adapter = GatedAdapter(n, expected)
    requests = [RequestInput(id=f"R{i}", message="m") for i in range(n)]
    run_batch(build_classifier(settings_for(app_root), adapter), requests, concurrency)
    assert adapter.peak == expected


def test_first_rejected_cancels_remaining(app_root):
    adapter = FakeAdapter([LLMError("rejected", "APIStatusError:401")] + [make_call()] * 9)
    requests = [RequestInput(id=f"R{i}", message="m") for i in range(10)]
    _, failures = run_batch(build_classifier(settings_for(app_root), adapter), requests, 1)
    assert len(adapter.calls) <= 2  # the single worker may already hold the next item
    assert "llm_rejected" in {f.kind for f in failures}
    assert sum(f.kind == "cancelled" for f in failures) >= 8


def test_ctrl_c_in_main_thread_abandons_and_writes_nothing(app_root, monkeypatch, capsys):
    real = batch_module.wait
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(batch_module, "wait", interrupted)
    code, adapter = run_main(app_root, [make_call()] * 5, concurrency=1)
    run, meta = run_paths(app_root, STEM)
    assert code == 130 and not run.exists() and not meta.exists()
    assert len(adapter.calls) <= 2
    assert "interrupted" in capsys.readouterr().err


@pytest.mark.parametrize("content,needle", [("{}", "list"), ("[]", "no requests")])
def test_non_list_or_empty_input_exits_2(app_root, capsys, content, needle):
    (app_root.parents[1] / "mensajes.json").write_text(content, encoding="utf-8")
    code, adapter = run_main(app_root, [make_call()])
    assert code == 2 and adapter.calls == []
    assert needle in capsys.readouterr().err
