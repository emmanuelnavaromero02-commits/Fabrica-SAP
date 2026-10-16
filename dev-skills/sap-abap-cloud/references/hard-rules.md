# Hard rules — ABAP Cloud / Clean Core / ZDSWF

These are absolute. No exceptions without human override documented in `handoff/decision-gates.md` under "Hard rule exemption".

## 1. Released API surface

1.1. Every SAP-delivered object the agent reads or calls must be in **released state for cloud development**. Verify via ADT → object → *API State* tab.
1.2. **Allowed CDS reads:** `I_*` interface views, `C_*` consumption views only when explicitly released, and custom `ZDSWF_*` views.
1.3. **Forbidden CDS reads:** `P_*` (private), `E_*` (extension), `R_*` (restricted) unless explicitly marked released for cloud — verify, do not assume.
1.4. **Allowed ABAP classes:** `XCO_*` library, `CL_ABAP_*` released subset, `CL_BALI_*` (logging), classes with `RELEASE_STATE = Released for Cloud Development`.
1.5. **Forbidden ABAP classes:** anything not released. Including the seductive ones: `CL_ABAP_RANDOM` (use `XCO_CP_RANDOM`), `CL_GUI_FRONTEND_SERVICES` (no GUI in cloud), `/UI2/CL_JSON` (use `XCO_CP_JSON`).
1.6. **Function modules:** every `CALL FUNCTION` is suspect. Even released-looking BAPIs are often not released for cloud. Check API state. Default assumption: forbidden until proven released.

## 2. Forbidden language constructs

| Construct | Replacement |
|---|---|
| `WRITE` / classical list output | RAP action returning structure, or analytical CDS |
| Dynpros / `CALL SCREEN` / module pools | Fiori Elements or freestyle UI5 |
| `SUBMIT` to standard reports | Released RAP action or BAPI alternative |
| `CALL TRANSACTION` | API-based integration |
| BDC (`bdcdata`, `BDC_OKCODE`) | Released API or RAP action |
| `OPEN DATASET` on app server | `XCO_CP_FS_*` released APIs, or no file IO at all |
| Native SQL / ADBC | CDS view + ABSL, or AMDP within released boundary |
| `EXEC SQL ... ENDEXEC` | Forbidden in cloud |
| `INSERT/UPDATE/DELETE` on SAP standard table | Released BAPI/RAP action only |
| `AUTHORITY-CHECK` against unreleased auth object | DCL with `pfcg_auth` aspect on released object |
| SAPscript, Smartforms | Adobe Forms via Output Management |
| `MESSAGE ... TYPE 'E'` ad-hoc | RAP `failed`/`reported` tables, or `RAISE EXCEPTION` of released exception class |

## 3. Forbidden patterns

3.1. **No silent error swallowing.** `CATCH cx_root INTO DATA(lx). " do nothing` is banned. Every catch must either log to `cl_bali_log` (released) and re-raise, or convert to a `reported` entry in RAP.
3.2. **No hardcoded clients, languages, sys-ids, system fields except where unavoidable.** `sy-mandt`, `sy-langu`, `sy-uname` are read-only context, never logic drivers. For user, use `xco_cp=>sy->user( )`.
3.3. **No hardcoded namespaces or transports.** Transport layer, software component, and package names come from the handoff config (`{{TRANSPORT_LAYER}}`, `{{SOFTWARE_COMPONENT}}`).
3.4. **No `IF NOT lt IS INITIAL`** — write `IF lt IS NOT INITIAL`.
3.5. **No `MOVE` between fields of different types.** Use `CONV #( )` or `EXACT #( )`.
3.6. **No type `c` or `n` for business semantics.** Use domains, data elements, or `i`/`int8`/`d`/`t`/quantities/currencies as appropriate.
3.7. **No SELECT \*** unless every column is genuinely needed. List columns explicitly.
3.8. **No `INTO TABLE @DATA(lt_x)` followed by a loop with a single `IF`** — push that into the SQL `WHERE`.
3.9. **No locking via `ENQUEUE_*` function modules** in RAP. Use BDEF `lock master` and let the framework lock.

## 4. RAP-specific hard rules

4.1. **`strict ( 2 )`** minimum on every BDEF. Strict 3 when target supports.
4.2. **`with draft`** on every transactional BDEF that backs a Fiori Elements UI, unless the use case is read-only or specifically non-draft (justify in G3).
4.3. **Managed scenario by default.** Unmanaged only when integrating an external system of record.
4.4. **Field control via BDEF, not in BIL.** `field ( readonly )`, `field ( mandatory )` declared in BDEF.
4.5. **Compositions for parent-child** within the same business object. Associations for cross-BO links. Mixing them up is a banned pattern.
4.6. **No business logic in determinations on save** unless it cannot be done on modify. Save-time logic is harder to test and reason about.
4.7. **Numbering:** `managed` (early or late) by default. `unmanaged` numbering only with external key source.
4.8. **ETag mandatory** for any updateable BO. Either `total etag` on root or field-level etag.
4.9. **No `MODIFY ENTITIES` outside the BIL** for the BO itself. External callers go through the service or through `CL_ABAP_BEHV_*` released APIs.

