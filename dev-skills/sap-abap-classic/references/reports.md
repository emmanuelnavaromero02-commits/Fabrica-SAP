# Reports — classical and interactive

For new work in classic: report = executable program with selection screen → call controller class → display ALV. No `WRITE`.

## Skeleton — ALV-based report

```abap
report zdswf_<module>_<name>.

" --- selection screen ---
selection-screen begin of block b1 with frame title text-001.
  parameters:    p_bukrs type bukrs obligatory,
                 p_gjahr type gjahr obligatory default sy-datum+0(4).
  select-options: s_belnr for bkpf-belnr.
selection-screen end of block b1.

" --- types ---
types: begin of ty_output,
         bukrs type bukrs,
         belnr type belnr_d,
         gjahr type gjahr,
         buzei type buzei,
         dmbtr type dmbtr,
         waers type waers,
       end of ty_output,
       tt_output type standard table of ty_output with empty key.

" --- main ---
start-of-selection.
  data(lo_report) = new zcl_zdswf_<name>_report(
    iv_bukrs = p_bukrs
    iv_gjahr = p_gjahr
    it_belnr = s_belnr[] ).

  data(lt_output) = lo_report->execute( ).

  data(lo_alv) = new zcl_zdswf_<name>_alv( lt_output ).
  lo_alv->display( ).
```

The `report` itself has no business logic. It instantiates a controller and dispatches.

## Controller skeleton

```abap
class zcl_zdswf_<name>_report definition
  public final create public.

  public section.
    types: tt_belnr_range type range of belnr_d.

    methods:
      constructor
        importing iv_bukrs type bukrs
                  iv_gjahr type gjahr
                  it_belnr type tt_belnr_range,
      execute returning value(rt_output) type tt_output.

  private section.
    data: mv_bukrs type bukrs,
          mv_gjahr type gjahr,
          mt_belnr type tt_belnr_range.

    methods:
      read raising zcx_zdswf_message,
      transform importing it_raw type table_type
                returning value(rt_output) type tt_output.
endclass.

class zcl_zdswf_<name>_report implementation.

  method constructor.
    mv_bukrs = iv_bukrs.
    mv_gjahr = iv_gjahr.
    mt_belnr = it_belnr.
  endmethod.

  method execute.
    me->read( ).
    rt_output = me->transform( ... ).
  endmethod.

  method read.
    " all SELECTs here. NEVER select * unless every field is needed.
    select bukrs, belnr, gjahr, buzei, dmbtr, waers
      from bseg
      where bukrs = @mv_bukrs
        and gjahr = @mv_gjahr
        and belnr in @mt_belnr
      into table @data(lt_bseg).
    " ...
  endmethod.

  method transform.
    " build output table from raw selection
  endmethod.

endclass.
```

The controller class is unit-tested. The report itself is not (it is a thin shell).

## Selection screen rules

- `OBLIGATORY` on every parameter the logic depends on.
- `DEFAULT` values explicit when sensible.
- `AT SELECTION-SCREEN ON p_bukrs` for cross-field validation — but delegate the check to a class method.
- No business logic in `AT SELECTION-SCREEN`.

## Variant management

If the report supports saved variants, declare them in `SE38` → variants, not in code. The user manages variants; the developer ensures the selection screen is variant-friendly (stable parameter names, no dynamic visibility).

## Background vs dialog

```abap
if sy-batch = 'X'.
  " background-specific: no popups, write progress to job log
  message i001(zdswf) with 'Run started'.
else.
  " dialog: progress bar via sapgui_progress_indicator
endif.
```

`sapgui_progress_indicator` for dialog progress; `message ... with` for background log entries.

## Interactive drill-down — prefer SALV events

If drill-down is needed, do not write `AT LINE-SELECTION` against `WRITE` output. Use `cl_salv_table` events (`link_click`, `double_click`) and call back into the controller.

## Common report mistakes

| Mistake | Effect | Fix |
|---|---|---|
| `SELECT *` | Wastes memory and DB cycles | Explicit field list |
| Nested LOOP with inner SELECT | N+1 queries | Pre-fetch with JOIN or FOR ALL ENTRIES |
| `FOR ALL ENTRIES` without empty-table check | Returns ALL rows when input is empty | Check `lt_filter IS NOT INITIAL` first |
| `READ TABLE ... INTO ... WITH KEY` on unsorted standard table | Linear scan | Use sorted/hashed table |
| Logic in `START-OF-SELECTION` body | Untestable | Delegate to controller class |
| `WRITE` for production output | Banned | ALV |
| Background job with popup | Hangs the job | Suppress with `sy-batch` check |
