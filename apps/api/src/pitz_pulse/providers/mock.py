"""Deterministic keyword rules so the stack runs without credentials. Never model quality.

The keywords were picked from the case messages' own wording, so a mock run scores well on the
case set by construction: never read eval scores of mock runs as model quality.
"""

import re
from typing import Any

from pitz_pulse.models_catalog import MOCK, lookup
from pitz_pulse.providers.base import LLMCall

_MESSAGE = re.compile(r"<message>(.*)</message>", re.DOTALL)
_RULES = (
    (("acceso", "acesso", "permiso", "permissão"), "acceso", "devops"),
    (
        ("error", "erro", "falla", "não funciona", "no funciona", "some ", "lenta", "errado"),
        "bug",
        "backend",
    ),
    (
        ("automatizar", "automatiz", "manualmente", "algo que"),
        "automatizacion",
        "digital_transformation",
    ),
    (("planilha", "reporte", "cuántos", "quantos", "registros", "datos", "dados"), "datos", "data"),
)
_PORTUGUESE = ("ção", "você", " não ", " um ", " uma ", "preciso", "pessoal", " pra ", "qual ")


class MockAdapter:
    provider = MOCK
    model = "mock"
    caps = lookup(MOCK, "mock")

    def invoke(self, system: str, user: str, tool: dict[str, Any], deadline_s: float) -> LLMCall:
        match = _MESSAGE.search(user)
        text = f" {(match.group(1) if match else user).lower()} "
        categoria, area = "consulta", "producto"
        for keywords, rule_categoria, rule_area in _RULES:
            if any(keyword in text for keyword in keywords):
                categoria, area = rule_categoria, rule_area
                break
        output = {
            "categoria": categoria,
            "prioridad": "media",
            "area_sugerida": area,
            "idioma": "pt" if any(marker in text for marker in _PORTUGUESE) else "es",
            "resumen": "Solicitud clasificada por el modo mock sin modelo.",
            "requiere_info": False,
            "pregunta_seguimiento": None,
            "confianza": 0.5,
        }
        return LLMCall(output, "tool_use", "mock", "mock", 0, 0, 0.0, 0.0, 0.0)
