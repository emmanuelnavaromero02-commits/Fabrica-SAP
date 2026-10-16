---
name: sap-abap-classic
description: Build or modify classic ABAP for the ZDSWF dark software factory — the legacy pre-Clean Core model. Use when work is unavoidably anchored in classic constructs such as SAP GUI Dynpros, module pools, classical and interactive reports, ALV via CL_GUI_ALV_GRID, SAPscript or Smartforms, BDC, classic RFC and BAPI integration, IDocs, classic enhancement framework (user exits, customer exits, BAdIs of any vintage, enhancement spots, append structures), or any object in SE80, SE38, or SE11. Trigger also for ECC, S/4HANA on-prem modifications outside Embedded Steampunk, and legacy Z-report maintenance. Before generating any code, this skill INTERROGATES whether the requirement could be delivered through ABAP Cloud and Clean Core — if yes, the skill blocks and escalates to sap-abap-cloud. Use only when classic is required and justified in writing in the decision gates. Do NOT use for RAP, ABAP Cloud, CDS view entities, Fiori Elements, BTP — that is sap-abap-cloud territory.
---

# SAP ABAP Classic — ZDSWF contract

This skill is a **contract** for the ZDSWF dark software factory. It treats classic ABAP as a constrained necessity, not a default. The reflex of this skill is to ask "could this be cloud instead?" before generating a single line of Dynpro or `WRITE`.

## Posture — classic is a last resort

ZDSWF strategic posture is Clean Core. Classic ABAP is built only when:

1. The system is ECC or S/4HANA on-prem *outside* Embedded Steampunk's scope.
2. The customer explicitly requires a classic deliverable (SAP GUI transaction, classical report) and there is no Fiori equivalent in scope.
3. An existing classic Z-object must be maintained (bug fix, regulatory change) and migration is out of scope.

Anything else: the skill blocks and escalates to `sap-abap-cloud`.

## Mandatory escalation check — BEFORE code

Before writing any code, the agent answers these and records the answers in `handoff/decision-gates.md`:

- **E1.** Is the target system ECC, on-prem S/4HANA without Embedded Steampunk, or another classic-only context? Justify.
- **E2.** Could this requirement be delivered as a Fiori Elements app + RAP business object? If yes → **STOP** and escalate.
- **E3.** Could this requirement be delivered as a custom CDS analytical query on the customer's existing analytics stack? If yes → STOP and escalate.
- **E4.** Could this be a side-by-side BTP app calling the classic system via released communication scenarios? If yes → STOP and escalate.
- **E5.** Is this maintenance of an existing classic Z-object that cannot be migrated within scope? If yes → proceed with classic, document migration ticket reference.
- **E6.** If proceeding with classic: what is the migration plan to cloud, and what is the sunset date for this classic artifact?

If E2/E3/E4 cannot be ruled out with concrete reasoning, the agent **does not** generate classic code. It writes the escalation note and stops.

## Versioning anchor (HARD)

| Layer | Target |
|---|---|
| Stack | ECC 6.0 EHP8 or S/4HANA 2023 on-prem (non-Embedded-Steampunk objects only) |
| ABAP version | 7.50 minimum (use modern syntax even in classic — see house style) |
| Unicode | Mandatory |
| ALV | `cl_salv_table` for new standalone code; `cl_gui_alv_grid` only for embedded-in-Dynpro |
| Forms | Smartforms (legacy maintenance) or Adobe Forms (new) — never SAPscript for new work |
| Enhancements | BAdIs (new BAdI framework) preferred; enhancement spots for SAP code; user/customer exits only for maintenance |

## Namespace and package layout (HARD)

| Item | Rule |
|---|---|
| Customer namespace | `Z*` |
| Root package | `ZDSWF` |
| Module sub-packages | `ZDSWF_<MODULE>` (e.g. `ZDSWF_LEGACY_FIN`) |
| Classic objects sub-package | `ZDSWF_LEGACY_<MODULE>` — to make sunset visible |
| Test package | `ZDSWF_LEGACY_<MODULE>_TEST` |
| Object prefix | `ZDSWF_` for every developer-authored object |
| Function group prefix | `ZDSWF_<MODULE>_FG_` |
| Module pool prefix | `SAPMZDSWF_<MODULE>_<NAME>` (SAP convention forces `SAPM`) |
| Transport layer | `{{TRANSPORT_LAYER}}` — from handoff config |
| Software component | `{{SOFTWARE_COMPONENT}}` — from handoff config |

Naming sunset signal: every classic ZDSWF object lives in a `ZDSWF_LEGACY_*` package so the factory's debt tracker can find them.

## Hard rules

Read `references/hard-rules.md` for the full list. Non-negotiables:

1. **No modifications to SAP standard objects.** Use BAdIs, enhancement spots, append structures, or copy-and-modify into Z-space.
2. **Modern syntax mandatory** even in classic. `DATA(...)` inline, `VALUE #(...)`, `FOR` loops, `COND #(`, table expressions `[ ]`. No `MOVE`, no `MOVE-CORRESPONDING`, no obsolete forms.
3. **OO-ABAP first.** Procedural FORMs only inside module pools where the runtime forces them.
4. **ALV: CL_SALV_TABLE** for standalone new code; `CL_GUI_ALV_GRID` only when embedded in a Dynpro custom container. `REUSE_ALV_*` function modules forbidden in new code.
5. **No `WRITE` for production output.** Reports return data; ALV displays it.
6. **`COMMIT WORK AND WAIT`** in every BAPI integration. Never bare `COMMIT WORK` after a BAPI.
7. **Unicode-clean.** No `RANGE OF` on character vs numeric type mismatches; no manual byte offsets; `cl_abap_codepage` for any conversion.
8. **Authorization checks at the right layer.** `AUTHORITY-CHECK` on every transaction entry, every action menu, every data export. Forgetting one = critical defect.
9. **`LEAVE TO SCREEN 0`** to exit a Dynpro stack — never `STOP` or `EXIT` to terminate a transaction.
10. **`OK_CODE` cleared in PBO** before the next round-trip; `SY-UCOMM` is read-only.
11. **`AT EXIT-COMMAND`** for back/cancel/exit modules; bypasses field validation correctly.
12. **`CHAIN ... ENDCHAIN`** in screen flow logic for multi-field validations.
13. **Enqueue/dequeue for every multi-step update.** Lock objects, not raw row locks.
14. **No client hardcoding** — `sy-mandt` is read-only context.
15. **ABAP Unit tests for every class.** Module pool screens cannot be unit-tested directly; their controller logic must live in a class that is tested.

