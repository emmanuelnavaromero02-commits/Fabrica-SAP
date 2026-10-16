# Handoff template — classic

Same structure as cloud, with classic-specific files.

## Folder layout

```
handoff/
  decision-gates.md         # E1..E6 + G1..G10 answers, signed
  release-notes.md
  diff-summary.md
  atc-report.txt
  test-results.txt
  enhancements-used.md      # every BAdI / spot / append touched
  handoff-checklist.md
objects/
  prog/                     # reports, module pools
  fugr/                     # function groups
  clas/                     # OO classes (preferred)
  dtel/                     # data elements
  doma/                     # domains
  tabl/                     # tables, appends
  view/                     # views, search helps
  enq/                      # lock objects
  form/                     # Smartforms / Adobe Forms metadata
  msag/                     # message classes
  enhc/                     # enhancement implementations
  badi/                     # BAdI implementations
tests/
  unit/
  integration/
scripts-run/
  validate_naming.log
  check_classic_compliance.log
  check_handoff.log
```

## decision-gates.md — classic-specific

Includes:
- E1..E6 escalation answers (with classic-justified or escalated decision).
- G1..G10 design answers.
- Sunset plan with target date and migration ticket.
- Hard rule exemptions (if any) with justification.

Must end with:
```
Agent: claude
Skill: sap-abap-classic
Date: <ISO 8601>
Escalation result: classic-justified | escalated-to-cloud
Status: gates-cleared | blocked
Sunset target: <date>
Migration ticket: <id>
```

## release-notes.md

```markdown
# Release notes — task <id>

## What was built
<one paragraph>

## Why classic (not cloud)
<reference E1..E6 answers>

## Scope
- Reports: <list>
- Module pools: <list>
- Classes: <list>
- Tables / appends: <list>
- BAdI / enhancement implementations: <list>
- Forms: <list>

## What changed in the system
<bulleted list>

## Test results
- Unit tests: <N>, all green
- Coverage: <%> on controller classes
- ATC: 0 P1/P2 findings (variant: <name>)

## Sunset plan
<from E6>

## Known limitations
<list>

## Rollback plan
<how>
```

## diff-summary.md

```markdown
# Diff summary — task <id>

## Created
- ZDSWF_LEGACY_<MODULE> package
- SAPMZDSWF_<MODULE>_<NAME> (module pool)
- ZCL_ZDSWF_<NAME>_CTRL (controller class)
- ZDSWF_<NAME> (Z-table)
- ZDSWF_<NAME>_APP (append structure on standard table) — if applicable
- EZDSWF_<NAME> (lock object) — if applicable
- ZDSWF_<NAME> (message class)

## Modified
- SAP standard objects: NONE (forbidden)
- Existing ZDSWF_LEGACY_* objects: <list with brief description>

## Deleted
none

## Transport package
ZDSWF_LEGACY_<MODULE>

## Transport layer (from handoff config)
<placeholder>
```

## atc-report.txt

```
ATC Result Run
Variant: <classic ATC variant, e.g. TRANSPORT or SLIN>
Date: <timestamp>
System: <SID>
User: <factory account>
Package: ZDSWF_LEGACY_<MODULE>

Findings (in modified scope):
  Priority 1: 0
  Priority 2: 0
  Priority 3: 0

Findings (pre-existing, outside modified scope):
  <reported separately; do not block this handoff>

Status: PASSED
```

## test-results.txt

```
ABAP Unit Test Result
Date: <timestamp>
Package: ZDSWF_LEGACY_<MODULE>

Total tests: <N>
Passed: <N>
Failed: 0
Errors: 0

Coverage (lines, on controller classes only):
  ZCL_ZDSWF_<NAME>_CTRL: <%>
  ZCL_ZDSWF_<DOC>_BAPI: <%>
  Total controllers in scope: <%>

Threshold: 70% (PASSED | FAILED)
```

## enhancements-used.md

```markdown
# Enhancements used — task <id>

| Type | SAP object | Z-implementation | Filter | Justification |
|---|---|---|---|---|
| New BAdI | <BADI_DEF> | ZCL_ZDSWF_<...>_IMPL | <filter> | <one sentence> |
| Append | <SAP table> | ZDSWF_<table>_APP | n/a | added ZZ-fields for <purpose> |
| Spot | <SPOT_ID> | ZDSWF_<...>_ENHC | n/a | <reason> |

User/customer exits used: <none, or list with maintenance ticket reference>
```

## handoff-checklist.md

```markdown
# Handoff checklist — task <id>

Gate type: [code-review | artifact-review]
Reviewer: <developer handle>

## Agent self-checks

- [ ] E1..E6 escalation gates answered.
- [ ] Classic justified (not escalatable to cloud).
- [ ] Sunset plan with target date.
- [ ] Naming compliant (validate_naming.py exited 0).
- [ ] Classic compliance check passed (check_classic_compliance.py exited 0).
- [ ] ATC zero P1/P2 in scope.
- [ ] ABAP Unit on every controller class.
- [ ] Coverage ≥ 70% on controller classes.
- [ ] No SAP standard object modified.
- [ ] Modern syntax used throughout.
- [ ] BAPI used (not BDC) wherever possible.
- [ ] All BAPI calls followed by `BAPI_TRANSACTION_COMMIT WAIT = X`.
- [ ] All enqueue calls have matching dequeue in every exit path.
- [ ] No `WRITE` for production output.
- [ ] No literal RFC destinations.
- [ ] No new SAPscript.
- [ ] check_handoff.py exited 0.

## Hard rule self-audit (`hard-rules.md`)
- [ ] Section 1 (SAP standard): compliant
- [ ] Section 2 (Language constructs): compliant
- [ ] Section 3 (Output): compliant
- [ ] Section 4 (Integration): compliant
- [ ] Section 5 (Dynpro / module pool): compliant or n/a
- [ ] Section 6 (Locking): compliant
- [ ] Section 7 (Authorization): compliant
- [ ] Section 8 (Unicode): compliant
- [ ] Section 9 (Background / parallel): compliant or n/a
- [ ] Section 10 (Testing): compliant
- [ ] Section 11 (ATC): compliant
- [ ] Section 12 (Sunset signal): compliant

## Agent sign-off
Agent: claude
Date: <ISO 8601>
Status: ready-for-developer-gate

## Developer sign-off
Reviewer:
Date:
Decision: [APPROVE | REJECT | REQUEST CHANGES]
Notes:
```
