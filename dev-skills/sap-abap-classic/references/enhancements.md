# Enhancement framework

Five techniques. Use the most modern that the SAP code supports.

## Decision order

1. **New BAdI** (new enhancement framework) — preferred. Use when SAP provides one.
2. **Classic BAdI** — only if SAP provides only this for the use case.
3. **Enhancement spot (implicit or explicit)** — for inline source code enhancement of SAP code at marked points.
4. **Append structure / table append** — to extend SAP-delivered tables and structures.
5. **User exit / customer exit** — maintenance only. Forbidden in new development.

## New BAdI

ADT: open the BAdI definition → *Create Implementation* → enter ZDSWF implementing class + filter values.

```abap
class zcl_zdswf_<badi_name>_impl definition
  public final create public.

  public section.
    interfaces if_<sap_badi_interface>.
endclass.

class zcl_zdswf_<badi_name>_impl implementation.

  method if_<sap_badi_interface>~<method_name>.
    " delegate to controller class — no business logic in BAdI impl body
    new zcl_zdswf_<usecase>_ctrl( )->handle_<event>(
      iv_param1 = iv_param1
      iv_param2 = iv_param2 ).
  endmethod.

endclass.
```

Filter values defined in the BAdI implementation registration. Multiple implementations can coexist; SAP runtime picks by filter.

## Classic BAdI

`SE19` → classic BAdI → enter `ZDSWF_<NAME>_IMPL`. Older mechanism; same delegation pattern.

> **No se crea por el MCP (headless).** El registro de una impl de BAdI **clásica** (SXSD) usa `SXO_IMPL_SAVE`, que es **GUI-bound** (su objeto ALV de filtros dispara un dynpro `SAPMSSY0` sin SAPGUI). El bridge / `aws_abapgit_crear_badi` **devuelven error** a propósito → **regístrala en SE19** (manual). Las BAdI **nuevas/kernel** sí se crean por MCP como `ENHO` (`aws_abapgit_crear_objeto object_type=ENHO`).

## Enhancement spot

For inline enhancement of SAP code at marked points:

ADT: navigate to SAP source → find `ENHANCEMENT-POINT` or `ENHANCEMENT-SECTION` marker → create enhancement implementation in `ZDSWF_<NAME>_ENHC`.

```abap
" inside the SAP code at enhancement-point ES_FB_...
ENHANCEMENT 1 ZDSWF_<NAME>_ENHC.
  new zcl_zdswf_<usecase>_ctrl( )->process( ... ).
ENDENHANCEMENT.
```

Same rule: enhancement body is one line that delegates to a class.

## Append structure

For extending SAP tables / structures (e.g. adding fields to `MARA`):

`SE11` → SAP table → *Append Structure* → create `ZDSWF_<TABLE>_APP` with the new Z-fields.

Hard rules for appends:
- Field names start with `ZZ` (SAP convention to mark customer fields).
- Data elements custom (`ZDSWF_*`).
- New fields nullable / have sensible defaults; do not break SAP's expectation that the field is empty.

## Forbidden in new development

- **User exits (`USEREXIT_*` FORMs in SAP function groups)** — only modify these for emergency maintenance of existing classic Z-objects that already use them.
- **Customer exits (`CALL CUSTOMER-FUNCTION '001'`)** — same.
- **Modification keys (request from SAP to modify standard)** — never in ZDSWF scope.

If a user/customer exit is the *only* SAP-provided hook for the requirement, flag it: this is a sign SAP code is old. Raise as part of E6 sunset plan — note the SAP improvement needed.

## Output declaration

In `handoff/enhancements-used.md`:

```markdown
# Enhancements used — task <id>

| Type | SAP object | Z-implementation | Filter | Justification |
|---|---|---|---|---|
| New BAdI | <BADI_DEF> | ZCL_ZDSWF_<...>_IMPL | <filter> | <one sentence> |
| Append | MARA | ZDSWF_MARA_APP | n/a | added ZZ-fields for <purpose> |
| Spot | <SPOT_ID> in <SAP fn group> | ZDSWF_<...>_ENHC | n/a | <reason> |

User/customer exits: <none, or list with maintenance ticket reference>
```

## Common mistakes

| Mistake | Fix |
|---|---|
| Business logic inside BAdI implementation method | Delegate to class |
| BAdI impl without filter when filter exists | Set filter — implementations without filter fire for every call |
| Append with non-`ZZ` prefix | Rename per convention |
| Customer exit used in new dev | Use BAdI or spot instead |
| Enhancement spot used to "patch" a bug | Wrong tool — escalate to SAP Note or OSS |
