"""Temp repo tree plus a hand-built real-provider baseline run for promote tests."""

import json

from evaluate_support import CASE_MESSAGES, RESULT_FIELDS

from pitz_pulse.config import DEFAULT_APP_ROOT
from pitz_pulse.prompts import load_prompt
from pitz_pulse.runs import canonical_sha256, run_paths, sha256_hex, write_pair
from pitz_pulse.tool_schema import build_tool_schema

BASELINE_STEM = "case__v1__anthropic_api__claude-haiku-4-5"


def build_repo(tmp_path):
    """<tmp>/apps/api with the real v1 prompt; <tmp>/mensajes.json with the case ids."""
    app_root = tmp_path / "apps" / "api"
    (app_root / "prompts").mkdir(parents=True)
    (app_root / "eval" / "runs").mkdir(parents=True)
    (app_root / "prompts" / "v1.md").write_bytes(
        (DEFAULT_APP_ROOT / "prompts" / "v1.md").read_bytes()
    )
    (tmp_path / "mensajes.json").write_text(json.dumps(CASE_MESSAGES), encoding="utf-8")
    return app_root


def baseline_items():
    return [{"id": message["id"], **RESULT_FIELDS} for message in CASE_MESSAGES]


def baseline_meta(app_root):
    return {
        "set": "case",
        "input_file": "mensajes.json",
        "input_sha256": sha256_hex((app_root.parents[1] / "mensajes.json").read_bytes()),
        "provider": "anthropic_api",
        "model": "claude-haiku-4-5",
        "billing": "api",
        "prompt_version": "v1",
        "prompt_sha256": load_prompt(app_root, "v1").sha256,
        "tool_schema_sha256": canonical_sha256(build_tool_schema(strict=True)),
        "temperature": 0.0,
        "n": len(CASE_MESSAGES),
        "failures": [],
        "run_at": "2026-09-27T10:00:00Z",
    }


def write_run(app_root, *, stem=BASELINE_STEM, items=None, meta_overrides=None):
    """Writes the baseline pair (correct hashes) with ONE optional mutation applied."""
    items = baseline_items() if items is None else items
    meta = {**baseline_meta(app_root), **(meta_overrides or {})}
    run_path, meta_path = run_paths(app_root, stem)
    write_pair(run_path, meta_path, (json.dumps(items, ensure_ascii=False) + "\n").encode(), meta)
    return stem
