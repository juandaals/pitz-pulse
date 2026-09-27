"""PATCH semantics (Spec 02 §7): merge, revalidate against the contract, record the diff."""

from typing import Any

from pydantic import ValidationError

from pitz_pulse.errors import ContractViolation, NotClassified, NotFound, field_errors
from pitz_pulse.repository import Repository, StoredRequest
from pitz_pulse.schema import Classification

CORRECTABLE_FIELDS = (
    "categoria",
    "prioridad",
    "area_sugerida",
    "idioma",
    "resumen",
    "requiere_info",
    "pregunta_seguimiento",
)


def apply_correction(
    repo: Repository,
    request_id: str,
    changes: dict[str, Any],
    author: str,
    reason: str | None,
    now: str,
) -> StoredRequest:
    unknown = set(changes) - set(CORRECTABLE_FIELDS)
    if unknown:
        raise ValueError(f"not correctable: {sorted(unknown)}")
    with repo.transaction():
        row = repo.get(request_id)
        if row is None:
            raise NotFound()
        if row.status != "classified":
            raise NotClassified()
        current = row.classification.model_dump(mode="json")
        try:
            merged = Classification.model_validate({**current, **changes})
        except ValidationError as exc:
            raise ContractViolation(field_errors(exc)) from None
        new = merged.model_dump(mode="json")
        diff = [name for name in CORRECTABLE_FIELDS if new[name] != current[name]]
        repo.insert_correction(
            request_id,
            {name: current[name] for name in diff},
            {name: new[name] for name in diff},
            author,
            reason,
            now,
        )
        repo.update_fields(
            request_id,
            {name: new[name] for name in diff},
            corrected=row.corrected or bool(diff),
            now=now,
        )
        return repo.get(request_id)
