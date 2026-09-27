"""Claude Agent SDK as a single-turn transport (D24). LangGraph stays the harness: no tools,
no agent loop, no .claude config, no @file expansion, no session persistence, allowlisted env."""

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import anyio
import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    CLINotFoundError,
    ResultError,
    ResultMessage,
    TextBlock,
    query,
)

from pitz_pulse.config import ConfigError, LLMSettings
from pitz_pulse.models_catalog import CLAUDE_AGENT_SDK, api_equivalent_cost_usd
from pitz_pulse.providers.base import Deadline, LLMCall, LLMError, elapsed_ms

logger = logging.getLogger("pitz_pulse.providers.claude_agent_sdk")
_ALLOWED_INHERITED = ("PATH", "TMPDIR", "LANG", "LC_ALL")
_REJECTED = {"authentication_failed", "billing_error", "invalid_request"}
_RETRYABLE_STATUS = {408, 409, 429}
_CLEANUP_MARGIN_S = 15  # the SDK's shielded transport close can take this long
_SLOTS: threading.BoundedSemaphore | None = None
_SLOTS_LOCK = threading.Lock()


def process_slots(size: int) -> threading.BoundedSemaphore:
    """Process-wide bound on concurrent CLI subprocesses (first size wins)."""
    global _SLOTS
    with _SLOTS_LOCK:
        if _SLOTS is None:
            _SLOTS = threading.BoundedSemaphore(size)
        return _SLOTS


