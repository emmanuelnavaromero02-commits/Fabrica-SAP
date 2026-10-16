# Handoff template — what the developer receives

The agent does not transport. The agent produces this bundle and stops. The developer reviews, signs, and transports.

## Folder layout (exact)

```
handoff/
  decision-gates.md         # G1..G15 answers, signed
  release-notes.md          # business-facing summary
  diff-summary.md           # objects created/modified/deleted
  atc-report.txt            # raw ATC output
  test-results.txt          # ABAP Unit + coverage
  released-apis-used.md     # API verification log
  handoff-checklist.md      # signed checklist
objects/
  cds/
  behavior/
  behavior-impl/
  service/
  ui/                       # only if freestyle
  auth/
  data/
tests/
  unit/
  integration/
scripts-run/
  validate_naming.log
  check_released_apis.log
  check_handoff.log
```

## decision-gates.md

Filled per `decision-gates.md` reference. Includes:
- All gate answers.
- Tier, scenario, draft, UI, API gap.
- Authorization, locking, numbering.
- List of validations / determinations / actions.
- Gate type (code-review vs artifact-review).
- Sign-off line.

## release-notes.md

```markdown
# Release notes — task <id>

## What was built
<one paragraph in business language>

## Why
<business outcome / requirement reference>

## Scope
- Custom BO: ZDSWF_R_<Entity>
- Service: ZDSWF_SD_<Name>
- Fiori app: <App name> (LROP)
- Validations: <count>, Determinations: <count>, Actions: <count>

## What changed in the system
<bulleted list of objects created, none modified — modifications forbidden>

## Test results
- Unit tests: <N> tests, all green
- Coverage: <%> on ZDSWF_<MODULE>
- ATC: 0 findings (variant: <name>)

## Known limitations
<honest list, if any>

## Rollback plan
<how to undo if needed>
```

## diff-summary.md

```markdown
# Diff summary — task <id>

## Created
- ZDSWF_<entity> (table)
- ZDSWF_R_<Entity> (root view + bdef)
- ZDSWF_R_<Entity>Item (child view + bdef)
- ZDSWF_BP_<Entity> (BIL class)
- ZDSWF_C_<Entity> (projection view + bdef + ddlx)
- ZDSWF_SD_<Name> (service definition)
- ZDSWF_SB_<Name> (service binding)
- ZDSWF_I_<Entity> (DCL)
- ZDSWF_TC_<Entity> (test class)

## Modified
none (modifications to SAP standard are forbidden)

## Deleted
none

## Transport package
ZDSWF_<MODULE>

## Transport layer (from handoff config)
<placeholder>
```

## atc-report.txt

Raw ATC output. The format depends on ADT export; what matters:

- Variant name printed at top.
- Zero priority 1, zero priority 2 findings.
- Priority 3 findings, if any, each have a corresponding exemption in `decision-gates.md`.

Example expected structure (paraphrased — actual ADT output may differ slightly):

```
ATC Result Run
Variant: SAP_CP_READINESS_<release>
Date: <timestamp>
System: <SID>
User: <factory account>
Package: ZDSWF_<MODULE>

Findings:
  Priority 1: 0
  Priority 2: 0
  Priority 3: 0

Status: PASSED
```

## test-results.txt

```
ABAP Unit Test Result
Date: <timestamp>
Package: ZDSWF_<MODULE>

Total tests: <N>
Passed: <N>
Failed: 0
Errors: 0
Skipped: 0

Coverage (lines):
  ZDSWF_BP_<entity>: <%>
  <other classes>: <%>
  Total ZDSWF_<MODULE>: <%>

Threshold: 80% (PASSED | FAILED)
```

## released-apis-used.md

Per the `released-apis.md` template.

## handoff-checklist.md

```markdown
# Handoff checklist — task <id>

Gate type: [code-review | artifact-review]
Reviewer: <developer handle>

## Agent self-checks (all must be ✅)

- [ ] All decision gates answered.
- [ ] Naming convention compliant (validate_naming.py exited 0).
- [ ] Released APIs verified (check_released_apis.py exited 0).
- [ ] ATC zero findings priority 1 and 2.
- [ ] Priority 3 findings (if any) exempted in decision-gates.md.
- [ ] ABAP Unit tests for every BIL class.
- [ ] CDS tests for every view with logic.
- [ ] Coverage ≥ 80% on ZDSWF_<MODULE>.
- [ ] No SAP standard object modified.
- [ ] Strict mode 2 minimum on every BDEF.
- [ ] With draft on transactional BDEFs.
- [ ] Currency/quantity semantics on all amount/qty fields.
- [ ] DCL access control on every published CDS.
- [ ] No literal strings — all i18n.
- [ ] check_handoff.py exited 0.

## Hard rule self-audit (`hard-rules.md`)
- [ ] Section 1 (Released API surface): compliant / exempted (see G#)
- [ ] Section 2 (Forbidden language constructs): compliant
- [ ] Section 3 (Forbidden patterns): compliant
- [ ] Section 4 (RAP-specific): compliant
- [ ] Section 5 (CDS-specific): compliant
- [ ] Section 6 (Fiori / UI): compliant
- [ ] Section 7 (Mobile): n/a or compliant
- [ ] Section 8 (Testing): compliant
- [ ] Section 9 (Security): compliant
- [ ] Section 10 (ATC / Clean Core): compliant

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

## What blocks handoff

`check_handoff.py` is the final gate. It blocks if:
1. Any file in the expected layout is missing.
2. `decision-gates.md` does not end with `Status: gates-cleared`.
3. `validate_naming.log` or `check_released_apis.log` show errors.
4. `atc-report.txt` shows priority 1 or 2 findings.
5. `test-results.txt` shows failed tests or coverage below threshold.
6. `handoff-checklist.md` has any unchecked box.

The agent fixes and re-runs. It does not declare done until `check_handoff.py` exits 0.
