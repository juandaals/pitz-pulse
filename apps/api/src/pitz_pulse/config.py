"""LLM settings: read and validate env once; fail fast with explicit messages (spec 01 §8.2-8.8)."""

import logging
import math
import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path

from pitz_pulse.models_catalog import (
    ANTHROPIC_API,
    CLAUDE_AGENT_SDK,
    MOCK,
    PROVIDERS,
    ProviderCaps,
    lookup,
)

logger = logging.getLogger(__name__)

ACTIVE_PROMPT_VERSION = "v1"  # single source; compose and .env.example must match (Spec 04a test)
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_APP_ROOT = Path(__file__).resolve().parents[2]  # apps/api (editable install)
DEFAULT_CONFIDENCE_THRESHOLD = 0.7  # single source; also evaluate.py's CLI default
RETRY_WAIT_CAP_S = 30
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
# Each would redirect provider auth, backend, headers, body or logging.
_FORBIDDEN = (
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL",
    "ANTHROPIC_LOG",
    "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_UNIX_SOCKET",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "CLAUDE_CODE_EXTRA_BODY",
    "CLAUDE_CODE_HOST_CREDS_FILE",
)
_CREDENTIALS = {ANTHROPIC_API: "ANTHROPIC_API_KEY", CLAUDE_AGENT_SDK: "CLAUDE_CODE_OAUTH_TOKEN"}
_TRACING_VARS = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    temperature: float | None
    prompt_version: str
    timeout_s: float
    max_retries: int
    invalid_output_retries: int
    concurrency: int
    confidence_threshold: float
    log_level: str
    anthropic_api_key: str | None = field(repr=False)
    claude_code_oauth_token: str | None = field(repr=False)
    app_root: Path
    caps: ProviderCaps

    def __post_init__(self) -> None:
        try:
            expected = lookup(self.provider, self.model)
        except KeyError:
            raise ConfigError(f"unknown model {self.model!r} for {self.provider}") from None
        if self.caps != expected:
            raise ConfigError("caps do not match provider and model")

    @property
    def deadline_s(self) -> float:
        """Per-invoke budget, checked between attempts (D28): not a wall-clock kill."""
        return self.timeout_s * (1 + self.max_retries) + self.max_retries * RETRY_WAIT_CAP_S + 10


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    return value.strip() or None


def _credential(env: Mapping[str, str], name: str) -> str | None:
    value = _get(env, name)
    if value and (value[0] in "'\"" or value[-1] in "'\""):
        raise ConfigError(f"{name} must not be wrapped in quotes")
    return value


def _number(env, name, default, low, high, cast):
    raw = _get(env, name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number") from None
    if not (math.isfinite(value) and low <= value <= high):
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


def _temperature(env: Mapping[str, str]) -> float | None:
    raw = env.get("LLM_TEMPERATURE")
    if raw is None:
        return 0.0
    raw = raw.strip()
    if raw == "":
        raise ConfigError("LLM_TEMPERATURE is empty; use LLM_TEMPERATURE=none to not send it")
    if raw.lower() == "none":
        return None
    return _number({"LLM_TEMPERATURE": raw}, "LLM_TEMPERATURE", 0.0, 0.0, 1.0, float)


def _select_provider(env, api_key: str | None, oauth: str | None) -> str:
    explicit = _get(env, "LLM_PROVIDER")
    if explicit:
        if explicit not in PROVIDERS:
            raise ConfigError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}")
        if explicit == MOCK and (api_key or oauth):
            logger.warning("mock provider selected although a credential is set")
        return explicit
    present = [p for p, c in ((ANTHROPIC_API, api_key), (CLAUDE_AGENT_SDK, oauth)) if c]
    if len(present) == 2:
        raise ConfigError("both credentials are set; set LLM_PROVIDER or remove one credential")
    if not present:
        logger.warning("no credential set: running in mock mode (not model quality)")
        return MOCK
    logger.info(
        "provider auto-selected from credential", extra={"fields": {"provider": present[0]}}
    )
    return present[0]


