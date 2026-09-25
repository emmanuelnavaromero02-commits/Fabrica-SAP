from __future__ import annotations

from pathlib import Path

from fabrica.catalog import ModelSpec
from fabrica.git.repo import LocalRepoStore
from fabrica.llm.codex_provider import CodexProvider
from fabrica.workspace.manager import WorkspaceManager
from fabrica.workspace.sandbox import Sandbox

SPEC = ModelSpec(provider="codex", model="gpt-6-sol", effort="high")


def test_codex_runs_directly_without_sandbox(tmp_path: Path) -> None:
    cmd = CodexProvider("codex", Sandbox()).command(SPEC, tmp_path / "w", tmp_path / "io", True)
    assert cmd[:2] == ["codex", "exec"]
    assert str(tmp_path / "w") in cmd
    assert f"{tmp_path / 'io'}/schema.json" in cmd


def test_codex_runs_inside_restricted_container(tmp_path: Path) -> None:
    sandbox = Sandbox(mode="docker", image="agente:1", network="fabrica")
    cmd = CodexProvider("codex", sandbox).command(SPEC, tmp_path / "w", tmp_path / "io", False)
    image_at = cmd.index("agente:1")
    docker, inner = cmd[:image_at], cmd[image_at + 1 :]
    assert docker[:3] == ["docker", "run", "--rm"]
    assert {"--cap-drop", "ALL", "no-new-privileges"} <= set(docker)
    assert f"{tmp_path / 'w'}:/work" in docker and f"{tmp_path / 'io'}:/io" in docker
    assert docker[docker.index("--network") + 1] == "fabrica"
    assert inner[inner.index("--cd") + 1] == "/work"
    assert "/io/last_message.txt" in inner
    assert "--output-schema" not in inner


async def test_workspace_clones_and_resyncs_requirement_repo(tmp_path: Path) -> None:
    repos = LocalRepoStore(tmp_path / "repos")
    await repos.ensure_repo(7, "Reporte")
    manager = WorkspaceManager(tmp_path / "ws")

    path = await manager.prepare(7, repos.clone_url(7))
    assert (path / "requisito.json").exists()
    (path / "basura.txt").write_text("x")

    await repos.write_files(7, {"diseno/spec.md": "# Spec"}, "Diseño", "arquitecto")
    path = await manager.prepare(7, repos.clone_url(7))
    assert (path / "diseno" / "spec.md").read_text() == "# Spec"
    assert not (path / "basura.txt").exists()
