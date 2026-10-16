# MDK — Mobile Development Kit on BTP

When to use vs alternatives:

| Choice | When |
|---|---|
| **MDK** | Cross-platform mobile app, offline-first, OData backend, metadata-driven, target is enterprise users with managed devices |
| UI5 mobile | Browser-first, online-only, lightweight |
| SAP Build Apps | Citizen developer, simple workflows, online |
| Native (Swift / Kotlin) | Heavy device integration (AR, advanced camera, BLE), single platform |

## Architecture

```
[ MDK Client App on iOS / Android / Windows ]
        │  metadata-driven UI (pages, actions, rules)
        ▼
[ MDK Runtime ]
        │  online + offline OData
        ▼
[ BTP Mobile Services ] ── destinations, push, logs, registration
        │
        ▼
[ Service Binding ZDSWF_SB_<name> — OData V4 ]
        │
        ▼
[ RAP business object ZDSWF_R_<Entity> ]
```

## Prerequisites

- BTP subaccount with **Mobile Services** booster run.
- **Business Application Studio** dev space with MDK Apps extension.
- BTP destination to S/4HANA configured (Mobile Services → Settings → Destinations).
- Mobile Services feature: Offline OData V4 enabled.

## Project structure

```
zdswf-mdk-<app>/
  Actions/
    Navigation/
      NavToObjectPage.action
    OData/
      CreateEntity.action
      UpdateEntity.action
  Application.app
  Globals/
    AppParameters.global
  Pages/
    Main/
      Main.page
    Detail/
      Detail.page
  Rules/
    Navigation.js
    Validation.js
    Formatters.js
  Services/
    com.sap.zdswf.service           " online OData definition
    com.sap.zdswf.offline.service   " offline-first definition
  i18n/
    i18n.properties
    i18n_es.properties
  manifest.json
```

## Application.app

```json
{
  "MainPage": "/zdswf-mdk-<app>/Pages/Main/Main.page",
  "OnLaunch": "/zdswf-mdk-<app>/Rules/OnLaunch.js",
  "OnWillUpdate": "/zdswf-mdk-<app>/Rules/OnWillUpdate.js",
  "OnDidUpdate": "/zdswf-mdk-<app>/Rules/OnDidUpdate.js",
  "Services": [
    "/zdswf-mdk-<app>/Services/com.sap.zdswf.offline.service"
  ],
  "Styles": "/zdswf-mdk-<app>/Styles/Styles.less",
  "Version": "1.0"
}
```

## Page metadata example

```json
{
  "Caption": "<Entities>",
  "Controls": [
    {
      "_Type": "Control.Type.SectionedTable",
      "Sections": [
        {
          "_Type": "Section.Type.ObjectTable",
          "Target": {
            "EntitySet": "<Entity>",
            "Service": "/zdswf-mdk-<app>/Services/com.sap.zdswf.offline.service",
            "QueryOptions": "$orderby=LastChangedAt desc"
          },
          "ObjectCell": {
            "Title": "{<BusinessField>}",
            "Subhead": "{ApprovalStatus}",
            "Description": "{Amount} {CurrencyCode}",
            "OnPress": "/zdswf-mdk-<app>/Actions/Navigation/NavToObjectPage.action"
          }
        }
      ]
    }
  ]
}
```

## Offline OData service

`com.sap.zdswf.offline.service`:
```json
{
  "DestinationName": "com.sap.zdswf",
  "OfflineEnabled": true,
  "DefiningRequests": [
    {
      "Name": "Entities",
      "Query": "<Entity>?$expand=_Items&$orderby=LastChangedAt desc",
      "AutomaticallyRetrievesStreams": false
    }
  ],
  "OnlineODataProvider": {
    "Name": "com.sap.zdswf.online"
  }
}
```

Hard rules:
- `OfflineEnabled: true` and `DefiningRequests` populated. Without `DefiningRequests` nothing syncs.
- Defining requests must match what the page actually queries; mismatched queries silently return empty offline.
- `$expand` chains explicitly listed. Offline OData does not auto-expand.

## Actions

OData create:
```json
{
  "_Type": "Action.Type.ODataService.CreateEntity",
  "Target": {
    "Service": "/zdswf-mdk-<app>/Services/com.sap.zdswf.offline.service",
    "EntitySet": "<Entity>"
  },
  "Properties": {
    "<BusinessField>": "{#Control:BusinessFieldCtl/#Value}",
    "Amount": "{#Control:AmountCtl/#Value}",
    "CurrencyCode": "USD"
  },
  "OnSuccess": "/zdswf-mdk-<app>/Rules/Navigation/AfterCreate.js",
  "OnFailure": "/zdswf-mdk-<app>/Rules/HandleODataError.js"
}
```

JS rule (`Validation.js`):
```js
export default function ValidateAmount(clientAPI) {
  const amount = clientAPI.evaluateTargetPath('#Control:AmountCtl/#Value');
  if (!amount || Number(amount) <= 0) {
    clientAPI.executeAction('/zdswf-mdk-<app>/Actions/ShowError.action');
    return false;
  }
  return true;
}
```

Hard rules:
- JS rules under 50 lines. Beyond that → push to RAP action.
- `export default function` signature.
- No external `fetch` — go through MDK service abstraction.
- No `eval`.

## Push notifications

Configured in Mobile Services console:
- *Push notifications* feature enabled.
- APNs (iOS) / FCM (Android) certificates uploaded.
- Topics defined for app subscriptions.

MDK app subscribes:
```js
clientAPI.executeAction({
  Name: '/zdswf-mdk-<app>/Actions/SubscribeToTopic.action',
  Properties: { Topic: 'Approvals' }
});
```

Server side: from RAP action, fire push via Mobile Services REST API (released communication scenario).

## Mobile Services setup

| Feature | Why |
|---|---|
| Mobile Connectivity | Destination to S/4HANA |
| Mobile App Update | OTA update of MDK metadata |
| Mobile Offline Access | Offline OData store |
| Mobile Push Notification | APNs / FCM |
| Mobile Sample OData ESPM | testing (delete in prod) |
| Mobile Settings Exchange | dynamic settings |
| Mobile Augmented Reality | only if AR scope |
| Mobile Client Resources | branding |

## Deployment lifecycle

1. Develop in BAS MDK Apps editor.
2. Deploy to Mobile Services — *Deploy* button in BAS.
3. Register devices via QR code or onboarding email.
4. Iterate: change metadata, redeploy, app pulls updated metadata at next launch (no app store re-submission for metadata-only changes).
5. Binary updates (MDK client itself) go through MDM / store.

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| Empty page offline, works online | Defining request mismatch | Match query options exactly |
| Action fails offline silently | OData operation not supported offline | Queue for sync, surface conflicts on next sync |
| Push not received | Topic mismatch or cert expired | Verify Mobile Services console |
| Metadata change not reflected | Client cached old version | Force pull via OnLaunch or version bump |
| Sync conflict | Two devices modified same record | Implement conflict handler rule |
