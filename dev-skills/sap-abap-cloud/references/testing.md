# Testing — ABAP Unit, CDS test environment, RAP doubles

Hard rule: every BIL class, every action, every validation, every determination has at least one ABAP Unit test. CDS views with logic get `cl_cds_test_environment` tests. 80% line coverage on `ZDSWF_*` to pass handoff.

## ABAP Unit — BIL class test skeleton

```abap
class ltc_<entity> definition deferred.
class zdswf_bp_<entity> definition local friends ltc_<entity>.

class ltc_<entity> definition final for testing
  risk level harmless
  duration short.

  private section.
    class-data:
      class_environment type ref to if_cds_test_environment.

    data:
      class_under_test type ref to lhc_<entity>,
      behv_environment type ref to if_abap_behv_test_environment.

    class-methods:
      class_setup raising cx_static_check,
      class_teardown.

    methods:
      setup raising cx_static_check,
      teardown,

      "! @testing zdswf_bp_<entity>~validateBusinessField
      validate_business_field_ok for testing raising cx_static_check,
      "! @testing zdswf_bp_<entity>~validateBusinessField
      validate_business_field_fail for testing raising cx_static_check,

      "! @testing zdswf_bp_<entity>~Approve
      approve_happy_path for testing raising cx_static_check.

endclass.

class ltc_<entity> implementation.

  method class_setup.
    behv_environment = cl_abap_behv_test_environment=>create(
      handler_for_managed_bos = value #(
        ( name = 'ZDSWF_R_<ENTITY>' )
      )
    ).
  endmethod.

  method class_teardown.
    behv_environment->cleanup( ).
  endmethod.

  method setup.
    behv_environment->clear_doubles( ).
  endmethod.

  method teardown.
    rollback entities.
  endmethod.

  method validate_business_field_ok.
    " Given: an entity with a valid BusinessField
    data(test_uuid) = cl_system_uuid=>create_uuid_x16_static( ).
    modify entities of ZDSWF_R_<Entity>
      entity <Entity>
        create from value #(
          ( %cid = 'ENT1'
            %data = value #(
              <Entity>Uuid = test_uuid
              <BusinessField> = 'VALID'
              Amount = '100.00'
              CurrencyCode = 'USD' ) ) )
      mapped data(mapped)
      failed data(failed)
      reported data(reported).

    " When: validation runs (at save)
    commit entities responses
      failed data(commit_failed)
      reported data(commit_reported).

    " Then: no failures
    cl_abap_unit_assert=>assert_initial( failed-<entity> ).
    cl_abap_unit_assert=>assert_initial( commit_failed-<entity> ).
  endmethod.

  method validate_business_field_fail.
    " Given: an entity with an empty BusinessField
    data(test_uuid) = cl_system_uuid=>create_uuid_x16_static( ).
    modify entities of ZDSWF_R_<Entity>
      entity <Entity>
        create from value #(
          ( %cid = 'ENT1'
            %data = value #(
              <Entity>Uuid = test_uuid
              <BusinessField> = ''      " invalid: empty
              Amount = '100.00'
              CurrencyCode = 'USD' ) ) )
      failed data(failed)
      reported data(reported).

    commit entities responses
      failed data(commit_failed).

    " Then: failure registered
    cl_abap_unit_assert=>assert_not_initial( commit_failed-<entity> ).
  endmethod.

  method approve_happy_path.
    " Set up an active entity, call action, verify state change.
    " ...
  endmethod.

endclass.
```

## CDS unit test

For views with logic (case, computed fields, parameters):

```abap
class ltc_<view> definition final for testing
  duration short
  risk level harmless.

  private section.
    class-data: environment type ref to if_cds_test_environment.

    class-methods: class_setup raising cx_static_check,
                   class_teardown.

    methods: setup raising cx_static_check,
             teardown,
             test_computed_field for testing raising cx_static_check.

endclass.

class ltc_<view> implementation.

  method class_setup.
    environment = cl_cds_test_environment=>create(
      i_for_entity = 'ZDSWF_I_<View>'
    ).
  endmethod.

  method class_teardown.
    environment->cleanup( ).
  endmethod.

  method setup.
    environment->clear_doubles( ).
  endmethod.

  method teardown.
    rollback entities.
  endmethod.

  method test_computed_field.
    " Given: test data inserted as double for the source tables of the view
    environment->insert_test_data( i_data = value zdswf_<entity>_tab(
      ( client = sy-mandt <entity>_uuid = '00...01' <field> = 'X' )
    ) ).

    " When: query the view
    select * from ZDSWF_I_<View> into table @data(result).

    " Then: assert
    cl_abap_unit_assert=>assert_equals(
      exp = 1
      act = lines( result ) ).
  endmethod.

endclass.
```

## RAP test doubles — for cross-BO interactions

When your BO calls another via released APIs and you want to test in isolation:

```abap
data(doubles) = cl_abap_behv_test_environment=>create(
  handler_for_managed_bos = value #(
    ( name = 'I_OTHER_BO' )       " stub the other BO
    ( name = 'ZDSWF_R_<ENTITY>' ) " managed BO under test
  )
).

" Configure the double's behavior
doubles->double( 'I_OTHER_BO' )->configure_read( ... ).
```

## Coverage measurement

In ADT: right-click test class → *Run As* → *ABAP Unit Test With Coverage*. Export coverage report → save to `handoff/test-results.txt`.

Threshold: **80% line coverage on `ZDSWF_*` package contents**. Below threshold = `check_handoff.py` blocks.

Branches: aim for 60% branch coverage on BIL classes. Not enforced as hard gate but flagged.

## What does not need a test

- CDS views that are pure projections (no logic).
- Service definitions (no logic).
- BDEFs (declarative).
- Generated metadata extensions.

Everything else: tested.

## Common test mistakes

| Mistake | Fix |
|---|---|
| `SELECT FROM zdswf_<entity>` in test | Use test environment doubles |
| Asserting on the database after action | Use `read entities` with `in local mode` instead |
| No `rollback entities` in teardown | Add it — state leaks between tests |
| Testing through the OData endpoint | Wrong level — that is integration; unit tests use BIL/behv environment |
| Hard-coded UUIDs | Generate fresh per test via `cl_system_uuid` |
| Sleeping / waiting | Forbidden — test is sync |
| Test depends on test execution order | Forbidden — each test sets up own state |
