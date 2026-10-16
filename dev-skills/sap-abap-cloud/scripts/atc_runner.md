# ATC runner — exact ADT execution

This is not a Python script. ATC runs inside ADT (Eclipse) against the actual SAP system. The agent does not run ATC itself; it documents the command and the human (or CI/CD with abapGit + abaplint pre-check) executes it.

## Manual run (the human executes; the agent records evidence)

1. In ADT, right-click the package `ZDSWF_<MODULE>`.
2. *Run* → *ABAP Test Cockpit With…*
3. Select the variant — preferred order:
   - `SAP_CLEAN_CORE_READINESS_*` (latest)
   - `SAP_CP_READINESS_*` (older fallback)
   - Custom factory variant `ZDSWF_ATC_VARIANT` if defined
4. Wait for completion.
5. *Export results* → save as `atc-report.txt` in `handoff/`.

## Required output format

The agent's `check_handoff.py` parses the ATC export and looks for:
- Line `Variant: <name>`
- Line `Priority 1: <n>`
- Line `Priority 2: <n>`
- Line `Status: PASSED`

If the export uses a different format, the human normalizes it before placing it in the handoff. ZDSWF policy: a wrapper script in CI/CD can normalize ADT XML → expected text format.

## Mass run (CI/CD integration)

For factory-scale operation, ATC is run from a central ATC system via `SATC` against all transports in the landscape. The factory's CI/CD invokes:

```
# pseudo — actual integration depends on landscape
abapgit pull --transport <TRKORR>
satc run --variant SAP_CLEAN_CORE_READINESS_LATEST --target <TRKORR>
satc export --format text --output handoff/atc-report.txt
```

## Exemption policy

Priority 1 and 2 findings: **no exemptions**. The code must be fixed.

Priority 3 findings: exemption permitted, recorded in `handoff/decision-gates.md` under "Hard rule exemption" with:
- Finding ID.
- Justification (one paragraph).
- Mitigating control.
- Reviewer who must approve at the developer gate.

## Common ATC findings in ZDSWF context

| Finding | Meaning | Typical fix |
|---|---|---|
| Use of non-released API | the code uses a class/FM not released for cloud | replace with released equivalent (XCO_* / I_*) |
| Direct table access | `SELECT FROM <std_table>` | use the released `I_*` view |
| Modification of SAP standard | code touches an SAP-delivered object | back out, use BAdI or side-by-side |
| Missing DCL | view exposed without access control | add `.dcls` |
| Strict mode insufficient | BDEF not `strict ( 2 )` | upgrade |
| Obsolete syntax | classic ABAP form | rewrite in modern syntax |
| Missing semantics | amount field without currency code | add `@Semantics.amount.currencyCode` |
