from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from fabrica.sap.bridge import Assertion, CheckResult, Finding, SapObject

_HEADERS = ("REPORT", "PROGRAM", "CLASS", "INTERFACE", "FUNCTION-POOL")
_ATC_RULES: list[tuple[str, str, str]] = [
    (r"SELECT\s+\*", "error", "SELECT * prohibido: lee solo los campos necesarios"),
    (r"EXEC\s+SQL", "error", "SQL nativo prohibido (Clean Core)"),
    (r"sy-uname\s*=\s*'", "warning", "Usuario escrito a mano en el código"),
    (r"\bCOMMIT\s+WORK\b", "warning", "COMMIT WORK en un reporte: revisar"),
]


class SimulatedSap:
    def __init__(self, root: Path, system: str = "SIM-DEV") -> None:
        self.system = system
        self.root = root / system
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name.upper()}.json"

    async def read_object(self, name: str) -> SapObject | None:
        path = self._path(name)
        if not path.exists():
            return None
        return SapObject(**json.loads(path.read_text(encoding="utf-8")))

    async def write_object(self, obj: SapObject, transport: str) -> None:
        self._path(obj.name).write_text(json.dumps(asdict(obj)), encoding="utf-8")

    async def _source(self, name: str) -> str:
        obj = await self.read_object(name)
        return obj.source if obj else ""

    async def syntax_check(self, name: str) -> CheckResult:
        source = await self._source(name)
        findings: list[Finding] = []
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("*"))
        if not code.strip():
            findings.append(Finding("syntax", "error", f"{name}: objeto vacío o inexistente"))
        elif not code.lstrip().upper().startswith(_HEADERS):
            findings.append(
                Finding("syntax", "error", "Falta la sentencia inicial (REPORT/CLASS…)")
            )
        if code.count("(") != code.count(")"):
            findings.append(Finding("syntax", "error", "Paréntesis sin cerrar"))
        if code.strip() and not code.rstrip().endswith("."):
            findings.append(Finding("syntax", "error", "La última sentencia no termina en punto"))
        return CheckResult(findings)

    async def activate(self, name: str) -> CheckResult:
        syntax = await self.syntax_check(name)
        if syntax.ok:
            return CheckResult()
        return CheckResult([Finding("activation", "error", "No se puede activar con errores")])

    async def run_atc(self, name: str) -> CheckResult:
        source = await self._source(name)
        findings = [
            Finding("atc", severity, message)  # type: ignore[arg-type]
            for pattern, severity, message in _ATC_RULES
            if re.search(pattern, source, re.IGNORECASE)
        ]
        return CheckResult(findings)

    async def run_unit(self, name: str, assertions: list[Assertion]) -> CheckResult:
        source = (await self._source(name)).lower()
        return CheckResult(
            [
                Finding("unit", "error", f"{a.id} no se cumple: {a.description}")
                for a in assertions
                if a.must_contain.lower() not in source
            ]
        )
