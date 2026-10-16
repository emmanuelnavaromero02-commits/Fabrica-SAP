# RAP skeleton — managed BO with draft

Canonical skeleton for the ZDSWF factory. Read after `decision-gates.md` has been answered. Substitute `{{...}}` placeholders with values from the gates file.

## The five layers

```
Data Definition  →  ZDSWF_R_<Entity>           (.ddls)   Persistent CDS root + child views
Behavior         →  ZDSWF_R_<Entity>           (.bdef)   Managed BDEF, strict(2), with draft
Behavior Impl    →  ZDSWF_BP_<Entity>          (.clas)   BIL: validations, determinations, actions
Projection       →  ZDSWF_C_<Entity>           (.ddls + .ddlx)  Consumption view + UI annotations
                 →  ZDSWF_C_<Entity>           (.bdef)   Projection BDEF
Service          →  ZDSWF_SD_<Name>            (.srvd)   Service definition
                 →  ZDSWF_SB_<Name>            (.srvb)   OData V4 binding
```

Names use the SAP convention reinterpreted for ZDSWF:
- `R_` = root data definition (persistent)
- `C_` = consumption (projection)
- `BP_` = behavior pool (BIL class)
- `SD_` / `SB_` = service definition / binding

---

## 1. Custom table (persistent storage)

`zdswf_<entity>.tabl`:

```abap
@EndUserText.label : 'ZDSWF <Entity> Table'
@AbapCatalog.enhancement.category : #NOT_EXTENSIBLE
@AbapCatalog.tableCategory : #TRANSPARENT
@AbapCatalog.deliveryClass : #A
@AbapCatalog.dataMaintenance : #LIMITED
define table zdswf_<entity> {
  key client       : mandt not null;
  key <entity>_uuid : sysuuid_x16 not null;
  parent_uuid      : sysuuid_x16;       " for child tables; null in root
  <business fields> : <typed via data elements>;
  " audit fields — every ZDSWF table includes these
  created_by       : abp_creation_user;
  created_at       : abp_creation_tstmpl;
  last_changed_by  : abp_locinst_lastchange_user;
  last_changed_at  : abp_locinst_lastchange_tstmpl;
  local_last_changed_at : abp_lastchange_tstmpl;
}
```

Notes:
- `client` always first key for `#A` delivery class.
- UUID via `sysuuid_x16` for managed early numbering.
- Audit fields use the SAP-delivered data elements `abp_*`; the framework populates them automatically when the BDEF maps them.

---

## 2. Root data definition

`zdswf_r_<entity>.ddls`:

```abap
@AccessControl.authorizationCheck: #CHECK
@EndUserText.label: 'ZDSWF <Entity> Root'
@Metadata.allowExtensions: true
define root view entity ZDSWF_R_<Entity>
  as select from zdswf_<entity>
  composition [0..*] of ZDSWF_R_<Entity>Item as _Items
{
  key <entity>_uuid as <Entity>Uuid,
      @Semantics.businessDate.from: true
      valid_from        as ValidFrom,
      <business_field>  as <BusinessField>,
      @Semantics.amount.currencyCode: 'CurrencyCode'
      amount            as Amount,
      currency_code     as CurrencyCode,
      @Semantics.user.createdBy: true
      created_by        as CreatedBy,
      @Semantics.systemDateTime.createdAt: true
      created_at        as CreatedAt,
      @Semantics.user.lastChangedBy: true
      last_changed_by   as LastChangedBy,
      @Semantics.systemDateTime.lastChangedAt: true
      last_changed_at   as LastChangedAt,
      @Semantics.systemDateTime.localInstanceLastChangedAt: true
      local_last_changed_at as LocalLastChangedAt,
      _Items
}
```

Item view `zdswf_r_<entity>item.ddls` similar, with `association to parent ZDSWF_R_<Entity> as _<Entity>`.

---

## 3. Behavior Definition (BDEF)

`zdswf_r_<entity>.bdef`:

