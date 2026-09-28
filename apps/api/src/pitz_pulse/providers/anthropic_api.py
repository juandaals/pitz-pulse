"""Anthropic Messages API via langchain-anthropic: forced tool call, strict when supported.

The adapter owns transport retries (ChatAnthropic max_retries=0): the SDK honors retry-after
without a bound, so only an adapter loop keeps every call inside the deadline (G33).
"""

import math
import time
from collections.abc import Callable
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from pitz_pulse.config import RETRY_WAIT_CAP_S, LLMSettings
from pitz_pulse.models_catalog import ANTHROPIC_API, cost_usd
from pitz_pulse.providers.base import Deadline, LLMCall, LLMError, elapsed_ms

_BASE_URL = "https://api.anthropic.com"
_RETRYABLE_STATUS = {408, 409, 429}


def map_anthropic_error(exc: Exception, latency_ms: float) -> LLMError:
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return LLMError("unavailable", type(exc).__name__, latency_ms)
    if isinstance(exc, anthropic.APIStatusError):
        code = exc.status_code
        kind = "unavailable" if code in _RETRYABLE_STATUS or code >= 500 else "rejected"
        return LLMError(kind, f"APIStatusError:{code}", latency_ms)
    raise exc


def _retry_after(exc: Exception) -> float | None:
    """Seconds the server asked us to wait: None when absent or unparseable, never negative."""
    response = getattr(exc, "response", None)
    raw = response.headers.get("retry-after") if response is not None else None
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    if value is None or not math.isfinite(value):
        return None
    return max(0.0, value)


class AnthropicApiAdapter:
    provider = ANTHROPIC_API

    def __init__(
        self,
        settings: LLMSettings,
        chat_model: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.model = settings.model
        self.caps = settings.caps
        self._timeout_s = settings.timeout_s
        self._max_retries = settings.max_retries
        self._sleep = sleep
        options: dict[str, Any] = {}
        if settings.temperature is not None:
            options["temperature"] = settings.temperature  # sent via extra_body, including 0
        self._chat = chat_model or ChatAnthropic(
            model=settings.model,
            api_key=settings.anthropic_api_key,
            base_url=_BASE_URL,
            max_retries=0,
            timeout=settings.timeout_s,
            max_tokens=1024,
            **options,
        )

    def invoke(self, system: str, user: str, tool: dict[str, Any], deadline_s: float) -> LLMCall:
        deadline = Deadline.after(deadline_s)
        bound = self._chat.bind_tools([tool], tool_choice=tool["name"])
        start = time.monotonic()
        retries = 0
        while True:
            try:
                message = bound.invoke([SystemMessage(system), HumanMessage(user)])
                break
            except (anthropic.APIConnectionError, anthropic.APIStatusError) as exc:
                error = map_anthropic_error(exc, elapsed_ms(start))
                if error.kind == "rejected" or retries >= self._max_retries:
                    raise error from None
                asked = _retry_after(exc)
                wait = min(RETRY_WAIT_CAP_S, 0.5 * 2**retries if asked is None else asked)
                if deadline.remaining() < wait + self._timeout_s:
                    raise error from None
                self._sleep(wait)
                retries += 1
        return self._to_call(message, elapsed_ms(start), retries)

    def _to_call(self, message: Any, latency_ms: float, retries: int) -> LLMCall:
        usage = message.usage_metadata or {}
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        cost = cost_usd(self.caps, input_tokens, output_tokens)
        metadata = message.response_metadata or {}
        return LLMCall(
            tool_input=message.tool_calls[0]["args"] if message.tool_calls else None,
            stop_reason=metadata.get("stop_reason"),
            model=self.model,
            actual_model=metadata.get("model_name") or self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            equivalent_api_cost_usd=cost,
            transport_retries=retries,
        )
