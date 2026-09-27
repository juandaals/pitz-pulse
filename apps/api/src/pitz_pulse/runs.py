"""Run files: <set>__<prompt>__<provider>__<model>[__suffix].json + .meta.json (D26)."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError

from pitz_pulse.schema import CONTRACT_FIELDS, Classification, RequestInput

SETS = {"case": "mensajes.json", "edge": "apps/api/eval/golden/edge_cases.messages.json"}
_SUFFIX = re.compile(r"[a-z0-9-]{1,20}")
_STEM = re.compile(r"[a-z0-9._-]+")


class RunError(ValueError):
    pass


def repo_root(app_root: Path) -> Path:
    if len(app_root.parents) < 2:
        raise RunError(f"APP_ROOT {app_root} is not inside the repository (<repo>/apps/api)")
    return app_root.parents[1]


def runs_dir(app_root: Path) -> Path:
    return app_root / "eval" / "runs"


def run_stem(
    set_name: str, prompt_version: str, provider: str, model: str, suffix: str | None = None
) -> str:
    if set_name not in SETS:
        raise RunError(f"unknown set {set_name!r}; use one of {sorted(SETS)}")
    if suffix is not None and not _SUFFIX.fullmatch(suffix):
        raise RunError("SUFFIX must match [a-z0-9-]{1,20}")
    safe_model = re.sub(r"[^a-z0-9.-]", "-", model.lower())
    parts = [set_name, prompt_version, provider, safe_model] + ([suffix] if suffix else [])
    return "__".join(parts)


def run_paths(app_root: Path, stem: str) -> tuple[Path, Path]:
    if not _STEM.fullmatch(stem) or ".." in stem:
        raise RunError("invalid run name")
    directory = runs_dir(app_root)
    return directory / f"{stem}.json", directory / f"{stem}.meta.json"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def serialize_run(items: list[Classification]) -> bytes:
    rows = []
    for item in items:
        dumped = item.model_dump(mode="json")
        rows.append({name: dumped[name] for name in CONTRACT_FIELDS})
    return (json.dumps(rows, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def write_pair(run_path: Path, meta_path: Path, run_bytes: bytes, meta: dict) -> None:
    """Meta first (with the results hash), then the run: a mismatch is always detectable."""
    meta = {**meta, "results_sha256": sha256_hex(run_bytes)}
    meta_bytes = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write(meta_path, meta_bytes)
    _atomic_write(run_path, run_bytes)


def ensure_writable(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, probe = tempfile.mkstemp(dir=directory, suffix=".tmp")
    os.close(fd)
    Path(probe).unlink()


def _label(item: object, index: int) -> object:
    if isinstance(item, dict) and isinstance(item.get("id"), str):
        return item["id"]
    return index


def load_requests(path: Path) -> list[RequestInput]:
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise RunError(f"{path.name} is not valid JSON") from None
    if not isinstance(items, list):
        raise RunError(f"{path.name} must be a JSON list of requests")
    if not items:
        raise RunError(f"{path.name} contains no requests")
    requests, seen = [], set()
    for index, item in enumerate(items):
        try:
            request = RequestInput.model_validate(item)
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)
            )
            raise RunError(f"invalid input item {_label(item, index)}: {fields}") from None
        if request.id in seen:
            raise RunError(f"duplicate id {request.id}")
        seen.add(request.id)
        requests.append(request)
    return requests
