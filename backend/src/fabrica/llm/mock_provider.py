"""Proveedor simulado: permite probar toda la fábrica sin llaves de API.

Comportamiento determinista y pensado para la demo:
- `analizar` pregunta al cliente si el requisito llega sin documentos.
- `implementar` en N1/N2 deja un `SELECT *` (lo detecta ATC) y así se ve el
  escalamiento; desde N3 entrega código limpio usando el paquete de relevo.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fabrica.catalog import ModelSpec
from fabrica.llm.base import LLMRequest, LLMResult


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", text.upper())[:20].strip("_") or "REQ"


def _title(prompt: str) -> str:
    match = re.search(r"Título: (.+)", prompt)
    return match.group(1).strip() if match else "Requisito"


class MockProvider:
    name = "mock"

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult:
        activity = request.tags.get("activity", "")
        tier = request.tags.get("tier", "N1")
        handler = getattr(self, f"_{activity}", None)
        data: dict[str, Any] = handler(request.prompt, tier) if handler else {"text": "ok"}
        text = json.dumps(data, ensure_ascii=False)
        return LLMResult(text, data, tokens_in=len(request.prompt) // 4, tokens_out=len(text) // 4)

    def _clasificar(self, prompt: str, tier: str) -> dict[str, Any]:
        text = prompt.lower()
        capability = next(
            (
                cap
                for key, cap in [
                    ("factura", "FI"),
                    ("venta", "SD"),
                    ("compra", "MM"),
                    ("material", "MM"),
                    ("pedido", "SD"),
                ]
                if key in text
            ),
            "FI",
        )
        ricefw = "F" if "formulario" in text else "I" if "interfaz" in text else "R"
        return {"capability": capability, "ricefw": ricefw, "confidence": 0.8}

    def _analizar(self, prompt: str, tier: str) -> dict[str, Any]:
        without_docs = "Documentos: (ninguno)" in prompt and "Respuestas del cliente:" not in prompt
        questions = (
            ["¿Qué campos debe mostrar el reporte y con qué filtros de selección?"]
            if without_docs
            else []
        )
        return {
            "summary": f"Análisis de '{_title(prompt)}': alcance claro para diseño.",
            "missing": ["especificación funcional"] if without_docs else [],
            "questions": questions,
        }

    def _disenar_spec(self, prompt: str, tier: str) -> dict[str, Any]:
        name = f"Z_{_slug(_title(prompt))}"
        return {
            "spec_markdown": (
                f"# Especificación técnica — {_title(prompt)}\n\n"
                f"## Objeto\nReporte `{name}` (paquete ZFAB).\n\n"
                "## Lógica\n1. Pantalla de selección con rango de fechas.\n"
                "2. Lectura con campos explícitos y cláusula WHERE.\n"
                "3. Salida ALV.\n"
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

    def _implementar(self, prompt: str, tier: str) -> dict[str, Any]:
        match = re.search(r"Objeto principal: (\S+)", prompt)
        name = match.group(1) if match else "Z_REQ"
        weak = tier in ("N1", "N2")
        select = "SELECT * FROM bkpf" if weak else "SELECT bukrs, belnr, budat FROM bkpf"
        code = (
            f"REPORT {name.lower()}.\n\n"
            "DATA gv_budat TYPE bkpf-budat.\n"
            "SELECT-OPTIONS s_budat FOR gv_budat.\n\n"
            "START-OF-SELECTION.\n"
            f"  {select}\n"
            "    WHERE budat IN @s_budat\n"
            "    INTO TABLE @DATA(lt_docs).\n"
        )
        if not weak:
            code += (
                "  cl_salv_table=>factory( IMPORTING r_salv_table = DATA(lo_alv)\n"
                "                          CHANGING  t_table      = lt_docs ).\n"
                "  lo_alv->display( ).\n"
            )
        return {
            "files": [{"path": f"src/{name.lower()}.prog.abap", "content": code}],
            "notes": "Borrador inicial" if weak else "Corregido con el paquete de relevo",
        }

    def _revisar(self, prompt: str, tier: str) -> dict[str, Any]:
        issues = ["Evitar SELECT *: leer solo campos necesarios"] if "SELECT *" in prompt else []
        return {"approved": not issues, "objections": issues}

    def _documentar(self, prompt: str, tier: str) -> dict[str, Any]:
        return {"markdown": f"# Manual técnico — {_title(prompt)}\n\nGenerado por la fábrica."}
