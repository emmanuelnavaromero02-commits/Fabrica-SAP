---
name: sap-abap-cloud
description: Build SAP solutions under ABAP Cloud and Clean Core for the ZDSWF dark software factory. Use whenever the task touches ABAP Cloud, S/4HANA 2023 (on-prem or Private Edition Cloud-mode), RAP (RESTful ABAP Programming Model), CDS view entities, behavior definitions and implementations, service definitions and bindings, OData V4, Fiori Elements, freestyle SAPUI5, SAP BTP ABAP Environment (Steampunk), Embedded Steampunk, ADT in Eclipse, released APIs, the three-tier extensibility model, draft handling, side-by-side extensions, Business Application Studio, the SAP Mobile Development Kit (MDK), or offline-first mobile apps on OData. Also for Clean Core readiness checks, cloud-readiness ATC variants, and migration of classic Z-objects to cloud equivalents. Do NOT use for ECC, on-prem S/4 modifications outside Embedded Steampunk, classic Dynpros, SE80 module pools, or anything in SAP GUI — that is sap-abap-classic and you must escalate there with a decision-gate note.
---

# SAP ABAP Cloud — ZDSWF contract

This skill is a **contract**, not a tutorial. The agent operating this skill is part of an autonomous software factory. The contract defines what the agent must do, must not do, and must hand off. Reference files in `references/` are operational recipes, not background reading.

## Versioning anchor (HARD)

All output assumes this stack. Do not generate code for any other release without explicit human override.

| Layer | Target |
|---|---|
| S/4HANA release | **2023** (on-prem or Private Edition Cloud-mode), with Embedded Steampunk enabled |
| ABAP language version | **Version 5 — ABAP for Cloud Development** |
| RAP strict mode | **Strict mode 2** minimum; strict mode 3 when the target system supports it |
| OData version | **V4** (no V2 in new developments) |
| CDS syntax | **`DEFINE VIEW ENTITY`** only (no legacy `DEFINE VIEW`) |
| Fiori Elements | **V4** templates via `sap.fe.templates` |
| BTP runtime (where applicable) | BTP ABAP Environment ("Steampunk") current release |
| Mobile | **MDK** on BTP Mobile Services |

If a task implies a different release, **STOP** and produce a handoff note asking for human override.

## Namespace and package layout (HARD)

| Item | Rule |
|---|---|
| Customer namespace | `Z*` (factory convention; not registered SAP namespace) |
| Root package | `ZDSWF` |
| Module sub-packages | `ZDSWF_<MODULE>` (e.g. `ZDSWF_FIN`, `ZDSWF_LOG`, `ZDSWF_HR`) |
| Core/shared package | `ZDSWF_CORE` — **revisión de código humana obligatoria** (Opción 3, criticidad alta) |
| Test package | `ZDSWF_TEST` or `ZDSWF_<MODULE>_TEST` |
| Object prefix | `ZDSWF_` for every developer-authored object: classes, CDS, BDEF, services, tables, includes |
| Transport layer | `{{TRANSPORT_LAYER}}` — provided by handoff config, never hardcoded |
| Software component | `{{SOFTWARE_COMPONENT}}` — provided by handoff config |

## Hard rules — never violate

Read `references/hard-rules.md` for full list. The non-negotiables:

1. **Released APIs only.** Every `SELECT`, every `CALL METHOD` against SAP-delivered objects must target a released API (`I_*` CDS view, `XCO_*` class, or class with API state = "Released for Cloud Development"). If unsure, run `scripts/check_released_apis.py` and verify in ADT. If still unsure, **STOP** and escalate.
2. **No classic constructs.** No Dynpros, no `WRITE`, no `SUBMIT`, no `CALL TRANSACTION`, no `CALL FUNCTION` to non-released FMs, no SAPscript/Smartforms, no `OPEN DATASET` against application server unless via released `XCO_CP_FS_*` or equivalent.
3. **No direct table access to SAP standard tables.** Always go via released `I_*` interface CDS view.
4. **No modifications to SAP standard objects.** Use BAdIs, key-user extensibility, or side-by-side.
5. **Strict mode 2 minimum on every BDEF.** No exceptions.
6. **OData V4 service bindings only** for new services.
7. **No UI5 controller code for standard CRUD.** If the use case is list-report or object-page, you write CDS annotations and let Fiori Elements generate the UI. Freestyle SAPUI5 only with documented justification in the handoff.
8. **No managed business object logic in the BIL** unless the framework cannot do it. For managed scenarios, prefer determinations, validations, and side effects over imperative ABAP.
9. **ATC variant must pass with zero findings** before handoff. Run via ADT against `SAP_CP_READINESS_*` (the latest variant in the system).
10. **ABAP Unit tests are mandatory** for every BIL class, every action, every validation. Coverage threshold for handoff: 80% lines in `ZDSWF_*`.

## Decision gates — answer these BEFORE writing code

This is the auto-stop checklist. The agent must produce written answers to every gate before generating any code. The answers go in `handoff/decision-gates.md` (see template). If any answer is "I don't know" or "I can't justify", **STOP** and ask the human.

Read `references/decision-gates.md` for the full set. The five most important:

