from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fabrica.sap.adt_xml import OBJECT_PATHS
from fabrica.sap.bridge import Assertion, CheckResult, PolicyViolation, SapObject
from fabrica.sap.factory import RequirementSap
from fabrica.verifiers.base import Verification


class AbapVerifier:
    def __init__(
        self,
        sap: RequirementSap,
        *,
        main_object: dict[str, str],
        assertions: list[Assertion],
    ) -> None:
        self.sap = sap
        self.bridge = sap.bridge
        self.main = main_object
        self.assertions = assertions

    async def verify(self, output: dict[str, Any]) -> Verification:
        files = [f for f in output.get("files") or [] if str(f.get("content", "")).strip()]
        name = self.main["name"]
        kind = self.main.get("type", "PROG").upper()
        if kind not in OBJECT_PATHS:
            return Verification(False, [f"[spec] Tipo de objeto {kind} no soportado por el puente"])
        if not files:
            return Verification(False, ["[formato] La respuesta no contiene código fuente"])
        source = next(
            (f["content"] for f in files if name.lower() in str(f.get("path", "")).lower()),
            files[0]["content"],
        )
        obj = SapObject(
            name=name, type=kind, package=self.main.get("package", "ZFAB"), source=source
        )
        try:
            transport = await self.sap.write(obj)
        except PolicyViolation as exc:
            return Verification(False, [f"[politica] {exc}"])
        except Exception as exc:
            return Verification(False, [f"[sap] {type(exc).__name__}: {exc}"])

        issues: list[str] = []
        stages: dict[str, str] = {}
        checks: tuple[tuple[str, Callable[[], Awaitable[CheckResult]]], ...] = (
            ("sintaxis", lambda: self.bridge.syntax_check(name)),
            ("activacion", lambda: self.bridge.activate(name)),
            ("atc", lambda: self.bridge.run_atc(name)),
            ("pruebas", lambda: self.bridge.run_unit(name, self.assertions)),
        )
        for label, check in checks:
            try:
                result = await check()
            except Exception as exc:
                failure = f"[sap/{label}] {type(exc).__name__}: {exc}"
                return Verification(False, [*issues, failure], {"stages": stages})
            stages[label] = "ok" if result.ok else "falla"
            issues += [f.as_text() for f in result.findings if f.severity == "error"]
            if label == "sintaxis" and not result.ok:
                break
        return Verification.from_issues(
            issues, stages=stages, system=self.bridge.system, transport=transport
        )