```abap
managed implementation in class zdswf_bp_<entity> unique;
strict ( 2 );
with draft;

define behavior for ZDSWF_R_<Entity> alias <Entity>
persistent table zdswf_<entity>
draft table zdswf_d_<entity>
lock master
total etag LocalLastChangedAt
authorization master ( instance )
etag master LocalLastChangedAt
{
  field ( numbering : managed, readonly ) <Entity>Uuid;
  field ( readonly ) CreatedBy, CreatedAt, LastChangedBy, LastChangedAt, LocalLastChangedAt;
  field ( mandatory ) <BusinessField>;

  create;
  update;
  delete;

  association _Items { create; with draft; }

  draft action Edit;
  draft action Activate optimized;
  draft action Discard;
  draft action Resume;
  draft determine action Prepare {
    validation ValidateBusinessField;
  }

  validation ValidateBusinessField on save { create; update; field <BusinessField>; }
  determination CalculateDerivedFields on modify { field <BusinessField>; }

  action ( features : instance ) Approve result [1] $self;

  mapping for zdswf_<entity>
  {
    <Entity>Uuid          = <entity>_uuid;
    <BusinessField>       = <business_field>;
    Amount                = amount;
    CurrencyCode          = currency_code;
    CreatedBy             = created_by;
    CreatedAt             = created_at;
    LastChangedBy         = last_changed_by;
    LastChangedAt         = last_changed_at;
    LocalLastChangedAt    = local_last_changed_at;
  }
}

define behavior for ZDSWF_R_<Entity>Item alias <Entity>Item
persistent table zdswf_<entity>item
draft table zdswf_d_<entity>item
lock dependent by _<Entity>
authorization dependent by _<Entity>
etag master LocalLastChangedAt
{
  field ( readonly ) <Entity>Uuid, ItemUuid;
  update;
  delete;

  association _<Entity> { with draft; }

  mapping for zdswf_<entity>item
  {
    <Entity>Uuid = <entity>_uuid;
    ItemUuid     = item_uuid;
    ...
  }
}
```

Hard requirements:
- `strict ( 2 )` minimum.
- `with draft` (G3 was yes).
- `lock master` on root, `lock dependent by _<Entity>` on children.
- `authorization master ( instance )` on root if records are user/org scoped.
- Mapping section is mandatory; no field-name shortcuts.

---

## 4. Behavior Implementation (BIL)

`zdswf_bp_<entity>.clas.abap`:

```abap
"! <p class="shorttext synchronized">Behavior pool for ZDSWF_R_&lt;Entity&gt;</p>
class zdswf_bp_<entity> definition
  public
  abstract
  final
  for behavior of ZDSWF_R_<Entity>.
endclass.

class zdswf_bp_<entity> implementation.
endclass.
```

Then local implementation classes per behavior block:

```abap
class lhc_<entity> definition inheriting from cl_abap_behavior_handler.

  private section.

    methods validateBusinessField for validate on save
      importing keys for <Entity>~ValidateBusinessField.

    methods calculateDerivedFields for determine on modify
      importing keys for <Entity>~CalculateDerivedFields.

    methods Approve for modify
      importing keys for action <Entity>~Approve result result.

    methods get_instance_features for instance features
      importing keys request requested_features for <Entity> result result.

    methods get_instance_authorizations for instance authorization
      importing keys request requested_authorizations for <Entity> result result.

endclass.

class lhc_<entity> implementation.

  method validateBusinessField.
    " Read current state of the entities being validated.
    read entities of ZDSWF_R_<Entity> in local mode
      entity <Entity>
        fields ( <BusinessField> Amount CurrencyCode )
        with corresponding #( keys )
      result data(entities).

    loop at entities into data(entity).
      " Business rule example.
      if entity-<BusinessField> is initial.
        append value #( %tky = entity-%tky ) to failed-<entity>.
        append value #( %tky = entity-%tky
                        %msg = new zcx_zdswf_message(
                                 textid   = zcx_zdswf_message=>field_required
                                 severity = if_abap_behv_message=>severity-error )
                        %element-<businessfield> = if_abap_behv=>mk-on
                      ) to reported-<entity>.
      endif.
    endloop.
  endmethod.

  method calculateDerivedFields.
    " Determination logic.
  endmethod.

  method Approve.
    " Action implementation. Use modify entities to update state.
    modify entities of ZDSWF_R_<Entity> in local mode
      entity <Entity>
        update set fields with
        value #( for k in keys ( %tky = k-%tky
                                 ApprovalStatus = 'A' ) )
      reported data(update_reported)
      failed data(update_failed).

    " Re-read for the result table.
    read entities of ZDSWF_R_<Entity> in local mode
      entity <Entity>
        all fields with corresponding #( keys )
      result data(approved).

    result = value #( for a in approved ( %tky = a-%tky %param = a ) ).
  endmethod.

endclass.
```

