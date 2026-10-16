#!/usr/bin/env python3
"""
validate_naming.py — ZDSWF naming convention validator (sap-abap-cloud)

Walks the `objects/` and `tests/` folders of a handoff bundle and verifies that
every file and every declared ABAP object follows ZDSWF naming conventions.

Usage:
    python validate_naming.py <handoff-bundle-root>

Exit codes:
    0 — all checks passed
    1 — one or more violations
    2 — bundle structure invalid (cannot validate)

This script is intentionally strict. It is the first gate before
check_released_apis.py and check_handoff.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

ROOT_PACKAGE = "ZDSWF"
ALLOWED_PREFIXES = {
    "cds":            ("ZDSWF_R_", "ZDSWF_I_", "ZDSWF_C_", "ZDSWF_E_", "ZDSWF_TF_"),
    "behavior":       ("ZDSWF_R_", "ZDSWF_C_"),
    "behavior-impl":  ("ZDSWF_BP_",),
    "service":        ("ZDSWF_SD_", "ZDSWF_SB_"),
    "auth":           ("ZDSWF_I_", "ZDSWF_C_", "ZDSWF_R_"),
    "data":           ("ZDSWF_", "ZDSWF_D_"),
    "tests":          ("ZDSWF_TC_", "ZDSWF_TI_", "LTC_", "LTI_"),
}

EXTENSION_BY_FOLDER = {
    "cds":           {".ddls", ".ddlx"},
    "behavior":      {".bdef"},
    "behavior-impl": {".clas.abap", ".clas.locals_imp.abap", ".clas.testclasses.abap"},
    "service":       {".srvd", ".srvb"},
    "auth":          {".dcls"},
    "data":          {".tabl"},
    "tests":         {".clas.abap"},
    "ui":            {".xml", ".js", ".json", ".properties", ".less", ".css"},
}

# Forbidden object prefixes (legacy / non-ZDSWF customer)
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
    # Strip multi-dot suffixes like ".clas.abap"
    name = p.name
    for suffix in (".clas.testclasses.abap", ".clas.locals_imp.abap", ".clas.abap"):
        if name.endswith(suffix):
            return name[: -len(suffix)].upper()
    return p.stem.upper()


def file_ext(p: Path) -> str:
    name = p.name
    for suffix in (".clas.testclasses.abap", ".clas.locals_imp.abap", ".clas.abap"):
        if name.endswith(suffix):
            return suffix
    return p.suffix


def validate_folder(root: Path, sub: str, violations: list[Violation]) -> None:
    folder = root / "objects" / sub if sub != "tests" else root / "tests"
    if not folder.exists():
        return  # absence is allowed; presence is validated

    allowed_prefixes = ALLOWED_PREFIXES.get(sub, (f"{ROOT_PACKAGE}_",))
    allowed_exts = EXTENSION_BY_FOLDER.get(sub, set())

    for f in iter_files(folder):
        ext = file_ext(f)
        if allowed_exts and ext not in allowed_exts:
            violations.append(Violation(f, f"unexpected extension '{ext}' in {sub}/ (expected one of {sorted(allowed_exts)})"))
            continue

        prefix = file_prefix(f)

        # Forbidden customer namespaces
        for fp in FORBIDDEN_PREFIXES:
            if prefix.startswith(fp) and not prefix.startswith("ZDSWF_"):
                violations.append(Violation(f, f"forbidden prefix '{fp}' — must be 'ZDSWF_*'"))
                break
        else:
            if not any(prefix.startswith(p) for p in allowed_prefixes):
                violations.append(Violation(
                    f,
                    f"prefix '{prefix.split('_', 2)[:2]}' not in allowed for {sub}/ (allowed: {allowed_prefixes})"
                ))


def validate_handoff_root(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    if not root.exists() or not root.is_dir():
        print(f"ERROR: {root} is not a directory")
        sys.exit(2)

    for required in ("handoff", "objects", "tests", "scripts-run"):
        if not (root / required).exists():
            print(f"ERROR: required folder '{required}/' missing in bundle")
            sys.exit(2)

    for sub in ("cds", "behavior", "behavior-impl", "service", "auth", "data"):
        validate_folder(root, sub, violations)
    validate_folder(root, "tests", violations)

    # UI folder is optional (only if freestyle UI5 was justified by G4)
    ui = root / "objects" / "ui"
    if ui.exists():
        # We only check for absence of forbidden patterns in UI; full UI validation
        # is out of scope for this script.
        for f in iter_files(ui):
            content = f.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"\bconsole\.log\b", content):
                violations.append(Violation(f, "console.log found in UI code — forbidden"))
            if "/UI2/CL_JSON" in content.upper():
                violations.append(Violation(f, "legacy /UI2/CL_JSON reference — use xco_cp_json"))

    return violations


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python validate_naming.py <handoff-bundle-root>")
        return 2
    root = Path(sys.argv[1]).resolve()
    print(f"Validating ZDSWF naming convention in: {root}")
    violations = validate_handoff_root(root)
    if violations:
        print(f"\n{len(violations)} violation(s):")
        for v in violations:
            print(v)
        return 1
    print("✓ All names compliant with ZDSWF conventions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
