from __future__ import annotations

from dataclasses import dataclass, replace

from fabrica.catalog import ModelSpec
from fabrica.llm.base import LLMRequest, LLMResult, Provider


@dataclass
class GatewayResult:
    spec: ModelSpec
    result: LLMResult
    cost_usd: float


class ModelGateway:
    def __init__(self, providers: dict[str, Provider] | None = None) -> None:
        self._providers = providers or {}

    def _provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = build_provider(name)
        return self._providers[name]

    async def call(self, spec: ModelSpec, request: LLMRequest, *, tier: str) -> GatewayResult:
        tagged = replace(request, tags={**request.tags, "tier": tier, "model": spec.model})
        result = await self._provider(spec.provider).complete(spec, tagged)
        return GatewayResult(spec, result, spec.cost(result.tokens_in, result.tokens_out))


def build_provider(name: str) -> Provider:
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
