"""Resultado común de cualquier verificador automático."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Verification:
    passed: bool
    issues: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_issues(cls, issues: list[str], **evidence: Any) -> Verification:
        return cls(passed=not issues, issues=issues, evidence=evidence)


class Verifier(Protocol):
    async def verify(self, output: dict[str, Any]) -> Verification: ...
