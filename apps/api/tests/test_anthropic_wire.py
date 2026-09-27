"""Anthropic adapter through the real langchain-anthropic + anthropic stack, offline.

Only the HTTP transport is replaced (httpx2 MockTransport): the request body is the one the
real SDKs would put on the wire, and the canned reply is parsed by the real stack.
"""

import json

import anthropic
import httpx2
import pytest

from pitz_pulse.config import parse_llm_settings
from pitz_pulse.providers.anthropic_api import AnthropicApiAdapter
from pitz_pulse.tool_schema import build_tool_schema

TOOL_INPUT = {
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "resumen": "Error al subir catálogo",
    "requiere_info": False,
    "pregunta_seguimiento": None,
    "confianza": 0.9,
}


def canned_reply(tool_name):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "tool_use", "id": "toolu_1", "name": tool_name, "input": TOOL_INPUT}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 100},
    }


@pytest.fixture
def wire():
    adapter = AnthropicApiAdapter(parse_llm_settings({"ANTHROPIC_API_KEY": "sk-ant-api-test"}))
    tool = build_tool_schema(strict=True)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json=canned_reply(tool["name"]))

    chat = adapter._chat
    http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    chat.__dict__["_client"] = anthropic.Client(**chat._client_params, http_client=http_client)
    return adapter, tool, seen


def test_wire_body_and_parsed_reply(wire):
    adapter, tool, seen = wire
    call = adapter.invoke("system", "user", tool, 300)
    assert len(seen) == 1 and seen[0].url.path.endswith("/v1/messages")
    assert seen[0].headers["x-api-key"] == "sk-ant-api-test"
    body = json.loads(seen[0].content)
    assert body["temperature"] == 0.0
    assert body["tool_choice"] == {"type": "tool", "name": tool["name"]}
    assert body["tools"][0]["name"] == tool["name"] and body["tools"][0]["strict"] is True
    assert call.tool_input == TOOL_INPUT and call.stop_reason == "tool_use"
    assert (call.input_tokens, call.output_tokens) == (1000, 100)
    assert call.actual_model == "claude-haiku-4-5-20251001" and call.transport_retries == 0