Hard requirements:
- Use `in local mode` for internal reads/writes within the BIL.
- Use `%tky` (transactional key) for stability with draft + active records.
- Errors via `failed-<entity>` + `reported-<entity>`. Never `RAISE EXCEPTION` from a BIL handler that bubbles to the framework.
- All exception classes used in messages must be `ZCX_ZDSWF_*` extending `cx_abap_behv_error` or via `T100_DYN_MSG` pattern.

---

## 5. Projection (consumption) layer

`zdswf_c_<entity>.ddls`:

```abap
@AccessControl.authorizationCheck: #CHECK
@EndUserText.label: 'ZDSWF <Entity> Projection'
@Metadata.allowExtensions: true
@Search.searchable: true
define root view entity ZDSWF_C_<Entity>
  provider contract transactional_query
  as projection on ZDSWF_R_<Entity>
{
  key <Entity>Uuid,
      @Search.defaultSearchElement: true
      <BusinessField>,
      Amount,
      CurrencyCode,
      ApprovalStatus,
      CreatedBy,
      CreatedAt,
      LastChangedBy,
      LastChangedAt,
      LocalLastChangedAt,
      _Items : redirected to composition child ZDSWF_C_<Entity>Item
}
```

Projection BDEF:

```abap
projection;
strict ( 2 );
use draft;

define behavior for ZDSWF_C_<Entity> alias <Entity>
{
  use create;
  use update;
  use delete;

  use association _Items { create; with draft; }

  use action Approve;
  use action Edit;
  use action Activate;
  use action Discard;
  use action Resume;
  use action Prepare;
}
```

Metadata extension `zdswf_c_<entity>.ddlx` — see `fiori-annotations.md`.

---

## 6. Service definition + binding

`zdswf_sd_<name>.srvd`:

```abap
@EndUserText.label: 'ZDSWF <Name> Service'
define service ZDSWF_SD_<Name> {
  expose ZDSWF_C_<Entity>     as <Entity>;
  expose ZDSWF_C_<Entity>Item as <Entity>Item;
  " value helps as needed
}
```

Service binding `zdswf_sb_<name>.srvb`:

```
Type: OData V4 - UI (or OData V4 - Web API depending on use case)
Binding: ZDSWF_SB_<Name>
```

---

## Common errors and what they mean

| Error / symptom | Cause | Fix |
|---|---|---|
| `Field xxx is read-only` on create | `field ( readonly )` includes a field the UI is trying to set | Remove from readonly, or compute via determination |
| Draft activation fails silently | Missing `total etag` or stale `LocalLastChangedAt` mapping | Add etag, ensure mapping correctly populates the field |
| `Lock not acquired` | Two sessions or missing `lock master` | Verify lock declaration; check for orphaned drafts |
| FE shows wrong currency | Missing `@Semantics.amount.currencyCode` annotation in root CDS | Add annotation, regenerate metadata |
| `Authorization failed` at save | DCL view rejects the user, or instance auth returns deny | Check DCL `pfcg_auth` and `get_instance_authorizations` |
| Determination loops forever | Determination triggers on a field it also writes | Use `on modify` triggers carefully; consider `on save` |
| Side effect not firing | Annotation in CDS not in `.ddlx`, or wrong path | Move to `.ddlx`, verify property path |

## Testing this BO

See `testing.md`. Minimum tests:
- Validation: one happy case, one failure case → check `failed`/`reported` tables.
- Determination: input state → check output state via `cl_abap_behv_test_environment`.
- Action: precondition → action → postcondition.
- Authorization: privileged vs unprivileged user → check rejection.
- DCL: select via privileged and non-privileged context, compare row counts.
