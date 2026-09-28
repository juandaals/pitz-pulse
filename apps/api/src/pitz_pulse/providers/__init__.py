"""Adapter factory: one registry entry per provider; SDK imports stay lazy."""

from collections.abc import Callable

from pitz_pulse.config import ConfigError, LLMSettings
from pitz_pulse.logs import pin_third_party_loggers
from pitz_pulse.models_catalog import ANTHROPIC_API, CLAUDE_AGENT_SDK, MOCK
from pitz_pulse.providers.base import ProviderAdapter


def _mock(settings: LLMSettings) -> ProviderAdapter:
    from pitz_pulse.providers.mock import MockAdapter

    return MockAdapter()


def _anthropic_api(settings: LLMSettings) -> ProviderAdapter:
    from pitz_pulse.providers.anthropic_api import AnthropicApiAdapter

    return AnthropicApiAdapter(settings)


def _claude_agent_sdk(settings: LLMSettings) -> ProviderAdapter:
    from pitz_pulse.providers.claude_agent_sdk import ClaudeAgentSdkAdapter

    adapter = ClaudeAgentSdkAdapter(settings)
    adapter.check_ready()
    return adapter


_REGISTRY: dict[str, Callable[[LLMSettings], ProviderAdapter]] = {
    MOCK: _mock,
    ANTHROPIC_API: _anthropic_api,
    CLAUDE_AGENT_SDK: _claude_agent_sdk,
}


def build_adapter(settings: LLMSettings) -> ProviderAdapter:
    try:
        factory = _REGISTRY[settings.provider]
    except KeyError:
        raise ConfigError(f"no adapter registered for provider {settings.provider}") from None
    adapter = factory(settings)
    pin_third_party_loggers()  # importing an SDK may have changed logger levels
    return adapter
