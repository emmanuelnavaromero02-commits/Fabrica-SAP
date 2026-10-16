#!/usr/bin/env python3
"""
check_handoff.py — final gate before declaring task done

Verifies that the handoff bundle is complete and that all internal signals
say "ready for developer". Exit code 0 means the agent may declare done.
Any non-zero exit means stop and fix.

Usage:
    python check_handoff.py <handoff-bundle-root>

Exit codes:
    0 — bundle complete, agent may declare done
    1 — bundle incomplete or signals inconsistent
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
    "released-apis-used.md",
    "handoff-checklist.md",
]

REQUIRED_TOP_DIRS = ["handoff", "objects", "tests", "scripts-run"]


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

    print(f"Checking handoff bundle at: {root}\n")
    errors = 0

    # 1. Required top-level directories
    for d in REQUIRED_TOP_DIRS:
        if not (root / d).exists():
            fail(f"missing required directory: {d}/")
            errors += 1
        else:
            ok(f"directory present: {d}/")

    if errors:
        print("\nBundle structure incomplete. Stopping further checks.")
        return 1

    # 2. Required handoff files
    handoff = root / "handoff"
    for f in REQUIRED_HANDOFF_FILES:
        fp = handoff / f
        if not fp.exists():
            fail(f"missing handoff file: handoff/{f}")
            errors += 1
        elif fp.stat().st_size == 0:
            fail(f"handoff file is empty: handoff/{f}")
            errors += 1
        else:
            ok(f"handoff file present and non-empty: {f}")

    # 3. decision-gates.md must end with cleared status
    dg = handoff / "decision-gates.md"
    if dg.exists():
        text = dg.read_text(encoding="utf-8")
        if not re.search(r"Status:\s*gates-cleared", text):
            fail("decision-gates.md does not declare 'Status: gates-cleared'")
            errors += 1
        else:
            ok("decision-gates.md signed and cleared")

    # 4. atc-report.txt must declare PASSED with zero P1/P2
    atc = handoff / "atc-report.txt"
    if atc.exists():
        text = atc.read_text(encoding="utf-8")
        if "Status: PASSED" not in text and "PASSED" not in text:
            fail("atc-report.txt does not declare PASSED")
            errors += 1
        m1 = re.search(r"Priority\s+1:\s*(\d+)", text)
        m2 = re.search(r"Priority\s+2:\s*(\d+)", text)
        if m1 and int(m1.group(1)) > 0:
            fail(f"ATC priority-1 findings: {m1.group(1)} (must be 0)")
            errors += 1
        if m2 and int(m2.group(1)) > 0:
            fail(f"ATC priority-2 findings: {m2.group(1)} (must be 0)")
            errors += 1
        if (not m1 or int(m1.group(1)) == 0) and (not m2 or int(m2.group(1)) == 0):
            ok("ATC report clean (no P1/P2 findings)")

    # 5. test-results.txt must declare PASSED and meet coverage
    tr = handoff / "test-results.txt"
    if tr.exists():
        text = tr.read_text(encoding="utf-8")
        m_fail = re.search(r"Failed:\s*(\d+)", text)
        m_err = re.search(r"Errors:\s*(\d+)", text)
        if m_fail and int(m_fail.group(1)) > 0:
            fail(f"ABAP Unit failed tests: {m_fail.group(1)}")
            errors += 1
        if m_err and int(m_err.group(1)) > 0:
            fail(f"ABAP Unit errors: {m_err.group(1)}")
            errors += 1
        # Coverage check: look for "Total ZDSWF_<MODULE>: <%>"
        m_cov = re.search(r"Total\s+ZDSWF[^:]*:\s*([\d.]+)\s*%", text)
        if m_cov:
            cov = float(m_cov.group(1))
            if cov < 80.0:
                fail(f"coverage {cov}% below threshold 80%")
                errors += 1
            else:
                ok(f"coverage {cov}% meets threshold")
        else:
            warn("no total coverage line found in test-results.txt")

    # 6. handoff-checklist.md must have no unchecked boxes in the agent self-check section
    cl = handoff / "handoff-checklist.md"
    if cl.exists():
        text = cl.read_text(encoding="utf-8")
        # Only inspect the "agent self-checks" portion, stop before "## Developer sign-off"
        agent_section = text.split("## Developer sign-off")[0]
        unchecked = re.findall(r"^\s*-\s*\[\s*\]", agent_section, re.MULTILINE)
        if unchecked:
            fail(f"{len(unchecked)} unchecked box(es) in agent self-check section")
            errors += 1
        else:
            ok("handoff-checklist.md: all agent self-check boxes ticked")

    # 7. scripts-run logs present and successful
    sr = root / "scripts-run"
    for log_name in ("validate_naming.log", "check_released_apis.log"):
        lf = sr / log_name
        if not lf.exists():
            fail(f"missing scripts-run/{log_name}")
            errors += 1
        else:
            text = lf.read_text(encoding="utf-8")
            if "✗" in text or "ERROR" in text or "violation" in text.lower():
                fail(f"{log_name} indicates errors")
                errors += 1
            else:
                ok(f"{log_name} clean")

    # 8. No untracked SAP standard object modifications declared
    diff = handoff / "diff-summary.md"
    if diff.exists():
        text = diff.read_text(encoding="utf-8")
        if re.search(r"##\s+Modified[^\n]*\n[^\n#]*[^\n]", text):
            # If "Modified" section has any content other than "none"
            modified_section = text.split("## Modified")[1].split("##")[0]
            if modified_section.strip().lower() not in ("none", "", "(none)"):
                fail("diff-summary.md declares modifications to objects (SAP standard mods forbidden — verify all are ZDSWF_*)")
                # Heuristic only — agent must verify; flagged as warn-level error
                errors += 1
            else:
                ok("diff-summary.md: no modifications declared")

    print()
    if errors:
        print(f"✗ Handoff blocked: {errors} issue(s). Agent must fix and re-run.")
        return 1

    print("✓ Handoff bundle complete and consistent. Agent may declare task done.")
    print("  The bundle is now READ-ONLY pending developer gate review.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
