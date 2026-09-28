import pytest

from pitz_pulse.models_catalog import (
    ANTHROPIC_API,
    CLAUDE_AGENT_SDK,
    MOCK,
    api_equivalent_cost_usd,
    cost_usd,
    lookup,
)


def test_lookup_is_keyed_by_provider_and_model():
    api = lookup(ANTHROPIC_API, "claude-haiku-4-5")
    sdk = lookup(CLAUDE_AGENT_SDK, "claude-haiku-4-5")
    assert api.supports_temperature and api.supports_forced_tool and api.supports_strict
    assert api.billing == "api"
    assert not (sdk.supports_temperature or sdk.supports_forced_tool or sdk.supports_strict)
    assert sdk.billing == "subscription"


def test_sonnet_rows():
    assert not lookup(ANTHROPIC_API, "claude-sonnet-5").supports_temperature
    assert not lookup(ANTHROPIC_API, "claude-sonnet-4-6").supports_strict


def test_mock_row():
    assert lookup(MOCK, "mock").billing == "none"


def test_unknown_pairs_raise():
    with pytest.raises(KeyError):
        lookup(ANTHROPIC_API, "claude-haiku-4-5-20251001")
    with pytest.raises(KeyError):
        lookup(MOCK, "claude-haiku-4-5")
    with pytest.raises(KeyError):
        api_equivalent_cost_usd("unknown-model", 1, 1)


def test_cost_math():
    caps = lookup(ANTHROPIC_API, "claude-haiku-4-5")
    assert cost_usd(caps, 1_000_000, 1_000_000) == pytest.approx(6.0)
    assert api_equivalent_cost_usd("claude-haiku-4-5", 2_000, 200) == pytest.approx(0.003)
