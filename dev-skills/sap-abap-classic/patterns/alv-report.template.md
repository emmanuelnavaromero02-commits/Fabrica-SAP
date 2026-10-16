# Pattern: ALV report with SALV + controller class

Three files: the report (shell), the controller class (logic), the ALV class (display).

---

## File: `objects/prog/zdswf_<module>_<name>.prog.abap`

```abap
report zdswf_<module>_<name>.

selection-screen begin of block b1 with frame title text-001.
  parameters:    p_bukrs type bukrs obligatory,
                 p_gjahr type gjahr obligatory default sy-datum+0(4).
  select-options: s_belnr for bkpf-belnr.
selection-screen end of block b1.

start-of-selection.
  try.
      data(lo_report) = new zcl_zdswf_<name>_report(
        iv_bukrs = p_bukrs
        iv_gjahr = p_gjahr
        it_belnr = s_belnr[] ).

      data(lt_data) = lo_report->execute( ).

      new zcl_zdswf_<name>_alv( lt_data )->display( ).

    catch zcx_zdswf_message into data(lx).
      message lx type 'E'.
  endtry.
```

---

## File: `objects/clas/zcl_zdswf_<name>_report.clas.abap`

```abap
"! <p class="shorttext synchronized">Report controller for {{Name}}</p>
class zcl_zdswf_<name>_report definition
  public final create public.

  public section.
    types:
      begin of ty_output,
        bukrs type bukrs,
        belnr type belnr_d,
        gjahr type gjahr,
        buzei type buzei,
        dmbtr type dmbtr,
        waers type waers,
      end of ty_output,
      tt_output type standard table of ty_output with empty key,
      tt_belnr_range type range of belnr_d.

    methods:
      constructor
        importing iv_bukrs type bukrs
                  iv_gjahr type gjahr
                  it_belnr type tt_belnr_range,
      execute
        returning value(rt_output) type tt_output
        raising   zcx_zdswf_message.

  private section.
    data: mv_bukrs type bukrs,
          mv_gjahr type gjahr,
          mt_belnr type tt_belnr_range.

    methods: read returning value(rt_raw) type bseg_t
                  raising   zcx_zdswf_message,
             transform importing it_raw type bseg_t
                       returning value(rt_output) type tt_output.

endclass.

class zcl_zdswf_<name>_report implementation.

  method constructor.
    " authorization check at entry
    authority-check object 'F_BKPF_BUK'
      id 'BUKRS' field iv_bukrs
      id 'ACTVT' field '03'.
    if sy-subrc <> 0.
      raise exception type zcx_zdswf_message
        exporting textid = zcx_zdswf_message=>authorization_failed.
    endif.

    mv_bukrs = iv_bukrs.
    mv_gjahr = iv_gjahr.
    mt_belnr = it_belnr.
  endmethod.

  method execute.
    rt_output = me->transform( me->read( ) ).
  endmethod.

  method read.
    select bukrs, belnr, gjahr, buzei, dmbtr, waers
      from bseg
      where bukrs = @mv_bukrs
        and gjahr = @mv_gjahr
        and ( @mt_belnr is initial or belnr in @mt_belnr )
      into corresponding fields of table @rt_raw.
  endmethod.

  method transform.
    rt_output = corresponding #( it_raw ).
  endmethod.

endclass.
```

---

## File: `objects/clas/zcl_zdswf_<name>_alv.clas.abap`

See `references/alv.md` SALV skeleton — adapt to `ty_output` type and column tweaks.

---

## File: `tests/unit/zcl_zdswf_<name>_report.clas.testclasses.abap`

```abap
class ltc_report definition deferred.
class zcl_zdswf_<name>_report definition local friends ltc_report.

class ltc_report definition final for testing
  duration short risk level harmless.

  private section.
    methods test_transform_basic for testing raising cx_static_check.

endclass.

class ltc_report implementation.
  method test_transform_basic.
    " Construct controller — for unit test, bypass DB by injecting raw data
    " (requires extending the controller with a protected setter or using DI)
    " Then call transform on a known input and assert the output shape.
  endmethod.
endclass.
```

Note: testing the `read` method requires either DI for DB access (preferred) or marking the test class as data-dependent and using `cl_aunit_test_class` with explicit setup/teardown. The pattern above tests `transform` which is pure.
