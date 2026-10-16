# Decision gates — answer BEFORE generating code

The agent must answer every applicable gate in writing in `handoff/decision-gates.md`. Any "I don't know" or unjustifiable answer = STOP and escalate.

## How to use

1. Read the task.
2. Walk through G1..G15 in order. Some gates are conditional ("only if G4 = freestyle").
3. Write the answer + one-sentence justification.
4. If any gate produces a blocker, write "BLOCKED — escalation required" and stop. Do not proceed to code.
5. Sign the file at the bottom: `Agent: claude. Date: <ISO date>. Status: gates-cleared | blocked.`

---

## G1 — Tier selection (extensibility model)

**Question:** Is the work Tier 1 (Key-user), Tier 2 (Developer / Embedded Steampunk), or Tier 3 (Side-by-side / BTP ABAP Env)?

**How to decide:** see `extensibility-tiers.md` decision tree.

**Default for ZDSWF factory work:** Tier 2 (Embedded Steampunk) unless the request is clearly a small business-admin change (then Tier 1, and the factory should not be doing it) or clearly a side-by-side app (then Tier 3).

**Output:** `Tier: <1|2|3>. Justification: <one sentence>.`

---

## G2 — RAP scenario (only if Tier 2 or 3)

**Question:** Managed, unmanaged, or unmanaged-with-additional-save?

**Decide:**
- *Managed*: SAP owns persistence in custom tables. RAP framework writes for you. **Default.**
- *Unmanaged*: external system or legacy framework owns persistence. You implement save sequence.
- *Unmanaged with additional save*: managed but with a save hook to a second system.

**Output:** `Scenario: <managed|unmanaged|unmanaged_additional_save>. Justification: <one sentence>.`

---

## G3 — Draft handling

**Question:** Draft-enabled (`with draft`) or non-draft?

**Decide:**
- Backs a Fiori Elements UI with create/edit? → **draft** (default).
- Read-only consumption? → non-draft.
- API-only (no UI)? → non-draft.

**Output:** `Draft: <yes|no>. Justification: <one sentence>.`

---

## G4 — UI strategy

**Question:** Fiori Elements (which template), Flexible Programming Model extension on Fiori Elements, or freestyle UI5?

**Decide:**
- List of records + detail + edit → **List Report + Object Page (LROP)**. Default.
- Work-queue with filters → Worklist.
- KPI/cards landing page → Overview Page (OVP).
- Analytics with filter bar → Analytical List Page (ALP).
- Above + specific UI extension → LROP/OP + Flexible Programming Model custom section.
- Genuinely non-tabular interaction (wizard, dashboard, drawing, etc.) → freestyle UI5. **Requires explicit justification.**

**Output:** `UI: <LROP|Worklist|OVP|ALP|FPM-extension|freestyle>. Justification: <one or two sentences>. If freestyle: list the specific UI requirement that Fiori Elements cannot meet.`

---

## G5 — Released API gap

**Question:** Is every SAP-side operation in scope backed by a released API?

**Decide:** list every read, write, function call against SAP standard. For each, declare the released artifact (`I_*` view, `XCO_*` class, released BAPI). If any has no released equivalent in S/4HANA 2023 → **BLOCKED**.

**Output:**
```
SAP operations:
  - Read customer master → I_Customer (released ✓)
  - Read sales orders → I_SalesOrder (released ✓)
  - Create journal entry → XCO library / API_JOURNALENTRY... (verify, cite release state)
Gaps: <none | list>
Status: <cleared | BLOCKED>
```

---

## G6 — Data model — custom tables and root entity

**Question:** What custom tables (`.tabl`) and what is the BO root?

**Decide:**
- Identify the root entity (`R_*` or `ZDSWF_R_*` data definition).
- Identify child entities (composition).
- Identify cross-BO associations (read-only links).
- Identify key strategy: UUID (default), GUID, or business key.

**Output:** brief ER sketch in markdown table form.

---

## G7 — Numbering

