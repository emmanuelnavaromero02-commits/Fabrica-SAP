from __future__ import annotations

from fabrica.agents.roles import COMMON_RULES, STRING, AgentRole, schema_object

ARQUITECTO = AgentRole(
    "arquitecto",
    "disenar_spec",
    f"{COMMON_RULES} Eres arquitecto SAP. Escribe la especificación técnica en Markdown, "
    "un prototipo interactivo en HTML (SAP Fiori), un plan de pruebas funcionales en Markdown, "
    "declara los objetos a crear (solo paquetes Z/Y) y aseveraciones verificables: cada una "
    "con un texto que el código DEBE contener (must_contain).",
    schema_object(
        {
            "spec_markdown": STRING,
            "prototype_html": STRING,
            "test_plan_markdown": STRING,
            "objects": {
                "type": "array",
                "items": schema_object(
                    {
                        "name": STRING,
                        "type": {"type": "string", "enum": ["PROG", "CLAS", "INTF"]},
                        "package": STRING,
                    }
                ),
            },
            "assertions": {
                "type": "array",
                "items": schema_object(
                    {"id": STRING, "description": STRING, "must_contain": STRING}
                ),
            },
        }
    ),
)
