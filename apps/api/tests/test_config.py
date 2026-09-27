import dataclasses
import logging
import os

import pytest
from conftest import TRACING_VARS

from pitz_pulse.config import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ConfigError,
    disable_tracing,
    parse_llm_settings,
)


@pytest.fixture
def app_root(tmp_path):
    (tmp_path / "prompts").mkdir()
    for version in ("v1", "v2"):
        (tmp_path / "prompts" / f"{version}.md").write_text("x", encoding="utf-8")
    return tmp_path


def settings(app_root, **env):
    return parse_llm_settings({"APP_ROOT": str(app_root), **env})


def test_defaults_are_mock_without_error(app_root, caplog):
    with caplog.at_level(logging.WARNING):
        s = settings(app_root)
    assert (s.provider, s.model, s.temperature) == ("mock", "mock", None)
    assert "mock" in caplog.text


def test_empty_temperature_is_ignored_in_mock(app_root):
    assert settings(app_root, LLM_TEMPERATURE="").provider == "mock"


def test_auto_selects_provider_from_single_credential(app_root):
    s = settings(app_root, ANTHROPIC_API_KEY="sk-ant-api-test")
    assert (s.provider, s.model, s.temperature) == ("anthropic_api", "claude-haiku-4-5", 0.0)
    s = settings(app_root, CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-test", LLM_TEMPERATURE="none")
    assert s.provider == "claude_agent_sdk"


def test_both_credentials_without_provider_is_an_error(app_root):
    with pytest.raises(ConfigError, match="both"):
        settings(app_root, ANTHROPIC_API_KEY="a", CLAUDE_CODE_OAUTH_TOKEN="b")


def test_selected_provider_needs_its_credential_and_only_it(app_root):
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        settings(app_root, LLM_PROVIDER="anthropic_api")
    with pytest.raises(ConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        settings(
            app_root,
            LLM_PROVIDER="anthropic_api",
            ANTHROPIC_API_KEY="a",
            CLAUDE_CODE_OAUTH_TOKEN="b",
        )


def test_explicit_mock_with_credential_warns(app_root, caplog):
    with caplog.at_level(logging.WARNING):
        settings(app_root, LLM_PROVIDER="mock", ANTHROPIC_API_KEY="a")
    assert "credential" in caplog.text


@pytest.mark.parametrize(
    "name",
    [
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
    ],
)
def test_redirecting_env_is_forbidden(app_root, name):
    with pytest.raises(ConfigError, match=name):
        settings(app_root, **{name: "x"})


def test_quoted_credential_is_rejected(app_root):
    with pytest.raises(ConfigError, match="quote"):
        settings(app_root, ANTHROPIC_API_KEY='"sk-ant"')


def test_repr_hides_credentials(app_root):
    s = settings(app_root, ANTHROPIC_API_KEY="SECRET-KEY")
    assert "SECRET-KEY" not in repr(s)


@pytest.mark.parametrize(
    "raw,expected", [(None, 0.0), ("none", None), ("NONE", None), ("0.2", 0.2)]
)
def test_temperature_states(app_root, raw, expected):
    env = {"ANTHROPIC_API_KEY": "a"}
    if raw is not None:
        env["LLM_TEMPERATURE"] = raw
    assert settings(app_root, **env).temperature == expected


@pytest.mark.parametrize("raw", ["", "abc", "1.5", "-0.1", "nan", "inf"])
def test_invalid_temperature(app_root, raw):
    with pytest.raises(ConfigError, match="LLM_TEMPERATURE"):
        settings(app_root, ANTHROPIC_API_KEY="a", LLM_TEMPERATURE=raw)


def test_temperature_with_unsupported_model_names_the_fix(app_root):
    with pytest.raises(ConfigError, match="LLM_TEMPERATURE=none"):
        settings(app_root, ANTHROPIC_API_KEY="a", LLM_MODEL="claude-sonnet-5")
    with pytest.raises(ConfigError, match="LLM_TEMPERATURE=none"):
        settings(app_root, CLAUDE_CODE_OAUTH_TOKEN="b")
    ok = settings(
        app_root, ANTHROPIC_API_KEY="a", LLM_MODEL="claude-sonnet-5", LLM_TEMPERATURE="none"
    )
    assert ok.temperature is None


def test_unknown_model_or_provider(app_root):
    with pytest.raises(ConfigError, match="unknown"):
        settings(app_root, ANTHROPIC_API_KEY="a", LLM_MODEL="gpt-x")
    with pytest.raises(ConfigError, match="LLM_PROVIDER"):
        settings(app_root, LLM_PROVIDER="openai")


@pytest.mark.parametrize(
    "name,value",
    [
        ("INVALID_OUTPUT_RETRIES", "4"),
        ("LLM_MAX_RETRIES", "6"),
        ("LLM_CONCURRENCY", "0"),
        ("LLM_CONCURRENCY", "17"),
        ("LLM_TIMEOUT_SECONDS", "2"),
        ("CONFIDENCE_THRESHOLD", "1.2"),
        ("LLM_CONCURRENCY", "four"),
        ("LOG_LEVEL", "verbose"),
    ],
)
def test_ranges(app_root, name, value):
    with pytest.raises(ConfigError, match=name):
        settings(app_root, **{name: value})


def test_confidence_threshold_default_has_one_source(app_root):
    assert DEFAULT_CONFIDENCE_THRESHOLD == 0.7
    assert settings(app_root).confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD


def test_prompt_version_format_and_file(app_root):
    assert settings(app_root, PROMPT_VERSION="v2").prompt_version == "v2"
    for bad in ("../etc", "v9"):
        with pytest.raises(ConfigError, match="PROMPT_VERSION"):
            settings(app_root, PROMPT_VERSION=bad)


def test_empty_string_means_unset(app_root):
    assert settings(app_root, LLM_MODEL="", LLM_PROVIDER="", PROMPT_VERSION="").provider == "mock"


def test_deadline_includes_backoff_budget(app_root):
    s = settings(app_root, LLM_TIMEOUT_SECONDS="30", LLM_MAX_RETRIES="3")
    assert s.deadline_s == 30 * 4 + 3 * 30 + 10


def test_post_init_rejects_inconsistent_caps(app_root):
    s = settings(app_root, ANTHROPIC_API_KEY="a")
    with pytest.raises(ConfigError):
        dataclasses.replace(s, model="claude-sonnet-4-6")


@pytest.mark.parametrize("name", TRACING_VARS)
def test_tracing_forced_off_for_every_variant(monkeypatch, caplog, name):
    from langsmith import utils

    for other in TRACING_VARS:
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(name, "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    utils.get_env_var.cache_clear()
    assert utils.tracing_is_enabled()  # warm the cache while enabled
    with caplog.at_level(logging.WARNING):
        disable_tracing(os.environ)
    assert not utils.tracing_is_enabled()
    assert "tracing" in caplog.text
