# Pattern: managed RAP BO with draft

Copy this file's contents into the right `objects/` subfolders, renaming files to `zdswf_*` and substituting `{{Entity}}` / `{{Module}}` / `{{BusinessField}}` placeholders.

This pattern delivers: persistent table → root + child CDS → BDEF (managed, draft) → BIL skeleton → projection + ddlx → service definition + binding.

---

## File: `objects/data/zdswf_{{entity}}.tabl`

```abap
@EndUserText.label : 'ZDSWF {{Entity}}'
@AbapCatalog.enhancement.category : #NOT_EXTENSIBLE
@AbapCatalog.tableCategory : #TRANSPARENT
@AbapCatalog.deliveryClass : #A
@AbapCatalog.dataMaintenance : #LIMITED
define table zdswf_{{entity}} {
  key client                 : mandt not null;
  key {{entity}}_uuid        : sysuuid_x16 not null;
  {{business_field}}         : zdswf_{{entity}}_{{business_field}};   " data element
  amount                     : zdswf_amount;
  currency_code              : waers;
  approval_status            : zdswf_approval_status;
  created_by                 : abp_creation_user;
  created_at                 : abp_creation_tstmpl;
  last_changed_by            : abp_locinst_lastchange_user;
  last_changed_at            : abp_locinst_lastchange_tstmpl;
  local_last_changed_at      : abp_lastchange_tstmpl;
}
```

## File: `objects/data/zdswf_{{entity}}item.tabl`

```abap
@EndUserText.label : 'ZDSWF {{Entity}} Item'
@AbapCatalog.enhancement.category : #NOT_EXTENSIBLE
@AbapCatalog.tableCategory : #TRANSPARENT
@AbapCatalog.deliveryClass : #A
define table zdswf_{{entity}}item {
  key client            : mandt not null;
  key item_uuid         : sysuuid_x16 not null;
  parent_uuid           : sysuuid_x16 not null;
  position              : numc4;
  description           : zdswf_description;
  amount                : zdswf_amount;
  currency_code         : waers;
  created_by            : abp_creation_user;
  created_at            : abp_creation_tstmpl;
  last_changed_by       : abp_locinst_lastchange_user;
  last_changed_at       : abp_locinst_lastchange_tstmpl;
  local_last_changed_at : abp_lastchange_tstmpl;
}
```

## File: `objects/cds/zdswf_r_{{entity}}.ddls`

```abap
@AccessControl.authorizationCheck: #CHECK
@EndUserText.label: 'ZDSWF {{Entity}} Root'
@Metadata.allowExtensions: true
define root view entity ZDSWF_R_{{Entity}}
  as select from zdswf_{{entity}}
  composition [0..*] of ZDSWF_R_{{Entity}}Item as _Items
{
  key {{entity}}_uuid     as {{Entity}}Uuid,
      {{business_field}}  as {{BusinessField}},
      @Semantics.amount.currencyCode: 'CurrencyCode'
      amount              as Amount,
      @Semantics.currencyCode: true
      currency_code       as CurrencyCode,
      approval_status     as ApprovalStatus,
      @Semantics.user.createdBy: true
      created_by          as CreatedBy,
      @Semantics.systemDateTime.createdAt: true
      created_at          as CreatedAt,
      @Semantics.user.lastChangedBy: true
      last_changed_by     as LastChangedBy,
      @Semantics.systemDateTime.lastChangedAt: true
      last_changed_at     as LastChangedAt,
      @Semantics.systemDateTime.localInstanceLastChangedAt: true
      local_last_changed_at as LocalLastChangedAt,
      _Items
}
```

## File: `objects/cds/zdswf_r_{{entity}}item.ddls`

```abap
@AccessControl.authorizationCheck: #CHECK
@EndUserText.label: 'ZDSWF {{Entity}} Item Root'
@Metadata.allowExtensions: true
define view entity ZDSWF_R_{{Entity}}Item
  as select from zdswf_{{entity}}item
  association to parent ZDSWF_R_{{Entity}} as _{{Entity}}
    on $projection.{{Entity}}Uuid = _{{Entity}}.{{Entity}}Uuid
{
  key item_uuid            as ItemUuid,
      parent_uuid          as {{Entity}}Uuid,
      position             as Position,
      description          as Description,
      @Semantics.amount.currencyCode: 'CurrencyCode'
      amount               as Amount,
      @Semantics.currencyCode: true
      currency_code        as CurrencyCode,
      @Semantics.systemDateTime.localInstanceLastChangedAt: true
      local_last_changed_at as LocalLastChangedAt,
      _{{Entity}}
}
```

