from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from fabrica.catalog import ModelSpec
from fabrica.config import get_settings
from fabrica.llm.base import LLMError, LLMRequest, LLMResult
from fabrica.workspace.sandbox import CONTAINER_IO, CONTAINER_WORK, Sandbox, configured_sandbox

_TIMEOUT_S = 1800


class CodexProvider:
    name = "codex"

    def __init__(self, binary: str | None = None, sandbox: Sandbox | None = None) -> None:
        self.binary = binary or get_settings().codex_bin
        self.sandbox = sandbox or configured_sandbox()

    def command(self, spec: ModelSpec, workdir: Path, io: Path, with_schema: bool) -> list[str]:
        work = self.sandbox.inside(workdir, CONTAINER_WORK)
        io_dir = self.sandbox.inside(io, CONTAINER_IO)
        cmd = [
            self.binary,
            "exec",
            "--model",
            spec.model,
            "--cd",
            work,
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            "--json",
            "--output-last-message",
            f"{io_dir}/last_message.txt",
        ]
        if spec.effort:
            cmd += ["-c", f"model_reasoning_effort={spec.effort}"]
        if with_schema:
            cmd += ["--output-schema", f"{io_dir}/schema.json"]
        return self.sandbox.wrap(cmd, workdir, io)

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
                    *self.command(spec, workdir, tmp_path, schema is not None),
                    prompt,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), _TIMEOUT_S)
            except FileNotFoundError as exc:
                raise LLMError(f"No se encontró la CLI de Codex ({self.binary})") from exc
            except TimeoutError as exc:
                raise LLMError("Codex excedió el tiempo máximo") from exc

            tokens_in, tokens_out = _usage_from_events(stdout.decode())
            if proc.returncode != 0:
                raise LLMError(
                    f"Codex terminó con código {proc.returncode}: {stderr.decode()[-500:]}",
                    tokens_in,
                    tokens_out,
                )

            text = out.read_text(encoding="utf-8") if out.exists() else ""
            data = None
            if request.schema:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise LLMError("Codex devolvió JSON inválido", tokens_in, tokens_out) from exc
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
