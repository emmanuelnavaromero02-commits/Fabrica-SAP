# Integration — BAPI / RFC / IDoc / BDC

Hierarchy: **BAPI > update FM > RFC to external > IDoc > BDC**. BDC is the last resort and requires explicit justification.

## BAPI pattern

```abap
data: lt_return type table of bapiret2,
      ls_header type bapiakopl,
      ls_headerx type bapiakoplx.

ls_header-comp_code = lv_bukrs.
ls_headerx-comp_code = abap_true.

call function 'BAPI_<NAME>_<OP>'
  exporting
    header        = ls_header
    headerx       = ls_headerx
  importing
    return        = lt_return.

" check return table
loop at lt_return into data(ls_ret) where type ca 'EAX'.
  " any error / abort / fatal
  call function 'BAPI_TRANSACTION_ROLLBACK'.
  raise exception type zcx_zdswf_message
    exporting textid = zcx_zdswf_message=>bapi_error
              text   = ls_ret-message.
endloop.

call function 'BAPI_TRANSACTION_COMMIT'
  exporting
    wait = abap_true.       " wait = X is mandatory
```

Hard rules:
- `BAPI_TRANSACTION_COMMIT` with `wait = abap_true` after every successful BAPI write. Never `COMMIT WORK` bare; never `BAPI_TRANSACTION_COMMIT` without `wait`.
- `BAPI_TRANSACTION_ROLLBACK` on error path before raising.
- Inspect `BAPIRET2` for type `E`, `A`, `X` — any of these = failure.
- Type `S`, `I`, `W` are informational; do not commit on `W` without business confirmation.

## Wrapping a BAPI

Always wrap BAPIs in a class method. The method's interface is clean ABAP types; the BAPI's flat structures live inside the method.

```abap
class zcl_zdswf_<doc>_bapi definition public final create public.
  public section.
    methods:
      create importing is_doc           type zdswf_<doc>_s
             returning value(rv_belnr)  type belnr_d
             raising   zcx_zdswf_message.
endclass.

class zcl_zdswf_<doc>_bapi implementation.
  method create.
    " translate is_doc to BAPI flat structures
    " call BAPI
    " on error: rollback + raise
    " on success: commit + return belnr
  endmethod.
endclass.
```

Tests target the wrapper, not the BAPI directly.

## RFC outbound

```abap
call function 'Z_<RFC_NAME>'
  destination 'ZDSWF_<LOGICAL_DEST>'           " logical RFC destination name, never literal
  exporting i_param = lv_param
  importing e_param = lv_result
  exceptions communication_failure = 1 message lv_msg
             system_failure        = 2 message lv_msg
             others                = 3.

if sy-subrc <> 0.
  raise exception type zcx_zdswf_message
    exporting textid = zcx_zdswf_message=>rfc_failure
              text   = lv_msg.
endif.
```

Destinations declared in `SM59` with a `ZDSWF_*` logical name; the code references the logical name only. Production / dev destinations differ in `SM59`, not in code.

## Parallel processing via RFC

```abap
loop at lt_work into data(ls).
  call function 'Z_<PROCESS_ONE>' starting new task |TASK{ sy-tabix }|
    destination in group 'ZDSWF_PARALLEL_GROUP'
    performing on_task_complete on end of task
    exporting i_input = ls.
endloop.

wait until lv_completed >= lines( lt_work ).
```

RFC group set up in `RZ12` by Basis. The agent does not configure groups; it consumes them.

## IDoc — outbound

For new outbound IDoc types:
- Define IDoc type in `WE30` / `WE31` (segment + IDoc type).
- Register message type in `WE81`, link in `WE82`.
- Process via `MASTER_IDOC_DISTRIBUTE` or `IDOC_OUTPUT_<TYPE>`.

The agent wraps `MASTER_IDOC_DISTRIBUTE` in a class:

```abap
data: lt_edidc type table of edidc,
      lt_edidd type table of edidd.

" populate lt_edidc / lt_edidd
call function 'MASTER_IDOC_DISTRIBUTE'
  exporting
    master_idoc_control                  = ls_control
  tables
    communication_idoc_control           = lt_edidc
    master_idoc_data                     = lt_edidd
  exceptions
    error_in_idoc_control                = 1
    error_writing_idoc_status            = 2
    error_in_idoc_data                   = 3
    sending_logical_system_unknown       = 4
    others                               = 5.

if sy-subrc <> 0.
  " handle
endif.

commit work and wait.
```

## IDoc — inbound

For new inbound process codes:
- Define function module in `BD51` with the standard signature (`input_method_<type>`).
- Register in `WE57` (function module / IDoc type / message type / direction).
- Implement in a function module that delegates to a class.

## BDC — last resort

Justification required in `decision-gates.md` G4: explain why no BAPI exists. If a BAPI exists and BDC is being used "for speed" or "for legacy reasons" → STOP, use the BAPI.

If BDC is genuinely required:

```abap
data: lt_bdcdata type table of bdcdata,
      lt_messtab type table of bdcmsgcoll,
      ls_options type ctu_params.

ls_options-dismode = 'N'.    " N=no display, E=error only, A=all
ls_options-updmode = 'S'.    " S=synchronous, A=async (banned), L=local
ls_options-defsize = abap_true.
ls_options-racommit = abap_false.   " don't commit between transactions

" populate lt_bdcdata via append value #( ... ) statements
" — every screen, every field

call transaction 'XX01' using lt_bdcdata
  options from ls_options
  messages into lt_messtab.

if sy-subrc <> 0.
  " inspect lt_messtab for error
  raise exception type zcx_zdswf_message
    exporting textid = zcx_zdswf_message=>bdc_failure.
endif.

commit work and wait.
```

Hard rules for BDC:
- `dismode = 'N'` in production; `'E'` only for diagnosing in dev.
- `updmode = 'S'` synchronous. Async update can cause silent data loss.
- Inspect `lt_messtab` even when `sy-subrc = 0` — partial successes happen.
- Never `racommit = abap_true` — let the caller commit after the full sequence.

## SOAP / REST (classic context)

If integration is to an external system via web service, use `cl_soap_*` (SOAP) or `if_http_client` (REST). For production-grade outbound, register a destination in `SM59` of type `G` (HTTP) or `H` (HTTPS) and reference the logical name.

## Common integration mistakes

| Mistake | Effect | Fix |
|---|---|---|
| BAPI without commit | Silent data loss | `BAPI_TRANSACTION_COMMIT` with `wait` |
| `COMMIT WORK` bare | No update task wait | Always `AND WAIT` |
| RFC hardcoded destination | Breaks on landscape change | Logical destination |
| BDC when BAPI exists | Slow, brittle, hard to test | Use the BAPI |
| Inbound IDoc FM with business logic | Untestable | Delegate to class |
| Outbound IDoc without `MASTER_IDOC_DISTRIBUTE` | Wrong dispatch | Use the standard FM |
| Async BDC | Update task delays cause race conditions | Synchronous |
