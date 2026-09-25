from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AttemptRecord:
    tier: str
    model: str
    output: dict[str, Any] | None
    issues: list[str]


@dataclass
class Handoff:
    activity: str
    history: list[AttemptRecord] = field(default_factory=list)

    def add(self, record: AttemptRecord) -> None:
        self.history.append(record)

    @property
    def last(self) -> AttemptRecord | None:
        return self.history[-1] if self.history else None

    def render(self) -> str:
        if not self.history:
            return ""
        last = self.history[-1]
        tried = "\n".join(
            f"- {r.tier} · {r.model}: {len(r.issues)} problema(s)" for r in self.history
        )
        issues = "\n".join(f"- {i}" for i in last.issues) or "- (sin detalle)"
        previous = (
            json.dumps(last.output, ensure_ascii=False, indent=2) if last.output else "(nada)"
        )
        return (
            "\n\n## PAQUETE DE RELEVO\n"
            "Intentos anteriores de esta misma tarea NO pasaron la verificación automática.\n"
            "No empieces de cero: parte del último resultado y corrige SOLO lo que falló.\n\n"
            f"### Intentos\n{tried}\n\n"
            f"### Problemas detectados en el último intento\n{issues}\n\n"
            f"### Último resultado\n```json\n{previous[:12000]}\n```\n"
        )
