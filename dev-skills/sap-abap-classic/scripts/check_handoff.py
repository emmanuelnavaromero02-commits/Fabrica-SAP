#!/usr/bin/env python3
"""
check_handoff.py — final gate for classic handoff bundle

Usage:
    python check_handoff.py <handoff-bundle-root>

Exit codes:
    0 — bundle complete
    1 — incomplete or signals inconsistent
    2 — bundle path invalid
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_HANDOFF_FILES = [
    "decision-gates.md",
    "release-notes.md",
    "diff-summary.md",
    "atc-report.txt",
    "test-results.txt",
    "enhancements-used.md",
    "handoff-checklist.md",
]

REQUIRED_TOP_DIRS = ["handoff", "objects", "tests", "scripts-run"]

COVERAGE_THRESHOLD = 70.0   # lower than cloud's 80 because UI shells are not testable


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


def warn(msg: str) -> None:
    print(f"  ! {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python check_handoff.py <handoff-bundle-root>")
        return 2

    root = Path(sys.argv[1]).resolve()
    if not root.exists() or not root.is_dir():
        print(f"ERROR: {root} is not a directory")
        return 2

    print(f"Checking classic handoff bundle at: {root}\n")
    errors = 0

    for d in REQUIRED_TOP_DIRS:
        if not (root / d).exists():
            fail(f"missing required directory: {d}/")
            errors += 1
        else:
            ok(f"directory present: {d}/")

    if errors:
        print("\nBundle structure incomplete. Stopping.")
        return 1

    handoff = root / "handoff"
    for f in REQUIRED_HANDOFF_FILES:
        fp = handoff / f
        if not fp.exists():
            fail(f"missing: handoff/{f}")
            errors += 1
        elif fp.stat().st_size == 0:
            fail(f"empty: handoff/{f}")
            errors += 1
        else:
            ok(f"present: {f}")

    # decision-gates.md — must be cleared and have escalation result
    dg = handoff / "decision-gates.md"
    if dg.exists():
        text = dg.read_text(encoding="utf-8")
        if not re.search(r"Status:\s*gates-cleared", text):
            fail("decision-gates.md does not declare 'Status: gates-cleared'")
            errors += 1
        else:
            ok("decision-gates.md cleared")
        if not re.search(r"Escalation result:\s*classic-justified", text):
            fail("decision-gates.md does not declare 'Escalation result: classic-justified'")
            errors += 1
        else:
            ok("classic justified vs cloud")
        if not re.search(r"Sunset target:\s*\S+", text):
            fail("decision-gates.md missing 'Sunset target'")
            errors += 1
        else:
            ok("sunset target declared")

    # ATC
    atc = handoff / "atc-report.txt"
    if atc.exists():
        text = atc.read_text(encoding="utf-8")
        if "Status: PASSED" not in text and "PASSED" not in text:
            fail("atc-report.txt not PASSED")
            errors += 1
        m1 = re.search(r"Priority\s+1:\s*(\d+)", text)
        m2 = re.search(r"Priority\s+2:\s*(\d+)", text)
        if m1 and int(m1.group(1)) > 0:
            fail(f"ATC P1 findings: {m1.group(1)}")
            errors += 1
        if m2 and int(m2.group(1)) > 0:
            fail(f"ATC P2 findings: {m2.group(1)}")
            errors += 1
        if (not m1 or int(m1.group(1)) == 0) and (not m2 or int(m2.group(1)) == 0):
            ok("ATC clean")

    # Tests
    tr = handoff / "test-results.txt"
    if tr.exists():
        text = tr.read_text(encoding="utf-8")
        m_fail = re.search(r"Failed:\s*(\d+)", text)
        m_err = re.search(r"Errors:\s*(\d+)", text)
        if m_fail and int(m_fail.group(1)) > 0:
            fail(f"failed tests: {m_fail.group(1)}")
            errors += 1
        if m_err and int(m_err.group(1)) > 0:
            fail(f"test errors: {m_err.group(1)}")
            errors += 1
        m_cov = re.search(r"Total controllers in scope:\s*([\d.]+)\s*%", text)
        if m_cov:
            cov = float(m_cov.group(1))
            if cov < COVERAGE_THRESHOLD:
                fail(f"coverage {cov}% below threshold {COVERAGE_THRESHOLD}%")
                errors += 1
            else:
                ok(f"coverage {cov}% meets threshold")
        else:
            warn("no controller coverage total found in test-results.txt")

    # Checklist
    cl = handoff / "handoff-checklist.md"
    if cl.exists():
        text = cl.read_text(encoding="utf-8")
        agent_section = text.split("## Developer sign-off")[0]
        unchecked = re.findall(r"^\s*-\s*\[\s*\]", agent_section, re.MULTILINE)
        if unchecked:
            fail(f"{len(unchecked)} unchecked box(es) in agent section")
            errors += 1
        else:
            ok("all agent self-checks ticked")

    # Scripts-run logs
    sr = root / "scripts-run"
    for log in ("validate_naming.log", "check_classic_compliance.log"):
        lf = sr / log
        if not lf.exists():
            fail(f"missing scripts-run/{log}")
            errors += 1
        else:
            t = lf.read_text(encoding="utf-8")
            if "✗" in t or "ERROR" in t or "violation" in t.lower():
                fail(f"{log} indicates errors")
                errors += 1
            else:
                ok(f"{log} clean")

    # Modified section in diff
    diff = handoff / "diff-summary.md"
    if diff.exists():
        text = diff.read_text(encoding="utf-8")
        # extract Modified section
        m = re.search(r"##\s+Modified(.*?)(##|\Z)", text, re.DOTALL)
        if m:
            body = m.group(1).strip()
            # Look for SAP standard objects (no ZDSWF_ prefix) listed
            for line in body.splitlines():
                line_s = line.strip(" -*")
                if not line_s or line_s.lower().startswith("sap standard objects: none"):
                    continue
                # If line lists an SAP object (no ZDSWF_), flag
                if "ZDSWF_" not in line_s.upper() and line_s and not line_s.startswith("-"):
                    # Be lenient — only flag clear SAP object names
                    if re.match(r"^[A-Z][A-Z0-9_/]+$", line_s):
                        fail(f"diff-summary.md lists modification of non-ZDSWF object: {line_s}")
                        errors += 1

    print()
    if errors:
        print(f"✗ Handoff blocked: {errors} issue(s).")
        return 1
    print("✓ Handoff bundle complete. Agent may declare task done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