## Decision gates — answer BEFORE writing code

Read `references/decision-gates.md`. The classic-specific gates:

- **G1.** UI strategy: classical report, interactive report, ALV (which kind), module pool with Dynpro, or none.
- **G2.** Persistence: Z-table, append structure, or read-only against standard.
- **G3.** Update strategy: direct DB write (forbidden if standard tables), BAPI call, or update FM.
- **G4.** Integration: RFC, IDoc, BDC, none.
- **G5.** Enhancement vector: new BAdI, classic BAdI, enhancement spot, append, copy-to-Z.
- **G6.** Authorization objects used.
- **G7.** Output: Smartform, Adobe Forms, ALV export, file via `OPEN DATASET`.
- **G8.** Background vs dialog execution.
- **G9.** Test strategy (controller logic in classes; screens via integration test harness).
- **G10.** Sunset plan and migration ticket.

## Output structure — the handoff bundle

Identical layout to the cloud skill, adapted:

```
handoff/
  decision-gates.md       # E1..E6 + G1..G10, signed
  release-notes.md
  diff-summary.md
  atc-report.txt          # classic ATC variant (TRANSPORT_VARIANT or SLIN)
  test-results.txt
  enhancements-used.md    # every enhancement, BAdI, spot touched
  handoff-checklist.md
objects/
  prog/                   # *.prog.abap (reports, module pools)
  fugr/                   # function groups + function modules
  clas/                   # OO classes (preferred)
  dtel/                   # data elements
  doma/                   # domains
  tabl/                   # tables, append structures
  view/                   # views, search helps
  enq/                    # lock objects
  form/                   # Smartforms (only if new SF is justified)
  msag/                   # message classes
  enhc/                   # enhancement implementations
  badi/                   # BAdI implementations
tests/
  unit/                   # ABAP Unit
  integration/            # eCATT / Tricentis stubs if used
scripts-run/
  validate_naming.log
  check_classic_compliance.log
  check_handoff.log
```

## Gate — human handoff

Per ZDSWF Opción 3:
- **Code review (line by line)** required for: anything in `ZDSWF_CORE`, anything that touches SAP enhancement points, anything in `ZDSWF_LEGACY_FIN` (finance is always sensitive), and any BDC.
- **Artifact review** sufficient for: read-only reports, ALV tools, standalone Z-tables maintenance.

The agent does not transport. The agent produces the bundle, runs scripts, signs, and stops.

## House style

- ADT in Eclipse preferred. SE80 only when ADT lacks support for the object type (rare in modern releases).
- Modern syntax (7.40+) even in classic programs.
- One responsibility per program. Module pools reference controller classes — they do not contain business logic in FORMs.
- English names. English comments. No Spanish in objects or transports.
- ABAPDoc on every public class method.
- `abap_cleaner` style applied before handoff.

## Anti-patterns — STOP triggers

If you find yourself doing any of these, stop:

- Writing business logic inside `PERFORM ... FORM` instead of in a class method.
- `MOVE-CORRESPONDING` → use `CORRESPONDING #( )`.
- `IF NOT lt IS INITIAL` → `IF lt IS NOT INITIAL`.
- `READ TABLE ... INTO ... INDEX 1` → `lt[ 1 ]`.
- `LOOP AT lt WHERE field = 'X'.` repeatedly → push to SQL `WHERE` or use a sorted/hashed table.
- `CALL TRANSACTION 'XX' USING bdcdata` for a write that has a BAPI → use the BAPI.
- `COMMIT WORK.` immediately after `BAPI_*` → use `COMMIT WORK AND WAIT.`.
- `MESSAGE 'literal' TYPE 'E'.` → use message class.
- `SUBMIT z_other_report` without `EXPORTING LIST TO MEMORY` plan → think hard about whether this is the right pattern.
- `WRITE: / 'literal'.` → no classical list output in production.

## Cross-reference

- If during work you realize the task should be cloud, **stop**, document in `handoff/decision-gates.md` under "Escalation to sap-abap-cloud", and notify the human. Do not silently context-switch.

## Reference index

| File | When to read |
|---|---|
| `references/hard-rules.md` | Before any code generation. |
| `references/decision-gates.md` | Before any code generation. Contains E1..E6 escalation gates. |
| `references/dynpros.md` | Module pool / Dynpro work. |
| `references/reports.md` | Classical or interactive reports. |
| `references/alv.md` | ALV implementation. |
| `references/enhancements.md` | Enhancement framework. |
| `references/forms.md` | Smartforms / Adobe Forms in classic context. |
| `references/integration.md` | BAPIs, RFC, IDoc, BDC. |
| `references/testing.md` | ABAP Unit, controller-class extraction for testability. |
| `references/handoff-template.md` | Producing the handoff bundle. |
| `patterns/` | Copy-paste skeletons. |
| `scripts/` | Mandatory validators. |
