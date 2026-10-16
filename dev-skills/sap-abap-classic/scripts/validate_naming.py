#!/usr/bin/env python3
"""
validate_naming.py — ZDSWF naming convention validator (sap-abap-classic)

Walks objects/ and tests/ folders of a handoff bundle and verifies that
every file follows ZDSWF classic naming conventions.

Usage:
    python validate_naming.py <handoff-bundle-root>

Exit codes:
    0 — clean
    1 — violations
    2 — bundle structure invalid
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

ROOT_PACKAGE = "ZDSWF"

# Allowed prefixes per folder
ALLOWED_PREFIXES = {
    "prog":  ("SAPMZDSWF_", "ZDSWF_"),               # SAPM* for module pools, ZDSWF_ for reports
    "fugr":  ("ZDSWF_",),
    "clas":  ("ZCL_ZDSWF_", "ZCX_ZDSWF_", "ZIF_ZDSWF_"),
    "dtel":  ("ZDSWF_",),
    "doma":  ("ZDSWF_",),
    "tabl":  ("ZDSWF_",),
    "view":  ("ZDSWF_",),
    "enq":   ("EZDSWF_",),                            # lock objects must start with E
    "form":  ("ZDSWF_",),
    "msag":  ("ZDSWF_",),
    "enhc":  ("ZDSWF_",),
    "badi":  ("ZCL_ZDSWF_",),                         # BAdI impl classes
    "tests": ("ZCL_ZDSWF_", "LTCL_", "LTC_"),
}

FORBIDDEN_PREFIXES = ("Y", "Z_", "ZZ_", "/")


class Violation:
    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        self.message = message

    def __str__(self) -> str:
        return f"  ✗ {self.path}: {self.message}"


def iter_files(folder: Path) -> Iterable[Path]:
    yield from (p for p in folder.rglob("*") if p.is_file())


def file_prefix(p: Path) -> str:
    name = p.name
    for suffix in (".clas.testclasses.abap", ".clas.locals_imp.abap", ".clas.abap",
                   ".prog.abap", ".fugr.abap"):
        if name.endswith(suffix):
            return name[: -len(suffix)].upper()
    return p.stem.upper()


def validate_folder(root: Path, sub: str, violations: list[Violation]) -> None:
    folder = root / "objects" / sub if sub != "tests" else root / "tests"
    if not folder.exists():
        return

    allowed = ALLOWED_PREFIXES.get(sub, (f"{ROOT_PACKAGE}_",))
    for f in iter_files(folder):
        prefix = file_prefix(f)
        for fp in FORBIDDEN_PREFIXES:
            if prefix.startswith(fp) and not any(prefix.startswith(a) for a in allowed):
                violations.append(Violation(f, f"forbidden prefix — must match one of {allowed}"))
                break
        else:
            if not any(prefix.startswith(a) for a in allowed):
                violations.append(Violation(
                    f, f"prefix not allowed in {sub}/ (expected one of {allowed})"
                ))

        # Sunset signal: every classic object should live under ZDSWF_LEGACY_*
        # We do not validate package membership from filename alone, but we warn
        # if the filename suggests it's directly under ZDSWF_ without LEGACY:
        # this is a heuristic, package validation is done at SAP system level.


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python validate_naming.py <handoff-bundle-root>")
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.exists() or not root.is_dir():
        print(f"ERROR: {root} is not a directory")
        return 2

    print(f"Validating ZDSWF classic naming convention in: {root}")

    for required in ("handoff", "objects", "tests", "scripts-run"):
        if not (root / required).exists():
            print(f"ERROR: required folder '{required}/' missing")
            return 2

    violations: list[Violation] = []
    for sub in ("prog", "fugr", "clas", "dtel", "doma", "tabl",
                "view", "enq", "form", "msag", "enhc", "badi"):
        validate_folder(root, sub, violations)
    validate_folder(root, "tests", violations)

    if violations:
        print(f"\n{len(violations)} violation(s):")
        for v in violations:
            print(v)
        return 1
    print("✓ All names compliant with ZDSWF classic conventions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