- **G1. Tier:** Is this Tier 1 (key-user), Tier 2 (developer extensibility / Embedded Steampunk), or Tier 3 (side-by-side / BTP ABAP Env)? Justify in one sentence why this tier and not the other two.
- **G2. RAP scenario:** Managed, unmanaged, or unmanaged-with-additional-save? Why? (Default to *managed* unless integrating with an external system of record.)
- **G3. Draft:** Draft-enabled or not? (Default: yes for any object created/edited through Fiori Elements.)
- **G4. UI:** Fiori Elements (LROP/Worklist/OVP/ALP), Flexible Programming Model extension, or freestyle UI5? (Default: LROP. Freestyle requires explicit justification.)
- **G5. Released API gap:** Is there any operation in scope that has no released API today? List them. If any, the task cannot proceed without human override.

## Output structure — what the agent must produce

Every task produces this bundle on disk, ready for the developer gate:

```
handoff/
  decision-gates.md       # answers to G1..Gn, signed by agent
  release-notes.md        # what was built, why, business outcome
  diff-summary.md         # objects touched, created, deleted
  atc-report.txt          # raw ATC output, must be empty findings
  test-results.txt        # ABAP Unit run output, coverage report
  released-apis-used.md   # list of every SAP API touched + release state
  handoff-checklist.md    # filled-in checklist, agent-signed
objects/
  cds/                    # *.ddls, *.ddlx (metadata extensions)
  behavior/               # *.bdef
  behavior-impl/          # BIL classes (*.clas.abap)
  service/                # *.srvd, *.srvb (definitions, bindings)
  ui/                     # only if freestyle UI5 was justified
  auth/                   # *.dcls (DCL)
  data/                   # *.tabl for custom tables
tests/
  unit/                   # ABAP Unit classes
  integration/            # CDS test environment, RAP test doubles
scripts-run/
  validate_naming.log
  check_released_apis.log
  check_handoff.log
```

The bundle is **immutable** once `check_handoff.py` passes. The developer receives this, not loose files.

## Gate — human handoff

There is **one** human gate: **the developer, before transport to SAP**. Per ZDSWF policy (Opción 3 — mixta):

- **Code review (line by line)** required for any object in `ZDSWF_CORE` or that exposes a service binding.
- **Artifact review** (ATC clean + tests pass + naming OK + diff sane) sufficient for objects in `ZDSWF_<MODULE>` packages outside `ZDSWF_CORE` and outside service bindings.

The agent does not transport. The agent does not release transports. The agent produces the bundle, runs scripts, signs the handoff, and stops. If `check_handoff.py` fails, the agent fixes and re-runs — it does not hand off broken work.

## House style

- ADT in Eclipse only. No SE80, no SE38, no SE11 for new work.
- One CDS view entity per `.ddls` file. One BDEF per `.bdef`. One class per `.clas.abap`.
- `abap_cleaner` style applied to every ABAP file before handoff.
- Modern syntax only: `DATA(...)`, inline declarations, `VALUE #( )`, `FOR` loops, table expressions `[ ]`, `COND #(`, `SWITCH #(`, `REF #(`. No `MOVE`, no `MOVE-CORRESPONDING` (use `CORRESPONDING #(`), no `IF NOT lt IS INITIAL`.
- English names everywhere. No Spanish in object names or comments.
- ABAPDoc comments mandatory on every public method and on every CDS root view.

## Anti-patterns — self-detect and STOP

If you find yourself writing any of these, stop immediately and either escalate or refactor:

- `SELECT ... FROM <standard_table>` where `<standard_table>` is not `I_*` → STOP, find the released view.
- `CALL FUNCTION '<name>'` where `<name>` is not on the released list → STOP, check API state, escalate if not released.
- Imperative loop inside a BIL when a determination/validation/side-effect could do it → refactor.
- Writing more than ~20 lines of UI5 controller code → STOP, this should be Fiori Elements + annotations.
- Hardcoding any client number, language, system ID, namespace, transport layer → STOP, parameterize.
- Catching `cx_root` to silently continue → STOP, this is forbidden.
- Disabling an ATC finding without a documented exemption → STOP.

## Cross-reference

- If the task turns out to require classic ABAP (Dynpros, classic reports, SAP GUI, ECC), **do not switch context silently**. Write the decision in `handoff/decision-gates.md` under "Escalation to sap-abap-classic" and stop. The human decides whether to invoke `sap-abap-classic`.

## Reference index

| File | When to read |
|---|---|
| `references/hard-rules.md` | Before *any* code generation. Full prohibition list. |
| `references/decision-gates.md` | Before code generation. Full gate questionnaire. |
| `references/rap-skeleton.md` | Building any RAP business object. |
| `references/cds-recipes.md` | Building CDS views (data definition layer). |
| `references/fiori-annotations.md` | Wiring CDS to Fiori Elements UI. |
| `references/fiori-elements.md` | Choosing template, manifest.json, FPM extensions. |
| `references/freestyle-ui5.md` | Only if G4 justified freestyle. Otherwise skip. |
| `references/mdk-recipe.md` | Mobile work (MDK, BTP Mobile Services). |
| `references/extensibility-tiers.md` | Tier selection (G1), key-user vs developer vs side-by-side. |
| `references/released-apis.md` | How to verify API release state. |
| `references/testing.md` | ABAP Unit and CDS test environment patterns. |
| `references/handoff-template.md` | Producing the handoff bundle. |
| `patterns/` | Copy-paste skeletons for common deliverables. |
| `scripts/` | Mandatory validators. Run before declaring done. |