**Question:** Managed early, managed late, or unmanaged?

**Decide:**
- Internal UUID → managed early (assign immediately).
- Human-readable sequence → managed late or unmanaged with number range.
- External system owns key → unmanaged.

**Output:** `Numbering: <managed_early|managed_late|unmanaged>. Justification: <one sentence>.`

---

## G8 — Authorization

**Question:** What DCL access controls, and what BDEF instance-based authorization?

**Decide:**
- Identify the auth object (released, PFCG-compatible).
- Define DCL rule (`pfcg_auth` aspect).
- For BDEF: `authorization master ( global )` or `( instance )`?

**Output:**
```
DCL: pfcg_auth on auth_object Z_<...> (or released SAP auth object)
BDEF authorization: <global|instance>. Justification: <one sentence>.
```

---

## G9 — Locking / ETag

**Question:** Lock master or lock dependent? ETag strategy?

**Decide:**
- Lock master on the root.
- ETag: `total etag <field>` (e.g. `last_changed_at`) or field-level.

**Output:** `Lock: master. ETag: total / field-level. Field: <name>.`

---

## G10 — Validations, determinations, actions

**Question:** Enumerate each.

**Output:**
```
Validations:
  - V_<name> on (create|update|save). Triggers: <fields>. Purpose: <one sentence>.
Determinations:
  - D_<name> on (modify|save). Triggers: <fields>. Purpose: <one sentence>.
Actions:
  - A_<name>. Static or instance. Purpose: <one sentence>. Side effects: <yes/no>.
```

---

## G11 — Service projection and binding

**Question:** What's exposed and how?

**Output:**
```
Service definition: ZDSWF_SD_<name> exposes: <list of projections>
Service binding: ZDSWF_SB_<name>. Type: OData V4. UI: <LROP|...>.
```

---

## G12 — Fiori annotations strategy

**Question:** Where do UI annotations live?

**Decide:**
- Default: in metadata extension `.ddlx` per projection view.
- Annotations in CDS itself only for semantic (currency, quantity, text, label) not for UI layout.

**Output:** `UI annotations in: <ddlx file path>.`

---

## G13 — Testing strategy

**Question:** What is tested how?

**Output:**
```
Unit tests:
  - BIL classes: <class names>
  - CDS: <which views, using cl_cds_test_environment>
  - Actions/validations: cl_abap_behv_test_environment
Integration tests: <list>
Coverage target: 80% lines in ZDSWF_*.
```

---

## G14 — Mobile (only if MDK in scope)

**Question:** What MDK app, offline strategy, defining requests?

**Output:** see `mdk-recipe.md` for the template.

---

## G15 — Handoff package and gate

**Question:** Does this work go through code review or artifact review?

**Decide:**
- Any object in `ZDSWF_CORE` → **code review** (line by line).
- Any service binding being published → **code review**.
- Otherwise → **artifact review** (ATC + tests + naming + diff).

**Output:** `Gate: <code-review|artifact-review>. Reviewer: <developer-handle if known>.`

---

## Escalation conditions (STOP signals)

Any of these → write "BLOCKED" and stop before code generation:

- G5 produces any gap with no released API.
- G1 indicates Tier 1 (key-user) — factory should not do this; escalate to functional team.
- Task description implies classic ABAP (Dynpros, GUI, ECC). Escalate to `sap-abap-classic` skill with a decision-gate note.
- Task description implies modification of SAP standard. Escalate.
- Task uses any S/4HANA release < 2023 → human override required.
- Task is read-only but request asks for `with draft` → contradiction, clarify.
- Naming convention conflict (something outside ZDSWF) → clarify with human.

---

## Sign-off

At the bottom of `handoff/decision-gates.md`:

```
Agent: claude
Skill: sap-abap-cloud
Date: <ISO 8601>
Status: gates-cleared | blocked
If blocked: reason: <text>
```

This file is the first thing the developer reads in the handoff bundle. It explains every architectural choice the agent made.
