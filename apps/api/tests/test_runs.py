import json
import os
import stat
from pathlib import Path

import pytest
from fakes import VALID_OUTPUT

from pitz_pulse.runs import (
    RunError,
    ensure_writable,
    load_requests,
    repo_root,
    run_paths,
    run_stem,
    serialize_run,
    sha256_hex,
    write_pair,
)
from pitz_pulse.schema import CONTRACT_FIELDS, Classification


def test_stem_includes_set_and_sanitizes_model():
    assert run_stem("case", "v1", "anthropic_api", "claude-haiku-4-5") == (
        "case__v1__anthropic_api__claude-haiku-4-5"
    )
    assert run_stem("edge", "v2", "x", "Org/Model:0", "b") == "edge__v2__x__org-model-0__b"


@pytest.mark.parametrize("suffix", ["../x", "a__b", "UPPER", "", "x" * 21])
def test_invalid_suffix(suffix):
    with pytest.raises(RunError):
        run_stem("case", "v1", "p", "m", suffix)


def test_unknown_set():
    with pytest.raises(RunError):
        run_stem("other", "v1", "p", "m")


def test_repo_root_guard(tmp_path):
    assert repo_root(tmp_path / "repo" / "apps" / "api") == tmp_path / "repo"
    with pytest.raises(RunError):
        repo_root(Path("/app"))


def test_paths_are_confined(tmp_path):
    run, meta = run_paths(tmp_path, "case__v1__p__m")
    assert run.parent == meta.parent == tmp_path / "eval" / "runs"
    assert meta.name == "case__v1__p__m.meta.json"
    with pytest.raises(RunError):
        run_paths(tmp_path, "../../etc")


def test_serialize_keeps_contract_order_and_nulls():
    item = Classification.model_validate(
        {
            "id": "A",
            "version_prompt": "v1",
            **VALID_OUTPUT,
            "requiere_info": False,
            "pregunta_seguimiento": None,
        }
    )
    data = json.loads(serialize_run([item]))
    assert tuple(data[0]) == CONTRACT_FIELDS and data[0]["pregunta_seguimiento"] is None


def test_write_pair_replaces_meta_before_run_with_hash_and_readable_mode(tmp_path, monkeypatch):
    run, meta = run_paths(tmp_path, "case__v1__p__m")
    run.parent.mkdir(parents=True)
    order = []
    real_replace = os.replace

    def spy(src, dst):
        order.append(os.path.basename(dst))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    write_pair(run, meta, b"[]\n", {"n": 0})
    assert order == [meta.name, run.name]
    assert json.loads(meta.read_text())["results_sha256"] == sha256_hex(b"[]\n")
    assert stat.S_IMODE(run.stat().st_mode) == 0o644
    assert not [p for p in run.parent.iterdir() if p.name.endswith(".tmp")]


def test_write_pair_cleans_temp_on_failure(tmp_path, monkeypatch):
    run, meta = run_paths(tmp_path, "case__v1__p__m")
    run.parent.mkdir(parents=True)

    def broken(*args):
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        write_pair(run, meta, b"[]", {})
    assert list(run.parent.iterdir()) == []


def test_ensure_writable(tmp_path):
    ensure_writable(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_load_requests_rejects_duplicates_without_text(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps([{"id": "A", "message": "SECRET1"}, {"id": "A", "message": "x"}]))
    with pytest.raises(RunError) as info:
        load_requests(path)
    assert "A" in str(info.value) and "SECRET1" not in str(info.value)


def test_load_requests_invalid_item_names_id_and_field_without_text(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps([{"id": "A", "message": "SECRET2" * 1200}]))
    with pytest.raises(RunError) as info:
        load_requests(path)
    assert "A" in str(info.value) and "message" in str(info.value)
    assert "SECRET2" not in str(info.value)


def test_load_requests_malformed_json(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("[{bad")
    with pytest.raises(RunError, match="JSON"):
        load_requests(path)
