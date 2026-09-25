from __future__ import annotations

import json
from typing import Any

import openai

from fabrica.catalog import ModelSpec
from fabrica.llm.base import LLMError, LLMRequest, LLMResult


class OpenAIProvider:
    name = "openai"

    def __init__(self, client: openai.AsyncOpenAI | None = None) -> None:
        self.client = client or openai.AsyncOpenAI()

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult:
        params: dict[str, Any] = {
            "model": spec.model,
            "instructions": request.system,
            "input": request.prompt,
            "max_output_tokens": request.max_tokens,
        }
        if spec.effort:
            params["reasoning"] = {"effort": spec.effort}
        if request.schema:
            params["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": request.tags.get("activity", "resultado"),
                    "schema": request.schema,
                    "strict": True,
                }
            }
        try:
            response = await self.client.responses.create(**params)
        except openai.APIStatusError as exc:
            raise LLMError(f"OpenAI {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Sin conexión con OpenAI") from exc

        text = response.output_text
        usage = response.usage
        data = None
        if request.schema:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LLMError("OpenAI devolvió JSON inválido") from exc
        return LLMResult(
            text,
            data,
            usage.input_tokens if usage else 0,
            usage.output_tokens if usage else 0,
        )
