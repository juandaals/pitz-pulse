import pytest

from pitz_pulse.config import ConfigError
from pitz_pulse.preflight import check_model, main


def test_valid_combo_passes_with_a_dummy_credential():
    check_model("anthropic_api", "claude-haiku-4-5", {})  # must not raise


def test_unknown_model_names_itself_in_the_error():
    with pytest.raises(ConfigError, match="claude-haiku-4-nope") as excinfo:
        check_model("anthropic_api", "claude-haiku-4-nope", {})
    assert "unknown" in str(excinfo.value)


def test_claude_sonnet_5_gets_temperature_none_automatically():
    check_model("anthropic_api", "claude-sonnet-5", {})  # must not raise despite default temp 0


def test_every_claude_agent_sdk_model_needs_temperature_none_too():
    """The whole `claude_agent_sdk` row of the catalog has `supports_temperature=False` (not
    just claude-sonnet-5): preflight must derive this from the catalog, not hardcode one model.
    """
    check_model("claude_agent_sdk", "claude-haiku-4-5", {})  # must not raise


def test_mock_provider_ignores_a_bogus_model_name():
    check_model("mock", "this-is-not-a-real-model", {})  # mock ignores LLM_MODEL; must not raise


def test_ambient_credentials_are_cleared_not_leaked():
    """A real credential (of either kind) already in the environment must not cause a false
    "both credentials set" failure, and must not appear in a raised error message.
    """
    env = {"ANTHROPIC_API_KEY": "sk-ant-real-secret", "CLAUDE_CODE_OAUTH_TOKEN": "real-oauth-token"}
    check_model("anthropic_api", "claude-haiku-4-5", env)  # must not raise despite both being set


def test_unknown_provider_is_a_config_error():
    with pytest.raises(ConfigError):
        check_model("openai", "gpt-4", {})


def test_cli_success_exits_zero(capsys):
    code = main(["--provider", "anthropic_api", "--models", "claude-haiku-4-5", "claude-sonnet-5"])

    out = capsys.readouterr()
    assert code == 0
    assert "claude-haiku-4-5" in out.out
    assert "claude-sonnet-5" in out.out


def test_cli_names_the_bad_model_and_exits_two(capsys):
    code = main(
        ["--provider", "anthropic_api", "--models", "claude-haiku-4-5", "claude-haiku-4-nope"]
    )

    out = capsys.readouterr()
    assert code == 2
    assert out.err.startswith("error: ")
    assert "claude-haiku-4-nope" in out.err


def test_cli_stops_at_the_first_bad_model_before_any_later_one():
    """Order matters: the Makefile relies on preflight failing before the first classify call for
    *any* model in the list, not only the last one.
    """
    code = main(["--provider", "anthropic_api", "--models", "bogus-1", "claude-haiku-4-5"])

    assert code == 2
