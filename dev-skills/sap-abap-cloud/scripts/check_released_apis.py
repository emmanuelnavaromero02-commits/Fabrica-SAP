#!/usr/bin/env python3
"""
check_released_apis.py — heuristic check for cloud-released API usage

Scans generated ABAP source files for forbidden constructs and references
to non-released APIs. This is a heuristic: it does not connect to ADT and
cannot verify API release state at the SAP system level. It catches the
common mistakes and produces a log the agent can attach to the handoff.

Usage:
    python check_released_apis.py <handoff-bundle-root>

Exit codes:
    0 — no suspect patterns found
    1 — suspect patterns found (review required)
    2 — bundle structure invalid
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from dataclasses import dataclass

# Forbidden language constructs (regex, message)
FORBIDDEN_CONSTRUCTS: list[tuple[str, str]] = [
    (r"\bCALL\s+TRANSACTION\b",      "CALL TRANSACTION not allowed in cloud"),
    (r"\bCALL\s+SCREEN\b",            "CALL SCREEN not allowed in cloud"),
    (r"\bSUBMIT\s+\w+",               "SUBMIT not allowed in cloud"),
    (r"\bWRITE\s*[:/]",               "WRITE (classical list) not allowed in cloud"),
    (r"\bOPEN\s+DATASET\b",           "OPEN DATASET — use XCO_CP_FS_* released APIs"),
    (r"\bEXEC\s+SQL\b",               "EXEC SQL forbidden in cloud"),
    (r"\bCL_GUI_FRONTEND_SERVICES\b", "CL_GUI_FRONTEND_SERVICES not in cloud"),
    (r"/UI2/CL_JSON",                 "/UI2/CL_JSON — use xco_cp_json"),
    (r"\bMODULE\s+\w+\s+(INPUT|OUTPUT)\b", "PBO/PAI modules belong to Dynpros — not allowed"),
    (r"\bDEFINE\s+VIEW\s+(?!ENTITY)", "Use 'DEFINE VIEW ENTITY' — legacy 'DEFINE VIEW' forbidden"),
    (r"\bAUTHORITY-CHECK\b",          "AUTHORITY-CHECK in BIL is discouraged — use DCL + privileged mode"),
    (r"\bDATA\s*\(.*\)\s*=\s*0\s*\.", "Possible uninitialized DATA(..) = 0 — review"),
    (r"\bENQUEUE_\w+\s*\(",           "ENQUEUE FM in RAP — use BDEF 'lock master'"),
    (r"\bDEQUEUE_\w+\s*\(",           "DEQUEUE FM in RAP — use BDEF 'lock master'"),
]

# Forbidden direct table reads — heuristic on SELECT FROM <table>
# Allowed prefixes for SELECT targets:
ALLOWED_SELECT_PREFIXES = ("I_", "C_", "R_", "ZDSWF_", "TADIR")  # TADIR sometimes needed for tooling
FORBIDDEN_SELECT_PATTERNS: list[tuple[str, str]] = [
    (r"\bSELECT\b[^.]*\bFROM\s+([A-Z][A-Z0-9_]+)",
     "SELECT FROM <table> — must read via released I_* CDS view"),
]

# Forbidden function module calls (heuristic — anything not on allow-list is suspect)
# Allow list is conservative; the agent must verify in ADT.
ALLOWED_FM_PATTERNS = [
    r"^/UI2/.*",       # selectively allowed UI2 utils — to be replaced
    r"^XCO_.*",        # XCO library
    r"^CL_BALI_.*",    # logging
]

# Forbidden class prefixes
FORBIDDEN_CLASS_REFERENCES: list[tuple[str, str]] = [
    (r"\bCL_ABAP_LIST_LINE\w*\b",     "Classical list — not in cloud"),
    (r"\bCL_GUI_ALV_GRID\b",          "GUI ALV — not in cloud (use CDS analytical)"),
    (r"\bCL_SALV_TABLE\b",            "SALV — not in cloud"),
    (r"\bCL_DD_DOCUMENT\b",           "Classical doc rendering — not in cloud"),
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
    upper_lines = [(i + 1, line, line.upper()) for i, line in enumerate(text.splitlines())]

    # Forbidden constructs
    for pattern, msg in FORBIDDEN_CONSTRUCTS:
        for line_no, original, upper in upper_lines:
            if re.search(pattern, upper):
                findings.append(Finding(p, line_no, original, msg))

    # Forbidden class refs
    for pattern, msg in FORBIDDEN_CLASS_REFERENCES:
        for line_no, original, upper in upper_lines:
            if re.search(pattern, upper):
                findings.append(Finding(p, line_no, original, msg))

    # SELECT FROM <table> — must be released
    for line_no, original, upper in upper_lines:
        m = re.search(r"\bSELECT\b.*?\bFROM\s+([A-Z][A-Z0-9_]+)", upper)
        if m:
            target = m.group(1)
            if not any(target.startswith(pref) for pref in ALLOWED_SELECT_PREFIXES):
                findings.append(Finding(
                    p, line_no, original,
                    f"SELECT FROM '{target}' — must target released I_* view or ZDSWF_*"
                ))

    # CALL FUNCTION '<name>' — check allow list
    for line_no, original, upper in upper_lines:
        m = re.search(r"CALL\s+FUNCTION\s+'([A-Z0-9_/]+)'", upper)
        if m:
            name = m.group(1)
            if not any(re.match(allow, name) for allow in ALLOWED_FM_PATTERNS):
                findings.append(Finding(
                    p, line_no, original,
                    f"CALL FUNCTION '{name}' — verify cloud release state in ADT and document in released-apis-used.md"
                ))

    return findings


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python check_released_apis.py <handoff-bundle-root>")
        return 2
    root = Path(sys.argv[1]).resolve()
    if not root.exists():
        print(f"ERROR: {root} does not exist")
        return 2

    all_findings: list[Finding] = []
    targets = []
    for sub in ("objects/behavior-impl", "objects/cds", "objects/auth", "tests"):
        d = root / sub
        if d.exists():
            targets.extend(d.rglob("*.abap"))
            targets.extend(d.rglob("*.ddls"))
            targets.extend(d.rglob("*.dcls"))

    for f in targets:
        all_findings.extend(scan_file(f))

    if all_findings:
        print(f"{len(all_findings)} suspect pattern(s) found:")
        for f in all_findings:
            print(f)
        print("\nAll findings must be either eliminated or documented as exemptions in")
        print("handoff/decision-gates.md under 'Hard rule exemption' before handoff.")
        return 1
    print("✓ No forbidden constructs or unreleased API references detected (heuristic).")
    print("  Reminder: this is a heuristic. Final verification is in ADT API State.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