def _model_and_temperature(env, provider: str, credentials: dict[str, str | None]):
    if provider == MOCK:
        return "mock", None
    needed = _CREDENTIALS[provider]
    other = next(name for p, name in _CREDENTIALS.items() if p != provider)
    if not credentials[needed]:
        raise ConfigError(f"{needed} is required for LLM_PROVIDER={provider}")
    if credentials[other]:
        raise ConfigError(f"{other} must be empty when LLM_PROVIDER={provider}")
    return _get(env, "LLM_MODEL") or DEFAULT_MODEL, _temperature(env)


def parse_llm_settings(env: Mapping[str, str]) -> LLMSettings:
    for name in _FORBIDDEN:
        if _get(env, name):
            raise ConfigError(f"{name} must not be set: it would redirect provider auth or backend")
    credentials = {name: _credential(env, name) for name in _CREDENTIALS.values()}
    provider = _select_provider(
        env, credentials["ANTHROPIC_API_KEY"], credentials["CLAUDE_CODE_OAUTH_TOKEN"]
    )
    model, temperature = _model_and_temperature(env, provider, credentials)
    try:
        caps = lookup(provider, model)
    except KeyError:
        raise ConfigError(f"unknown model {model!r} for provider {provider}") from None
    if temperature is not None and not caps.supports_temperature:
        raise ConfigError(
            f"{model} via {provider} does not accept temperature; set LLM_TEMPERATURE=none"
        )
    log_level = (_get(env, "LOG_LEVEL") or "INFO").upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {', '.join(_LOG_LEVELS)}")
    app_root = Path(_get(env, "APP_ROOT") or DEFAULT_APP_ROOT).resolve()
    prompt_version = _get(env, "PROMPT_VERSION") or ACTIVE_PROMPT_VERSION
    if not re.fullmatch(r"v\d+", prompt_version):
        raise ConfigError("PROMPT_VERSION must look like v1, v2, ...")
    if not (app_root / "prompts" / f"{prompt_version}.md").is_file():
        raise ConfigError(f"PROMPT_VERSION {prompt_version} has no prompts/{prompt_version}.md")
    return LLMSettings(
        provider=provider,
        model=model,
        temperature=temperature,
        prompt_version=prompt_version,
        timeout_s=_number(env, "LLM_TIMEOUT_SECONDS", 30.0, 5, 120, float),
        max_retries=_number(env, "LLM_MAX_RETRIES", 3, 0, 5, int),
        invalid_output_retries=_number(env, "INVALID_OUTPUT_RETRIES", 1, 0, 3, int),
        concurrency=_number(env, "LLM_CONCURRENCY", 4, 1, 16, int),
        confidence_threshold=_number(
            env, "CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD, 0, 1, float
        ),
        log_level=log_level,
        anthropic_api_key=credentials["ANTHROPIC_API_KEY"],
        claude_code_oauth_token=credentials["CLAUDE_CODE_OAUTH_TOKEN"],
        app_root=app_root,
        caps=caps,
    )


def disable_tracing(environ: MutableMapping[str, str]) -> None:
    """LangSmith (transitive dependency) would upload graph state: force it off everywhere.

    Only the env vars it reads are touched (no client, no request). ``run_trees.configure``
    is deliberately not used here: it sets a process-global context var with no way back to
    "unset", which would leak this call's effect into unrelated code running afterwards.
    """
    if _get(environ, "LANGSMITH_API_KEY") or _get(environ, "LANGCHAIN_API_KEY"):
        logger.warning("LangSmith key present: tracing is forcibly disabled")
    for name in _TRACING_VARS:
        environ[name] = "false"
    from langsmith import utils

    utils.get_env_var.cache_clear()


def load_llm_settings() -> LLMSettings:
    disable_tracing(os.environ)
    return parse_llm_settings(os.environ)


def resolve_app_root(env: Mapping[str, str]) -> Path:
    """APP_ROOT trimmed of surrounding whitespace, defaulting like `parse_llm_settings` does.

    Used by `evaluate` and `promote`, which read only this one variable from the environment.
    """
    return Path(_get(env, "APP_ROOT") or DEFAULT_APP_ROOT).resolve()
