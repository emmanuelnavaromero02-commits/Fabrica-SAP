from __future__ import annotations

from fabrica.agents.roles import COMMON_RULES, STRING, STRINGS, AgentRole, schema_object

ESTIMADOR = AgentRole(
    "estimador",
    "estimar",
    f"{COMMON_RULES} Eres líder técnico SAP. Asigna una talla (XS, S, M, L, XL) a cada "
    "objeto de la spec según su esfuerzo real de construcción y pruebas. Justifica cada "
    "talla y lista las asunciones. Las horas las calcula la fábrica a partir de las tallas.",
    schema_object(
        {
            "items": {
                "type": "array",
                "items": schema_object(
                    {
                        "object": STRING,
                        "size": {"type": "string", "enum": ["XS", "S", "M", "L", "XL"]},
                        "rationale": STRING,
                    }
                ),
            },
            "assumptions": STRINGS,
        }
    ),
)
