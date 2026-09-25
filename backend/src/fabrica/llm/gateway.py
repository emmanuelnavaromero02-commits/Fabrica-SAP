"""Gateway de modelos: único punto por el que la fábrica llama a cualquier IA.

Resuelve el proveedor, etiqueta la petición, mide tokens y calcula el costo.
En modo `mock` todas las llamadas van al proveedor simulado.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from fabrica.catalog import ModelSpec
from fabrica.config import get_settings
from fabrica.llm.base import LLMRequest, LLMResult, Provider


@dataclass
class GatewayResult:
    spec: ModelSpec
    result: LLMResult
    cost_usd: float


class ModelGateway:
    def __init__(self, providers: dict[str, Provider] | None = None) -> None:
        self._providers = providers or {}
        self._mock = get_settings().llm_mode == "mock"

    def _provider(self, name: str) -> Provider:
        key = "mock" if self._mock else name
        if key not in self._providers:
            self._providers[key] = _build(key)
        return self._providers[key]

    async def call(self, spec: ModelSpec, request: LLMRequest, *, tier: str) -> GatewayResult:
        tagged = replace(request, tags={**request.tags, "tier": tier, "model": spec.model})
        result = await self._provider(spec.provider).complete(spec, tagged)
        return GatewayResult(spec, result, spec.cost(result.tokens_in, result.tokens_out))


def _build(name: str) -> Provider:
    # Importes diferidos: cada SDK se carga solo si se usa.
    if name == "mock":
        from fabrica.llm.mock_provider import MockProvider

        return MockProvider()
    if name == "anthropic":
        from fabrica.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if name == "openai":
        from fabrica.llm.openai_provider import OpenAIProvider

        return OpenAIProvider()
    if name == "codex":
        from fabrica.llm.codex_provider import CodexProvider

        return CodexProvider()
    raise ValueError(f"Proveedor desconocido: {name}")
