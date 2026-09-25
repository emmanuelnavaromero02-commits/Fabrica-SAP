from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Protocol

import httpx

from fabrica.config import get_settings


class RepoStore(Protocol):
    async def ensure_repo(self, req_id: int, title: str) -> str: ...
    async def write_files(
        self, req_id: int, files: dict[str, str], message: str, author: str
    ) -> str: ...
    async def read_file(self, req_id: int, path: str) -> str | None: ...


def repo_name(req_id: int) -> str:
    return f"req-{req_id}"


class LocalRepoStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, req_id: int) -> Path:
        return self.root / repo_name(req_id)

    async def _git(self, cwd: Path, *args: str, env_author: str | None = None) -> str:
        env = None
        if env_author:
            email = f"{env_author}@fabrica.local"
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": env_author,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": "fabrica",
                "GIT_COMMITTER_EMAIL": "fabrica@fabrica.local",
            }
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {err.decode().strip()}")
        return out.decode().strip()

    async def ensure_repo(self, req_id: int, title: str) -> str:
        path = self.path(req_id)
        if not (path / ".git").exists():
            path.mkdir(parents=True, exist_ok=True)
            await self._git(path, "init", "-q", "-b", "main")
            (path / "requisito.json").write_text(
                json.dumps({"id": req_id, "titulo": title}, ensure_ascii=False), encoding="utf-8"
            )
            await self._git(path, "add", "-A")
            await self._git(
                path, "commit", "-q", "-m", "Inicio del requisito", env_author="fabrica"
            )
        return path.as_uri()

    async def write_files(
        self, req_id: int, files: dict[str, str], message: str, author: str
    ) -> str:
        path = self.path(req_id)
        for rel, content in files.items():
            target = (path / rel).resolve()
            if path.resolve() not in target.parents:
                raise ValueError(f"Ruta fuera del repo: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        await self._git(path, "add", "-A")
        if not await self._git(path, "status", "--porcelain"):
            return await self._git(path, "rev-parse", "HEAD")
        await self._git(path, "commit", "-q", "-m", message, env_author=author)
        return await self._git(path, "rev-parse", "HEAD")

    async def read_file(self, req_id: int, path: str) -> str | None:
        target = self.path(req_id) / path
        return target.read_text(encoding="utf-8") if target.exists() else None


class GiteaRepoStore:
    def __init__(self, url: str, token: str, org: str) -> None:
        self.org = org
        self.http = httpx.AsyncClient(
            base_url=f"{url.rstrip('/')}/api/v1",
            headers={"Authorization": f"token {token}"},
            timeout=30,
        )
        self.web = url.rstrip("/")

    async def ensure_repo(self, req_id: int, title: str) -> str:
        resp = await self.http.post(
            f"/orgs/{self.org}/repos",
            json={
                "name": repo_name(req_id),
                "description": title,
                "private": True,
                "auto_init": True,
                "default_branch": "main",
            },
        )
        if resp.status_code not in (201, 409):
            resp.raise_for_status()
        return f"{self.web}/{self.org}/{repo_name(req_id)}"

    async def _sha(self, req_id: int, path: str) -> str | None:
        resp = await self.http.get(f"/repos/{self.org}/{repo_name(req_id)}/contents/{path}")
        return resp.json().get("sha") if resp.status_code == 200 else None

    async def write_files(
        self, req_id: int, files: dict[str, str], message: str, author: str
    ) -> str:
        changes = []
        for path, content in files.items():
            sha = await self._sha(req_id, path)
            changes.append(
                {
                    "operation": "update" if sha else "create",
                    "path": path,
                    "sha": sha,
                    "content": base64.b64encode(content.encode()).decode(),
                }
            )
        resp = await self.http.post(
            f"/repos/{self.org}/{repo_name(req_id)}/contents",
            json={
                "message": message,
                "branch": "main",
                "files": changes,
                "author": {"name": author, "email": f"{author}@fabrica.local"},
            },
        )
        resp.raise_for_status()
        return str(resp.json()["commit"]["sha"])

    async def read_file(self, req_id: int, path: str) -> str | None:
        resp = await self.http.get(f"/repos/{self.org}/{repo_name(req_id)}/raw/{path}")
        return resp.text if resp.status_code == 200 else None


def repo_store() -> RepoStore:
    settings = get_settings()
    if settings.git_backend == "gitea":
        return GiteaRepoStore(settings.gitea_url, settings.gitea_token, settings.gitea_org)
    return LocalRepoStore(settings.data_dir / "repos")
