"""Puente SAP: contrato + reglas de seguridad que ningún agente puede saltarse.

Reglas fijas:
- Solo escribe en sistemas DEV.
- Solo toca objetos de paquetes permitidos (Z*/Y*).
- Nunca libera transportes (no existe la operación).
- Toda llamada queda auditada.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    check: str  # syntax | activation | atc | unit
    severity: Severity
    message: str

    def as_text(self) -> str:
        return f"[{self.check}/{self.severity}] {self.message}"


@dataclass(frozen=True)
class SapObject:
    name: str
    type: str  # PROG, CLAS, FUGR…
    package: str
    source: str


@dataclass(frozen=True)
class Assertion:
    id: str
    description: str
    must_contain: str


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


class SapBridge(Protocol):
    system: str

    async def read_object(self, name: str) -> SapObject | None: ...
    async def write_object(self, obj: SapObject, transport: str) -> None: ...
    async def syntax_check(self, name: str) -> CheckResult: ...
    async def activate(self, name: str) -> CheckResult: ...
    async def run_atc(self, name: str) -> CheckResult: ...
    async def run_unit(self, name: str, assertions: list[Assertion]) -> CheckResult: ...


class PolicyViolation(PermissionError):
    """Operación prohibida por las reglas del puente."""


AuditFn = Callable[[str, str, bool, str], Awaitable[None]]


class GuardedBridge:
    """Envuelve un puente real o simulado y aplica las reglas antes de cada llamada."""

    def __init__(self, inner: SapBridge, allowed_packages: tuple[str, ...], audit: AuditFn) -> None:
        self.inner = inner
        self.system = inner.system
        self.allowed = allowed_packages
        self.audit = audit

    def _check_write(self, obj: SapObject) -> None:
        if not self.system.upper().endswith("DEV"):
            raise PolicyViolation(f"Escritura prohibida fuera de DEV ({self.system})")
        if not obj.package.upper().startswith(self.allowed):
            raise PolicyViolation(f"Paquete {obj.package} fuera de los permitidos {self.allowed}")
        if not obj.name.upper().startswith(self.allowed):
            raise PolicyViolation(f"Objeto {obj.name} no es de cliente (Z/Y)")

    async def read_object(self, name: str) -> SapObject | None:
        obj = await self.inner.read_object(name)
        await self.audit("read_object", name, True, "")
        return obj

    async def write_object(self, obj: SapObject, transport: str) -> None:
        try:
            self._check_write(obj)
        except PolicyViolation as exc:
            await self.audit("write_object", obj.name, False, str(exc))
            raise
        await self.inner.write_object(obj, transport)
        await self.audit("write_object", obj.name, True, f"transporte {transport}")

    async def _run(self, tool: str, name: str, result: CheckResult) -> CheckResult:
        detail = "; ".join(f.as_text() for f in result.findings)
        await self.audit(tool, name, result.ok, detail)
        return result

    async def syntax_check(self, name: str) -> CheckResult:
        return await self._run("syntax_check", name, await self.inner.syntax_check(name))

    async def activate(self, name: str) -> CheckResult:
        return await self._run("activate", name, await self.inner.activate(name))

    async def run_atc(self, name: str) -> CheckResult:
        return await self._run("run_atc", name, await self.inner.run_atc(name))

    async def run_unit(self, name: str, assertions: list[Assertion]) -> CheckResult:
        return await self._run("run_unit", name, await self.inner.run_unit(name, assertions))
