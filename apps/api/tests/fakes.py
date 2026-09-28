from pitz_pulse.models_catalog import ProviderCaps
from pitz_pulse.providers.base import LLMCall

VALID_OUTPUT = {
    "categoria": "bug",
    "prioridad": "alta",
    "area_sugerida": "backend",
    "idioma": "es",
    "resumen": "Vendedor recibe error 500 al subir su catálogo",
    "requiere_info": True,
    "pregunta_seguimiento": "¿Qué archivo intentó subir?",
    "confianza": 0.86,
}


def make_call(tool_input=VALID_OUTPUT, **overrides) -> LLMCall:
    values = dict(
        tool_input=tool_input,
        stop_reason="tool_use",
        model="fake-model",
        actual_model="fake-model",
        input_tokens=100,
        output_tokens=20,
        latency_ms=50.0,
        cost_usd=0.0002,
        equivalent_api_cost_usd=0.0002,
    )
    values.update(overrides)
    return LLMCall(**values)


class FakeAdapter:
    provider = "fake"
    model = "fake-model"
    caps = ProviderCaps(1.0, 5.0, True, True, True, "api")

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def invoke(self, system, user, tool, deadline_s):
        self.calls.append({"system": system, "user": user, "tool": tool, "deadline_s": deadline_s})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
