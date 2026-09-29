from __future__ import annotations

from typing import Any

from fabrica.llm.simulated_text import title


def clasificar(prompt: str, tier: str) -> dict[str, Any]:
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


def analizar(prompt: str, tier: str) -> dict[str, Any]:
    without_docs = "Documentos: (ninguno)" in prompt and "Respuestas del cliente:" not in prompt
    questions = (
        ["¿Qué campos debe mostrar el reporte y con qué filtros de selección?"]
        if without_docs
        else []
    )
    return {
        "summary": f"Análisis de '{title(prompt)}': alcance claro para diseño.",
        "missing": ["especificación funcional"] if without_docs else [],
        "questions": questions,
    }


HANDLERS = {"clasificar": clasificar, "analizar": analizar}
