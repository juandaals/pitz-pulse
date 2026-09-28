import logging
import os

import pytest

_PREFIXES = (
    "LLM_",
    "ANTHROPIC_",
    "CLAUDE_CODE_",
    "CLAUDE_AGENT_",
    "LANGSMITH_",
    "LANGCHAIN_",
    "SLACK_",
)
_NAMES = {
    "PROMPT_VERSION",
    "INVALID_OUTPUT_RETRIES",
    "CONFIDENCE_THRESHOLD",
    "APP_ROOT",
    "API_KEY",
    "DB_PATH",
    "PENDING_STALE_SECONDS",
    "DUPLICATE_THRESHOLD",
    "LOG_LEVEL",
    "API_PORT",
}
TRACING_VARS = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Tests never see the developer's .env, shell credentials or tracing settings."""
    for name in list(os.environ):
        if name.startswith(_PREFIXES) or name in _NAMES:
            monkeypatch.delenv(name)
    for name in TRACING_VARS:
        monkeypatch.setenv(name, "false")


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
