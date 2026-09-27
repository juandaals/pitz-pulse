"""`.env.example` and `docker-compose.yml` agree with the Spec 04 §3 environment inventory.

Parses both files as text (no YAML/dotenv dependency, Spec 04 Task 2): a simple `NAME=` regex
for `.env.example` and a `NAME:` regex scoped to the `environment:` block of `docker-compose.yml`.
"""

import re
from pathlib import Path

from pitz_pulse.config import ACTIVE_PROMPT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Spec 04 §3 inventory, minus the LangSmith/LangChain tracing variables (never enabled; checked
# separately below).
INVENTORY = {
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "PROMPT_VERSION",
    "LLM_TIMEOUT_SECONDS",
    "LLM_MAX_RETRIES",
    "INVALID_OUTPUT_RETRIES",
    "LLM_CONCURRENCY",
    "CONFIDENCE_THRESHOLD",
    "LOG_LEVEL",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "API_KEY",
    "DB_PATH",
    "PENDING_STALE_SECONDS",
    "API_PORT",
    "SLACK_SIGNING_SECRET",
    "SLACK_BOT_TOKEN",
    "DUPLICATE_THRESHOLD",
    "SLACK_CHANNEL_AREAS",
    "APP_ROOT",
}

TRACING_VARS = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)

_ENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_ENVIRONMENT_BLOCK = re.compile(r"^\s{4}environment:\s*$")
_COMPOSE_VAR_LINE = re.compile(r"^\s{6}([A-Z][A-Z0-9_]*):\s*(.*)$")


def _parse_env_example() -> dict[str, str]:
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def _parse_compose_environment() -> dict[str, str]:
    """Variables under the api service's `environment:` block (6-space indent in this file)."""
    values: dict[str, str] = {}
    in_block = False
    for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines():
        if _ENVIRONMENT_BLOCK.match(line):
            in_block = True
            continue
        if not in_block:
            continue
        if line.strip() and not line.startswith(" " * 6):
            in_block = False
            continue
        match = _COMPOSE_VAR_LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def test_env_example_has_every_inventory_variable():
    env_vars = _parse_env_example()
    missing = INVENTORY - env_vars.keys()
    assert not missing, f"missing from .env.example: {sorted(missing)}"


def test_compose_environment_vars_are_all_in_the_inventory():
    compose_vars = _parse_compose_environment()
    unknown = compose_vars.keys() - INVENTORY
    assert not unknown, f"docker-compose.yml declares vars outside Spec 04 §3: {sorted(unknown)}"


def test_prompt_version_matches_code_default_in_both_files():
    env_vars = _parse_env_example()
    compose_vars = _parse_compose_environment()
    assert env_vars["PROMPT_VERSION"] == ACTIVE_PROMPT_VERSION
    match = re.search(r"\$\{PROMPT_VERSION:-([^}]+)\}", compose_vars.get("PROMPT_VERSION", ""))
    assert match, f"PROMPT_VERSION has no default in docker-compose.yml: {compose_vars!r}"
    assert match.group(1) == ACTIVE_PROMPT_VERSION


def test_env_example_secrets_are_empty():
    env_vars = _parse_env_example()
    assert env_vars["ANTHROPIC_API_KEY"] == ""
    assert env_vars["CLAUDE_CODE_OAUTH_TOKEN"] == ""


def test_no_tracing_variable_is_enabled():
    env_vars = _parse_env_example()
    compose_vars = _parse_compose_environment()
    for name in TRACING_VARS:
        assert env_vars.get(name, "").lower() != "true", f"{name} must never be true"
        assert compose_vars.get(name, "").lower() != "true", f"{name} must never be true"