## File: `objects/behavior/zdswf_r_{{entity}}.bdef`

(See `references/rap-skeleton.md` for full skeleton — abridged here.)

```abap
managed implementation in class zdswf_bp_{{entity}} unique;
strict ( 2 );
with draft;

define behavior for ZDSWF_R_{{Entity}} alias {{Entity}}
persistent table zdswf_{{entity}}
draft table zdswf_d_{{entity}}
lock master
total etag LocalLastChangedAt
authorization master ( instance )
{
  field ( numbering : managed, readonly ) {{Entity}}Uuid;
  field ( readonly ) CreatedBy, CreatedAt, LastChangedBy, LastChangedAt, LocalLastChangedAt;
  field ( mandatory ) {{BusinessField}};

  create;
  update;
  delete;
  association _Items { create; with draft; }

  draft action Edit;
  draft action Activate optimized;
  draft action Discard;
  draft action Resume;
  draft determine action Prepare { validation ValidateBusinessField; }

  validation ValidateBusinessField on save { create; update; field {{BusinessField}}; }
  determination CalculateApprovalStatus on modify { field ApprovalStatus; }
  action ( features : instance ) Approve result [1] $self;

  mapping for zdswf_{{entity}}
  {
    {{Entity}}Uuid        = {{entity}}_uuid;
    {{BusinessField}}     = {{business_field}};
    Amount                = amount;
    CurrencyCode          = currency_code;
    ApprovalStatus        = approval_status;
    CreatedBy             = created_by;
    CreatedAt             = created_at;
    LastChangedBy         = last_changed_by;
    LastChangedAt         = last_changed_at;
    LocalLastChangedAt    = local_last_changed_at;
  }
}

define behavior for ZDSWF_R_{{Entity}}Item alias {{Entity}}Item
persistent table zdswf_{{entity}}item
draft table zdswf_d_{{entity}}item
lock dependent by _{{Entity}}
authorization dependent by _{{Entity}}
{
  field ( readonly ) ItemUuid, {{Entity}}Uuid;
  update;
  delete;
  association _{{Entity}} { with draft; }
  mapping for zdswf_{{entity}}item { ItemUuid = item_uuid; {{Entity}}Uuid = parent_uuid; Position = position; Description = description; Amount = amount; CurrencyCode = currency_code; LocalLastChangedAt = local_last_changed_at; }
}
```

## File: `objects/behavior-impl/zdswf_bp_{{entity}}.clas.abap`

See `references/rap-skeleton.md` section 4 for full BIL skeleton.

## File: `objects/cds/zdswf_c_{{entity}}.ddls` and `.ddlx`

See `references/rap-skeleton.md` section 5 and `references/fiori-annotations.md`.

## File: `objects/service/zdswf_sd_{{entity}}.srvd`

```abap
@EndUserText.label: 'ZDSWF {{Entity}} Service'
define service ZDSWF_SD_{{Entity}} {
  expose ZDSWF_C_{{Entity}}     as {{Entity}};
  expose ZDSWF_C_{{Entity}}Item as {{Entity}}Item;
}
```

## File: `objects/service/zdswf_sb_{{entity}}.srvb`

OData V4 - UI. Bound to `ZDSWF_SD_{{Entity}}`.

## File: `objects/auth/zdswf_i_{{entity}}.dcls`

```abap
@EndUserText.label: '{{Entity}} access control'
@MappingRole: true
define role ZDSWF_I_{{Entity}} {
  grant select on ZDSWF_R_{{Entity}}
    where ( inheriting conditions from entity ZDSWF_R_{{Entity}} )
       or ( CreatedBy ) = aspect user
       or ( {{auth_field}} ) = aspect pfcg_auth ( Z_ZDSWF_{{MODULE}}, {{AUTH_FIELD}}, ACTVT = '03' );
}
```

## Test class

See `references/testing.md` for the test skeleton.
