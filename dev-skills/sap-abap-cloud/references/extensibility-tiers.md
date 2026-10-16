# Extensibility tiers — Clean Core

Three tiers. Pick exactly one per task. G1 fixes the choice.

## Decision tree

```
Is this a small UI tweak, a custom field, or a custom CDS view
that a business admin can configure through Fiori "Custom *" apps?
  └─► YES → Tier 1 (Key-User). FACTORY DOES NOT BUILD THIS.
            Escalate to functional team.

Does the extension need to:
  - run inside S/4HANA stack,
  - share the HANA DB synchronously with standard tables (via released views),
  - extend a standard BO or build a custom BO that lives "next to" SAP code?
  └─► YES → Tier 2 (Developer / Embedded Steampunk). ZDSWF default.

Should the extension:
  - have its own lifecycle independent of S/4HANA upgrades,
  - be a SaaS-style multi-tenant extension,
  - run as a side-by-side app on BTP ABAP Environment,
  - integrate via released communication scenarios (not direct DB)?
  └─► YES → Tier 3 (Side-by-side / BTP ABAP Env).
```

## Tier 1 — Key-User Extensibility

**Out of scope for the ZDSWF factory.** Documented here for escalation completeness.

Tools (Fiori apps):
- Custom Fields and Logic — add a field, push it through CDS/UI/OData.
- Custom Business Objects.
- Custom CDS Views.
- Custom Analytical Queries.
- Custom Reusable Elements.
- Custom Catalog Extensions, Adapt UI (page variants, adaptation projects).
- Output Management (Adobe Forms templates).
- Business Rules in BTP Business Rules service.

If a task arrives that is genuinely Tier 1: write that in `decision-gates.md`, mark BLOCKED with reason "Tier 1 — out of factory scope", do not generate code. The functional team picks it up.

## Tier 2 — Developer Extensibility (Embedded Steampunk)

**This is where most ZDSWF work happens.**

- ADT in Eclipse, ABAP language version 5 (`ABAP for Cloud Development`).
- Custom objects in `ZDSWF` package hierarchy.
- Released APIs only.
- Same HANA DB as S/4 standard, but you can only see standard data through released `I_*` views.
- Full RAP business objects with managed/unmanaged scenarios.
- Service bindings → Fiori Elements apps inside the system.
- BAdI implementations for SAP-provided cloud BAdIs.
- Background jobs via Application Job templates.

What you cannot do (already in `hard-rules.md`):
- `SELECT` from SAP standard transparent tables directly.
- Use unreleased FMs / classes / BAPIs.
- Classic Dynpros, SAPGUI, SUBMIT to standard reports.
- Direct file system access except via `XCO_*`.

## Tier 3 — Side-by-Side (BTP ABAP Environment / Steampunk)

When to choose:
- Multi-tenant SaaS extension.
- Logic must survive S/4HANA upgrade/downgrade independently.
- Heavy integration / orchestration that is healthier outside the ERP.
- Customer is S/4HANA Cloud Public Edition (Tier 2 restricted there; Tier 3 default).
- Extension is a standalone product, not customer-specific code.

Topology:
- Own HANA Cloud schema on BTP. Cannot see S/4 tables — they are remote.
- Reaches into S/4HANA via **Communication Arrangements** (released scenarios, OData/SOAP/RFC).
- Reaches out to other BTP services (Workflow, Business Rules, Destination, Event Mesh / AEM).
- Same ABAP Cloud language restrictions as Tier 2.

Practical project structure on BTP ABAP Env:

```
ZDSWF_<APP> (software component)
  ZDSWF_<APP>_CORE
  ZDSWF_<APP>_DATA
  ZDSWF_<APP>_INTEGRATION
  ZDSWF_<APP>_UI
  ZDSWF_<APP>_TEST
```

Communication scenarios used in Tier 3 (released, examples):
- `SAP_COM_0008` — Business Partner Replication.
- `SAP_COM_0009` — Sales Order Integration.
- `SAP_COM_0109` — Material Replication.
- Always check the latest released list — they evolve.

## Common confusion points

**"Custom BO via Key-User vs Custom BO via RAP."**
Key-user custom BOs are no-code. They give you persistence + Fiori list/detail + OData with zero coding. Good for very simple things. Don't scale: hard to add complex logic, no real testing, no transport across landscapes without effort. RAP custom BOs are code. Full control, full testing, full extensibility. ZDSWF builds RAP custom BOs in Tier 2.

**"Embedded Steampunk vs Side-by-Side Steampunk — both are 'ABAP Cloud'."**
Same language scope, different topology. Embedded = inside S/4HANA. Side-by-side = its own BTP subaccount. The language restrictions are identical; the data access pattern is the differentiator.

**"BAdI in cloud — is that allowed?"**
Yes for *released cloud BAdIs* with cloud-suitable implementations. Old classic BAdIs are not exposed in cloud unless SAP explicitly released them. Check API state.

**"Can I lift a Z-report to cloud as-is?"**
Almost never. Run the SAP Readiness Check / Custom Code Migration tool first. Each Z-object gets a verdict: lift unchanged (rare), refactor in Tier 2, rebuild in Tier 3, replace with standard, or retire. Most legacy Z-reports become custom CDS analytical queries.

## Mapping classic → cloud

| Classic | Cloud Tier 2 |
|---|---|
| Z-table + module pool maintenance | Custom BO via RAP + Fiori Elements |
| Z-report with selection screen | Analytical CDS + custom analytical query, or RAP action |
| BDC / `CALL TRANSACTION` | Released RAP action / released BAPI |
| Mod to SAP code | Cloud BAdI, key-user extensibility, or side-by-side |
| Append structure to standard table | Custom Fields & Logic (key-user) |
| User exit / customer exit | Released cloud BAdI |
| SAPscript / Smartform | Adobe Forms via Output Management |
| RFC outbound | Communication Arrangement (Tier 3) or released cloud API |
| IDoc outbound | Communication Arrangement OData/SOAP, or event via Event Mesh / AEM |
| Background job in SM36 | Application Job template + Application Jobs app |
| Z-auth object + AUTHORITY-CHECK | DCL + role in IAM app |
