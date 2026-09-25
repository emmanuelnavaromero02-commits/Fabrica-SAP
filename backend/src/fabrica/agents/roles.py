from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

_COMMON = (
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
    f"{_COMMON} Clasifica el requisito: módulo SAP (capability: FI, CO, MM, SD, PP, QM, PS, "
    "HCM, EAM, TM, BASIS) y tipo RICEFW (R, I, C, E, F, W).",
    _obj(
        {
            "capability": _STR,
            "ricefw": {"type": "string", "enum": ["R", "I", "C", "E", "F", "W"]},
            "confidence": {"type": "number"},
        }
    ),
)

ANALISTA = AgentRole(
    "analista",
    "analizar",
    f"{_COMMON} Eres analista funcional. Lee el requisito y sus documentos. Resume el "
    "alcance, lista lo que falta y formula preguntas concretas al cliente solo si bloquean el "
    "diseño.",
    _obj({"summary": _STR, "missing": _STRS, "questions": _STRS}),
)

ARQUITECTO = AgentRole(
    "arquitecto",
    "disenar_spec",
    f"{_COMMON} Eres arquitecto SAP. Escribe la especificación técnica en Markdown, declara "
    "los objetos a crear (solo paquetes Z/Y) y aseveraciones verificables: cada una con un "
    "texto que el código DEBE contener (must_contain).",
    _obj(
        {
            "spec_markdown": _STR,
            "objects": {
                "type": "array",
                "items": _obj({"name": _STR, "type": _STR, "package": _STR}),
            },
            "assertions": {
                "type": "array",
                "items": _obj({"id": _STR, "description": _STR, "must_contain": _STR}),
            },
        }
    ),
)

DESARROLLADOR = AgentRole(
    "desarrollador",
    "implementar",
    f"{_COMMON} Eres desarrollador ABAP senior. Implementa la spec en ABAP moderno "
    "(sintaxis 7.5+, SQL con campos explícitos, sin SELECT *, sin SQL nativo) y cumple todas "
    "las aseveraciones.",
    _obj(
        {
            "files": {"type": "array", "items": _obj({"path": _STR, "content": _STR})},
            "notes": _STR,
        }
    ),
)

REVISOR = AgentRole(
    "revisor",
    "revisar",
    f"{_COMMON} Eres revisor ABAP adversarial. Busca errores reales: rendimiento, seguridad, "
    "Clean Core y desvíos de la spec. Aprueba solo si no hay objeciones bloqueantes.",
    _obj({"approved": {"type": "boolean"}, "objections": _STRS}),
)

DOCUMENTADOR = AgentRole(
    "documentador",
    "documentar",
    f"{_COMMON} Redacta el manual técnico breve en Markdown a partir de la spec y el código.",
    _obj({"markdown": _STR}),
)
