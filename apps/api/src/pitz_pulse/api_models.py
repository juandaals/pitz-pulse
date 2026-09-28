"""HTTP models. Enums are typed so OpenAPI carries their values; bodies forbid extra fields."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from pitz_pulse.corrections import CORRECTABLE_FIELDS
from pitz_pulse.repository import ListFilters, StoredRequest
from pitz_pulse.schema import CONTRACT_FIELDS, Area, Categoria, Idioma, Prioridad, ensure_utf8

Status = Literal["pending", "classified", "failed"]
_NOT_NULLABLE = ("categoria", "prioridad", "area_sugerida", "idioma", "resumen", "requiere_info")


class PatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Plain types with a None default: OpenAPI shows no null branch; omitted fields stay unset.
    categoria: Categoria = None  # type: ignore[assignment]
    prioridad: Prioridad = None  # type: ignore[assignment]
    area_sugerida: Area = None  # type: ignore[assignment]
    idioma: Idioma = None  # type: ignore[assignment]
    resumen: StrictStr = None  # type: ignore[assignment]
    requiere_info: StrictBool = None  # type: ignore[assignment]
    pregunta_seguimiento: StrictStr | None = None
    author: StrictStr
    reason: StrictStr | None = None

    @field_validator(*_NOT_NULLABLE, mode="before")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("cannot be null; omit the field to keep its value")
        return value

    @field_validator("resumen", "pregunta_seguimiento")
    @classmethod
    def _utf8(cls, value: str | None) -> str | None:
        return ensure_utf8(value) if value is not None else None

    @field_validator("author")
    @classmethod
    def _author(cls, value: str) -> str:
        value = ensure_utf8(value).strip()
        if not 1 <= len(value) <= 100:
            raise ValueError("must have 1-100 characters after strip")
        return value

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = ensure_utf8(value).strip()
        if len(value) > 500:
            raise ValueError("must have at most 500 characters")
        return value or None

    def changes(self) -> dict[str, Any]:
        present = self.model_dump(mode="json", exclude_unset=True)
        return {name: present[name] for name in CORRECTABLE_FIELDS if name in present}


class ListQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categoria: Categoria | None = None
    prioridad: Prioridad | None = None
    area_sugerida: Area | None = None
    status: Status | None = None
    needs_review: bool | None = None
    limit: int = Field(20, ge=1, le=100)
    offset: int = Field(0, ge=0, le=2**31 - 1)

    def filters(self) -> ListFilters:
        return ListFilters(
            categoria=self.categoria,
            prioridad=self.prioridad,
            area_sugerida=self.area_sugerida,
            status=self.status,
            needs_review=self.needs_review,
        )


class Item(BaseModel):
    id: str
    message: str
    source_area: str | None
    status: Status
    categoria: Categoria | None
    prioridad: Prioridad | None
    area_sugerida: Area | None
    idioma: Idioma | None
    resumen: str | None
    requiere_info: bool | None
    pregunta_seguimiento: str | None
    confianza: float | None
    version_prompt: str | None
    provider: str | None
    model: str | None
    needs_review: bool
    corrected: bool
    error: str | None
    created_at: str
    updated_at: str
    possible_duplicate_of: str | None

    @classmethod
    def from_stored(cls, row: StoredRequest, needs_review: bool) -> "Item":
        values = row.classification.model_dump() if row.classification else {}
        return cls(
            id=row.id,
            message=row.message,
            source_area=row.source_area,
            status=row.status,
            **{name: values.get(name) for name in CONTRACT_FIELDS[1:]},
            provider=row.provider,
            model=row.model,
            needs_review=needs_review,
            corrected=row.corrected,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
            possible_duplicate_of=row.possible_duplicate_of,
        )


class CorrectionOut(BaseModel):
    previous_values: dict[str, Any]
    new_values: dict[str, Any]
    author: str
    reason: str | None
    created_at: str


class ItemDetail(Item):
    original_classification: dict[str, Any] | None
    corrections: list[CorrectionOut]

    @classmethod
    def from_stored(  # type: ignore[override]
        cls, row: StoredRequest, needs_review: bool, corrections: list[dict[str, Any]]
    ) -> "ItemDetail":
        item = Item.from_stored(row, needs_review)
        return cls(
            **item.model_dump(),
            original_classification=row.original_classification,
            corrections=[CorrectionOut(**c) for c in corrections],
        )


class FieldError(BaseModel):
    loc: list[str]
    msg: str


class ErrorBody(BaseModel):
    error: str
    detail: str
    fields: list[FieldError] | None = None
    kind: str | None = None


class Page(BaseModel):
    items: list[Item]
    total: int
    limit: int
    offset: int
