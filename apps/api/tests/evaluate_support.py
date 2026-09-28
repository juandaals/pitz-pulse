import json

from pitz_pulse.evaluate import main
from pitz_pulse.runs import run_paths, sha256_hex, write_pair

CASE_MESSAGES = [
    {"id": "EX-1", "message": "Mensaje de prueba uno."},
    {"id": "EX-2", "message": "Mensaje de prueba dos."},
]
CASE_LABELS = [
    {
        "id": "EX-1",
        "categoria": "bug",
        "prioridad": "alta",
        "area_sugerida": "backend",
        "idioma": "es",
        "requiere_info": False,
        "label_status": "approved",
    },
    {
        "id": "EX-2",
        "categoria": "consulta",
        "prioridad": "baja",
        "area_sugerida": "producto",
        "idioma": "es",
        "requiere_info": False,
        "label_status": "approved",
    },
]
RESULT_FIELDS = {
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "resumen": "Resumen corto de prueba",
    "requiere_info": False,
    "pregunta_seguimiento": None,
    "confianza": 0.9,
    "version_prompt": "v1",
}
DROP = object()


def build_fixture(
    tmp_path,
    *,
    set_name="case",
    stem=None,
    items=None,
    run_bytes=None,
    meta_overrides=None,
    labels_text=None,
    input_text=None,
):
    """A shared valid fixture builder: each failing test applies ONE mutation to it."""
    app_root = tmp_path / "apps" / "api"
    (app_root / "eval" / "runs").mkdir(parents=True, exist_ok=True)

    if set_name == "case":
        input_path = tmp_path / "mensajes.json"
        default_labels_path = tmp_path / "etiquetas_esperadas.json"
    else:
        (app_root / "eval" / "golden").mkdir(parents=True, exist_ok=True)
        input_path = app_root / "eval" / "golden" / "edge_cases.messages.json"
        default_labels_path = app_root / "eval" / "golden" / "edge_cases.labels.json"

    input_path.write_text(
        input_text if input_text is not None else json.dumps(CASE_MESSAGES), encoding="utf-8"
    )
    default_labels_path.write_text(
        labels_text if labels_text is not None else json.dumps(CASE_LABELS), encoding="utf-8"
    )

    result_items = (
        items
        if items is not None
        else [
            {"id": "EX-1", **RESULT_FIELDS},
            {"id": "EX-2", **RESULT_FIELDS},
        ]
    )
    final_run_bytes = (
        run_bytes if run_bytes is not None else (json.dumps(result_items) + "\n").encode("utf-8")
    )

    meta = {
        "set": set_name,
        "provider": "mock",
        "model": "mock",
        "prompt_version": "v1",
        "prompt_sha256": "a" * 64,
        "tool_schema_sha256": "b" * 64,
        "temperature": 0.5,
        "n": len(result_items),
        "failures": [],
        "input_sha256": sha256_hex(input_path.read_bytes()),
    }
    if meta_overrides:
        for key, value in meta_overrides.items():
            if value is DROP:
                meta.pop(key, None)
            else:
                meta[key] = value

    stem = stem or f"{set_name}__v1__mock__mock"
    run_path, meta_path = run_paths(app_root, stem)
    write_pair(run_path, meta_path, final_run_bytes, meta)
    return app_root, stem


def run_eval(app_root, argv, **extra_env):
    env = {"APP_ROOT": str(app_root), **extra_env}
    return main(argv, env=env)
