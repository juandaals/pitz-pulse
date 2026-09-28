import json

from pitz_pulse.prompts import TOOL_NAME
from pitz_pulse.schema import MODEL_FIELDS, Area, Categoria, Idioma, Prioridad
from pitz_pulse.tool_schema import build_tool_schema

FORBIDDEN = {
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "pattern",
    "default",
    "title",
    "$ref",
    "$defs",
    "allOf",
}


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key != "properties":
                yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _keys(item)


def test_no_keywords_strict_mode_rejects():
    assert not FORBIDDEN & set(_keys(build_tool_schema(strict=True)["input_schema"]))


def test_shape():
    tool = build_tool_schema(strict=True)
    schema = tool["input_schema"]
    assert tool["name"] == TOOL_NAME and tool["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == list(MODEL_FIELDS)
    assert set(schema["properties"]) == set(MODEL_FIELDS)


def test_strict_flag_absent_when_unsupported():
    assert "strict" not in build_tool_schema(strict=False)


def test_nullable_question_and_enums():
    props = build_tool_schema(strict=True)["input_schema"]["properties"]
    assert {o.get("type") for o in props["pregunta_seguimiento"]["anyOf"]} == {"string", "null"}
    assert set(props["categoria"]["enum"]) == {e.value for e in Categoria}
    assert set(props["prioridad"]["enum"]) == {e.value for e in Prioridad}
    assert set(props["area_sugerida"]["enum"]) == {e.value for e in Area}
    assert set(props["idioma"]["enum"]) == {e.value for e in Idioma}


def test_every_field_describes_its_rule():
    props = build_tool_schema(strict=True)["input_schema"]["properties"]
    assert all(props[name].get("description") for name in MODEL_FIELDS)
    assert "20 words" in props["resumen"]["description"]
    assert "200 characters" in props["resumen"]["description"]
    assert "Spanish" in props["resumen"]["description"]
    assert "300 characters" in props["pregunta_seguimiento"]["description"]
    assert "minimum=0" in props["confianza"]["description"]
    json.dumps(props)
