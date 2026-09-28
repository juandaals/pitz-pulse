"""A real, isolated repo tree for driving `make compare-models` end-to-end (mock provider only).

Mirrors `promote_support.build_repo` (the real v1 prompt, a synthetic `mensajes.json`) and adds
the `edge` set's golden files, since `compare-models` always classifies both sets.
"""

import json

from evaluate_support import CASE_LABELS, CASE_MESSAGES

from pitz_pulse.config import DEFAULT_APP_ROOT

EDGE_MESSAGES = [{"id": "EDGE-1", "message": "Otro mensaje de prueba para el set edge."}]
EDGE_LABELS = [
    {
        "id": "EDGE-1",
        "categoria": "consulta",
        "prioridad": "media",
        "area_sugerida": "producto",
        "idioma": "es",
        "requiere_info": False,
        "label_status": "approved",
    }
]


def build_compare_tree(tmp_path):
    """<tmp>/apps/api with the real v1 prompt and both sets' golden files; <tmp>/mensajes.json
    and <tmp>/etiquetas_esperadas.json at repo root, matching the real layout (`runs.SETS`,
    `labels.LABEL_FILES`).
    """
    app_root = tmp_path / "apps" / "api"
    (app_root / "prompts").mkdir(parents=True)
    (app_root / "eval" / "runs").mkdir(parents=True)
    (app_root / "eval" / "golden").mkdir(parents=True)
    (app_root / "prompts" / "v1.md").write_bytes(
        (DEFAULT_APP_ROOT / "prompts" / "v1.md").read_bytes()
    )
    (tmp_path / "mensajes.json").write_text(json.dumps(CASE_MESSAGES), encoding="utf-8")
    (tmp_path / "etiquetas_esperadas.json").write_text(json.dumps(CASE_LABELS), encoding="utf-8")
    (app_root / "eval" / "golden" / "edge_cases.messages.json").write_text(
        json.dumps(EDGE_MESSAGES), encoding="utf-8"
    )
    (app_root / "eval" / "golden" / "edge_cases.labels.json").write_text(
        json.dumps(EDGE_LABELS), encoding="utf-8"
    )
    return app_root
