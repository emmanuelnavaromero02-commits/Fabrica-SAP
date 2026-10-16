# Forms — Adobe Forms preferred, Smartforms maintenance only, no new SAPscript

## Decision

| Need | Tool |
|---|---|
| New form | **Adobe Forms** (Interactive or Print) |
| Existing Smartform, bug fix | Smartform, in place |
| Existing SAPscript | Migrate to Adobe Forms if scope permits; otherwise minimal maintenance |
| New SAPscript | **Forbidden** |

## Adobe Forms — call from ABAP

```abap
data: lv_fm_name type rs38l_fnam.

" 1. Get the generated FM name for the form
call function 'FP_FUNCTION_MODULE_NAME'
  exporting i_name     = 'ZDSWF_<FORM_NAME>'
  importing e_funcname = lv_fm_name.

" 2. Open the form processing job
data: ls_outputparams type sfpoutputparams.
ls_outputparams-nodialog = abap_true.       " no dialog in background
ls_outputparams-preview  = abap_false.

call function 'FP_JOB_OPEN'
  changing ie_outputparams = ls_outputparams
  exceptions cancel = 1 usage_error = 2 system_error = 3 internal_error = 4 others = 5.
if sy-subrc <> 0. " handle " endif.

" 3. Call the generated FM with the form's interface
call function lv_fm_name
  exporting
    /1bcdwb/docparams = ls_docparams
    is_data           = ls_form_data            " your custom interface
  exceptions usage_error = 1 system_error = 2 internal_error = 3 others = 4.
if sy-subrc <> 0. " handle " endif.

" 4. Close the job
call function 'FP_JOB_CLOSE'
  exceptions usage_error = 1 system_error = 2 internal_error = 3 others = 4.
```

Wrap this sequence in a class method. Tests cover data preparation; rendering is integration-tested.

## Form interface

Defined in `SFP` (Adobe Forms) → *Interface* tab. Strongly typed (data elements, structures). The interface is the contract between code and the form layout.

ZDSWF rule: the interface structure is named `ZDSWF_<FORM>_INT_S`. The form is `ZDSWF_<FORM>`. The interface is `ZDSWF_<FORM>_INT`.

## Smartform maintenance

ADT supports Smartform navigation but most editing still happens in `SMARTFORMS` GUI. For bug fixes:
- Change in `SMARTFORMS` GUI.
- Test in development.
- Transport via standard mechanism.
- Document the change in `release-notes.md`.

Do not create new Smartforms in ZDSWF. If the customer insists, raise it in E1/E6 — the system is genuinely classic-only and a sunset plan must accompany the form.

## Output management

Forms attached to output via `NACE` configuration. The agent does not change `NACE` directly; that is a functional config task. Agent provides the form and its calling FM; functional team wires it to `NACE`.

For Output Management on S/4HANA (newer mechanism, sometimes available even in classic-context systems): use the OM framework apps. This is preferred over `NACE` when available.

## Common mistakes

| Mistake | Fix |
|---|---|
| `NODIALOG` not set in background | Job hangs on dialog popup | Set `nodialog = abap_true` |
| `PREVIEW` left as true | Job hangs waiting for preview | Set `preview = abap_false` for background |
| Calling FM directly without `FP_FUNCTION_MODULE_NAME` | Hardcoded generated name breaks on regeneration | Always look up at runtime |
| `FP_JOB_OPEN` without `FP_JOB_CLOSE` | Job-handle leak | Always pair, in `try ... cleanup` if possible |
| Business logic in form layout (calculations in Adobe Forms scripts) | Untestable | Calculate in ABAP, pass result to form |
| New SAPscript | Forbidden | Use Adobe Forms |
