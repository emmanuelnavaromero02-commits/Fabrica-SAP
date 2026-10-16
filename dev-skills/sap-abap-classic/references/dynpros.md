# Dynpros and module pools

Only when G1 selected `module-pool` and E2 confirmed Fiori Elements is not viable. Otherwise refactor or escalate.

## Architecture — thin screen, fat controller

```
Module pool SAPMZDSWF_<MODULE>_<NAME>
  ├─ Screen 0100 (Dynpro)
  │   flow logic calls modules in TOP include
  └─ Modules → delegate immediately to controller class

Controller class ZCL_ZDSWF_<NAME>_CTRL
  - All business logic
  - All data access
  - ABAP Unit tested
```

Module bodies are 1–3 lines. Anything longer goes in a class method.

## Includes layout

```
SAPMZDSWF_<MODULE>_<NAME>          " main module pool
  ZDSWF_<MODULE>_<NAME>TOP          " global data, controller instance
  ZDSWF_<MODULE>_<NAME>O01          " PBO modules (output)
  ZDSWF_<MODULE>_<NAME>I01          " PAI modules (input)
  ZDSWF_<MODULE>_<NAME>F01          " forms (minimal — delegate to class)
```

## TOP include

```abap
program SAPMZDSWF_<MODULE>_<NAME>.

tables: <only what the screen runtime needs>.

data: gv_okcode      type sy-ucomm,
      go_controller  type ref to zcl_zdswf_<name>_ctrl.

initialization.
  go_controller = new zcl_zdswf_<name>_ctrl( ).
```

## PBO module template

```abap
module status_0100 output.
  set pf-status 'STATUS_0100'.
  set titlebar 'TITLE_0100'.
  clear gv_okcode.                          " always clear OK_CODE in PBO
  go_controller->prepare_screen_data( ).
endmodule.
```

## PAI module template

```abap
module user_command_0100 input.
  data(lv_cmd) = gv_okcode.                 " copy SY-UCOMM / OK_CODE
  clear gv_okcode.

  case lv_cmd.
    when 'SAVE'.
      go_controller->save( ).
    when 'REFRESH'.
      go_controller->refresh( ).
    when others.
      " let other modules handle
  endcase.
endmodule.

module exit_command_0100 input at exit-command.
  case gv_okcode.
    when 'BACK' or 'EXIT' or 'CANC'.
      go_controller->discard_changes( ).
      leave to screen 0.                    " not STOP, not EXIT
  endcase.
endmodule.
```

`at exit-command` modules bypass field validation — they let the user cancel even when a mandatory field is empty.

## Field validation with CHAIN

In flow logic:

```
process before output.
  module status_0100.

process after input.
  module exit_command_0100 at exit-command.

  chain.
    field: gs_screen-field_a,
           gs_screen-field_b.
    module validate_a_and_b on chain-request.
  endchain.

  module user_command_0100.
```

If `validate_a_and_b` flags an error, both `field_a` and `field_b` are re-prompted together. Without `chain`, only the last field is re-prompted.

## Controller class skeleton

```abap
"! <p class="shorttext synchronized">{{Name}} screen controller</p>
class zcl_zdswf_<name>_ctrl definition
  public
  final
  create public.

  public section.
    methods:
      constructor,
      prepare_screen_data,
      save raising zcx_zdswf_message,
      discard_changes,
      refresh.

  private section.
    data: ms_data   type zdswf_<name>_screen_s,
          mt_locks  type table of zdswf_<name>_lock.

    methods:
      enqueue raising zcx_zdswf_message,
      dequeue,
      read_data,
      validate raising zcx_zdswf_message,
      persist  raising zcx_zdswf_message.
endclass.

class zcl_zdswf_<name>_ctrl implementation.

  method constructor.
    me->read_data( ).
  endmethod.

  method prepare_screen_data.
    " populate screen variables from ms_data
  endmethod.

  method save.
    me->enqueue( ).
    try.
        me->validate( ).
        me->persist( ).
        commit work and wait.
        me->dequeue( ).
        message s001(zdswf) with 'Saved'.
      catch zcx_zdswf_message into data(lx).
        rollback work.
        me->dequeue( ).
        raise exception lx.
    endtry.
  endmethod.

  method discard_changes.
    me->dequeue( ).
  endmethod.

  method enqueue.
    call function 'ENQUEUE_EZDSWF_<NAME>'
      exporting
        mode_zdswf_<name> = 'E'
        mandt             = sy-mandt
        " key fields
      exceptions
        foreign_lock      = 1
        system_failure    = 2
        others            = 3.
    if sy-subrc <> 0.
      raise exception type zcx_zdswf_message
        exporting textid = zcx_zdswf_message=>locked_by_other.
    endif.
  endmethod.

  method dequeue.
    call function 'DEQUEUE_EZDSWF_<NAME>'
      exporting
        mandt = sy-mandt
        " key fields
        _scope = '1'.
  endmethod.

  " read_data, validate, persist implementations...

endclass.
```

The controller is unit-testable. The Dynpro is not (and does not need to be) — its only role is to render data and dispatch.

## Common screen mistakes

| Mistake | Effect | Fix |
|---|---|---|
| Forgot `clear gv_okcode` in PBO | Phantom commands trigger on next user action | Clear in PBO |
| Business logic in `MODULE` body | Untestable, brittle | Delegate to controller |
| Cancel module without `at exit-command` | User cannot exit if mandatory field empty | Add `at exit-command` |
| No `CHAIN` for related fields | Only one field re-prompted on error | Wrap in `chain ... endchain` |
| `STOP` to exit | Leaves transaction open in some flows | `leave to screen 0` |
| Hardcoded screen 0100 in `CALL SCREEN` | Hard to refactor | Constant in TOP |
| Forgot to dequeue on error path | Lock leaks | Dequeue in all exit paths including `endtry` |
