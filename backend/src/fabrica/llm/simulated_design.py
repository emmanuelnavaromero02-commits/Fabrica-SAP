from __future__ import annotations

from typing import Any

from fabrica.llm.simulated_text import slug, title


def disenar_spec(prompt: str, tier: str) -> dict[str, Any]:
    name = f"Z_{slug(title(prompt))}"
    return {
        "spec_markdown": (
            f"# Especificación técnica — {title(prompt)}\n\n"
            f"## Objeto\nReporte `{name}` (paquete ZFAB).\n\n"
            "## Lógica\n1. Pantalla de selección con rango de fechas.\n"
            "2. Lectura con campos explícitos y cláusula WHERE.\n"
            "3. Salida ALV.\n"
        ),
        "prototype_html": (
            "<!DOCTYPE html><html><head><title>Prototipo SAP Fiori</title>"
            "<style>body{font-family:sans-serif;padding:20px;}</style></head>"
            "<body><header><h2>Fiori Launchpad</h2></header>"
            "<main><div><p>Filtros y tabla ALV</p></div></main></body></html>"
        ),
        "test_plan_markdown": (
            f"# Plan de Pruebas Funcionales — {title(prompt)}\n\n"
            "## Escenario 1: Ejecución con filtros estándar (Caso Positivo)\n"
            "- **Dado que:** Existen partidas en la sociedad seleccionada.\n"
            "- **Cuando:** El usuario ejecuta el reporte con fecha del mes.\n"
            "- **Entonces:** La tabla muestra los registros y el totalizador coincide.\n\n"
            "## Escenario 2: Sin datos coincidentes (Caso Borde)\n"
            "- **Dado que:** Se ingresa un rango sin movimientos.\n"
            "- **Cuando:** Se presiona ejecutar.\n"
            "- **Entonces:** El sistema emite el mensaje informativo de no datos.\n"
        ),
        "objects": [{"name": name, "type": "PROG", "package": "ZFAB"}],
        "assertions": [
            {
                "id": "AS-1",
                "description": "Tiene pantalla de selección",
                "must_contain": "SELECT-OPTIONS",
            },
            {"id": "AS-2", "description": "Usa salida ALV", "must_contain": "cl_salv_table"},
        ],
    }


HANDLERS = {"disenar_spec": disenar_spec}
