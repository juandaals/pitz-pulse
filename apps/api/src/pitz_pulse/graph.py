"""LangGraph harness: call_llm → validate → retry | done | fail (spec 01 §5)."""

import logging
import time
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from pitz_pulse.logs import log_event
from pitz_pulse.masking import MaskedRequest
from pitz_pulse.prompts import Prompt
from pitz_pulse.providers.base import LLMCall, LLMError, ProviderAdapter, elapsed_ms
from pitz_pulse.schema import ModelOutput

logger = logging.getLogger("pitz_pulse.llm")
_ERROR_KINDS = {"unavailable": "llm_unavailable", "rejected": "llm_rejected"}
_INVALID_STOP_REASONS = {"max_tokens", "refusal"}


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    outcome: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    equivalent_api_cost_usd: float = 0.0
    latency_ms: float = 0.0
    transport_retries: int = 0


class ClassifyState(TypedDict, total=False):
    masked: MaskedRequest
    message_id: str
    attempt: int
    feedback: str | None
    last_call: LLMCall | None
    output: ModelOutput | None
    error_kind: str | None
    sink: list[AttemptRecord]  # same list object for the whole run: survives node exceptions


def format_errors(exc: ValidationError) -> str:
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    return "\n".join(f"- {'.'.join(map(str, e['loc'])) or 'answer'}: {e['msg']}" for e in errors)


def recursion_limit(settings) -> int:
    return 4 + 2 * (1 + settings.invalid_output_retries)


def _evaluate(call: LLMCall) -> tuple[ModelOutput | None, str | None]:
    if call.tool_input is None:
        return None, "- answer: no classification was returned; answer with all 8 fields"
    if call.stop_reason in _INVALID_STOP_REASONS:
        return None, f"- answer: the answer was cut off (stop reason {call.stop_reason})"
    try:
        return ModelOutput.model_validate(call.tool_input), None
    except ValidationError as exc:
        return None, format_errors(exc)


def build_graph(adapter: ProviderAdapter, prompt: Prompt, tool: dict[str, Any], settings):
    def record(state: ClassifyState, attempt: AttemptRecord, actual_model: str, error_type=None):
        state["sink"].append(attempt)
        fields = {
            "message_id": state["message_id"],
            "provider": adapter.provider,
            "model": adapter.model,
            "actual_model": actual_model,
            "prompt_version": prompt.version,
            "attempt": attempt.attempt,
            "outcome": attempt.outcome,
            "latency_ms": round(attempt.latency_ms, 1),
            "input_tokens": attempt.input_tokens,
            "output_tokens": attempt.output_tokens,
            "cost_usd": attempt.cost_usd,
            "equivalent_api_cost_usd": attempt.equivalent_api_cost_usd,
            "billing": adapter.caps.billing,
            "transport_retries": attempt.transport_retries,
            "pii_masked": state["masked"].pii_counts,
        }
        if error_type:
            fields["error_type"] = error_type
        log_event(logger, "llm_call", **fields)

    def call_llm(state: ClassifyState) -> dict[str, Any]:
        attempt = state.get("attempt", 0) + 1
        user = prompt.render_user(state["masked"], state.get("feedback"))
        start = time.monotonic()
        try:
            call = adapter.invoke(prompt.system, user, tool, settings.deadline_s)
        except LLMError as exc:
            latency = exc.latency_ms or elapsed_ms(start)
            if exc.kind not in _ERROR_KINDS:
                record(
                    state,
                    AttemptRecord(attempt, "error", latency_ms=latency),
                    adapter.model,
                    f"UnknownLLMErrorKind:{exc.kind}",
                )
                raise RuntimeError(f"unknown LLMError kind {exc.kind}") from None
            record(
                state,
                AttemptRecord(attempt, exc.kind, latency_ms=latency),
                adapter.model,
                exc.error_type,
            )
            return {"attempt": attempt, "last_call": None, "error_kind": _ERROR_KINDS[exc.kind]}
        except Exception as exc:
            record(
                state,
                AttemptRecord(attempt, "error", latency_ms=elapsed_ms(start)),
                adapter.model,
                type(exc).__name__,
            )
            raise
        return {"attempt": attempt, "last_call": call, "error_kind": None}

    def validate(state: ClassifyState) -> dict[str, Any]:
        call = state["last_call"]
        output, problems = _evaluate(call)
        attempt = AttemptRecord(
            state["attempt"],
            "ok" if output is not None else "invalid_output",
            call.input_tokens,
            call.output_tokens,
            call.cost_usd,
            call.equivalent_api_cost_usd,
            call.latency_ms,
            call.transport_retries,
        )
        record(state, attempt, call.actual_model)
        if output is not None:
            return {"output": output}
        if state["attempt"] < 1 + settings.invalid_output_retries:
            return {"feedback": problems}
        return {"error_kind": "invalid_output"}

    graph = StateGraph(ClassifyState)
    graph.add_node("call_llm", call_llm)
    graph.add_node("validate", validate)
    graph.add_edge(START, "call_llm")
    graph.add_conditional_edges(
        "call_llm",
        lambda s: "end" if s.get("error_kind") else "validate",
        {"validate": "validate", "end": END},
    )
    graph.add_conditional_edges(
        "validate",
        lambda s: "end" if s.get("output") is not None or s.get("error_kind") else "retry",
        {"retry": "call_llm", "end": END},
    )
    return graph.compile()
