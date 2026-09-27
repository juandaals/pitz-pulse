"""Case contract. Field names and enum values are the case contract: never translate them."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)


class Categoria(StrEnum):
    BUG = "bug"
    DATOS = "datos"
    ACCESO = "acceso"
    AUTOMATIZACION = "automatizacion"
    CONSULTA = "consulta"
    OTRO = "otro"


class Prioridad(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAJA = "baja"


class Area(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    DATA = "data"
    DEVOPS = "devops"
    PRODUCTO = "producto"
    DIGITAL_TRANSFORMATION = "digital_transformation"


class Idioma(StrEnum):
    ES = "es"
    PT = "pt"


CONTRACT_FIELDS = (
    "id",
    "categoria",
    "prioridad",
    "area_sugerida",
    "idioma",
    "resumen",
    "requiere_info",
    "pregunta_seguimiento",
    "confianza",
    "version_prompt",
)
MODEL_FIELDS = CONTRACT_FIELDS[1:-1]
MAX_SUMMARY_WORDS = 20
MAX_SUMMARY_CHARS = 200
MAX_QUESTION_CHARS = 300
MAX_MESSAGE_CHARS = 4000
MAX_MESSAGE_RAW_CHARS = 8000

# Field-level strictness: model-level strict=True would reject enum values given as strings.
Confidence = Annotated[float, Field(strict=True, ge=0, le=1)]
_FORBID_EXTRA = ConfigDict(extra="forbid")


def word_count(text: str) -> int:
    return len(text.split())


ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


def ensure_utf8(value: str) -> str:
    """JSON allows lone surrogates; they cannot be hashed, stored or sent, so reject them."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid UTF-8 text") from None
    return value


class RequestInput(BaseModel):
    model_config = _FORBID_EXTRA

    id: StrictStr = Field(pattern=ID_PATTERN)
    message: StrictStr
    source_area: StrictStr | None = None

    @field_validator("message")
    @classmethod
    def _message_length(cls, value: str) -> str:
        ensure_utf8(value)
        size = len(value.strip())
        if not 1 <= size <= MAX_MESSAGE_CHARS or len(value) > MAX_MESSAGE_RAW_CHARS:
            raise ValueError(
                f"must have 1-{MAX_MESSAGE_CHARS} characters after strip "
                f"and at most {MAX_MESSAGE_RAW_CHARS} in total"
            )
        return value  # the original text is stored and hashed; never strip it

    @field_validator("source_area")
    @classmethod
    def _source_area_length(cls, value: str | None) -> str | None:
        if value is not None:
            ensure_utf8(value)
            if not 1 <= len(value.strip()) <= 100:
                raise ValueError("must have 1-100 characters after strip")
        return value


class _ModelFields(BaseModel):
    model_config = _FORBID_EXTRA

    categoria: Categoria = Field(description="Request type.")
    prioridad: Prioridad = Field(
        description="alta: affects customers, money, legal obligations or blocks an operation; "
        "media: affects an internal area or few users and has a workaround; "
        "baja: questions or improvements without immediate impact."
    )
    area_sugerida: Area = Field(description="Team that should handle the request.")
    idioma: Idioma = Field(description="Language of the original message.")
    resumen: StrictStr = Field(
        description="Clear summary of what is asked, in Spanish, "
        "at most 20 words and 200 characters."
    )
    requiere_info: StrictBool = Field(
        description="true if the receiving team cannot start work without asking something first."
    )
    pregunta_seguimiento: StrictStr | None = Field(
        description="Only when requiere_info is true: one question for the requester in the "
        "language of the original message, at most 300 characters. null otherwise."
    )
    confianza: Confidence = Field(description="Confidence in this classification, from 0 to 1.")


def _check_rules(model: _ModelFields) -> None:
    words = word_count(model.resumen)
    if not 1 <= words <= MAX_SUMMARY_WORDS:
        raise ValueError(f"resumen must have 1-{MAX_SUMMARY_WORDS} words, has {words}")
    if len(model.resumen) > MAX_SUMMARY_CHARS:
        raise ValueError(f"resumen must have at most {MAX_SUMMARY_CHARS} characters")
    question = model.pregunta_seguimiento
    if model.requiere_info:
        if question is None or not question.strip():
            raise ValueError("pregunta_seguimiento is required when requiere_info is true")
        if len(question.strip()) > MAX_QUESTION_CHARS:
            raise ValueError(
                f"pregunta_seguimiento must have at most {MAX_QUESTION_CHARS} characters"
            )
    elif question is not None:
        raise ValueError("pregunta_seguimiento must be null when requiere_info is false")


class ModelOutput(_ModelFields):
    """The 8 fields the model produces."""

    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_rules(self)
        return self


class ClassificationShape(_ModelFields):
    """Types and enums only: Spec 03 validates run files structurally with it."""

    id: StrictStr
    version_prompt: StrictStr


class Classification(ClassificationShape):
    @model_validator(mode="after")
    def _rules(self) -> Self:
        _check_rules(self)
        return self
