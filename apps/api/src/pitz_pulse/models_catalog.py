"""Capabilities and prices per (provider, model).

Prices verified 2026-09-25; re-verify on the final run day.
"""

from dataclasses import dataclass
from typing import Literal

ANTHROPIC_API = "anthropic_api"
CLAUDE_AGENT_SDK = "claude_agent_sdk"
MOCK = "mock"
PROVIDERS = (ANTHROPIC_API, CLAUDE_AGENT_SDK, MOCK)

Billing = Literal["api", "subscription", "none"]


@dataclass(frozen=True)
class ProviderCaps:
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    supports_temperature: bool
    supports_forced_tool: bool
    supports_strict: bool
    billing: Billing


API_PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
}

CATALOG: dict[tuple[str, str], ProviderCaps] = {
    (ANTHROPIC_API, "claude-haiku-4-5"): ProviderCaps(1.00, 5.00, True, True, True, "api"),
    (ANTHROPIC_API, "claude-sonnet-4-6"): ProviderCaps(3.00, 15.00, True, True, False, "api"),
    (ANTHROPIC_API, "claude-sonnet-5"): ProviderCaps(2.00, 10.00, False, True, True, "api"),
    **{
        (CLAUDE_AGENT_SDK, model): ProviderCaps(i, o, False, False, False, "subscription")
        for model, (i, o) in API_PRICES.items()
    },
    (MOCK, "mock"): ProviderCaps(0.0, 0.0, False, False, False, "none"),
}


def lookup(provider: str, model: str) -> ProviderCaps:
    return CATALOG[(provider, model)]


def cost_usd(caps: ProviderCaps, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * caps.input_usd_per_mtok + output_tokens * caps.output_usd_per_mtok) / 1e6


def api_equivalent_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = API_PRICES[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1e6
