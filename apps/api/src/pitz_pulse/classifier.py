"""Classifier: masks before the graph (raw text never enters graph state) and never traces."""

import os
from dataclasses import dataclass
from typing import Literal

from langsmith import tracing_context

from pitz_pulse.config import LLMSettings, disable_tracing
from pitz_pulse.graph import AttemptRecord, build_graph, recursion_limit
from pitz_pulse.masking import mask_request
from pitz_pulse.prompts import Prompt, load_prompt
from pitz_pulse.providers import build_adapter
from pitz_pulse.providers.base import ProviderAdapter
from pitz_pulse.schema import Classification, RequestInput
from pitz_pulse.tool_schema import build_tool_schema

ErrorKind = Literal["llm_unavailable", "llm_rejected", "invalid_output"]


@dataclass(frozen=True)
class ClassifyOutcome:
    classification: Classification
    attempts: list[AttemptRecord]


class ClassificationError(Exception):
    def __init__(self, kind: ErrorKind, attempts: list[AttemptRecord]):
        super().__init__(kind)
        self.kind = kind
        self.attempts = attempts


class ClassificationCrash(RuntimeError):
    """Unexpected failure; carries the attempts already billed. Message holds the class only."""

    def __init__(self, error_type: str, attempts: list[AttemptRecord]):
        super().__init__(error_type)
        self.error_type = error_type
        self.attempts = attempts


class Classifier:
    def __init__(self, adapter: ProviderAdapter, prompt: Prompt, settings: LLMSettings):
        self.adapter = adapter
        self.prompt = prompt
        self.settings = settings
        self.tool = build_tool_schema(strict=adapter.caps.supports_strict)
        self._graph = build_graph(adapter, prompt, self.tool, settings)

    def classify(self, req: RequestInput) -> ClassifyOutcome:
        sink: list[AttemptRecord] = []
        try:  # masking, the graph and the final validation all surface as our own errors
            state = {
                "masked": mask_request(req.message, req.source_area),
                "message_id": req.id,
                "attempt": 0,
                "sink": sink,
            }
            with tracing_context(enabled=False):
                final = self._graph.invoke(
                    state, config={"recursion_limit": recursion_limit(self.settings)}
                )
            if final.get("output") is not None:
                data = {
                    "id": req.id,
                    **final["output"].model_dump(mode="json"),
                    "version_prompt": self.prompt.version,
                }
                return ClassifyOutcome(Classification.model_validate(data), list(sink))
        except Exception as exc:
            raise ClassificationCrash(type(exc).__name__, list(sink)) from exc
        if final.get("error_kind"):
            raise ClassificationError(final["error_kind"], list(sink))
        raise ClassificationCrash("GraphEndedWithoutResult", list(sink))


def build_classifier(settings: LLMSettings, adapter: ProviderAdapter | None = None) -> Classifier:
    disable_tracing(os.environ)
    if adapter is None:
        adapter = build_adapter(settings)
    return Classifier(adapter, load_prompt(settings.app_root, settings.prompt_version), settings)
