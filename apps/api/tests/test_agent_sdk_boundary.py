"""Agent SDK adapter at its process boundary: readiness probe, error mapping, stderr, argv/env."""

import dataclasses
import logging
import subprocess

import anyio
import pytest
from claude_agent_sdk._internal.transport import subprocess_cli

from pitz_pulse.config import ConfigError, parse_llm_settings
from pitz_pulse.providers import claude_agent_sdk as module
from pitz_pulse.providers.base import LLMError
from pitz_pulse.providers.claude_agent_sdk import ClaudeAgentSdkAdapter

SENTINEL = "SENTINELXYZ"


@pytest.fixture(autouse=True)
def fresh_slots(monkeypatch):
    monkeypatch.setattr(module, "_SLOTS", None)


def adapter(query_fn=None):
    settings = parse_llm_settings(
        {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test", "LLM_TEMPERATURE": "none"}
    )
    return ClaudeAgentSdkAdapter(settings, **({"query_fn": query_fn} if query_fn else {}))


@pytest.mark.parametrize(
    "error",
    [subprocess.TimeoutExpired(["claude", "-v"], 20), OSError("exec format"), PermissionError()],
)
def test_check_ready_maps_probe_failures_to_config_error(monkeypatch, error):
    monkeypatch.setattr(module.os, "access", lambda *_: True)

    def failing_run(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(module.subprocess, "run", failing_run)
    with pytest.raises(ConfigError, match="Claude Code CLI"):
        adapter().check_ready()


def test_programming_errors_are_not_mapped_to_unavailable():
    async def broken(*, prompt, options):
        raise ValueError(SENTINEL)
        yield  # pragma: no cover

    with pytest.raises(ValueError):
        adapter(broken).invoke("s", "u", {"name": "t"}, 30)


@pytest.mark.parametrize(
    "text", ["Control request timeout: initialize", f"control protocol broke {SENTINEL}"]
)
def test_bare_sdk_exception_is_unavailable_without_text(text, caplog):
    async def failing(*, prompt, options):
        raise Exception(text)  # the SDK raises bare Exception for control-protocol failures
        yield  # pragma: no cover

    with pytest.raises(LLMError) as info:
        adapter(failing).invoke("s", "u", {"name": "t"}, 30)
    assert (info.value.kind, info.value.error_type) == ("unavailable", "SDKControlError")
    assert SENTINEL not in str(info.value) and SENTINEL not in caplog.text
    assert info.value.__cause__ is None and info.value.__suppress_context__


def test_stderr_is_logged_as_a_constant_event_without_text(caplog):
    options = adapter().build_options("SYS", "/tmp/w")
    with caplog.at_level(logging.INFO):
        options.stderr(f"token {SENTINEL} leaked")
    record = next(r for r in caplog.records if r.getMessage() == "agent_sdk_stderr")
    assert record.fields == {"length": len(f"token {SENTINEL} leaked")}
    assert SENTINEL not in caplog.text


def test_child_argv_and_merged_env_are_isolated(monkeypatch, tmp_path):
    for name in ("ANTHROPIC_API_KEY", "RANDOM_SECRET", "NODE_OPTIONS"):
        monkeypatch.setenv(name, "leak")
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    captured = {}

    async def fake_open_process(cmd, **kwargs):
        captured.update(cmd=cmd, env=kwargs["env"])
        raise RuntimeError("stop before spawning")

    monkeypatch.setattr(subprocess_cli.anyio, "open_process", fake_open_process)
    options = adapter().build_options("SYS", str(tmp_path))
    options = dataclasses.replace(options, cli_path="/nonexistent/claude")
    transport = subprocess_cli.SubprocessCLITransport("", options)
    with pytest.raises(Exception):  # noqa: B017  (the SDK may wrap our stop signal)
        anyio.run(transport.connect)
    cmd, env = captured["cmd"], captured["env"]
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--max-turns") + 1] == "1"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert {"--strict-mcp-config", "--setting-sources=", "--no-session-persistence"} <= set(cmd)
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS"
    assert all(env[name] == "" for name in ("ANTHROPIC_API_KEY", "RANDOM_SECRET", "NODE_OPTIONS"))
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test"
    assert env["HOME"] == str(tmp_path) and env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".claude")
    assert env["DISABLE_TELEMETRY"] == "1"


def test_zero_retries_config_error_names_the_only_fix():
    settings = parse_llm_settings(
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "x",
            "LLM_TEMPERATURE": "none",
            "LLM_TIMEOUT_SECONDS": "30",
            "LLM_MAX_RETRIES": "0",
        }
    )
    with pytest.raises(ConfigError) as info:
        ClaudeAgentSdkAdapter(settings)
    assert "LLM_MAX_RETRIES" in str(info.value) and "at least 1" in str(info.value)
