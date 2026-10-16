# ALV — CL_SALV_TABLE first

Rule: new standalone ALV uses `cl_salv_table`. `cl_gui_alv_grid` only when embedded in a Dynpro custom container where SALV cannot run. `REUSE_ALV_*` function modules forbidden in new code.

## SALV skeleton

```abap
class zcl_zdswf_<name>_alv definition
  public final create public.

  public section.
    methods:
      constructor importing it_data type tt_output,
      display.

  private section.
    data: mt_data type tt_output,
          mo_alv  type ref to cl_salv_table.

    methods:
      build raising cx_salv_error,
      set_columns,
      set_functions,
      set_layout,
      on_link_click for event link_click of cl_salv_events_table
        importing row column.
endclass.

class zcl_zdswf_<name>_alv implementation.

  method constructor.
    mt_data = it_data.
  endmethod.

  method display.
    try.
        me->build( ).
        me->set_columns( ).
        me->set_functions( ).
        me->set_layout( ).
        mo_alv->display( ).
      catch cx_salv_error into data(lx).
        message lx type 'E'.
    endtry.
  endmethod.

  method build.
    cl_salv_table=>factory(
      importing r_salv_table = mo_alv
      changing  t_table      = mt_data ).
  endmethod.

  method set_columns.
    data(lo_cols) = mo_alv->get_columns( ).
    lo_cols->set_optimize( abap_true ).

    " column-specific tweaks
    try.
        data(lo_col_dmbtr) = lo_cols->get_column( 'DMBTR' ).
        lo_col_dmbtr->set_long_text( 'Amount in local currency' ).
        lo_col_dmbtr->set_short_text( 'Amount' ).
      catch cx_salv_not_found.
    endtry.
  endmethod.

  method set_functions.
    data(lo_funcs) = mo_alv->get_functions( ).
    lo_funcs->set_all( abap_true ).

    " events
    data(lo_events) = mo_alv->get_event( ).
    set handler me->on_link_click for lo_events.
  endmethod.

  method set_layout.
    data(lo_layout) = mo_alv->get_layout( ).
    lo_layout->set_key( value #( report = sy-repid ) ).
    lo_layout->set_save_restriction( cl_salv_layout=>restrict_none ).
    lo_layout->set_default( abap_true ).
  endmethod.

  method on_link_click.
    " drill-down to detail
    read table mt_data into data(ls) index row.
    if sy-subrc = 0.
      " open detail view, call BAPI, navigate, etc.
    endif.
  endmethod.

endclass.
```

## Hierarchical / tree ALV

`cl_salv_hierseq_table` for hierarchical sequential, `cl_salv_tree` for tree. Same factory pattern.

## ALV in Dynpro (when SALV won't work)

Embedded in a Dynpro custom container, you use `cl_gui_alv_grid`:

```abap
data: go_container type ref to cl_gui_custom_container,
      go_grid      type ref to cl_gui_alv_grid.

" PBO of the screen, first time only:
if go_container is initial.
  go_container = new cl_gui_custom_container( container_name = 'CC_ALV' ).
  go_grid      = new cl_gui_alv_grid( i_parent = go_container ).

  go_grid->set_table_for_first_display(
    exporting
      is_layout       = value lvc_s_layo( zebra = abap_true sel_mode = 'A' )
    changing
      it_outtab       = mt_data
      it_fieldcatalog = lt_fcat ).
endif.

" subsequent PBOs:
go_grid->refresh_table_display( ).
```

Use this only when SALV's lack of custom container support forces it.

## Forbidden

- `REUSE_ALV_GRID_DISPLAY`, `REUSE_ALV_LIST_DISPLAY`, `REUSE_ALV_FIELDCATALOG_MERGE` — all banned in new code.
- Modifying field catalog manually entry by entry when factory + `set_optimize` + per-column tweaks does the same.
- `cl_gui_alv_grid` outside a Dynpro context.

## Layout management

`set_key` ties layouts to a key (typically `sy-repid`). Users save layouts per report. `set_default( abap_true )` allows a default layout. `set_save_restriction` controls user vs global save.

## Export

`cl_salv_table` supports export to spreadsheet, HTML, XML out of the box. No custom code needed for "download to Excel" — it is a standard ALV function.

## Common mistakes

| Mistake | Fix |
|---|---|
| Field catalog hand-built | Let factory derive it, tweak per column |
| Forgot `set_optimize` | Columns too wide |
| No layout key | Users can't save personal layouts |
| Catching `cx_salv_error` and swallowing | Always surface to user via `message ... type 'E'` or log |
| `cl_gui_alv_grid` for a standalone report | Use SALV |
