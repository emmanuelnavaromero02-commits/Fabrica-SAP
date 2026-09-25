from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fabrica.config import get_settings

CONTAINER_WORK = "/work"
CONTAINER_IO = "/io"


@dataclass(frozen=True)
class Sandbox:
    mode: Literal["none", "docker"] = "none"
    image: str = "fabrica-agente:latest"
    network: str = "bridge"
    cpus: str = "2"
    memory: str = "4g"
    env: tuple[str, ...] = field(default=("OPENAI_API_KEY", "ANTHROPIC_API_KEY"))

    def inside(self, host: Path, container: str) -> str:
        return container if self.mode == "docker" else str(host)

    def wrap(self, command: list[str], work: Path, io: Path) -> list[str]:
        if self.mode == "none":
            return command
        prefix = [
            "docker", "run", "--rm",
            "--network", self.network,
            "--cpus", self.cpus,
            "--memory", self.memory,
            "--pids-limit", "512",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "-v", f"{work}:{CONTAINER_WORK}",
            "-v", f"{io}:{CONTAINER_IO}",
            "-w", CONTAINER_WORK,
        ]  # fmt: skip
        for name in self.env:
            prefix += ["-e", name]
        return [*prefix, self.image, *command]


def configured_sandbox() -> Sandbox:
    settings = get_settings()
    return Sandbox(
        mode=settings.sandbox,
        image=settings.sandbox_image,
        network=settings.sandbox_network,
    )
