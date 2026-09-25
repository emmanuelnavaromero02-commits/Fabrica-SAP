from __future__ import annotations

import json
from typing import Any

import anthropic

from fabrica.catalog import ModelSpec
from fabrica.llm.base import LLMError, LLMRequest, LLMResult

_NO_ADAPTIVE = ("claude-haiku-4-5",)
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None) -> None:
        self.client = client or anthropic.AsyncAnthropic()

    def _params(self, spec: ModelSpec, req: LLMRequest) -> dict[str, Any]:
        output_config: dict[str, Any] = {}
        params: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": req.max_tokens,
            "system": [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if spec.model not in _NO_ADAPTIVE:
            params["thinking"] = {"type": "adaptive"}
            if spec.effort:
                output_config["effort"] = spec.effort
        if req.schema:
            output_config["format"] = {"type": "json_schema", "schema": req.schema}
        if output_config:
            params["output_config"] = output_config
        return params

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult:
        params = self._params(spec, request)
        try:
            if spec.fallbacks:
                async with self.client.beta.messages.stream(
                    **params, betas=[_FALLBACK_BETA], fallbacks="default"
                ) as beta_stream:
                    message: Any = await beta_stream.get_final_message()
            else:
                async with self.client.messages.stream(**params) as stream:
                    message = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            raise LLMError(f"Límite de uso de Anthropic: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Sin conexión con Anthropic") from exc

        usage = message.usage
        tokens_in = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
        )
        if message.stop_reason == "refusal":
            return LLMResult("", None, tokens_in, usage.output_tokens, refused=True)

        text = "".join(b.text for b in message.content if b.type == "text")
        data = None
        if request.schema:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"Claude devolvió JSON inválido (stop_reason={message.stop_reason})",
                    tokens_in,
                    usage.output_tokens,
                ) from exc
        return LLMResult(text, data, tokens_in, usage.output_tokens)
