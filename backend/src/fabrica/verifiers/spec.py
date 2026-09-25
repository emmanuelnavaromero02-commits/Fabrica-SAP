"""Verifica que la spec sea construible: objetos Z válidos y aseveraciones ejecutables."""

from __future__ import annotations

from typing import Any

from fabrica.verifiers.base import Verification


class SpecVerifier:
    def __init__(self, allowed_packages: tuple[str, ...]) -> None:
        self.allowed = allowed_packages

    async def verify(self, output: dict[str, Any]) -> Verification:
        issues: list[str] = []
        spec = str(output.get("spec_markdown", ""))
        objects = output.get("objects") or []
        assertions = output.get("assertions") or []

        if len(spec) < 80:
            issues.append("La spec es demasiado corta para construir")
        if not objects:
            issues.append("La spec no declara objetos SAP")
        for obj in objects:
            name, package = str(obj.get("name", "")), str(obj.get("package", ""))
            if not name.upper().startswith(self.allowed):
                issues.append(f"Objeto {name} no es de cliente (Z/Y)")
            if not package.upper().startswith(self.allowed):
                issues.append(f"Paquete {package} no permitido")
        if not assertions:
            issues.append("Sin aseveraciones verificables: no se podría probar")
        for a in assertions:
            if not a.get("must_contain"):
                issues.append(f"Aseveración {a.get('id', '?')} sin criterio ejecutable")
        return Verification.from_issues(issues, objects=len(objects), assertions=len(assertions))
