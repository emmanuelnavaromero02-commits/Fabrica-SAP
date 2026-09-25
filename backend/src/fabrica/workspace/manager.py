from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fabrica.config import get_settings


class WorkspaceError(RuntimeError): ...


async def _git(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise WorkspaceError(f"git {args[0]}: {err.decode().strip()}")


class WorkspaceManager:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().data_dir / "workspaces"

    def path(self, req_id: int) -> Path:
        return self.root / f"req-{req_id}"

    async def prepare(
        self, req_id: int, clone_url: str, auth_env: dict[str, str] | None = None
    ) -> Path:
        path = self.path(req_id)
        if (path / ".git").exists():
            await _git("remote", "set-url", "origin", clone_url, cwd=path)
            await _git("fetch", "--quiet", "origin", cwd=path, env=auth_env)
            await _git("reset", "--quiet", "--hard", "origin/main", cwd=path)
            await _git("clean", "--quiet", "-fdx", cwd=path)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            await _git("clone", "--quiet", "--branch", "main", clone_url, str(path), env=auth_env)
        return path
