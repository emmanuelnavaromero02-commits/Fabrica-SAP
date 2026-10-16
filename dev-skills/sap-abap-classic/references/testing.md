# Testing — classic context

Classic UI (Dynpros, classical reports) cannot be unit-tested directly. The strategy: extract business logic into classes, unit-test the classes, leave the UI as a thin shell.

## What gets tested

- **Controller classes** for module pools and reports → ABAP Unit, 70% line coverage minimum.
- **BAPI wrappers** → ABAP Unit with mocked BAPI returns via dependency injection or via `cl_aunit_test_class` doubles.
- **BAdI implementations' delegated classes** → ABAP Unit.
- **Calculation / transformation logic** → ABAP Unit.

What does **not** get unit-tested:
- The screen flow logic itself (it is declarative).
- The Dynpro PBO/PAI module bodies (one-liners).
- The selection screen itself.
- SAPscript / Smartform / Adobe Form layouts.

## ABAP Unit skeleton

```abap
class ltc_<controller> definition deferred.
class zcl_zdswf_<name>_ctrl definition local friends ltc_<controller>.

class ltc_<controller> definition final for testing
  duration short
  risk level harmless.

  private section.
    data class_under_test type ref to zcl_zdswf_<name>_ctrl.

    methods:
      setup,
      teardown,

      "! @testing zcl_zdswf_<name>_ctrl~save
      save_happy_path for testing raising cx_static_check,
      "! @testing zcl_zdswf_<name>_ctrl~save
      save_with_lock_conflict for testing raising cx_static_check.

endclass.

class ltc_<controller> implementation.

  method setup.
    " inject dependencies via constructor or setter
    class_under_test = new zcl_zdswf_<name>_ctrl(
      io_persistence = new ltcl_persistence_double( )
      io_locker      = new ltcl_locker_double( ) ).
  endmethod.

  method teardown.
    clear class_under_test.
  endmethod.

  method save_happy_path.
    " given: prepared state
    class_under_test->set_data( value #( ... ) ).

    " when
    class_under_test->save( ).

    " then: persistence double recorded the save
    cl_abap_unit_assert=>assert_equals(
      exp = 1
      act = class_under_test->ms_data->save_count ).
  endmethod.

  method save_with_lock_conflict.
    " given: locker double configured to refuse
    " when / then: exception raised
    try.
        class_under_test->save( ).
        cl_abap_unit_assert=>fail( 'Expected exception' ).
      catch zcx_zdswf_message into data(lx).
        cl_abap_unit_assert=>assert_equals(
          exp = zcx_zdswf_message=>locked_by_other
          act = lx->textid ).
    endtry.
  endmethod.

endclass.
```

## Dependency injection for testability

Classic controllers must accept their dependencies via constructor:

```abap
class zcl_zdswf_<name>_ctrl definition
  public final create public.

  public section.
    interfaces zif_zdswf_<name>_ctrl.

    methods constructor
      importing io_persistence type ref to zif_zdswf_<name>_persistence optional
                io_locker      type ref to zif_zdswf_lock optional.

  private section.
    data: mo_persistence type ref to zif_zdswf_<name>_persistence,
          mo_locker      type ref to zif_zdswf_lock.
endclass.

class zcl_zdswf_<name>_ctrl implementation.
  method constructor.
    mo_persistence = cond #( when io_persistence is bound
                             then io_persistence
                             else new zcl_zdswf_<name>_persistence( ) ).
    mo_locker      = cond #( when io_locker is bound
                             then io_locker
                             else new zcl_zdswf_locker( ) ).
  endmethod.
endclass.
```

Production code calls `new zcl_zdswf_<name>_ctrl( )` and gets defaults. Tests pass doubles.

## Test doubles for FMs

Wrap function modules (BAPIs, RFCs) in classes that implement an interface. The interface is what the controller depends on. Tests substitute a local class implementing the interface.

```abap
interface zif_zdswf_<doc>_bapi public.
  methods create importing is_doc type zdswf_<doc>_s
                 returning value(rv_belnr) type belnr_d
                 raising zcx_zdswf_message.
endinterface.

" Production class wraps the real BAPI.
" Test double inside test include returns canned values.

class ltcl_<doc>_bapi_double definition for testing.
  public section.
    interfaces zif_zdswf_<doc>_bapi.
    data: returned_belnr type belnr_d,
          should_raise   type abap_bool.
endclass.

class ltcl_<doc>_bapi_double implementation.
  method zif_zdswf_<doc>_bapi~create.
    if should_raise = abap_true.
      raise exception type zcx_zdswf_message.
    endif.
    rv_belnr = returned_belnr.
  endmethod.
endclass.
```

## Coverage measurement

ADT → right-click test class → *Run As* → *ABAP Unit Test With Coverage*. Export to `handoff/test-results.txt`.

Threshold for classic: **70% line coverage on controller classes**. Lower than cloud's 80% because module pool screens contribute non-testable lines. The threshold applies to the classes, not the module pool itself.

## Integration tests

For end-to-end classic transaction flows: `eCATT` or third-party (Tricentis, Worksoft). Out of agent scope; the agent provides the controller class with stable interfaces, the integration test team builds the harness.

## Common mistakes

| Mistake | Fix |
|---|---|
| Controller class has hidden dependency on `sy-uname` / global state | Inject context |
| BAPI called directly from controller | Wrap in interface, inject |
| Test depends on test execution order | Each test sets up own state |
| `select` in test against productive DB | Use double |
| Module body has logic that isn't covered by any test | Move logic to class |
| Coverage measured against module pool, not controller | Measure against controller class only |
