from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from fabrica.catalog import ModelSpec
from fabrica.llm import simulated_build, simulated_design, simulated_estimation, simulated_intake
from fabrica.llm.base import LLMRequest, LLMResult

Handler = Callable[[str, str], dict[str, Any]]

HANDLERS: dict[str, Handler] = {
    **simulated_intake.HANDLERS,
    **simulated_design.HANDLERS,
    **simulated_estimation.HANDLERS,
    **simulated_build.HANDLERS,
}


class SimulatedProvider:
    name = "simulated"

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult:
        activity = request.tags.get("activity", "")
        tier = request.tags.get("tier", "N1")
        handler = HANDLERS.get(activity)
        data: dict[str, Any] = handler(request.prompt, tier) if handler else {"text": "ok"}
        text = json.dumps(data, ensure_ascii=False)
        return LLMResult(text, data, tokens_in=len(request.prompt) // 4, tokens_out=len(text) // 4)
