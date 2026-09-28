"""JSON schema of the classification tool, shaped for Anthropic strict tool use (G23)."""

from typing import Any

from pitz_pulse.prompts import TOOL_NAME
from pitz_pulse.schema import MODEL_FIELDS, ModelOutput

_DROP = {"default", "title"}
_MOVE_TO_DESCRIPTION = {"minimum", "maximum", "minLength", "maxLength", "pattern"}


def build_tool_schema(strict: bool) -> dict[str, Any]:
    raw = ModelOutput.model_json_schema()
    definitions = raw.pop("$defs", {})
    schema = _clean(_inline(raw, definitions))
    schema["additionalProperties"] = False
    schema["required"] = list(MODEL_FIELDS)
    tool: dict[str, Any] = {
        "name": TOOL_NAME,
        "description": "Record the triage classification of one internal request.",
        "input_schema": schema,
    }
    if strict:
        tool["strict"] = True  # langchain-anthropic ignores bind_tools(strict=) for dict tools
    return tool


def _inline(node: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            target = definitions[node["$ref"].rsplit("/", 1)[-1]]
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            return {**_inline(target, definitions), **_inline(siblings, definitions)}
        return {key: _inline(value, definitions) for key, value in node.items()}
    if isinstance(node, list):
        return [_inline(item, definitions) for item in node]
    return node


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    notes = []
    for key, value in node.items():
        if key == "properties":  # keys here are field names, never schema keywords
            cleaned[key] = {name: _clean(sub) for name, sub in value.items()}
        elif key in _DROP:
            continue
        elif key in _MOVE_TO_DESCRIPTION:
            notes.append(f"{key}={value}")
        else:
            cleaned[key] = _clean(value)
    if notes:
        base = cleaned.get("description", "")
        cleaned["description"] = f"{base} ({', '.join(notes)})".strip()
    return cleaned
