import json
import re
from pathlib import Path

import pytest

from pitz_pulse import runs
from pitz_pulse.labels import LABEL_FILES, LabelError, load_labels
from pitz_pulse.masking import mask_request

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parents[1]

MESSAGE_FILES = {
    "case": REPO_ROOT / "mensajes.json",
    "edge": APP_ROOT / "eval" / "golden" / "edge_cases.messages.json",
}
LABEL_PATHS = {
    "case": REPO_ROOT / "etiquetas_esperadas.json",
    "edge": APP_ROOT / "eval" / "golden" / "edge_cases.labels.json",
}

VALID_LABEL = {
    "id": "X-1",
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "requiere_info": False,
    "label_status": "draft",
}


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("set_name", ["case", "edge"])
def test_message_files_load_with_runs(set_name):
    assert runs.load_requests(MESSAGE_FILES[set_name])


@pytest.mark.parametrize("set_name", ["case", "edge"])
def test_label_files_load_with_labels(set_name):
    assert load_labels(LABEL_PATHS[set_name])


@pytest.mark.parametrize("set_name", ["case", "edge"])
def test_label_ids_match_message_ids(set_name):
    requests = runs.load_requests(MESSAGE_FILES[set_name])
    labels = load_labels(LABEL_PATHS[set_name])
    assert sorted(r.id for r in requests) == sorted(label.id for label in labels)


def test_case_labels_are_all_approved():
    labels = load_labels(LABEL_PATHS["case"])
    assert all(label.label_status == "approved" for label in labels)


def test_edge_label_set_has_18_entries():
    assert len(load_labels(LABEL_PATHS["edge"])) == 18


_PII_PLACEHOLDERS = re.compile(r"\[(EMAIL|PHONE|CPF|CNPJ|CURP|RFC)\]")


def test_edge_set_covers_every_pii_type():
    items = json.loads(MESSAGE_FILES["edge"].read_text(encoding="utf-8"))
    found: set[str] = set()
    for item in items:
        masked = mask_request(item["message"], item.get("source_area"))
        text = masked.message + " " + (masked.source_area or "")
        found |= set(_PII_PLACEHOLDERS.findall(text))
    assert found == {"EMAIL", "PHONE", "CPF", "CNPJ", "CURP", "RFC"}


def test_label_files_keys_match_runs_sets():
    assert LABEL_FILES.keys() == runs.SETS.keys()


def test_load_labels_rejects_extra_field(tmp_path):
    path = _write(tmp_path, [{**VALID_LABEL, "extra_field": "x"}])
    with pytest.raises(LabelError) as info:
        load_labels(path)
    assert "X-1" in str(info.value) and "extra_field" in str(info.value)


def test_load_labels_rejects_missing_field(tmp_path):
    item = dict(VALID_LABEL)
    del item["prioridad"]
    path = _write(tmp_path, [item])
    with pytest.raises(LabelError) as info:
        load_labels(path)
    assert "prioridad" in str(info.value)


def test_load_labels_rejects_bad_enum(tmp_path):
    path = _write(tmp_path, [{**VALID_LABEL, "categoria": "automatización"}])
    with pytest.raises(LabelError) as info:
        load_labels(path)
    assert "categoria" in str(info.value)
    assert "automatización" not in str(info.value)


def test_load_labels_rejects_duplicate_id(tmp_path):
    path = _write(tmp_path, [VALID_LABEL, VALID_LABEL])
    with pytest.raises(LabelError) as info:
        load_labels(path)
    assert "X-1" in str(info.value)


def test_load_labels_rejects_non_list_json(tmp_path):
    path = _write(tmp_path, {"id": "X-1"})
    with pytest.raises(LabelError):
        load_labels(path)
