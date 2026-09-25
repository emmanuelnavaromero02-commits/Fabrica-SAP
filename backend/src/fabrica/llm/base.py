from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from fabrica.catalog import ModelSpec


@dataclass(frozen=True)
class LLMRequest:
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
    def __init__(self, message: str, tokens_in: int = 0, tokens_out: int = 0) -> None:
        super().__init__(message)
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out


class Provider(Protocol):
    name: str

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult: ...
