# Fiori annotations — metadata extension (.ddlx)

UI annotations go in **metadata extension files**, not in the CDS view. Keeps the CDS reusable for non-UI consumers and lets you version UI separately.

## Skeleton

`zdswf_c_<entity>.ddlx`:
```abap
@Metadata.layer: #CORE

@UI: {
  headerInfo: {
    typeName:       '<Entity>',
    typeNamePlural: '<Entities>',
    title:          { type: #STANDARD, value: '<BusinessField>' },
    description:    { type: #STANDARD, value: 'Description' }
  }
}
annotate entity ZDSWF_C_<Entity> with
{
  @UI.facet: [
    { id:       'GeneralInfo',
      type:     #COLLECTION,
      label:    'General Information',
      position: 10 },
    { id:       'GeneralInfoIdent',
      parentId: 'GeneralInfo',
      type:     #FIELDGROUP_REFERENCE,
      targetQualifier: 'GeneralInformation',
      position: 10 },
    { id:       'Items',
      type:     #LINEITEM_REFERENCE,
      label:    'Items',
      position: 20,
      targetElement: '_Items' }
  ]
  <Entity>Uuid;

  @UI.hidden: true
  <Entity>Uuid;

  @UI: {
    lineItem:       [ { position: 10, importance: #HIGH }, { type: #FOR_ACTION, dataAction: 'Approve', label: 'Approve' } ],
    identification: [ { position: 10 } ],
    fieldGroup:     [ { qualifier: 'GeneralInformation', position: 10 } ],
    selectionField: [ { position: 10 } ]
  }
  @EndUserText.label: 'Business Field'
  <BusinessField>;

  @UI: {
    lineItem:       [ { position: 20, importance: #HIGH } ],
    identification: [ { position: 20 } ],
    fieldGroup:     [ { qualifier: 'GeneralInformation', position: 20 } ]
  }
  Amount;

  @UI.hidden: true
  CurrencyCode;

  @UI: {
    lineItem:       [ { position: 30, importance: #MEDIUM, criticality: 'ApprovalStatusCriticality' } ],
    identification: [ { position: 30 } ]
  }
  ApprovalStatus;

  @UI.hidden: true
  ApprovalStatusCriticality;
}
```

## Annotation taxonomy

| Annotation | What it does |
|---|---|
| `headerInfo` | Title / subtitle / type name on Object Page header |
| `lineItem` | Column in the List Report table |
| `identification` | Field in the "General Information" form of Object Page |
| `fieldGroup` | Reusable group of fields, referenced from facets |
| `facet` | Section structure of Object Page (collection, reference, etc.) |
| `selectionField` | Filter bar field in List Report |
| `dataPoint` | KPI / metric card (OVP, ALP) |
| `chart` | Chart definition (ALP, OVP) |
| `presentationVariant` | Default sort, group, visualization |
| `selectionVariant` | Saved filter combo |
| `hidden` | Field exists but never shown |
| `lineItem.type: #FOR_ACTION` | Render button in row for an action |
| `lineItem.criticality` | Color the row/cell by criticality field (1=neg, 2=crit, 3=pos) |

## Value helps

In CDS or `.ddlx`, on the field:

```abap
@Consumption.valueHelpDefinition: [
  { entity: { name: 'I_CompanyCodeStdVH', element: 'CompanyCode' },
    additionalBinding: [
      { localElement: 'ChartOfAccounts',
        element: 'ChartOfAccounts',
        usage: #RESULT } ]
  }
]
CompanyCode
```

`additionalBinding` cascades — selecting a Company Code also fills Chart of Accounts.

## Search and filter

In the CDS root projection:
```abap
@Search.searchable: true
```

On fields:
```abap
@Search.defaultSearchElement: true
@Search.fuzzinessThreshold: 0.8
<field>
```

## Side effects

When changing field A should refresh field B, in `.ddlx`:

```abap
@UI.sideEffects: [
  { sourceProperties: [ 'BusinessField' ],
    targetProperties: [ 'DerivedField', 'Amount' ] }
]
```

Side effects also fire actions:
```abap
@UI.sideEffects: [
  { sourceEntities: [ '_Items' ],
    targetProperties: [ 'TotalAmount' ] }
]
```

## Criticality

Server-side field returning 0/1/2/3/5 (neutral/negative/critical/positive/info). Annotation in `.ddlx`:
```abap
@UI.lineItem: [ { position: 10, criticality: 'StatusCriticality' } ]
Status
```

`StatusCriticality` is a computed field in the CDS or projection.

## Semantic objects (cross-app navigation)

```abap
@Consumption.semanticObject: 'Material'
MaterialNumber
```

The framework renders the field as a navigation link to the Material Fiori app if the user has authorization.

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| Field doesn't appear in LR table | Missing `@UI.lineItem` | Add with position |
| Field appears but always empty | Field not in projection or wrong path | Add to projection |
| Side effect doesn't refresh | Wrong `targetProperties` path | Use slash-delimited path for navigations |
| Filter bar empty | No `@UI.selectionField` annotations | Add to filterable fields |
| Action button missing | Missing `@UI.lineItem: [{type: #FOR_ACTION, ...}]` | Add to root projection |
| Cascading value help broken | Wrong `additionalBinding` field names | Names must exist in both source and result entity |
