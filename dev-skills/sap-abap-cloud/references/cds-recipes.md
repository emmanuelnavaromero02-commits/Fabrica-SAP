# CDS recipes — view entities for ZDSWF

`DEFINE VIEW ENTITY` only. No legacy `DEFINE VIEW`.

## Skeleton — interface view

```abap
@AccessControl.authorizationCheck: #CHECK
@EndUserText.label: '<entity> interface view'
@Metadata.allowExtensions: true
@VDM.viewType: #BASIC
define view entity ZDSWF_I_<Entity>
  as select from zdswf_<entity>
{
  key <entity>_uuid    as <Entity>Uuid,
      field1           as Field1,
      field2           as Field2,
      created_by       as CreatedBy,
      created_at       as CreatedAt,
      last_changed_by  as LastChangedBy,
      last_changed_at  as LastChangedAt
}
```

## Skeleton — consumption view (Fiori UI)

Uses `provider contract transactional_query` for RAP-backed UI, or `analytical_query` for ALP.

```abap
@AccessControl.authorizationCheck: #CHECK
@EndUserText.label: '<Entity> consumption'
@Metadata.allowExtensions: true
@Search.searchable: true
define root view entity ZDSWF_C_<Entity>
  provider contract transactional_query
  as projection on ZDSWF_R_<Entity>
{
  key <Entity>Uuid,
      @Search.defaultSearchElement: true
      @Search.fuzzinessThreshold: 0.8
      Field1,
      Field2
}
```

## Associations vs compositions

| Concept | Use |
|---|---|
| `composition [0..*] of` | Parent owns children. Children deleted with parent. Same BO. |
| `association [0..*] to` | Read-only link to another BO or master data. Different lifecycle. |
| `association to parent` | Child view's back-reference to parent. Required in composition trees. |

Composition example in root:
```abap
composition [0..*] of ZDSWF_R_<Entity>Item as _Items
```

Association example:
```abap
association [0..1] to I_Customer as _Customer on $projection.CustomerId = _Customer.Customer
```

## Currency and quantity — mandatory annotations

Without these, Fiori renders amounts wrong:

```abap
@Semantics.amount.currencyCode: 'CurrencyCode'
amount as Amount,
@Semantics.currencyCode: true
currency_code as CurrencyCode,

@Semantics.quantity.unitOfMeasure: 'UnitOfMeasure'
quantity as Quantity,
@Semantics.unitOfMeasure: true
unit_of_measure as UnitOfMeasure
```

## Currency conversion

```abap
currency_conversion(
  amount             => Amount,
  source_currency    => CurrencyCode,
  target_currency    => cast('USD' as abap.cuky),
  exchange_rate_date => $session.system_date,
  exchange_rate_type => cast('M' as abap.char(4)),
  error_handling     => 'SET_TO_NULL'
) as AmountInUsd
```

## Text and language

```abap
@ObjectModel.text.element: ['StatusText']
status as Status,
_Status._Text.StatusText as StatusText
```

Text view association:
```abap
association [1..*] to ZDSWF_I_StatusText as _Status on $projection.Status = _Status.Status
```

`_Text` is a localized association inside the text view itself.

## Parameters

```abap
define view entity ZDSWF_I_<Entity>WithParams
  with parameters
    p_company_code : bukrs,
    p_date         : datn
  as select from zdswf_<entity>
{
  key <entity>_uuid as <Entity>Uuid,
      ...
}
where company_code = $parameters.p_company_code
  and valid_from  <= $parameters.p_date
```

## Aggregations (analytical)

```abap
@Analytics.dataCategory: #CUBE
define view entity ZDSWF_I_<Entity>Cube
  as select from ZDSWF_I_<Entity>
{
  @Analytics.dimension: true
  key CompanyCode,
  @Analytics.dimension: true
  key FiscalYear,
  @Analytics.measure: true
  @DefaultAggregation: #SUM
  Amount,
  CurrencyCode
}
group by CompanyCode, FiscalYear, CurrencyCode
```

## Table function (AMDP — only when CDS expression cannot)

```abap
@EndUserText.label: '<purpose>'
@ClientHandling.algorithm: #SESSION_VARIABLE
define table function ZDSWF_TF_<Name>
  with parameters
    @Environment.systemField: #CLIENT
    p_client : abap.clnt
  returns {
    client : abap.clnt;
    <fields>
  }
  implemented by method zcl_zdswf_tf_<name>=>compute;
```

AMDP class:
```abap
class zcl_zdswf_tf_<name> definition public final create public.
  public section.
    interfaces if_amdp_marker_hdb.
    class-methods compute
      for table function ZDSWF_TF_<Name>.
endclass.

class zcl_zdswf_tf_<name> implementation.
  method compute by database function for hdb language sqlscript options read-only.
    return select :p_client as client, ...
           from   ...
           where  ...;
  endmethod.
endclass.
```

Hard rules:
- Only when CDS native cannot do it (recursive logic, complex windowing, custom HANA functions).
- Marker interface `if_amdp_marker_hdb`.
- `options read-only` mandatory.
- Tested via ABAP Unit with `cl_cds_test_environment`.

## DCL — Data Control Language (access control)

`zdswf_i_<entity>.dcls`:
```abap
@EndUserText.label: '<Entity> access control'
@MappingRole: true
define role ZDSWF_I_<Entity> {
  grant select on ZDSWF_I_<Entity>
    where ( CompanyCode ) = aspect pfcg_auth ( Z_ZDSWF_<E>, BUKRS, ACTVT = '03' );
}
```

Required for every view that backs a service. Without DCL, the binding is rejected at activation.

Variants:
- `inherit ZDSWF_I_Parent` — inherit rules from another view.
- `where ... = aspect user` — bind to current user.
- `where ... = aspect pfcg_auth(...)` — bind to authorization object.
- `bypass when #SQL_DDIC` — for ABAP-only reads via privileged.

## Search

```abap
@Search.searchable: true
define view entity ... {
  @Search.defaultSearchElement: true
  @Search.fuzzinessThreshold: 0.8
  field
}
```

Default search element on at least one field; without it, FE search bar does nothing.

## Annotations cheat-sheet

| Annotation | Purpose |
|---|---|
| `@AccessControl.authorizationCheck` | `#CHECK` enforces DCL; `#NOT_REQUIRED` only for technical views |
| `@VDM.viewType` | `#BASIC` (raw), `#COMPOSITE` (joined), `#CONSUMPTION` (UI/API) |
| `@Metadata.allowExtensions` | Allow metadata extensions (`.ddlx`) — set to `true` |
| `@Semantics.*` | Field semantics (amount, quantity, user, date) |
| `@ObjectModel.text.element` | Wire a text field to a key |
| `@Analytics.*` | Analytical metadata (dimensions, measures) |
| `@Search.*` | Search behavior |
| `@EndUserText.label` | Human-readable label |
| `@UI.*` | UI annotations — put these in `.ddlx`, not in CDS |

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| `View not authorization-checked` warning | Missing `@AccessControl.authorizationCheck` or DCL | Add both |
| FE shows raw amount without currency | Missing `@Semantics.amount.currencyCode` | Add semantics |
| `_assoc` not navigable in FE | Association without `to composition child` redirect in projection | Add `redirected to composition child` |
| `Field not found` in metadata extension | UI annotation references field not in projection | Add field to projection or move annotation |
| Aggregation returns wrong values | Missing `@DefaultAggregation` or wrong `@Analytics.dimension` | Annotate correctly, regenerate |
