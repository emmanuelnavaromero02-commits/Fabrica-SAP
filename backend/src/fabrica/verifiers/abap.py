from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fabrica.sap.adt_bridge import AdtError
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
        files = output.get("files") or []
        name = self.main["name"]
        source = next(
            (f["content"] for f in files if name.lower() in str(f.get("path", "")).lower()),
            files[0]["content"] if files else "",
        )
        obj = SapObject(
            name=name,
            type=self.main.get("type", "PROG"),
            package=self.main.get("package", "ZFAB"),
            source=source,
        )
        try:
            transport = await self.sap.write(obj)
        except PolicyViolation as exc:
            return Verification(False, [f"[politica] {exc}"])
        except AdtError as exc:
            return Verification(False, [f"[sap] {exc}"])

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
            except AdtError as exc:
                return Verification(False, [*issues, f"[sap/{label}] {exc}"], {"stages": stages})
            stages[label] = "ok" if result.ok else "falla"
            issues += [f.as_text() for f in result.findings if f.severity == "error"]
            if label == "sintaxis" and not result.ok:
                break
        return Verification.from_issues(
            issues, stages=stages, system=self.bridge.system, transport=transport
        )
