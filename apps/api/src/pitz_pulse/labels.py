"""Expected labels: the golden classifications the eval harness scores runs against."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from pitz_pulse.runs import item_label
from pitz_pulse.schema import Area, Categoria, Idioma, Prioridad

LABEL_FILES = {
    "case": "etiquetas_esperadas.json",
    "edge": "apps/api/eval/golden/edge_cases.labels.json",
}
SCORED_FIELDS = ("categoria", "prioridad", "area_sugerida", "idioma", "requiere_info")


class LabelError(ValueError):
    pass


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    categoria: Categoria
    prioridad: Prioridad
    area_sugerida: Area
    idioma: Idioma
    requiere_info: StrictBool
    label_status: Literal["approved", "draft"]
    justificacion: str | None = None


def load_labels(path: Path) -> list[Label]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise LabelError(f"{path.name} does not exist") from None
    except UnicodeDecodeError:
        raise LabelError(f"{path.name} is not valid UTF-8") from None
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        raise LabelError(f"{path.name} is not valid JSON") from None
    if not isinstance(items, list):
        raise LabelError(f"{path.name} must be a JSON list of labels")
    if not items:
        raise LabelError(f"{path.name} contains no labels")
    labels, seen = [], set()
    for index, item in enumerate(items):
        try:
            label = Label.model_validate(item)
        except ValidationError as exc:
            fields = ", ".join(
                ".".join(map(str, e["loc"])) for e in exc.errors(include_input=False)
            )
            raise LabelError(f"invalid label item {item_label(item, index)}: {fields}") from None
        if label.id in seen:
            raise LabelError(f"duplicate id {label.id}")
        seen.add(label.id)
        labels.append(label)
    return labels
