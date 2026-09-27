import anthropic
import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from pitz_pulse.config import parse_llm_settings
from pitz_pulse.providers.anthropic_api import AnthropicApiAdapter, map_anthropic_error
from pitz_pulse.providers.base import LLMError
from pitz_pulse.tool_schema import build_tool_schema

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
TOOL = {"name": "record_classification"}


def settings(**env):
    return parse_llm_settings({"ANTHROPIC_API_KEY": "sk-ant-api-test", **env})


class StubChat:
    def __init__(self, results):
        self.results = list(results)
        self.bound_with = None
        self.calls = 0

    def bind_tools(self, tools, **kwargs):
        self.bound_with = (tools, kwargs)
        return self

    def invoke(self, messages):
        self.calls += 1
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def ai(tool_calls, stop="tool_use", invalid=None):
    return AIMessage(
        content="",
        tool_calls=tool_calls,
        invalid_tool_calls=invalid or [],
        usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
        response_metadata={"stop_reason": stop, "model_name": "claude-haiku-4-5-20251001"},
    )


OK = ai([{"name": "record_classification", "args": {"a": 1}, "id": "1"}])


def status_error(code, retry_after=None):
    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    response = httpx.Response(code, request=REQUEST, headers=headers)
    return anthropic.APIStatusError("x", response=response, body=None)


def adapter_with(results, sleeps=None, **env):
    stub = StubChat(results)
    sleep = sleeps.append if sleeps is not None else (lambda seconds: None)
    return AnthropicApiAdapter(settings(**env), chat_model=stub, sleep=sleep), stub


def test_temperature_zero_is_sent_and_none_is_not():
    zero = AnthropicApiAdapter(settings())._chat._get_request_payload([HumanMessage("u")])
    assert zero["extra_body"]["temperature"] == 0.0
    none = AnthropicApiAdapter(settings(LLM_MODEL="claude-sonnet-5", LLM_TEMPERATURE="none"))
    payload = none._chat._get_request_payload([HumanMessage("u")])
    assert "temperature" not in payload and not (payload.get("extra_body") or {}).get("temperature")


def test_forced_tool_and_strict_in_payload():
    chat = AnthropicApiAdapter(settings())._chat
    bound = chat.bind_tools([build_tool_schema(strict=True)], tool_choice="record_classification")
    payload = chat._get_request_payload([HumanMessage("u")], **bound.kwargs)
    assert payload["tool_choice"] == {"type": "tool", "name": "record_classification"}
    assert payload["tools"][0]["strict"] is True


def test_client_uses_explicit_key_default_url_and_no_sdk_retries():
    chat = AnthropicApiAdapter(settings())._chat
    assert chat.anthropic_api_key.get_secret_value() == "sk-ant-api-test"
    assert chat.anthropic_api_url == "https://api.anthropic.com"
    assert chat.max_retries == 0 and chat.default_request_timeout == 30


def test_parses_tool_call_usage_cost_and_actual_model():
    adapter, stub = adapter_with([OK])
    call = adapter.invoke("s", "u", TOOL, 300)
    assert call.tool_input == {"a": 1} and call.stop_reason == "tool_use"
    assert (call.input_tokens, call.output_tokens, call.transport_retries) == (1000, 100, 0)
    assert call.cost_usd == pytest.approx(0.0015) == call.equivalent_api_cost_usd
    assert (call.model, call.actual_model) == ("claude-haiku-4-5", "claude-haiku-4-5-20251001")
    assert stub.bound_with[1]["tool_choice"] == "record_classification"


@pytest.mark.parametrize(
    "message",
    [
        ai([], stop="max_tokens", invalid=[{"name": "x", "args": "{", "id": "1", "error": None}]),
        ai([]),
    ],
)
def test_missing_tool_call_is_none_not_an_exception(message):
    adapter, _ = adapter_with([message])
    assert adapter.invoke("s", "u", TOOL, 300).tool_input is None


@pytest.mark.parametrize(
    "exc,kind",
    [
        (anthropic.APIConnectionError(request=REQUEST), "unavailable"),
        (anthropic.APITimeoutError(request=REQUEST), "unavailable"),
        (status_error(408), "unavailable"),
        (status_error(409), "unavailable"),
        (status_error(429), "unavailable"),
        (status_error(500), "unavailable"),
        (status_error(529), "unavailable"),
        (status_error(400), "rejected"),
        (status_error(401), "rejected"),
        (status_error(413), "rejected"),
        (status_error(422), "rejected"),
    ],
)
def test_error_mapping(exc, kind):
    assert map_anthropic_error(exc, 1.0).kind == kind


def test_retries_unavailable_then_succeeds():
    sleeps = []
    adapter, stub = adapter_with([status_error(529), status_error(429, retry_after=2), OK], sleeps)
    call = adapter.invoke("s", "u", TOOL, 300)
    assert stub.calls == 3 and call.transport_retries == 2
    assert sleeps[0] == pytest.approx(0.5) and sleeps[1] == 2


def test_retry_after_is_capped():
    sleeps = []
    adapter, _ = adapter_with([status_error(429, retry_after=999), OK], sleeps)
    adapter.invoke("s", "u", TOOL, 300)
    assert sleeps == [30]


@pytest.mark.parametrize("raw,expected", [("-5", 0.0), ("nan", 0.5), ("soon", 0.5), ("0", 0.0)])
def test_retry_after_negative_or_garbage_never_crashes(raw, expected):
    sleeps = []
    response = httpx.Response(429, request=REQUEST, headers={"retry-after": raw})
    error = anthropic.APIStatusError("x", response=response, body=None)
    adapter, _ = adapter_with([error, OK], sleeps)
    adapter.invoke("s", "u", TOOL, 300)
    assert sleeps == [expected]


def test_rejected_is_never_retried():
    adapter, stub = adapter_with([status_error(401), OK])
    with pytest.raises(LLMError) as info:
        adapter.invoke("s", "u", TOOL, 300)
    assert (info.value.kind, info.value.error_type, stub.calls) == (
        "rejected",
        "APIStatusError:401",
        1,
    )


def test_retries_exhausted():
    adapter, stub = adapter_with([status_error(500)] * 4, LLM_MAX_RETRIES="1")
    with pytest.raises(LLMError) as info:
        adapter.invoke("s", "u", TOOL, 300)
    assert info.value.kind == "unavailable" and stub.calls == 2


def test_never_starts_an_attempt_that_cannot_finish_before_the_deadline():
    sleeps = []
    adapter, stub = adapter_with([status_error(429, retry_after=20), OK], sleeps)
    with pytest.raises(LLMError) as info:
        adapter.invoke("s", "u", TOOL, 40)  # 20 s wait + 30 s timeout > 40 s budget
    assert stub.calls == 1 and sleeps == [] and info.value.kind == "unavailable"
