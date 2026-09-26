import os
import threading
import time

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    ResultError,
    ResultMessage,
    TextBlock,
)

from pitz_pulse.config import ConfigError, parse_llm_settings
from pitz_pulse.providers import claude_agent_sdk as module
from pitz_pulse.providers.base import LLMError
from pitz_pulse.providers.claude_agent_sdk import ClaudeAgentSdkAdapter, parse_json_object

SENTINEL = "SENTINELXYZ"
JSON_REPLY = (
    '{"categoria":"bug","prioridad":"alta","area_sugerida":"backend","idioma":"es",'
    '"resumen":"Error al subir catálogo","requiere_info":false,'
    '"pregunta_seguimiento":null,"confianza":0.9}'
)
LEAKY = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_EXTRA_BODY",
    "CLAUDE_CODE_USE_FOUNDRY",
    "ANTHROPIC_CUSTOM_HEADERS",
    "RANDOM_SECRET",
    "API_KEY",
    "NODE_OPTIONS",
)


@pytest.fixture(autouse=True)
def fresh_slots(monkeypatch):
    monkeypatch.setattr(module, "_SLOTS", None)


def settings(**env):
    base = {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test", "LLM_TEMPERATURE": "none"}
    return parse_llm_settings({**base, **env})


def result(**overrides):
    values = dict(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="s",
        usage={"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 500},
    )
    values.update(overrides)
    return ResultMessage(**values)


def assistant(text, error=None, model="claude-haiku-4-5-20251001"):
    return AssistantMessage(content=[TextBlock(text=text)], model=model, error=error)


def stub_query(messages=(), raises=None):
    async def query(*, prompt, options):
        for message in messages:
            yield message
        if raises:
            raise raises

    return query


def invoke(adapter, deadline=30):
    return adapter.invoke("system", "user", {"name": "t"}, deadline)


def test_legal_minimum_deadline_cannot_fit_one_attempt_plus_cleanup_is_rejected():
    with pytest.raises(ConfigError, match="LLM_TIMEOUT_SECONDS"):
        ClaudeAgentSdkAdapter(settings(LLM_TIMEOUT_SECONDS="5", LLM_MAX_RETRIES="0"))


def test_default_settings_leave_room_for_one_attempt_plus_cleanup():
    ClaudeAgentSdkAdapter(settings())  # does not raise


def test_isolation_options():
    options = ClaudeAgentSdkAdapter(settings()).build_options("SYS", "/tmp/work")
    assert options.tools == [] and options.allowed_tools == []
    assert options.mcp_servers == {} and options.strict_mcp_config is True
    assert options.setting_sources == [] and options.skills == [] and options.plugins == []
    assert options.agents is None and options.hooks is None
    assert options.max_turns == 1 and options.permission_mode == "dontAsk"
    assert options.verbatim_prompts is True and options.thinking == {"type": "disabled"}
    assert options.system_prompt == "SYS" and options.model == "claude-haiku-4-5"
    assert options.cwd == "/tmp/work" and not options.cwd.startswith(os.getcwd())
    assert "no-session-persistence" in options.extra_args


def test_env_is_an_allowlist(monkeypatch):
    for name in LEAKY:
        monkeypatch.setenv(name, "leak")
    env = ClaudeAgentSdkAdapter(settings()).build_env("/tmp/w")
    for name in LEAKY:
        assert env[name] == "", name
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test"
    assert env["API_TIMEOUT_MS"] == "30000" and env["CLAUDE_CODE_MAX_RETRIES"] == "3"
    assert env["HOME"] == "/tmp/w" and env["PATH"] == os.environ.get("PATH", "")


def test_json_reply_parsed_with_cache_aware_equivalent_cost():
    stream = [assistant(JSON_REPLY), result()]
    call = invoke(ClaudeAgentSdkAdapter(settings(), query_fn=stub_query(stream)))
    assert call.tool_input["categoria"] == "bug"
    assert call.input_tokens == 1500 and call.cost_usd == 0
    assert call.equivalent_api_cost_usd == pytest.approx((1500 * 1 + 100 * 5) / 1e6)
    assert call.actual_model == "claude-haiku-4-5-20251001"


@pytest.mark.parametrize(
    "text,expected",
    [
        (JSON_REPLY, True),
        (f"```json\n{JSON_REPLY}\n```", True),
        ("Claro! " + JSON_REPLY, True),
        ("no json here", False),
        ("[1, 2]", False),
        ("{bad json}", False),
    ],
)
def test_parse_json_object(text, expected):
    assert (parse_json_object(text) is not None) is expected


def test_prose_reply_is_invalid_output_not_error():
    stream = [assistant("Hola"), result()]
    assert invoke(ClaudeAgentSdkAdapter(settings(), query_fn=stub_query(stream))).tool_input is None


def result_error(**data):
    return ResultError("failed", data={"subtype": "success", "is_error": True, **data}, exit_code=1)


@pytest.mark.parametrize(
    "messages,raises,kind",
    [
        (
            [assistant("", error="authentication_failed"), result(is_error=True)],
            result_error(),
            "rejected",
        ),
        ([assistant("", error="rate_limit"), result(is_error=True)], result_error(), "unavailable"),
        (
            [result(is_error=True, api_error_status=529)],
            result_error(api_error_status=529),
            "unavailable",
        ),
        (
            [result(is_error=True, api_error_status=400)],
            result_error(api_error_status=400),
            "rejected",
        ),
        ([], CLINotFoundError("missing"), "rejected"),
        ([], CLIConnectionError("down"), "unavailable"),
        ([], Exception("Control request timeout: initialize"), "unavailable"),
    ],
)
def test_error_mapping_with_real_shaped_streams(messages, raises, kind):
    adapter = ClaudeAgentSdkAdapter(settings(), query_fn=stub_query(messages, raises))
    with pytest.raises(LLMError) as info:
        invoke(adapter)
    assert info.value.kind == kind


def test_max_turns_result_error_is_invalid_output():
    stream = [assistant("Hola"), result(is_error=True, subtype="error_max_turns")]
    query = stub_query(stream, result_error(subtype="error_max_turns"))
    assert invoke(ClaudeAgentSdkAdapter(settings(), query_fn=query)).tool_input is None


def test_process_error_text_never_surfaces(caplog):
    error = ProcessError(f"failed {SENTINEL}", exit_code=1, stderr=SENTINEL)
    adapter = ClaudeAgentSdkAdapter(settings(), query_fn=stub_query([], error))
    with pytest.raises(LLMError) as info:
        invoke(adapter)
    assert SENTINEL not in str(info.value) and SENTINEL not in caplog.text


def test_deadline_includes_cleanup():
    async def slow(*, prompt, options):
        await anyio.sleep(5)
        yield result()

    start = time.monotonic()
    with pytest.raises(LLMError) as info:
        invoke(ClaudeAgentSdkAdapter(settings(), query_fn=slow), deadline=16)  # budget 1 s
    assert info.value.error_type == "DeadlineExceeded" and time.monotonic() - start < 3


def test_slot_wait_counts_toward_deadline():
    gate = threading.Event()

    async def blocking(*, prompt, options):
        await anyio.to_thread.run_sync(gate.wait)
        yield result()

    adapter = ClaudeAgentSdkAdapter(settings(LLM_CONCURRENCY="1"), query_fn=blocking)
    worker = threading.Thread(target=lambda: invoke(adapter, deadline=60))
    worker.start()
    time.sleep(0.1)
    start = time.monotonic()
    with pytest.raises(LLMError) as info:
        invoke(adapter, deadline=0.3)
    assert info.value.error_type == "ConcurrencyTimeout" and time.monotonic() - start < 1
    gate.set()
    worker.join()


def test_snapshot_model_is_not_a_mismatch(caplog):
    invoke(
        ClaudeAgentSdkAdapter(settings(), query_fn=stub_query([assistant(JSON_REPLY), result()]))
    )
    assert "agent_sdk_model_mismatch" not in caplog.text


def test_different_model_is_logged(caplog):
    stream = [assistant(JSON_REPLY, model="claude-other"), result()]
    invoke(ClaudeAgentSdkAdapter(settings(), query_fn=stub_query(stream)))
    assert "agent_sdk_model_mismatch" in caplog.text
