"""Contrato común a todos los proveedores de IA."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from fabrica.catalog import ModelSpec


@dataclass(frozen=True)
class LLMRequest:
    """Petición independiente del proveedor.

    `schema` pide una respuesta JSON validada con ese JSON Schema.
    `workdir` solo lo usan proveedores que trabajan sobre archivos (Codex).
    """

    system: str
    prompt: str
    schema: dict[str, Any] | None = None
    max_tokens: int = 16000
    workdir: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str
    data: dict[str, Any] | None
    tokens_in: int = 0
    tokens_out: int = 0
    refused: bool = False


class LLMError(RuntimeError):
    """Fallo del proveedor (red, cuota, respuesta inválida). Se trata como intento fallido."""


class Provider(Protocol):
    name: str

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult: ...
