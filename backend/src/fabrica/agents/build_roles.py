from __future__ import annotations

from fabrica.agents.roles import COMMON_RULES, STRING, STRINGS, AgentRole, schema_object

DESARROLLADOR = AgentRole(
    "desarrollador",
    "implementar",
    f"{COMMON_RULES} Eres desarrollador ABAP senior. Implementa la spec en ABAP moderno "
    "(sintaxis 7.5+, SQL con campos explícitos, sin SELECT *, sin SQL nativo) y cumple todas "
    "las aseveraciones.",
    schema_object(
        {
            "files": {"type": "array", "items": schema_object({"path": STRING, "content": STRING})},
            "notes": STRING,
        }
    ),
)


REVISOR = AgentRole(
    "revisor",
    "revisar",
    f"{COMMON_RULES} Eres revisor ABAP adversarial. Busca errores reales: rendimiento, seguridad, "
    "Clean Core y desvíos de la spec. Aprueba solo si no hay objeciones bloqueantes.",
    schema_object({"approved": {"type": "boolean"}, "objections": STRINGS}),
)


DOCUMENTADOR = AgentRole(
    "documentador",
    "documentar",
    f"{COMMON_RULES} Redacta el manual técnico breve en Markdown a partir de la spec y el código.",
    schema_object({"markdown": STRING}),
)
