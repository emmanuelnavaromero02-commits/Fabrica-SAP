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
            '<div class="f-header">'
            f"  <h1>🏭 SAP Fiori — {title(prompt)}</h1>"
            '  <div class="f-spacer"></div>'
            '  <button class="f-btn primary">▶ Ejecutar (F8)</button>'
            '  <button class="f-btn">📊 Exportar Excel</button>'
            "</div>"
            '<div class="f-main">'
            '  <div class="f-kpis">'
            '    <div class="f-kpi"><div class="lbl">Total Registros</div>'
            '      <div class="val">1,248</div></div>'
            '    <div class="f-kpi"><div class="lbl">Importe Facturado</div>'
            '      <div class="val">$482,910.00</div></div>'
            '    <div class="f-kpi"><div class="lbl">Partidas Pendientes</div>'
            '      <div class="val">14</div></div>'
            "  </div>"
            '  <div class="f-card">'
            "    <h2>🔍 Criterios de Selección (Pantalla de Selección)</h2>"
            '    <div class="f-form">'
            '      <div><label>Sociedad (BUKRS)</label><input class="f-input" value="1000" /></div>'
            "      <div><label>Ejercicio / Período</label>"
            '        <input class="f-input" value="2026 / 10" /></div>'
            "      <div><label>Fecha de Emisión (Rango)</label>"
            '        <input class="f-input" value="01.10.2026 - 31.10.2026" /></div>'
            '      <div><label>Clase de Documento</label><select class="f-select">'
            "        <option>Factura F2</option><option>Nota de Crédito G2</option>"
            "        <option>Boleta BV</option></select></div>"
            "    </div>"
            "  </div>"
            '  <div class="f-card">'
            '    <div class="f-toolbar">'
            "      <h2>📋 Grilla ALV — Partidas Encontradas</h2>"
            '      <div class="f-spacer"></div>'
            '      <input class="f-input" style="width:200px" placeholder="Filtrar en tabla…" />'
            "    </div>"
            '    <table class="f-table" id="alv-grid">'
            "      <thead><tr>"
            "        <th>Doc. Comercial</th><th>Cliente</th><th>Fecha</th>"
            "        <th>Importe Neto</th><th>Moneda</th><th>Estado SUNAT</th>"
            "      </tr></thead>"
            "      <tbody>"
            "        <tr><td>90001821</td><td>TAILOY PERU S.A.</td><td>15.10.2026</td>"
            "          <td>$14,250.00</td><td>USD</td>"
            '          <td><span class="f-badge ok">Aceptada</span></td></tr>'
            "        <tr><td>90001822</td><td>DISTRIBUIDORA LIMA</td><td>15.10.2026</td>"
            "          <td>$8,920.50</td><td>USD</td>"
            '          <td><span class="f-badge ok">Aceptada</span></td></tr>'
            "        <tr><td>90001823</td><td>CORPORACION ANDINA</td><td>16.10.2026</td>"
            "          <td>$23,100.00</td><td>USD</td>"
            '          <td><span class="f-badge warn">Pendiente CDR</span></td></tr>'
            "        <tr><td>90001824</td><td>IMPORTACIONES SUR</td><td>16.10.2026</td>"
            "          <td>$5,410.00</td><td>USD</td>"
            '          <td><span class="f-badge ok">Aceptada</span></td></tr>'
            "      </tbody>"
            "    </table>"
            "  </div>"
            "</div>"
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
