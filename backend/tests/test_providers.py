from fabrica.catalog import ModelSpec
from fabrica.llm.anthropic_provider import AnthropicProvider
from fabrica.llm.base import LLMRequest
from fabrica.llm.codex_provider import _usage_from_events

REQ = LLMRequest(system="s", prompt="p", schema={"type": "object"})


def _params(model: str, effort: str | None) -> dict:
    provider = AnthropicProvider(client=object())  # type: ignore[arg-type]
    return provider._params(ModelSpec(provider="anthropic", model=model, effort=effort), REQ)


def test_opus_uses_adaptive_thinking_effort_and_schema() -> None:
    p = _params("claude-opus-5", "xhigh")
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"]["effort"] == "xhigh"
    assert p["output_config"]["format"]["type"] == "json_schema"
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_haiku_skips_thinking_and_effort() -> None:
    p = _params("claude-haiku-4-5", "low")
    assert "thinking" not in p
    assert "effort" not in p["output_config"]


def test_codex_usage_is_summed_from_jsonl() -> None:
    events = '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":4}}\nnot json\n'
    assert _usage_from_events(events) == (10, 4)
