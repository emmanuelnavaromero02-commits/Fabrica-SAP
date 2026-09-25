from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from fabrica.catalog import ModelSpec
from fabrica.config import get_settings
from fabrica.llm.base import LLMError, LLMRequest, LLMResult

_TIMEOUT_S = 1800


class CodexProvider:
    name = "codex"

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or get_settings().codex_bin

    def _command(self, spec: ModelSpec, workdir: Path, out: Path, schema: Path | None) -> list[str]:
        cmd = [
            self.binary,
            "exec",
            "--model",
            spec.model,
            "--cd",
            str(workdir),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            str(out),
        ]
        if spec.effort:
            cmd += ["-c", f"model_reasoning_effort={spec.effort}"]
        if schema:
            cmd += ["--output-schema", str(schema)]
        return cmd

    async def complete(self, spec: ModelSpec, request: LLMRequest) -> LLMResult:
        with tempfile.TemporaryDirectory(prefix="codex-") as tmp:
            tmp_path = Path(tmp)
            workdir = Path(request.workdir) if request.workdir else tmp_path
            out = tmp_path / "last_message.txt"
            schema = None
            if request.schema:
                schema = tmp_path / "schema.json"
                schema.write_text(json.dumps(request.schema), encoding="utf-8")

            prompt = f"{request.system}\n\n{request.prompt}"
            try:
                proc = await asyncio.create_subprocess_exec(
                    *self._command(spec, workdir, out, schema),
                    prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), _TIMEOUT_S)
            except FileNotFoundError as exc:
                raise LLMError(f"No se encontró la CLI de Codex ({self.binary})") from exc
            except TimeoutError as exc:
                raise LLMError("Codex excedió el tiempo máximo") from exc

            if proc.returncode != 0:
                raise LLMError(
                    f"Codex terminó con código {proc.returncode}: {stderr.decode()[-500:]}"
                )

            text = out.read_text(encoding="utf-8") if out.exists() else ""
            tokens_in, tokens_out = _usage_from_events(stdout.decode())
            data = None
            if request.schema:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise LLMError("Codex devolvió JSON inválido") from exc
            return LLMResult(text, data, tokens_in, tokens_out)


def _usage_from_events(jsonl: str) -> tuple[int, int]:
    tokens_in = tokens_out = 0
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage") if isinstance(event, dict) else None
        if isinstance(usage, dict):
            tokens_in += int(usage.get("input_tokens", 0))
            tokens_out += int(usage.get("output_tokens", 0))
    return tokens_in, tokens_out