def parse_json_object(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _log_stderr_line(line: str) -> None:
    """CLI stderr can echo request or credential details: log its size, never its text."""
    logger.info("agent_sdk_stderr", extra={"fields": {"length": len(line)}})


def _status_kind(status: int) -> str:
    return "unavailable" if status in _RETRYABLE_STATUS or status >= 500 else "rejected"


class ClaudeAgentSdkAdapter:
    provider = CLAUDE_AGENT_SDK

    def __init__(self, settings: LLMSettings, query_fn=query):
        if settings.deadline_s < _CLEANUP_MARGIN_S + settings.timeout_s:
            # With zero retries the deadline is timeout + 10 s, always below timeout + cleanup.
            raise ConfigError(
                "LLM_TIMEOUT_SECONDS and LLM_MAX_RETRIES leave no room for one full attempt "
                "plus SDK cleanup; set LLM_MAX_RETRIES to at least 1"
            )
        self.model = settings.model
        self.caps = settings.caps
        self._settings = settings
        self._query = query_fn

    def check_ready(self) -> None:
        cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        if not os.access(cli, os.X_OK):
            raise ConfigError("Claude Code CLI not found or not executable in claude-agent-sdk")
        try:
            with tempfile.TemporaryDirectory(prefix="pitz-sdk-check-") as home:
                probe = subprocess.run(
                    [str(cli), "-v"],
                    env=self.build_env(home),
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
        except (subprocess.SubprocessError, OSError) as exc:
            raise ConfigError(
                f"Claude Code CLI version check could not run ({type(exc).__name__})"
            ) from None
        if probe.returncode != 0:
            raise ConfigError("Claude Code CLI failed its version check")
        os.environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"  # checked once, here

    def build_env(self, workdir: str) -> dict[str, str]:
        # The SDK merges os.environ into the child env: blank everything we did not allow.
        env = dict.fromkeys(os.environ, "")
        env.update({name: os.environ.get(name, "") for name in _ALLOWED_INHERITED})
        env.update(
            {
                "CLAUDE_CODE_OAUTH_TOKEN": self._settings.claude_code_oauth_token or "",
                "API_TIMEOUT_MS": str(int(self._settings.timeout_s * 1000)),
                "CLAUDE_CODE_MAX_RETRIES": str(self._settings.max_retries),
                "HOME": workdir,
                "CLAUDE_CONFIG_DIR": str(Path(workdir) / ".claude"),
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_TELEMETRY": "1",
                "DISABLE_ERROR_REPORTING": "1",
            }
        )
        return env

    def build_options(self, system: str, workdir: str) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            tools=[],
            allowed_tools=[],
            mcp_servers={},
            strict_mcp_config=True,
            setting_sources=[],
            skills=[],
            plugins=[],
            agents=None,
            hooks=None,
            max_turns=1,
            permission_mode="dontAsk",
            system_prompt=system,
            model=self.model,
            cwd=workdir,
            env=self.build_env(workdir),
            verbatim_prompts=True,
            thinking={"type": "disabled"},
            extra_args={"no-session-persistence": None},
            stderr=_log_stderr_line,  # piped to us instead of inherited raw by the terminal
        )

    def invoke(self, system: str, user: str, tool: dict[str, Any], deadline_s: float) -> LLMCall:
        deadline = Deadline.after(deadline_s)
        start = time.monotonic()
        slots = process_slots(self._settings.concurrency)
        if not slots.acquire(timeout=max(0.0, deadline.remaining())):
            raise LLMError("unavailable", "ConcurrencyTimeout", elapsed_ms(start))
        try:
            with tempfile.TemporaryDirectory(prefix="pitz-sdk-") as workdir:
                budget = deadline.remaining() - _CLEANUP_MARGIN_S
                if budget <= 0:
                    raise LLMError("unavailable", "DeadlineExceeded", elapsed_ms(start))
                messages: list[Any] = []
                options = self.build_options(system, workdir)
                failure = self._run(user, options, budget, messages, start)
        finally:
            slots.release()
        return self._to_call(messages, failure, elapsed_ms(start))

    def _run(self, user, options, budget, messages, start) -> ResultError | None:
        try:
            anyio.run(self._collect, user, options, budget, messages)
        except TimeoutError:
            raise LLMError("unavailable", "DeadlineExceeded", elapsed_ms(start)) from None
        except ResultError as exc:
            return exc  # raised after the error result was yielded: map it with the messages
        except CLINotFoundError:
            raise LLMError("rejected", "CLINotFoundError", elapsed_ms(start)) from None
        except ClaudeSDKError as exc:  # CLIConnectionError, ProcessError, decode errors
            raise LLMError("unavailable", type(exc).__name__, elapsed_ms(start)) from None
        except Exception as exc:
            if type(exc) is not Exception:
                raise  # a programming error is not a transient provider failure
            # The SDK raises bare Exception for control-protocol failures (e.g. timeouts); its
            # text is never kept.
            raise LLMError("unavailable", "SDKControlError", elapsed_ms(start)) from None
        return None

    async def _collect(self, user: str, options: ClaudeAgentOptions, budget: float, sink: list):
        with anyio.fail_after(budget):
            async for message in self._query(prompt=user, options=options):
                sink.append(message)

    def _to_call(self, messages: list[Any], failure: ResultError | None, latency: float) -> LLMCall:
        texts, final, error, stop, actual = [], None, None, None, self.model
        for message in messages:
            if isinstance(message, AssistantMessage):
                error = message.error or error
                stop = message.stop_reason or stop
                actual = message.model or actual
                texts += [b.text for b in message.content if isinstance(b, TextBlock)]
            elif isinstance(message, ResultMessage):
                final = message
        if actual != self.model and not actual.startswith(self.model + "-"):
            logger.warning(
                "agent_sdk_model_mismatch",
                extra={"fields": {"configured": self.model, "actual": actual}},
            )
        status = (failure.api_error_status if failure else None) or (
            final.api_error_status if final is not None and final.is_error else None
        )
        if error in _REJECTED:
            raise LLMError("rejected", error, latency)
        if status:
            raise LLMError(_status_kind(status), f"api_error_status:{status}", latency)
        if error:
            raise LLMError("unavailable", error, latency)
        if failure is not None and failure.subtype != "error_max_turns":
            raise LLMError("unavailable", f"ResultError:{failure.subtype}", latency)
        usage = (final.usage if final else None) or {}
        input_tokens = sum(
            int(usage.get(key, 0))
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        output_tokens = int(usage.get("output_tokens", 0))
        return LLMCall(
            tool_input=parse_json_object("".join(texts)),
            stop_reason=stop,
            model=self.model,
            actual_model=actual,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            cost_usd=0.0,
            equivalent_api_cost_usd=api_equivalent_cost_usd(
                self.model, input_tokens, output_tokens
            ),
        )
