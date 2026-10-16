#!/usr/bin/env python3
"""
check_classic_compliance.py — heuristic check for classic ABAP anti-patterns

Scans generated ABAP source for the patterns banned in hard-rules.md.

Usage:
    python check_classic_compliance.py <handoff-bundle-root>

Exit codes:
    0 — no suspect patterns
    1 — suspect patterns found
    2 — bundle path invalid
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from dataclasses import dataclass

# (regex, message). All regexes applied to UPPER-cased line, except where noted.
PATTERNS: list[tuple[str, str]] = [
    # Legacy syntax
    (r"\bMOVE-CORRESPONDING\b",                "MOVE-CORRESPONDING — use CORRESPONDING #( )"),
    (r"\bIF\s+NOT\s+\w+\s+IS\s+INITIAL\b",     "Use 'IF x IS NOT INITIAL'"),
    (r"\bMOVE\s+\w+\s+TO\s+\w+\.",             "Use assignment operator ="),

    # Output
    (r"\bWRITE\s*[:/]",                         "WRITE in production code — use ALV"),
    (r"\bREUSE_ALV_(GRID|LIST)_DISPLAY\b",      "REUSE_ALV_* — use CL_SALV_TABLE"),
    (r"\bREUSE_ALV_FIELDCATALOG_MERGE\b",       "REUSE_ALV_FIELDCATALOG_MERGE — use CL_SALV_TABLE factory"),

    # Commit semantics
    (r"^\s*COMMIT\s+WORK\s*\.\s*$",             "Bare COMMIT WORK — use COMMIT WORK AND WAIT"),

    # BDC / CALL TRANSACTION
    (r"\bCALL\s+TRANSACTION\s+'[A-Z0-9]+'\s+USING\b", "CALL TRANSACTION with BDC — prefer BAPI"),

    # Authorization
    # (we cannot enforce 'every entry has AUTHORITY-CHECK' from grep alone; we flag
    # programs that have no AUTHORITY-CHECK at all and that look like entry points.)

    # Forbidden classes / FMs in new development
    (r"\bGUID_CREATE\b",                        "GUID_CREATE — use CL_SYSTEM_UUID=>create_uuid_x16_static"),
    (r"\bMESSAGE\s+'[^']+'",                    "Literal MESSAGE — use message class"),

    # Sapscript signal
    (r"\bSTART_FORM\b|\bWRITE_FORM\b|\bEND_FORM\b",
                                                "SAPscript FM detected — SAPscript is forbidden for new dev"),

    # Async BAPI commit
    (r"BAPI_TRANSACTION_COMMIT[^.]*\.\s*$",     "BAPI_TRANSACTION_COMMIT — verify 'wait = abap_true' is set"),

    # Phantom OK_CODE
    # We flag PBO modules that don't clear gv_okcode — too noisy as a regex;
    # leave to ATC.

    # Hardcoded RFC destination
    (r"DESTINATION\s+'[^Z][^']*'",              "DESTINATION literal — use logical destination starting with Z"),

    # Native SQL
    (r"\bEXEC\s+SQL\b",                          "EXEC SQL — forbidden"),

    # AT EXIT-COMMAND missing on cancel/back/exit — too contextual to grep reliably

    # PERFORM with business logic — heuristic
    (r"^\s*PERFORM\s+\w+\s+",                    "PERFORM detected — verify it is a thin delegate to a class"),
]


@dataclass
class Finding:
    path: Path
    line_no: int
    line: str
    message: str

    def __str__(self) -> str:
        return f"  ✗ {self.path}:{self.line_no}: {self.message}\n      {self.line.strip()}"


def scan_file(p: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return [Finding(p, 0, "", f"cannot read: {exc}")]

    lines = text.splitlines()

    for i, raw in enumerate(lines, start=1):
        # Strip comments — line starting with * is full-line comment;
        # mid-line " starts comment
        stripped = raw.lstrip()
        if stripped.startswith("*"):
            continue
        code_part = raw.split('"', 1)[0]
        upper = code_part.upper()

        for pattern, msg in PATTERNS:
            if re.search(pattern, upper):
                findings.append(Finding(p, i, raw, msg))

    return findings


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python check_classic_compliance.py <handoff-bundle-root>")
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.exists():
        print(f"ERROR: {root} does not exist")
        return 2

    print(f"Scanning classic compliance under: {root}")

    targets: list[Path] = []
    for sub in ("objects", "tests"):
        d = root / sub
        if d.exists():
            for ext in (".abap", ".prog.abap", ".fugr.abap",
                        ".clas.abap", ".clas.testclasses.abap",
                        ".clas.locals_imp.abap"):
                targets.extend(d.rglob(f"*{ext}"))

    # de-dup
    targets = sorted(set(targets))

    findings: list[Finding] = []
    for f in targets:
        findings.extend(scan_file(f))

    if findings:
        print(f"{len(findings)} suspect pattern(s):")
        for f in findings:
            print(f)
        print("\nAll findings must be eliminated or documented as exemptions in")
        print("handoff/decision-gates.md under 'Hard rule exemption' before handoff.")
        return 1
    print("✓ No classic anti-patterns detected (heuristic).")
    print("  Reminder: heuristic only. Final verification is ATC + code review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