## 5. CDS hard rules

5.1. **`DEFINE VIEW ENTITY`** only. No `DEFINE VIEW`.
5.2. **No `client` annotation** — client handling is automatic in view entities.
5.3. **Every key field annotated** with `@ObjectModel.text.element` if it has a human-readable description elsewhere.
5.4. **Currency/quantity:** every amount field must have `@Semantics.amount.currencyCode: '<field>'`; every quantity field `@Semantics.quantity.unitOfMeasure: '<field>'`. Without this, Fiori Elements renders wrong.
5.5. **No `SELECT` in the projection layer** beyond what the BO needs to expose. Don't leak fields.
5.6. **Joins:** prefer associations + `_assoc.field` syntax over explicit joins. Joins only for analytical/aggregation cases.
5.7. **DCL is mandatory** for any view that backs a transactional service. No service binding without an access control.

## 6. Fiori / UI hard rules

6.1. **No freestyle UI5 for CRUD use cases.** If it's list + detail + edit, it's Fiori Elements. Period.
6.2. **Annotations in metadata extensions (`.ddlx`)**, not inline in the CDS, for UI-only annotations. Keeps CDS reusable.
6.3. **No business logic in UI5 controllers.** Validation and derivation belong in RAP.
6.4. **i18n mandatory.** No literal strings in UI. Even for one-off apps. The factory does not produce single-language code.
6.5. **No `formatter.js` magic** for things RAP can compute. Push the computation server-side.

## 7. Mobile (MDK) hard rules

7.1. **No native iOS/Android code** in MDK rules unless the use case explicitly demands it (camera deep integration, AR, etc.).
7.2. **Offline-first by default.** Every MDK app declares offline OData provider with `DefiningRequests`. No online-only MDK apps in ZDSWF.
7.3. **JS rules under 50 lines.** If a rule grows beyond that, the logic belongs on the server (RAP action / determination).

## 8. Testing hard rules

8.1. **ABAP Unit test class** for every BIL class. No exceptions.
8.2. **`cl_cds_test_environment`** for CDS unit tests where the view has any logic (case, computed field, parameter).
8.3. **RAP test doubles via `cl_abap_behv_test_environment`** for action/validation/determination tests.
8.4. **Coverage: 80% lines minimum** in `ZDSWF_*`. Coverage report goes in the handoff.
8.5. **No test that hits the database directly.** Tests use test environments and doubles.

## 9. Security hard rules

9.1. **DCL on every read CDS** that backs a service.
9.2. **Instance-based authorization in BDEF** (`authorization master`) when records belong to users/orgs.
9.3. **No `AUTHORITY-CHECK` in BIL** — go through DCL and `with privileged mode` only when justified and documented.
9.4. **No secrets in code.** Use Communication Arrangements and Destination service for credentials.
9.5. **PII handling:** any field containing PII must have `@EndUserText.label` indicating so and an `@Semantics.personalData` annotation. Logging of PII is forbidden.

## 10. ATC and Clean Core readiness

10.1. ATC variant: latest `SAP_CP_READINESS_*` or `SAP_CLEAN_CORE_READINESS_*` in the target system. Ask Basis if unsure.
10.2. **Zero priority 1 and priority 2 findings.** Priority 3 findings require a documented exemption.
10.3. ATC report (`atc-report.txt`) is part of the handoff. Without it, handoff is rejected by `check_handoff.py`.

## 11. Comentarios y literales

11.1. **El código se comenta.** Cada clase y cada método público lleva comentario de cabecera con su PROPÓSITO (el porqué, no repetir el código). Los bloques de lógica no obvia llevan un comentario de intención de una línea. No dejar código comentado/muerto.
11.2. **Prohibidas las cadenas constantes (literales) para texto de usuario o mensajes.** Todo texto que se muestra o se emite como mensaje va por **text elements / i18n (Fiori, ver 6.4)** o **clase de mensajes / excepción liberada** — nunca literales en línea — para que sea traducible. Se manda **como texto**, no como string constante.
11.3. **Constantes técnicas** (claves, códigos, tipos) → `CONSTANTS` con nombre con significado; nunca literales mágicos dispersos en el código.

## Self-check before declaring done

Before signing `handoff-checklist.md`, the agent reruns this list and writes "compliant" or "exempted (see G#)" against each numbered rule. If anything is non-compliant and not exempted, the agent does not hand off.
