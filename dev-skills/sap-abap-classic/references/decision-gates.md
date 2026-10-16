# Decision gates — answer BEFORE generating code

Two layers:
- **E1..E6** — escalation gates. Determine if work should be cloud instead.
- **G1..G10** — classic-design gates. Only proceed here if E1..E6 cleared classic as the right path.

The agent writes answers in `handoff/decision-gates.md`. Any "I don't know" = STOP.

---

## E1 — Is the target system genuinely classic-only?

**Question:** Where does the work execute? ECC, on-prem S/4HANA without Embedded Steampunk, S/4HANA Private Edition in non-cloud mode, or another classic context?

**Decide:**
- ECC 6.x → classic only.
- S/4HANA on-prem with Embedded Steampunk enabled → cloud preferred (escalate to `sap-abap-cloud`).
- S/4HANA Private Edition Cloud-mode → cloud preferred.
- S/4HANA Public Cloud → cloud only (escalate).

**Output:** `Target: <system + release>. Classic justified: <yes|no>. Justification: <one sentence>.`

If "no" → STOP and escalate to `sap-abap-cloud`.

---

## E2 — Could this be Fiori Elements + RAP?

**Question:** Is the requirement effectively list + detail + edit of a business entity?

**Decide:** if yes and the system supports Embedded Steampunk → escalate. Classic Dynpro for what amounts to a Fiori LROP is a banned anti-pattern.

**Output:** `Fiori+RAP alternative: <viable|not viable>. Justification: <one or two sentences>.`

---

## E3 — Could this be a custom CDS analytical query?

**Question:** Is this reporting / aggregation / KPI work?

**Decide:** if yes and customer has Embedded Analytics or BTP analytics → escalate. A new classic SE38 report for KPI work is debt.

**Output:** `Analytical alternative: <viable|not viable>. Justification.`

---

## E4 — Could this be a side-by-side BTP app?

**Question:** Is this a workflow, integration orchestration, or extension that could live next to S/4 via released APIs?

**Decide:** if yes and BTP is available → escalate.

**Output:** `Side-by-side alternative: <viable|not viable>. Justification.`

---

## E5 — Is this maintenance of an existing classic Z-object?

**Question:** Are we modifying an existing classic artifact (bug fix, regulatory change) where migration is explicitly out of scope?

**Decide:** if yes, classic is justified for this task. Migration ticket required.

**Output:** `Maintenance: <yes|no>. Existing object: <ZDSWF_LEGACY_*>. Migration ticket: <id or "none — flag for sunset plan">.`

---

## E6 — Sunset plan

**Question:** What is the migration path and target date?

**Output:**
```
Sunset plan:
  Target architecture: <RAP custom BO | analytical query | side-by-side | retire>
  Target date: <ISO date or quarter>
  Migration ticket: <id>
  Owner: <name or team>
```

If E5 is "yes — maintenance" and E6 is empty → BLOCKED. Every maintenance event on classic must update the sunset plan.

---

## G1 — UI strategy

**Question:** Classical report, interactive report, ALV (which kind), module pool with Dynpro, or no UI?

**Decide:**
- Read-only tabular output → `cl_salv_table` ALV report. Default.
- Drill-down / interactive analysis → `cl_salv_table` with events, or interactive report only if SALV cannot do it.
- Data maintenance with custom layout → module pool + Dynpro + `cl_gui_alv_grid` in custom container.
- Single-screen input → simple selection screen + processing.
- No UI → executable program with selection screen only, or background-only.

**Output:** `UI: <SALV|interactive|module-pool|selection-screen-only|none>. Justification.`

---

## G2 — Persistence

**Question:** Z-table, append structure, or read-only against standard?

**Output:**
```
Tables: <list of Z-tables created>
Append structures: <list, with the standard tables they extend>
Read-only access to standard tables: <list>
```

If "modify SAP standard table" anywhere → STOP, this is forbidden (hard rule 1.2).

---

## G3 — Update strategy

**Question:** How is data written?

**Decide:**
- Z-tables → direct `INSERT/UPDATE/MODIFY/DELETE` from controller class.
- SAP standard data → BAPI or update FM only.
- Mixed → both, with consistent commit boundary.

**Output:** `Update: <Z-direct | BAPI | update-FM | mixed>. Commit strategy: <description>.`

---

## G4 — Integration

**Question:** Outbound or inbound integration?

**Output:**
```
RFC: <inbound|outbound|none>. Destinations: <Z-names>.
IDoc: <type, direction, process code>.
BDC: <transaction, justification — BDC requires explicit justification because BAPI is preferred>.
SOAP/REST: <if any, via what mechanism>.
```

---

## G5 — Enhancement vector

**Question:** If extending SAP, what mechanism?

**Decide:**
- New BAdI (new enhancement framework) → preferred.
- Classic BAdI → only if SAP provides only this.
- Enhancement spot → for inline source code enhancement.
- User exit / customer exit → maintenance only; forbidden in new development.
- Append structure → for table extension.
- Copy-to-Z → last resort, requires explicit justification.

**Output:** `Vector: <new-BAdI|classic-BAdI|spot|user-exit|append|copy>. Object: <ID>. Justification.`

---

## G6 — Authorization objects

**Output:**
```
Auth checks:
  - Object: <S_TCODE | other> Activity: <01|02|03|...> Layer: <transaction-entry|action|export>
  - ...
Z-auth objects created: <list, with fields>
```

---

## G7 — Output / forms

**Output:** `Output: <SALV-export | Adobe Forms | Smartform (maintenance only) | file via OPEN DATASET | none>.`

If "SAPscript" → BLOCKED.

---

## G8 — Background vs dialog

**Output:** `Execution: <dialog|background|both>. Background scheduling: <Application Job template ID or SM36 variant>.`

---

## G9 — Testing

**Output:**
```
ABAP Unit tests for controller classes: <list>
Coverage target: 70% lines on controller classes
Integration test harness: <eCATT | Tricentis | manual | none>
```

---

## G10 — Sunset reminder

Restate the sunset plan from E6 here. Coverage redundancy is intentional — sunset is the most easily forgotten obligation.

---

## Escalation conditions (STOP signals)

- E1 ≠ classic-only → STOP and escalate to `sap-abap-cloud`.
- E2/E3/E4 indicate a viable cloud alternative → STOP and escalate.
- E6 sunset plan missing → STOP.
- G2 includes any modify-SAP-standard-table → STOP, forbidden.
- G5 indicates user exit / customer exit in *new* development → STOP, must use BAdI or spot.
- G7 indicates SAPscript for new work → STOP.

---

## Sign-off

```
Agent: claude
Skill: sap-abap-classic
Date: <ISO 8601>
Escalation result: <classic-justified | escalated-to-cloud>
Status: gates-cleared | blocked
Sunset target: <date>
```
