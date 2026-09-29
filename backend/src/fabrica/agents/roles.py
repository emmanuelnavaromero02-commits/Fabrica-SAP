from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def schema_object(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}

COMMON_RULES = (
    "Trabajas en una fábrica de software SAP (S/4HANA, Clean Core). "
    "Responde SOLO con el JSON pedido. Si te falta información, dilo en el campo previsto; "
    "nunca inventes datos del cliente."
)


@dataclass(frozen=True)
class AgentRole:
    name: str
    activity: str
    system: str
    schema: dict[str, Any]


CLASIFICADOR = AgentRole(
    "clasificador",
    "clasificar",
    f"{COMMON_RULES} Clasifica el requisito: módulo SAP (capability: FI, CO, MM, SD, PP, QM, PS, "
    "HCM, EAM, TM, BASIS) y tipo RICEFW (R, I, C, E, F, W).",
    schema_object(
        {
            "capability": STRING,
            "ricefw": {"type": "string", "enum": ["R", "I", "C", "E", "F", "W"]},
            "confidence": {"type": "number"},
        }
    ),
)

ANALISTA = AgentRole(
    "analista",
    "analizar",
    f"{COMMON_RULES} Eres analista funcional. Lee el requisito y sus documentos. Resume el "
    "alcance, lista lo que falta y formula preguntas concretas al cliente solo si bloquean el "
    "diseño.",
    schema_object({"summary": STRING, "missing": STRINGS, "questions": STRINGS}),
)
