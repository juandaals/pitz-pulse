"""Provider seam (Strategy): every adapter returns an LLMCall or raises LLMError in time."""

import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pitz_pulse.models_catalog import ProviderCaps


@dataclass(frozen=True)
class LLMCall:
    tool_input: dict[str, Any] | None
    stop_reason: str | None
    model: str
    actual_model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    equivalent_api_cost_usd: float
    transport_retries: int = 0


class LLMError(Exception):
    def __init__(
        self, kind: Literal["unavailable", "rejected"], error_type: str, latency_ms: float = 0.0
    ):
        super().__init__(f"{kind}: {error_type}")
        self.kind = kind
        self.error_type = error_type  # class or literal name only, never an exception message
        self.latency_ms = latency_ms


# Rejections that no retry or other item can fix: the batch stops spending on these only.
CREDENTIAL_ERROR_TYPES = frozenset(
    {
        "APIStatusError:401",
        "APIStatusError:403",
        "api_error_status:401",
        "api_error_status:403",
        "authentication_failed",
        "billing_error",
        "CLINotFoundError",
    }
)


@dataclass(frozen=True)
class Deadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> "Deadline":
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        return self.expires_at - time.monotonic()


def elapsed_ms(start: float) -> float:
    return (time.monotonic() - start) * 1000


class ProviderAdapter(Protocol):
    provider: str
    model: str
    caps: ProviderCaps

    def invoke(self, system: str, user: str, tool: dict[str, Any], deadline_s: float) -> LLMCall:
        """Return or raise LLMError within deadline_s. Call from a worker thread only."""
        ...
