import json
import time
from pathlib import Path

import pytest

from pitz_pulse.config import parse_llm_settings
from pitz_pulse.masking import mask_request
from pitz_pulse.prompts import load_prompt
from pitz_pulse.providers import build_adapter
from pitz_pulse.providers.base import Deadline
from pitz_pulse.providers.mock import MockAdapter
from pitz_pulse.schema import ModelOutput
from pitz_pulse.tool_schema import build_tool_schema

APP_ROOT = Path(__file__).resolve().parents[1]
MESSAGES = json.loads((APP_ROOT.parents[1] / "mensajes.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("item", MESSAGES, ids=lambda item: item["id"])
def test_mock_output_is_valid_and_deterministic(item):
    prompt = load_prompt(APP_ROOT, "v1")
    user = prompt.render_user(mask_request(item["message"], item["source_area"]))
    adapter = MockAdapter()
    first = adapter.invoke(prompt.system, user, build_tool_schema(False), 10)
    assert first == adapter.invoke(prompt.system, user, build_tool_schema(False), 10)
    ModelOutput.model_validate(first.tool_input)
    assert (first.model, first.actual_model, first.cost_usd) == ("mock", "mock", 0)


def test_mock_detects_portuguese():
    prompt = load_prompt(APP_ROOT, "v1")
    user = prompt.render_user(mask_request("Preciso de uma planilha com as vendas", None))
    assert MockAdapter().invoke("", user, {}, 10).tool_input["idioma"] == "pt"


def test_factory_builds_mock_from_default_settings():
    adapter = build_adapter(parse_llm_settings({}))
    assert (adapter.provider, adapter.model) == ("mock", "mock")


def test_deadline():
    deadline = Deadline.after(0.05)
    assert deadline.remaining() > 0
    time.sleep(0.06)
    assert deadline.remaining() < 0
