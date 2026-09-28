import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pitz_pulse.schema import (
    CONTRACT_FIELDS,
    Area,
    Categoria,
    Classification,
    ClassificationShape,
    Idioma,
    ModelOutput,
    Prioridad,
    RequestInput,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

# Literal lists copied from the case — never derived from the enums under test.
CASE_FIELDS = (
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
CASE_CATEGORIAS = {"bug", "datos", "acceso", "automatizacion", "consulta", "otro"}
CASE_PRIORIDADES = {"alta", "media", "baja"}
CASE_AREAS = {"backend", "frontend", "data", "devops", "producto", "digital_transformation"}
CASE_IDIOMAS = {"es", "pt"}

VALID_OUTPUT = {
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "resumen": "Vendedor recibe error 500 al subir su catálogo",
    "requiere_info": True,
    "pregunta_seguimiento": "¿Qué archivo intentó subir?",
    "confianza": 0.86,
}


def test_contract_field_names_match_the_case():
    assert CONTRACT_FIELDS == CASE_FIELDS
    assert set(Classification.model_fields) == set(CASE_FIELDS)
    assert set(ClassificationShape.model_fields) == set(CASE_FIELDS)


def test_enum_values_match_the_case():
    assert {e.value for e in Categoria} == CASE_CATEGORIAS
    assert {e.value for e in Prioridad} == CASE_PRIORIDADES
    assert {e.value for e in Area} == CASE_AREAS
    assert {e.value for e in Idioma} == CASE_IDIOMAS


def test_valid_output_passes():
    assert ModelOutput.model_validate(VALID_OUTPUT).categoria is Categoria.BUG


@pytest.mark.parametrize(
    "field,value",
    [
        ("categoria", "automatización"),
        ("prioridad", "urgente"),
        ("area_sugerida", "infra"),
        ("idioma", "en"),
    ],
)
def test_unknown_enum_value_rejected(field, value):
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID_OUTPUT, field: value})


@pytest.mark.parametrize("words,ok", [(0, False), (1, True), (20, True), (21, False)])
def test_resumen_word_limit(words, ok):
    data = {**VALID_OUTPUT, "resumen": " ".join(["palabra"] * words)}
    if ok:
        ModelOutput.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            ModelOutput.model_validate(data)


def test_resumen_char_limit():
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID_OUTPUT, "resumen": "x" * 201})


@pytest.mark.parametrize(
    "requiere,pregunta,ok",
    [
        (True, "¿Cuál?", True),
        (True, None, False),
        (True, "", False),
        (True, "   ", False),
        (True, "x" * 301, False),
        (True, "¿Qué?" + " " * 300, False),  # raw length: trailing spaces count
        (True, "x" * 300, True),
        (False, None, True),
        (False, "", False),
        (False, "¿Cuál?", False),
    ],
)
def test_pregunta_rules(requiere, pregunta, ok):
    data = {**VALID_OUTPUT, "requiere_info": requiere, "pregunta_seguimiento": pregunta}
    if ok:
        ModelOutput.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            ModelOutput.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("requiere_info", "yes"),
        ("requiere_info", 1),
        ("confianza", True),
        ("confianza", "0.8"),
        ("confianza", -0.1),
        ("confianza", 1.1),
    ],
)
def test_strict_types(field, value):
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID_OUTPUT, field: value})


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID_OUTPUT, "version_prompt": "v9"})


def test_validator_messages_do_not_echo_values():
    secret = "SENTINEL " * 25
    with pytest.raises(ValidationError) as info:
        ModelOutput.model_validate({**VALID_OUTPUT, "resumen": secret})
    assert "SENTINEL" not in str(info.value.errors(include_input=False, include_url=False))


@pytest.mark.parametrize(
    "request_id,ok",
    [
        ("MSG-01", True),
        ("a_b-9", True),
        ("", False),
        ("x" * 65, False),
        ("bad id", False),
    ],
)
def test_request_id(request_id, ok):
    data = {"id": request_id, "message": "hola"}
    if ok:
        RequestInput.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            RequestInput.model_validate(data)


@pytest.mark.parametrize(
    "message,ok",
    [
        ("   ", False),
        ("a", True),
        (" a ", True),
        ("x" * 4000, True),
        ("x" * 4001, False),
        (" " * 7000 + "a", True),
        (" " * 8000 + "a", False),
    ],
)
def test_message_limits(message, ok):
    if ok:
        req = RequestInput.model_validate({"id": "A", "message": message})
        assert req.message == message  # original kept, never stripped
    else:
        with pytest.raises(ValidationError):
            RequestInput.model_validate({"id": "A", "message": message})


def test_source_area_limits():
    RequestInput.model_validate({"id": "A", "message": "m", "source_area": "Comercial MX"})
    with pytest.raises(ValidationError):
        RequestInput.model_validate({"id": "A", "message": "m", "source_area": "x" * 101})


def test_shape_accepts_rule_violations_that_classification_rejects():
    data = {"id": "A", "version_prompt": "v1", **VALID_OUTPUT, "resumen": "w " * 25}
    ClassificationShape.model_validate(data)
    with pytest.raises(ValidationError):
        Classification.model_validate(data)


def test_case_messages_file_is_valid():
    items = json.loads((REPO_ROOT / "mensajes.json").read_text(encoding="utf-8"))
    requests = [RequestInput.model_validate(item) for item in items]
    assert [r.id for r in requests] == [f"MSG-{n:02d}" for n in range(1, 13)]


def test_lone_surrogates_are_rejected_as_invalid_text():
    with pytest.raises(ValidationError, match="valid UTF-8"):
        RequestInput.model_validate({"id": "a", "message": "hola \ud800"})
    with pytest.raises(ValidationError, match="valid UTF-8"):
        RequestInput.model_validate({"id": "a", "message": "hola", "source_area": "\udfff"})
